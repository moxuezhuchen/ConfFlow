#!/usr/bin/env python3
"""MCS-based atom mapping and chain index transfer between molecules."""

from __future__ import annotations

import logging

import numpy as np
from rdkit import Chem
from rdkit.Chem import rdFMCS

logger = logging.getLogger("confflow.confgen")

__all__ = [
    "get_mcs_mapping",
    "transfer_chain_indices",
]


def _run_mcs(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    timeout: int,
    min_coverage: float,
    verbose: bool,
) -> Chem.Mol:
    """Run MCS search and return the parsed SMARTS pattern.

    Raises ValueError on timeout (no atoms), no common substructure, or low coverage.
    Emits a warning when search times out with a partial result.
    """
    params = rdFMCS.MCSParameters()
    params.AtomTyper = rdFMCS.AtomCompare.CompareAny
    params.BondTyper = rdFMCS.BondCompare.CompareAny
    params.MaximizeBonds = True
    params.Timeout = timeout

    res = rdFMCS.FindMCS([ref_mol, target_mol], params)

    # P0-1: Treat timeout explicitly; never silently fall back on partial results.
    if res.canceled:
        if res.numAtoms == 0:
            raise ValueError(
                f"MCS search timed out after {timeout}s with no common substructure found. "
                "Consider increasing the `timeout` parameter."
            )
        logger.warning(
            "MCS search timed out after %ds; using partial result (%d/%d atoms). "
            "Chain mapping may be unreliable — consider increasing `timeout`.",
            timeout,
            res.numAtoms,
            ref_mol.GetNumAtoms(),
        )

    if res.numAtoms == 0:
        raise ValueError("MCS search found no common substructure")

    if verbose:
        logger.info("MCS match: %d atoms, %d bonds", res.numAtoms, res.numBonds)

    ratio = res.numAtoms / max(ref_mol.GetNumAtoms(), 1)
    if ratio < min_coverage:
        raise ValueError(f"MCS coverage too low ({ratio:.1%} < {min_coverage:.1%})")

    patt = Chem.MolFromSmarts(res.smartsString)
    if patt is None:
        raise ValueError("cannot parse MCS SMARTS")
    return patt


def get_mcs_mapping(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    timeout: int = 30,
    verbose: bool = False,
    min_coverage: float = 0.7,
) -> dict[int, int]:
    """Compute atom index mapping from reference to target molecule (0-based).

    Uses whole-molecule MCS (Maximum Common Substructure) matching,
    ignoring element types and bond orders for topology-only matching.

    Parameters
    ----------
    ref_mol : Chem.Mol
        Reference molecule.
    target_mol : Chem.Mol
        Target molecule.
    timeout : int
        MCS search timeout in seconds.
    verbose : bool
        Whether to log MCS match details.
    min_coverage : float
        Minimum fraction of atoms that must be covered by MCS.

    Returns
    -------
    dict[int, int]
        Mapping of ref_idx -> target_idx.

    Raises
    ------
    ValueError
        If MCS times out (with no atoms), no common substructure is found,
        coverage is too low, or molecules cannot be matched.
    """
    patt = _run_mcs(ref_mol, target_mol, timeout, min_coverage, verbose)

    ref_match = ref_mol.GetSubstructMatch(patt)
    target_match = target_mol.GetSubstructMatch(patt)

    if not ref_match or not target_match:
        raise ValueError("cannot map MCS back to original molecule")

    return {r: t for r, t in zip(ref_match, target_match)}


def _best_mapping_for_chain(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    patt: Chem.Mol,
    ref_chain: list[int],
) -> dict[int, int]:
    """P0-2: Select the MCS mapping that minimises 3-D displacement for chain atoms.

    Enumerates all (ref_match × target_match) combinations and returns the mapping
    whose chain atoms have the smallest total 3-D displacement.  Falls back to
    first-match when 3-D coordinates are unavailable or only one match exists.
    """
    ref_matches = ref_mol.GetSubstructMatches(patt)
    target_matches = target_mol.GetSubstructMatches(patt)

    if not ref_matches or not target_matches:
        raise ValueError("cannot map MCS back to original molecule")

    # Try to obtain 3-D coordinates for displacement-based ranking.
    try:
        ref_pos = np.array(ref_mol.GetConformer().GetPositions())
        tgt_pos = np.array(target_mol.GetConformer().GetPositions())
        has_coords = True
    except (AttributeError, RuntimeError, ValueError):
        has_coords = False

    chain_set = set(ref_chain)
    best_mapping: dict[int, int] | None = None
    best_score = float("inf")

    for r_match in ref_matches:
        for t_match in target_matches:
            mapping = {r: t for r, t in zip(r_match, t_match)}
            if not chain_set.issubset(mapping):
                continue

            if has_coords:
                score = float(
                    sum(np.linalg.norm(ref_pos[r] - tgt_pos[mapping[r]]) for r in ref_chain)
                )
            else:
                score = 0.0  # No coordinates → all mappings equally ranked

            if score < best_score:
                best_score = score
                best_mapping = mapping

    if best_mapping is None:
        raise ValueError(
            "cannot build a chain-covering mapping from MCS "
            f"(chain atoms {sorted(chain_set - set(best_mapping or {}))} not reachable)"
        )

    n_combos = len(ref_matches) * len(target_matches)
    if has_coords and n_combos > 1:
        logger.debug(
            "Symmetric molecule: %d MCS match combination(s); "
            "selected mapping with chain-atom displacement=%.3f Å",
            n_combos,
            best_score,
        )

    return best_mapping


def transfer_chain_indices(
    ref_mol: Chem.Mol, target_mol: Chem.Mol, ref_chain: list[int]
) -> list[int]:
    """Transfer chain indices from reference to target molecule.

    Uses symmetry-aware MCS matching: when multiple equivalent sub-structure
    matches exist (e.g. symmetric molecules), the mapping that minimises the
    total 3-D displacement of chain atoms is selected.

    Parameters
    ----------
    ref_mol : Chem.Mol
        Reference molecule.
    target_mol : Chem.Mol
        Target molecule.
    ref_chain : list[int]
        0-based atom indices in the reference molecule.

    Returns
    -------
    list[int]
        0-based atom indices in the target molecule.

    Raises
    ------
    ValueError
        If MCS times out, coverage is too low, or any chain atom cannot be mapped.
    """
    patt = _run_mcs(ref_mol, target_mol, timeout=30, min_coverage=0.7, verbose=False)
    mapping = _best_mapping_for_chain(ref_mol, target_mol, patt, ref_chain)

    target_chain = []
    missing = []
    for idx in ref_chain:
        if idx in mapping:
            target_chain.append(mapping[idx])
        else:
            missing.append(idx)

    if missing:
        raise ValueError(
            f"chain atoms {missing} could not be mapped to target molecule via MCS "
            "(possibly in non-isomorphic region)"
        )

    return target_chain
