#!/usr/bin/env python3

"""Workflow input validation module."""

from __future__ import annotations

import logging
from typing import Any

from ..core.chem_validation import ChainValidator, load_mol_from_xyz
from ..core.exceptions import InputFileError, XYZFormatError
from ..core.utils import validate_xyz_file
from .helpers import as_list

__all__ = [
    "load_mol_from_xyz",
    "ChainValidator",
    "validate_inputs_compatible",
]

logger = logging.getLogger("confflow.workflow.validation")


def validate_inputs_compatible(
    input_files: list[str],
    confgen_params: dict[str, Any] | None = None,
    force_consistency: bool = False,
) -> None:
    """Ensure multiple inputs are compatible for confgen merging.

    Validates that all inputs are single-frame, and that atom counts and
    element sequences are consistent.

    Parameters
    ----------
    input_files : list of str
        Input file paths.
    confgen_params : dict or None
        Optional confgen step parameters, used for flexible-chain alignment
        checks. Both ``chain`` and ``chains`` enable the mapped multi-input
        mode.
    force_consistency : bool
        If True, log a warning instead of raising on inconsistency.
    """

    def _raise_or_warn(message: str) -> None:
        if force_consistency:
            logger.warning(
                "Skipping the input consistency error because force_consistency=true: %s", message
            )
            return
        raise ValueError(message)

    if not input_files:
        raise ValueError("no input files provided")

    chain_values = None
    if confgen_params:
        # Accept both the YAML plural form and the CLI-style singular alias.
        chain_values = confgen_params.get("chains", confgen_params.get("chain"))
    allow_chain_mapping = bool(chain_values)

    ref_atoms = None
    ref_natoms = None
    for fp in input_files:
        try:
            ok, geoms = validate_xyz_file(fp, strict=True)
        except (InputFileError, XYZFormatError) as e:
            _raise_or_warn(f"cannot parse input XYZ: {fp} ({e})")
            return
        if not ok or not geoms:
            _raise_or_warn(f"cannot parse input XYZ: {fp}")
            return
        if len(geoms) != 1:
            _raise_or_warn(
                f"multi-input mode requires single-frame XYZ per input (current {fp} has {len(geoms)} frames)."
            )
            return
        atoms = list(geoms[0].get("atoms") or [])
        natoms = len(atoms)
        if ref_atoms is None:
            ref_atoms = atoms
            ref_natoms = natoms
            continue
        if natoms != ref_natoms:
            _raise_or_warn(f"atom count mismatch: {fp} ({natoms}) vs reference ({ref_natoms})")
            return

        if allow_chain_mapping:
            # Allow atom reordering, but require identical element counts.
            if sorted(atoms) != sorted(ref_atoms):
                _raise_or_warn(
                    "element composition mismatch (chains mode requires equal element counts):\n"
                    f"File: {fp}"
                )
                return
        else:
            # By default, require identical atom ordering across all inputs.
            if atoms != ref_atoms:
                diffs = []
                for i, (a1, a2) in enumerate(zip(atoms, ref_atoms)):
                    if a1 != a2:
                        diffs.append(f"#{i + 1} {a1} vs {a2}")
                        if len(diffs) >= 3:
                            break
                _raise_or_warn(
                    "all inputs must have the same atom count and element order.\n"
                    "element order mismatch (multi-input mode requires full match):\n"
                    f"File: {fp}\nDifference: {', '.join(diffs)}..."
                )
                return

    # -------------------------------------------------------------------------
    # Flexible chain consistency check (if confgen params are present)
    # -------------------------------------------------------------------------
    if confgen_params and chain_values is not None:
        chains = as_list(chain_values)
        if chains:
            try:
                if not bool(confgen_params.get("validate_chain_bonds", False)):
                    return
                bond_threshold = float(confgen_params.get("bond_threshold", 1.15))
                validator = ChainValidator(chains)
                mol = load_mol_from_xyz(input_files[0], bond_threshold)
                ref_data = validator.validate_mol(mol, input_files[0])
                messages = [
                    f"{entry.get('raw_chain')}: {entry.get('error')}"
                    for entry in ref_data
                    if not entry.get("valid")
                ]
                if messages:
                    _raise_or_warn(
                        "Flexible chains are invalid in the reference input file:\n"
                        + "\n".join(messages)
                    )
                    return
            except ValueError:
                raise
            except (OSError, RuntimeError) as e:
                _raise_or_warn(f"failed to validate flexible chains: {e}")
                return
