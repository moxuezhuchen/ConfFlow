"""Tests for small compatibility adapters."""

from __future__ import annotations

from unittest.mock import Mock, patch

from confflow.core.chem_validation import (
    validate_chain_definitions,
)


def test_validate_chain_definitions_returns_invalid_messages() -> None:
    validator = Mock()
    validator.validate_mol.return_value = [
        {"valid": True, "raw_chain": "1-2"},
        {"valid": False, "raw_chain": "2-3", "error": "not bonded"},
    ]

    with (
        patch("confflow.core.chem_validation.ChainValidator", return_value=validator),
        patch("confflow.core.chem_validation.load_mol_from_xyz", return_value=object()) as load_mol,
    ):
        messages = validate_chain_definitions(
            input_file="mol.xyz",
            chains=["1-2", "2-3"],
            bond_threshold=1.2,
        )

    load_mol.assert_called_once_with("mol.xyz", 1.2)
    validator.validate_mol.assert_called_once()
    assert messages == ["2-3: not bonded"]
