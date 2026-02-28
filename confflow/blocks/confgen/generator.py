#!/usr/bin/env python3

"""
ConfGen - Conformer Generator (v1.0).

Systematic conformational search based on RDKit.
Dual-mode architecture (importable as library or runnable as script).
"""

from __future__ import annotations

import itertools
import logging
import multiprocessing
import os
import traceback
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

# --- Library Imports ---
try:
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem, rdMolTransforms

    # Suppress RDKit warnings (e.g. valence errors) to avoid noisy output for TS/metal systems
    RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined]
except ImportError as e:
    raise ImportError("RDKit not found. Please install it (e.g. conda install rdkit).") from e

from ...core.console import create_progress
from ...core.contracts import ExitCode, cli_output_to_txt
from ...core.pairs import normalize_pair_list
from ...core.utils import get_numba_jit, index_to_letter_prefix
from .collision import GV_RADII_ARRAY, check_clash_core
from .mapping import transfer_chain_indices
from .rotations import (
    _build_chain_rotations,
    _parse_chain,
    _resolve_angle_lists,
    _rotate_atoms_around_bond,
    _validate_chain_bonds,
)

logger = logging.getLogger("confflow.confgen")

__all__ = [
    "init_worker",
    "process_task",
    "load_mol_from_xyz",
    "get_rotatable_bonds",
    "write_xyz",
    "run_generation",
    "main",
]

numba = get_numba_jit("confflow.confgen")

# ------------------------------------------------------------------------------
# Multiprocessing worker
# ------------------------------------------------------------------------------

w_mol: Any = None
w_conf: Any = None
w_bonds: Any = None
w_clash: Any = None
w_topo: Any = None
w_atoms: Any = None
w_opt: Any = None


def init_worker(mol, conf, bonds, clash, topo, atoms, opt):
    global w_mol, w_conf, w_bonds, w_clash, w_topo, w_atoms, w_opt
    w_mol, w_conf, w_bonds = mol, conf, bonds
    w_clash, w_topo, w_atoms, w_opt = clash, topo, atoms, opt


def process_task(angle_combo):
    # 1. Generate conformer (coordinate array)
    temp_conf = Chem.Conformer(w_conf)
    coords = np.array(temp_conf.GetPositions(), dtype=np.float64)

    # w_bonds supports two modes:
    # - Auto mode: (n1, a1, a2, n2) -> SetDihedralDeg
    # - Manual chain mode: (a1, a2, atoms_to_rotate_array) -> geometric rotation
    for bond_spec, angle in zip(w_bonds, angle_combo):
        try:
            if len(bond_spec) == 4:
                n1, a1, a2, n2 = bond_spec
                rdMolTransforms.SetDihedralDeg(
                    temp_conf, int(n1), int(a1), int(a2), int(n2), float(angle)
                )
                coords = np.array(temp_conf.GetPositions(), dtype=np.float64)
            else:
                a1, a2, atom_indices = bond_spec
                _rotate_atoms_around_bond(coords, int(a1), int(a2), atom_indices, float(angle))
        except (ValueError, RuntimeError):
            return None

    # Write back to conformer
    for idx in range(coords.shape[0]):
        temp_conf.SetAtomPosition(idx, coords[idx])

    # 2. Pre-optimization (optional)
    if w_opt:
        try:
            m_opt = Chem.Mol(w_mol)
            m_opt.RemoveAllConformers()
            m_opt.AddConformer(temp_conf)
            AllChem.MMFFOptimizeMolecule(m_opt, maxIters=200, mmffVariant="MMFF94s")  # type: ignore[attr-defined]
            temp_conf = m_opt.GetConformer(0)
        except (RuntimeError, ValueError) as e:
            logger = logging.getLogger("confflow.confgen")
            logger.debug(f"MMFF optimization failed: {e}")

    new_coords = temp_conf.GetPositions()

    # 3. Clash filtering
    is_clash = check_clash_core(w_atoms, new_coords, w_clash, w_topo, GV_RADII_ARRAY)

    if is_clash:
        return None
    return new_coords


# ------------------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------------------


def load_mol_from_xyz(filename, bond_coeff):
    """Load molecular structure from an XYZ file.

    Parameters
    ----------
    filename : str
        Path to the XYZ file.
    bond_coeff : float
        Scaling factor for covalent-radii bond detection.

    Returns
    -------
    Mol
        RDKit molecule with 3D coordinates and detected bonds.

    Raises
    ------
    FileNotFoundError
        If the input file does not exist.
    ValueError
        If the file is empty or has an invalid format.
    """
    # Validate file existence
    if not os.path.exists(filename):
        raise FileNotFoundError(f"input file does not exist: {filename}")
    if not os.path.isfile(filename):
        raise ValueError(f"path is not a file: {filename}")
    if os.path.getsize(filename) == 0:
        raise ValueError(f"file is empty: {filename}")

    # Read XYZ
    symbols, positions = [], []
    with open(filename) as f:
        lines = f.readlines()

    if len(lines) < 3:
        raise ValueError(f"XYZ file format error, insufficient lines: {filename}")

    try:
        num_atoms = int(lines[0].strip())
    except ValueError as e:
        raise ValueError(f"cannot parse atom count: {lines[0].strip()}") from e

    if len(lines) < num_atoms + 2:
        raise ValueError(f"file declares {num_atoms} atoms but has insufficient lines")

    for line in lines[2 : 2 + num_atoms]:
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"coordinate line format error: {line.strip()}")
        symbols.append(parts[0])
        positions.append((float(parts[1]), float(parts[2]), float(parts[3])))

    # Build RDKit Mol
    rw_mol = Chem.RWMol()
    for s in symbols:
        rw_mol.AddAtom(Chem.Atom(s))
    atom_nums = [atom.GetAtomicNum() for atom in rw_mol.GetAtoms()]

    conf = Chem.Conformer(num_atoms)
    for i in range(num_atoms):
        conf.SetAtomPosition(i, positions[i])
    rw_mol.AddConformer(conf)

    # Topology detection — use cKDTree instead of O(N²) full matrix
    # Significantly reduces memory usage and computation time for large molecules (>500 atoms)
    radii = np.array([GV_RADII_ARRAY[z] if z < 120 else 1.5 for z in atom_nums])
    pos_array = np.array(positions)

    # Max bond threshold = 2 * max_radius * bond_coeff, used for cKDTree search
    max_threshold = 2.0 * float(np.max(radii)) * bond_coeff
    tree = cKDTree(pos_array)
    pairs = tree.query_pairs(max_threshold, output_type="ndarray")  # shape (M, 2)

    for i, j in pairs:
        ri, rj = radii[i], radii[j]
        threshold = (ri + rj) * bond_coeff
        dist = float(np.linalg.norm(pos_array[i] - pos_array[j]))
        if 0.4 < dist < threshold:
            rw_mol.AddBond(int(i), int(j), Chem.BondType.SINGLE)

    mol = rw_mol.GetMol()
    try:
        Chem.SanitizeMol(mol)
    except Exception:  # RDKit raises arbitrary exception types; fall back gracefully
        mol.UpdatePropertyCache(strict=False)

    from ...core.console import console, print_kv

    # --- Print bond topology summary ---
    print_kv("Topology", f"{mol.GetNumBonds()} bonds detected (1-based)")
    bonds_str = []
    for b in mol.GetBonds():
        a1 = b.GetBeginAtom()
        a2 = b.GetEndAtom()
        bonds_str.append(f"{a1.GetIdx()+1}({a1.GetSymbol()})-{a2.GetIdx()+1}({a2.GetSymbol()})")

    # Dynamic columns based on console width
    cw = console.width or 80
    # Indent is 14 chars ("  Topology  " label area); subtract it before dividing.
    num_cols = 4 if cw >= 75 else 3 if cw >= 58 else 2
    col_w = (cw - 14) // num_cols

    for i in range(0, len(bonds_str), num_cols):
        chunk = bonds_str[i : i + num_cols]
        line_str = "".join(f"{s:<{col_w}}" for s in chunk)
        console.print(f"[muted]{'':14}{line_str}[/muted]")

    return mol


def get_rotatable_bonds(mol, no_rot, force_rot):
    # Legacy interface compatibility: parameters retained but functionality removed.
    del mol, no_rot, force_rot
    raise RuntimeError(
        "automatic flexible bond detection has been removed: use --chain/--steps/--angles to specify rotation chains and angles manually"
    )


def write_xyz(mol, conformers, filename):
    with open(filename, "w") as f:
        syms = [a.GetSymbol() for a in mol.GetAtoms()]
        natoms = len(syms)
        for i, item in enumerate(conformers):
            if isinstance(item, dict):
                coords = item.get("coords")
                cid = item.get("cid")
            else:
                coords = item
                cid = None

            # Assign a stable ID for downstream workflow traceability
            if not cid:
                cid = f"A{i+1:06d}"
            f.write(f"{natoms}\nConformer {i+1} | CID={cid}\n")
            for j, s in enumerate(syms):
                assert coords is not None
                x, y, z = coords[j]
                f.write(f"{s:<4s} {x:12.6f} {y:12.6f} {z:12.6f}\n")


# ------------------------------------------------------------------------------
# run_generation sub-steps
# ------------------------------------------------------------------------------


def _modify_topology(
    mol: Any,
    add_bond: list[list[int]] | None,
    del_bond: list[list[int]] | None,
) -> tuple[Any, bool]:
    """Apply add/del bond topology corrections to the molecule.

    Parameters
    ----------
    mol : Any
        RDKit molecule object.
    add_bond : list[list[int]] or None
        Bonds to add (1-based index pairs).
    del_bond : list[list[int]] or None
        Bonds to remove (1-based index pairs).

    Returns
    -------
    tuple[Any, bool]
        (modified_mol, was_modified)
    """
    rw_mol = Chem.RWMol(mol)
    is_mod = False

    if del_bond:
        for p in del_bond:
            if len(p) == 2 and rw_mol.GetBondBetweenAtoms(p[0] - 1, p[1] - 1):
                rw_mol.RemoveBond(p[0] - 1, p[1] - 1)
                is_mod = True

    if add_bond:
        for p in add_bond:
            if len(p) == 2 and not rw_mol.GetBondBetweenAtoms(p[0] - 1, p[1] - 1):
                rw_mol.AddBond(p[0] - 1, p[1] - 1, Chem.BondType.SINGLE)
                is_mod = True

    if is_mod:
        mol = rw_mol.GetMol()
        try:
            Chem.SanitizeMol(mol)
        except Exception:  # RDKit raises arbitrary exception types; fall back gracefully
            mol.UpdatePropertyCache(strict=False)
        logging.info(f"after manual correction, now {mol.GetNumBonds()} bonds.")

    return mol, is_mod


def _run_parallel_confgen(
    mol: Any,
    rot_bonds: list[tuple[int, int, Any]],
    angle_lists: list[list[float]],
    clash_threshold: float,
    optimize: bool,
) -> list[Any]:
    """Run parallel conformer generation and return valid coordinate arrays.

    Parameters
    ----------
    mol : Any
        RDKit molecule with initial conformer.
    rot_bonds : list[tuple[int, int, Any]]
        Rotatable bond specifications.
    angle_lists : list[list[float]]
        Angle values for each rotatable bond.
    clash_threshold : float
        Clash detection threshold.
    optimize : bool
        Whether to apply MMFF94s pre-optimization.

    Returns
    -------
    list[Any]
        List of valid coordinate arrays.
    """
    topo_mat = Chem.GetDistanceMatrix(mol).astype(np.int64)
    atom_nums = np.array([a.GetAtomicNum() for a in mol.GetAtoms()], dtype=np.int64)

    per_bond_angles = angle_lists
    combos = list(itertools.product(*per_bond_angles))
    total_tasks = len(combos)

    cpu_count = multiprocessing.cpu_count()
    init_args = (
        mol,
        mol.GetConformer(0),
        rot_bonds,
        clash_threshold,
        topo_mat,
        atom_nums,
        optimize,
    )

    with multiprocessing.Pool(cpu_count, initializer=init_worker, initargs=init_args) as pool:
        chunk = max(1, total_tasks // (cpu_count * 10))
        results: list[Any] = []
        with create_progress() as progress:
            task_id = progress.add_task("ConfGen", total=total_tasks)
            for res in pool.imap(process_task, combos, chunksize=chunk):
                results.append(res)
                progress.advance(task_id)

    return [r for r in results if r is not None]


# ------------------------------------------------------------------------------
# Library API
# ------------------------------------------------------------------------------


def run_generation(
    input_files,
    angle_step=120,
    bond_threshold=1.15,
    clash_threshold=0.65,
    add_bond=None,
    del_bond=None,
    no_rotate=None,
    force_rotate=None,
    optimize=False,
    confirm=False,
    chains: list[str] | None = None,
    chain_steps: list[str] | None = None,
    chain_angles: list[str] | None = None,
    rotate_side: str = "left",
):
    """Entry point for conformer generation.

    Parameters
    ----------
    input_files : str or list[str]
        XYZ file path(s).
    angle_step : int
        Rotation angle step size in degrees.
    bond_threshold : float
        Bond detection coefficient (default 1.15).
    clash_threshold : float
        Clash detection coefficient (default 0.65).
    add_bond : list[list[int]] or None
        Bonds to force-add (1-based index pairs).
    del_bond : list[list[int]] or None
        Bonds to force-delete (1-based index pairs).
    no_rotate : list[list[int]] or None
        Bonds to exclude from rotation (1-based).
    force_rotate : list[list[int]] or None
        Bonds to force-rotate (1-based).
    optimize : bool
        Whether to apply MMFF pre-optimization.
    confirm : bool
        Whether to prompt for user confirmation.
    chains : list[str] or None
        Chain definitions (1-based, dash-separated).
    chain_steps : list[str] or None
        Per-chain step sizes.
    chain_angles : list[str] or None
        Per-chain explicit angle lists.
    rotate_side : str
        Which side to rotate ('left' or 'right').

    Returns
    -------
    list[dict]
        List of generated conformer data dicts.
    """
    from ...core.console import console

    # Normalize bond pair inputs (allows '1-2' format)
    add_bond = normalize_pair_list(add_bond)
    del_bond = normalize_pair_list(del_bond)
    no_rotate = normalize_pair_list(no_rotate)
    force_rotate = normalize_pair_list(force_rotate)

    # Ensure input is a list
    if isinstance(input_files, str):
        input_files = [input_files]

    master_mol = None
    ref_mol = None
    ref_parsed_chains = None
    all_confs_data = []

    from ...core.console import error, warning

    for file_idx, xyz_file in enumerate(input_files):
        cid_prefix = index_to_letter_prefix(file_idx)
        local_count = 0
        console.print(f"  [muted]·[/muted]  {os.path.basename(xyz_file)}")

        try:
            mol = load_mol_from_xyz(xyz_file, bond_threshold)
            if master_mol is None:
                master_mol = Chem.Mol(mol)

            # Pre-parse chains (reference only); mapping performed after topology modification
            parsed_chains = None
            if chains and ref_parsed_chains is None:
                ref_parsed_chains = [_parse_chain(c) for c in chains]

            # Topology modification
            mol, _ = _modify_topology(mol, add_bond, del_bond)

            # Record reference molecule (using modified topology)
            if file_idx == 0 and ref_mol is None:
                ref_mol = Chem.Mol(mol)

            # Chain mapping (first input is reference; others mapped via topology)
            if chains:
                if file_idx == 0:
                    parsed_chains = ref_parsed_chains
                else:
                    if ref_mol is None or ref_parsed_chains is None:
                        raise ValueError(
                            "cannot establish reference chain definition, check input order"
                        )
                    parsed_chains = [
                        transfer_chain_indices(ref_mol, mol, ch) for ch in ref_parsed_chains
                    ]

            if parsed_chains:
                _validate_chain_bonds(mol, parsed_chains, xyz_file)

            # Force-refresh RDKit ring perception
            try:
                mol.UpdatePropertyCache(strict=False)
            except (ValueError, RuntimeError):
                pass
            try:
                Chem.GetSymmSSSR(mol)
            except (ValueError, RuntimeError):
                pass

            # Rotatable bond determination: manual chain mode only
            if not chains:
                raise ValueError(
                    "use --chain to specify rotation chains (automatic flexible bond detection has been removed)"
                )

            if parsed_chains is None:
                parsed_chains = [_parse_chain(c) for c in chains]

            per_chain_angle_lists = _resolve_angle_lists(
                parsed_chains,
                chain_steps,
                chain_angles,
                angle_step,
            )
            rot_bonds, angle_lists = _build_chain_rotations(
                mol,
                parsed_chains,
                per_chain_angle_lists,
                no_rotate,
                force_rotate,
                rotate_side,
            )

            # Print rotatable bond information
            from ...core.console import print_kv as _pkv
            _pkv("Rotatable", f"{len(rot_bonds)} bonds")
            if rot_bonds:
                bond_items = []
                for i, b in enumerate(rot_bonds):
                    a1, a2, _ = b
                    aa1, aa2 = mol.GetAtomWithIdx(a1), mol.GetAtomWithIdx(a2)
                    bond_items.append(
                        f"{i+1}: {a1+1}({aa1.GetSymbol()}) - {a2+1}({aa2.GetSymbol()})"
                    )
                cw = console.width or 80
                col_w = (cw - 8) // 2
                for i in range(0, len(bond_items), 2):
                    chunk = bond_items[i : i + 2]
                    line_str = "".join(f"{s:<{col_w}}" for s in chunk)
                    console.print(f"[muted]{'':14}{line_str}[/muted]")

            from ...core.console import print_kv as _pkv2
            _pkv2("Clash", f"threshold = {clash_threshold}")

            if not rot_bonds:
                warning("No rotatable bonds. Skipping.")
                local_count += 1
                all_confs_data.append(
                    {
                        "coords": mol.GetConformer(0).GetPositions(),
                        "cid": f"{cid_prefix}{local_count:06d}",
                    }
                )
                continue

            if confirm:
                if input("Start generation? (y/n): ").lower() != "y":
                    continue

            valid_coords = _run_parallel_confgen(
                mol,
                rot_bonds,
                angle_lists,
                clash_threshold,
                optimize,
            )
            for coords in valid_coords:
                local_count += 1
                all_confs_data.append({"coords": coords, "cid": f"{cid_prefix}{local_count:06d}"})

        except (ValueError, RuntimeError, OSError) as e:
            error(f"Failed to process {xyz_file}: {e}")
            traceback.print_exc()

    if all_confs_data and master_mol:
        out_name = "search.xyz"
        write_xyz(master_mol, all_confs_data, out_name)
    else:
        warning("No conformers generated.")

    return all_confs_data


# ------------------------------------------------------------------------------
# Command-line entry
# ------------------------------------------------------------------------------


def main():
    multiprocessing.freeze_support()
    import argparse

    parser = argparse.ArgumentParser(
        description="ConfGen v1.0 - Conformer Generator",
        epilog=(
            "Chain mode example (default angle step=120): confgen mol.xyz --chain 81-69-78-86-92 --steps 120,60,120,120 -y\n"
            "Chain mode (explicit angle list): confgen mol.xyz --chain 81-69-78-86-92 --angles '0,120,240;0,60,120,180;180;0,120' -y\n"
            "Optional: append angle_step at the end to override default, e.g.: confgen mol.xyz 60 --chain 81-69-78-86-92 -y\n"
            "Note: automatic flexible bond detection has been removed, --chain is required"
        ),
    )
    # Compatible positional arguments:
    # - Legacy usage: confgen mol.xyz 120
    # - Multi-file: confgen a.xyz b.xyz 120
    # Parse strategy: collect as inputs; if last token is an integer and not a file, treat as angle_step.
    parser.add_argument(
        "inputs", nargs="+", help="Input XYZ files (+ optional trailing angle_step, default=120)"
    )

    # Add -m alias for backward compatibility
    parser.add_argument(
        "-b",
        "-m",
        "--bond_threshold",
        type=float,
        default=1.15,
        help="Bond detection scale (default 1.15)",
    )
    parser.add_argument(
        "-c",
        "--clash_threshold",
        type=float,
        default=0.65,
        help="Clash threshold scale (default 0.65).",
    )

    parser.add_argument("--add_bond", nargs=2, type=int, action="append")
    parser.add_argument("--del_bond", nargs=2, type=int, action="append")
    parser.add_argument("--no_rotate", nargs=2, type=int, action="append")
    parser.add_argument("--force_rotate", nargs=2, type=int, action="append")

    # New: manual chain mode (auto flexible-bond detection removed)
    parser.add_argument(
        "--chain",
        action="append",
        default=None,
        help="specify a chain (1-based, dash-separated), e.g. 81-69-78-86-92; repeatable",
    )
    parser.add_argument(
        "--steps",
        action="append",
        default=None,
        help="per-chain per-bond angle step list (comma-separated), e.g. 120,60,120,120; repeatable, corresponds to --chain",
    )
    parser.add_argument(
        "--angles",
        action="append",
        default=None,
        help="per-chain per-bond angle list, e.g. '0,120,240;0,60,120,180;180;0,120' (';' separates bonds, ',' separates angles)",
    )
    parser.add_argument(
        "--rotate_side",
        choices=["left", "right"],
        default="left",
        help="which side to rotate around the chain: left=side containing first chain atom (default), right=side containing last chain atom",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Auto confirm")
    parser.add_argument("--optimize", "--opt", action="store_true", help="MMFF94s pre-optimization")

    args = parser.parse_args()

    # Parse inputs -> input_files + angle_step
    angle_step = 120
    input_files = list(args.inputs)
    if len(input_files) >= 2:
        last = input_files[-1]
        if last.isdigit() and not os.path.exists(last):
            angle_step = int(last)
            input_files = input_files[:-1]
    if not input_files:
        parser.error("Missing input XYZ file")

    with cli_output_to_txt(input_files[0]):
        run_generation(
            input_files=input_files,
            angle_step=angle_step,
            bond_threshold=args.bond_threshold,
            clash_threshold=args.clash_threshold,
            add_bond=args.add_bond,
            del_bond=args.del_bond,
            no_rotate=args.no_rotate,
            force_rotate=args.force_rotate,
            optimize=args.optimize,
            confirm=args.yes,
            chains=args.chain,
            chain_steps=args.steps,
            chain_angles=args.angles,
            rotate_side=args.rotate_side,
        )
    return ExitCode.SUCCESS


if __name__ == "__main__":
    main()
