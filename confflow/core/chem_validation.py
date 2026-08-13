#!/usr/bin/env python3

"""Chemistry validation services shared by workflow and block adapters.

This module is deliberately owned by ``core``.  The confgen block keeps
compatibility imports, but the validation implementation must not import the
block layer back from core.  RDKit/SciPy are loaded lazily so importing the
workflow/config packages remains usable in environments without chemistry
extras installed.
"""

from __future__ import annotations

import os
from typing import Any

from .data import GV_COVALENT_RADII
from .elements import canonicalize_element_symbol

__all__ = [
    "ChainValidator",
    "load_mol_from_xyz",
    "validate_chain_definitions",
]


def _parse_chain(chain_str: str) -> list[int]:
    """Parse a 1-based chain specification into zero-based atom indices."""
    parts = [part.strip() for part in chain_str.replace(",", "-").split("-") if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"chain format error: {chain_str}")
    try:
        atoms_1based = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"chain must be a list of integers: {chain_str}") from exc
    if any(atom <= 0 for atom in atoms_1based):
        raise ValueError(f"chain indices must be positive (1-based): {chain_str}")
    atoms = [atom - 1 for atom in atoms_1based]
    if len(set(atoms)) != len(atoms):
        raise ValueError(f"chain contains duplicate atoms: {chain_str}")
    return atoms


def _covalent_radius(atomic_number: int) -> float:
    """Return a usable covalent radius for bond detection."""
    if 0 <= atomic_number < len(GV_COVALENT_RADII):
        radius = float(GV_COVALENT_RADII[atomic_number])
        if radius > 0:
            return radius
    return 1.5


def load_mol_from_xyz(filename: str, bond_coeff: float):
    """Load an RDKit molecule with 3D coordinates and detected bonds.

    The implementation intentionally lives in ``core`` so workflow input
    validation can use it without depending on ``blocks.confgen``.  RDKit and
    SciPy remain optional at import time and are required only when this
    chemistry operation is called.
    """
    try:
        import numpy as np
        from rdkit import Chem, RDLogger
        from scipy.spatial import cKDTree
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "Chemistry validation requires RDKit and SciPy; install the chemistry extras"
        ) from exc

    RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined]

    if not os.path.exists(filename):
        raise FileNotFoundError(f"input file does not exist: {filename}")
    if not os.path.isfile(filename):
        raise ValueError(f"path is not a file: {filename}")
    if os.path.getsize(filename) == 0:
        raise ValueError(f"file is empty: {filename}")

    with open(filename, encoding="utf-8") as handle:
        lines = handle.readlines()
    if len(lines) < 3:
        raise ValueError(f"XYZ file format error, insufficient lines: {filename}")

    try:
        num_atoms = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError(f"cannot parse atom count: {lines[0].strip()}") from exc
    if num_atoms <= 0:
        raise ValueError(f"XYZ file must contain at least one atom: {filename}")
    if len(lines) < num_atoms + 2:
        raise ValueError(f"file declares {num_atoms} atoms but has insufficient lines")

    symbols: list[str] = []
    positions: list[tuple[float, float, float]] = []
    for line in lines[2 : 2 + num_atoms]:
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"coordinate line format error: {line.strip()}")
        symbols.append(canonicalize_element_symbol(parts[0]))
        positions.append((float(parts[1]), float(parts[2]), float(parts[3])))

    rw_mol = Chem.RWMol()
    for symbol in symbols:
        rw_mol.AddAtom(Chem.Atom(symbol))
    atom_numbers = [atom.GetAtomicNum() for atom in rw_mol.GetAtoms()]

    conformer = Chem.Conformer(num_atoms)
    for index, position in enumerate(positions):
        conformer.SetAtomPosition(index, position)
    rw_mol.AddConformer(conformer)

    radii = np.array([_covalent_radius(number) for number in atom_numbers])
    position_array = np.array(positions)
    max_threshold = 2.0 * float(np.max(radii)) * float(bond_coeff)
    tree = cKDTree(position_array)
    pairs = tree.query_pairs(max_threshold, output_type="ndarray")
    for i, j in pairs:
        threshold = (radii[i] + radii[j]) * float(bond_coeff)
        distance = float(np.linalg.norm(position_array[i] - position_array[j]))
        if 0.4 < distance < threshold:
            rw_mol.AddBond(int(i), int(j), Chem.BondType.SINGLE)

    mol = rw_mol.GetMol()
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        mol.UpdatePropertyCache(strict=False)

    # Keep the historical topology summary emitted by the public loader.
    from .console import console, print_kv

    print_kv("Topology", f"{mol.GetNumBonds()} bonds detected (1-based)")
    bonds = []
    for bond in mol.GetBonds():
        atom_a = bond.GetBeginAtom()
        atom_b = bond.GetEndAtom()
        bonds.append(
            f"{atom_a.GetIdx() + 1}({atom_a.GetSymbol()})-{atom_b.GetIdx() + 1}({atom_b.GetSymbol()})"
        )
    columns = 4 if (console.width or 80) >= 75 else 3 if (console.width or 80) >= 58 else 2
    column_width = ((console.width or 80) - 14) // columns
    for start in range(0, len(bonds), columns):
        line = "".join(f"{item:<{column_width}}" for item in bonds[start : start + columns])
        console.print(f"[muted]{'':14}{line}[/muted]")
    return mol


class ChainValidator:
    """Validate chain connectivity and atom identities across input molecules."""

    def __init__(self, chains: list[str]):
        self.raw_chains = chains
        self.parsed_chains = [_parse_chain(chain) for chain in chains] if chains else []

    def validate_mol(self, mol: Any, filename: str) -> list[dict[str, Any]]:
        """Return per-chain connectivity and element validation details."""
        del filename  # retained in the public signature for compatibility
        n_atoms = mol.GetNumAtoms()
        results: list[dict[str, Any]] = []
        for index, chain_indices in enumerate(self.parsed_chains):
            chain_info: dict[str, Any] = {
                "chain_id": index,
                "raw_chain": self.raw_chains[index],
                "indices": chain_indices,
                "elements": [],
                "connected": True,
                "valid": True,
                "error": None,
            }
            if any(atom_index >= n_atoms for atom_index in chain_indices):
                chain_info["valid"] = False
                chain_info["error"] = f"Indices out of range (max {n_atoms - 1})"
                results.append(chain_info)
                continue
            try:
                chain_info["elements"] = [
                    mol.GetAtomWithIdx(atom_index).GetSymbol() for atom_index in chain_indices
                ]
            except (IndexError, RuntimeError) as exc:
                chain_info["valid"] = False
                chain_info["error"] = str(exc)
                results.append(chain_info)
                continue
            for left, right in zip(chain_indices, chain_indices[1:]):
                if mol.GetBondBetweenAtoms(int(left), int(right)) is None:
                    chain_info["connected"] = False
                    chain_info["valid"] = False
                    chain_info["error"] = f"not bonded: {left + 1}-{right + 1}"
                    break
            results.append(chain_info)
        return results

    @staticmethod
    def compare_inputs(inputs_data: dict[str, list[dict[str, Any]]]) -> tuple[bool, list[str]]:
        """Compare validated chain features from multiple input molecules."""
        if not inputs_data:
            return True, []
        filenames = list(inputs_data)
        if len(filenames) < 2:
            return True, []
        reference_file = filenames[0]
        reference_chains = inputs_data[reference_file]
        errors: list[str] = []
        consistent = True
        for index, reference in enumerate(reference_chains):
            if not reference["valid"]:
                continue
            for other_file in filenames[1:]:
                other_chains = inputs_data[other_file]
                if index >= len(other_chains):
                    continue
                other = other_chains[index]
                if not other["valid"]:
                    consistent = False
                    errors.append(
                        f"Chain {reference['raw_chain']} in {other_file}: Invalid ({other['error']})"
                    )
                    continue
                if other["elements"] != reference["elements"]:
                    consistent = False
                    errors.append(
                        f"Chain {reference['raw_chain']} mismatch:\n"
                        f"  - {reference_file}: {'-'.join(reference['elements'])}\n"
                        f"  - {other_file}: {'-'.join(other['elements'])}"
                    )
        return consistent, errors


def validate_chain_definitions(
    *,
    input_file: str,
    chains: list[str],
    bond_threshold: float,
) -> list[str]:
    """Validate flexible chain definitions against a reference XYZ file."""
    validator = ChainValidator(chains)
    molecule = load_mol_from_xyz(input_file, bond_threshold)
    ref_data = validator.validate_mol(molecule, input_file)
    return [
        f"{entry.get('raw_chain')}: {entry.get('error')}"
        for entry in ref_data
        if not entry.get("valid")
    ]
