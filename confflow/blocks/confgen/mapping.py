#!/usr/bin/env python3
"""MCS-based atom mapping and chain index transfer between molecules."""

from __future__ import annotations

import logging

from rdkit import Chem
from rdkit.Chem import rdFMCS, rdMolAlign

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
    """Run an element-aware MCS search and return the parsed SMARTS pattern.

    Atom matching is element-aware (``CompareElements``); bonds are matched by
    topology only (``CompareAny``), which is the right policy here because
    ConfFlow perceives all bonds as single bonds from coordinates.

    Raises ValueError on timeout (even with a partial result), no common
    substructure, or low coverage.  A partial MCS is never silently used.
    """
    params = rdFMCS.MCSParameters()
    params.AtomTyper = rdFMCS.AtomCompare.CompareElements
    params.BondTyper = rdFMCS.BondCompare.CompareAny
    params.MaximizeBonds = True
    params.Timeout = timeout

    res = rdFMCS.FindMCS([ref_mol, target_mol], params)

    # A timed-out search may return a partial substructure.  Continuing with it
    # would map chemically unrelated atoms, so fail closed in every case.
    if res.canceled:
        raise ValueError(
            f"MCS search timed out after {timeout}s (partial match: "
            f"{res.numAtoms}/{ref_mol.GetNumAtoms()} atoms); refusing to use a "
            "partial mapping. Consider increasing the `timeout` parameter."
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

    Uses whole-molecule element-aware MCS matching (bond orders are ignored).
    Returns the first substructure match; callers that must disambiguate
    symmetric matches should use :func:`_best_mapping_for_chain` /
    :func:`transfer_chain_indices` instead.

    Raises
    ------
    ValueError
        If MCS times out (or only a partial result is available), no common
        substructure is found, coverage is too low, or molecules cannot be
        matched.
    """
    patt = _run_mcs(ref_mol, target_mol, timeout, min_coverage, verbose)

    ref_match = ref_mol.GetSubstructMatch(patt)
    target_match = target_mol.GetSubstructMatch(patt)

    if not ref_match or not target_match:
        raise ValueError("cannot map MCS back to original molecule")

    return {r: t for r, t in zip(ref_match, target_match)}


def _has_conformer(mol: Chem.Mol) -> bool:
    try:
        return bool(mol.GetNumConformers() > 0)
    except (AttributeError, RuntimeError):
        return False


def _aligned_rmsd(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    mapping: dict[int, int],
) -> float:
    """Kabsch-aligned RMSD over all mapped atoms (ref -> target correspondence)."""
    atom_map = [(target_idx, ref_idx) for ref_idx, target_idx in mapping.items()]
    probe = Chem.Mol(target_mol)
    return float(rdMolAlign.AlignMol(probe, ref_mol, atomMap=atom_map))


def _best_mapping_for_chain(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    patt: Chem.Mol,
    ref_chain: list[int],
) -> dict[int, int]:
    """Select the MCS mapping that best superposes onto the reference molecule.

    Every candidate is a complete, chain-covering mapping between the two
    substructure matches.  Candidates are ranked by a Kabsch-aligned RMSD over
    **all** mapped atoms (never raw coordinate displacement).  When 3-D
    coordinates are unavailable and more than one equivalent mapping exists,
    the choice is ambiguous and the call fails closed.
    """
    ref_matches = ref_mol.GetSubstructMatches(patt)
    target_matches = target_mol.GetSubstructMatches(patt)

    if not ref_matches or not target_matches:
        raise ValueError("cannot map MCS back to original molecule")

    chain_set = set(ref_chain)
    candidates: list[dict[int, int]] = []
    for r_match in ref_matches:
        for t_match in target_matches:
            mapping = {r: t for r, t in zip(r_match, t_match)}
            if chain_set.issubset(mapping):
                candidates.append(mapping)

    if not candidates:
        raise ValueError(
            "cannot build a chain-covering mapping from MCS "
            f"(chain atoms {sorted(chain_set)} not reachable)"
        )

    # Deduplicate identical dicts so "multiple equivalent mappings" is judged on
    # distinct atom correspondences.
    unique: dict[tuple[tuple[int, int], ...], dict[int, int]] = {}
    for mapping in candidates:
        key = tuple(sorted(mapping.items()))
        unique.setdefault(key, mapping)
    candidates = list(unique.values())

    if len(candidates) == 1:
        return candidates[0]

    if not (_has_conformer(ref_mol) and _has_conformer(target_mol)):
        raise ValueError(
            "multiple equivalent MCS mappings and no 3-D coordinates to rank "
            "them; refusing to pick an arbitrary mapping"
        )

    best_mapping: dict[int, int] | None = None
    best_score = float("inf")
    for mapping in candidates:
        score = _aligned_rmsd(ref_mol, target_mol, mapping)
        if score < best_score:
            best_score = score
            best_mapping = mapping

    if best_mapping is None:  # pragma: no cover - candidates is non-empty
        raise ValueError("cannot select a chain mapping from MCS")

    logger.debug(
        "Symmetric molecule: %d distinct MCS mapping(s); selected mapping with "
        "aligned RMSD=%.3f Å",
        len(candidates),
        best_score,
    )
    return best_mapping


def transfer_chain_indices(
    ref_mol: Chem.Mol, target_mol: Chem.Mol, ref_chain: list[int]
) -> list[int]:
    """Transfer chain indices from reference to target molecule.

    Requires a **complete** molecule mapping: the MCS must cover every reference
    atom (``min_coverage=1.0``).  Symmetric matches are disambiguated by
    Kabsch-aligned RMSD over all mapped atoms.

    Raises
    ------
    ValueError
        If MCS times out, does not cover the whole molecule, or any chain atom
        cannot be mapped.
    """
    patt = _run_mcs(ref_mol, target_mol, timeout=30, min_coverage=1.0, verbose=False)
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
