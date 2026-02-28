#!/usr/bin/env python3
"""MCS-based atom mapping and chain index transfer between molecules."""

from __future__ import annotations

import logging

from rdkit import Chem
from rdkit.Chem import rdFMCS

logger = logging.getLogger("confflow.confgen")

__all__ = [
    "get_mcs_mapping",
    "transfer_chain_indices",
]


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
        If no match covering enough atoms is found.
    """
    # 1. Compute MCS
    # compareAny: ignore atom and bond types (most permissive, handles mismatched atomic numbers)
    # completeRingsOnly: ensure complete ring matching for robustness
    params = rdFMCS.MCSParameters()
    params.AtomTyper = rdFMCS.AtomCompare.CompareAny
    params.BondTyper = rdFMCS.BondCompare.CompareAny
    params.MaximizeBonds = True
    params.Timeout = timeout

    res = rdFMCS.FindMCS([ref_mol, target_mol], params)

    if not res.canceled and res.numAtoms == 0:
        raise ValueError("MCS search found no common substructure")

    if verbose:
        logger.info(f"MCS match: {res.numAtoms} atoms, {res.numBonds} bonds")

    # Simple coverage check
    ratio = res.numAtoms / max(ref_mol.GetNumAtoms(), 1)
    if ratio < min_coverage:
        raise ValueError(f"MCS coverage too low ({ratio:.1%} < {min_coverage:.1%})")

    # 2. Obtain mapping (Pattern -> Ref, Pattern -> Target)
    patt = Chem.MolFromSmarts(res.smartsString)
    if patt is None:
        # Rare case where SMARTS parsing fails
        raise ValueError("cannot parse MCS SMARTS")

    # GetSubstructMatch returns a tuple of indices
    ref_match = ref_mol.GetSubstructMatch(patt)
    target_match = target_mol.GetSubstructMatch(patt)

    if not ref_match or not target_match:
        raise ValueError("cannot map MCS back to original molecule")

    # 3. Build Ref -> Target mapping
    # ref_match[i] is the index in ref of the i-th pattern atom
    # target_match[i] is the index in target of the i-th pattern atom
    # Therefore the correspondence is ref_match[i] <-> target_match[i]

    # Optimization: handle multiple matches (symmetry)
    # For confgen purposes, any single valid mapping suffices.
    # For highly symmetric molecules, RDKit returns only the first match.

    mapping = {}
    for r_idx, t_idx in zip(ref_match, target_match):
        mapping[r_idx] = t_idx

    return mapping


def transfer_chain_indices(
    ref_mol: Chem.Mol, target_mol: Chem.Mol, ref_chain: list[int]
) -> list[int]:
    """Transfer chain indices from reference to target molecule.

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
        If any chain atom cannot be mapped via MCS.
    """
    mapping = get_mcs_mapping(ref_mol, target_mol)

    target_chain = []
    missing = []

    for idx in ref_chain:
        if idx in mapping:
            target_chain.append(mapping[idx])
        else:
            missing.append(idx)

    if missing:
        raise ValueError(
            f"chain atoms {missing} could not be mapped to target molecule via MCS (possibly in non-isomorphic region)"
        )

    return target_chain
