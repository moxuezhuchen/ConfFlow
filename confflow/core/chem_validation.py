#!/usr/bin/env python3

"""Stable chemistry validation services used by higher layers."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ChainValidator",
    "load_mol_from_xyz",
    "validate_chain_definitions",
]


def load_mol_from_xyz(filename: str, bond_coeff: float):
    """Compatibility wrapper for loading an RDKit molecule from XYZ."""
    from ..blocks.confgen.generator import load_mol_from_xyz as _impl

    return _impl(filename, bond_coeff)


def ChainValidator(*args, **kwargs):
    """Compatibility wrapper for chain validation construction."""
    from ..blocks.confgen.validator import ChainValidator as _impl

    return _impl(*args, **kwargs)


def validate_chain_definitions(
    *,
    input_file: str,
    chains: list[str],
    bond_threshold: float,
) -> list[str]:
    """Validate flexible chain definitions against a reference XYZ file."""
    validator = ChainValidator(chains)
    mol = load_mol_from_xyz(input_file, bond_threshold)
    ref_data = validator.validate_mol(mol, input_file)
    return [f"{entry.get('raw_chain')}: {entry.get('error')}" for entry in ref_data if not entry.get("valid")]
