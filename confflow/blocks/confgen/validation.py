#!/usr/bin/env python3
"""Confgen-specific chemistry validation entry points."""

from __future__ import annotations

from . import generator, validator

__all__ = ["ChainValidator", "load_mol_from_xyz", "validate_chain_definitions"]


def validate_chain_definitions(
    *,
    input_file: str,
    chains: list[str],
    bond_threshold: float,
) -> list[str]:
    """Validate flexible chain definitions against a reference XYZ file."""
    chain_validator = ChainValidator(chains)
    mol = load_mol_from_xyz(input_file, bond_threshold)
    ref_data = chain_validator.validate_mol(mol, input_file)
    return [
        f"{entry.get('raw_chain')}: {entry.get('error')}"
        for entry in ref_data
        if not entry.get("valid")
    ]


def load_mol_from_xyz(filename: str, bond_coeff: float):
    """Delegate molecule loading to the confgen generator implementation."""
    return generator.load_mol_from_xyz(filename, bond_coeff)


def ChainValidator(*args, **kwargs):  # noqa: N802 - preserve the public v2 name
    """Construct the confgen chain validator through its owning module."""
    return validator.ChainValidator(*args, **kwargs)
