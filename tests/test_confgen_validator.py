# -*- coding: utf-8 -*-
"""Tests for confflow.blocks.confgen.validator module"""

import pytest
from unittest.mock import Mock, MagicMock
from confflow.blocks.confgen.validator import ChainValidator


class TestChainValidator:
    """Tests for ChainValidator class"""

    def test_init_with_empty_chains(self):
        """Test initialization with empty chain list"""
        validator = ChainValidator([])
        assert validator.raw_chains == []
        assert validator.parsed_chains == []

    def test_init_with_chains(self):
        """Test initialization with chain strings"""
        validator = ChainValidator(["1-2-3", "4-5-6"])
        assert validator.raw_chains == ["1-2-3", "4-5-6"]
        # parsed_chains should be 0-based indices
        assert validator.parsed_chains == [[0, 1, 2], [3, 4, 5]]

    def test_validate_mol_indices_out_of_range(self):
        """Test validation when chain indices exceed atom count"""
        validator = ChainValidator(["1-2-10"])  # Index 10 will be out of range
        
        # Create mock molecule with 5 atoms
        mol = Mock()
        mol.GetNumAtoms.return_value = 5
        
        results = validator.validate_mol(mol, "test.xyz")
        
        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "out of range" in results[0]["error"]
        assert results[0]["connected"] is True  # Default before check

    def test_validate_mol_successful(self):
        """Test successful chain validation"""
        validator = ChainValidator(["1-2-3"])
        
        # Create mock molecule
        mol = Mock()
        mol.GetNumAtoms.return_value = 5
        
        # Mock atoms
        atom_c = Mock()
        atom_c.GetSymbol.return_value = "C"
        atom_h = Mock()
        atom_h.GetSymbol.return_value = "H"
        atom_o = Mock()
        atom_o.GetSymbol.return_value = "O"
        
        mol.GetAtomWithIdx.side_effect = lambda i: [atom_c, atom_h, atom_o][i]
        
        # Mock bonds - all connected
        mol.GetBondBetweenAtoms.return_value = Mock()  # Non-None = bonded
        
        results = validator.validate_mol(mol, "test.xyz")
        
        assert len(results) == 1
        assert results[0]["valid"] is True
        assert results[0]["elements"] == ["C", "H", "O"]
        assert results[0]["connected"] is True
        assert results[0]["error"] is None

    def test_validate_mol_not_connected(self):
        """Test validation when atoms are not bonded"""
        validator = ChainValidator(["1-2-3"])
        
        mol = Mock()
        mol.GetNumAtoms.return_value = 5
        
        # Mock atoms
        atom_c = Mock()
        atom_c.GetSymbol.return_value = "C"
        atom_h = Mock()
        atom_h.GetSymbol.return_value = "H"
        atom_o = Mock()
        atom_o.GetSymbol.return_value = "O"
        
        mol.GetAtomWithIdx.side_effect = lambda i: [atom_c, atom_h, atom_o][i]
        
        # First bond exists, second doesn't
        mol.GetBondBetweenAtoms.side_effect = lambda a, b: Mock() if (a, b) == (0, 1) else None
        
        results = validator.validate_mol(mol, "test.xyz")
        
        assert len(results) == 1
        assert results[0]["valid"] is False
        assert results[0]["connected"] is False
        assert "未成键" in results[0]["error"]

    def test_validate_mol_exception_on_get_atom(self):
        """Test handling exception when getting atom"""
        validator = ChainValidator(["1-2-3"])
        
        mol = Mock()
        mol.GetNumAtoms.return_value = 5
        mol.GetAtomWithIdx.side_effect = RuntimeError("Atom error")
        
        results = validator.validate_mol(mol, "test.xyz")
        
        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "Atom error" in results[0]["error"]

    def test_compare_inputs_empty(self):
        """Test compare_inputs with empty data"""
        is_consistent, errors = ChainValidator.compare_inputs({})
        assert is_consistent is True
        assert errors == []

    def test_compare_inputs_single_file(self):
        """Test compare_inputs with only one file"""
        inputs_data = {
            "file1.xyz": [{"valid": True, "elements": ["C", "H"], "raw_chain": "1-2"}]
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        assert is_consistent is True
        assert errors == []

    def test_compare_inputs_consistent(self):
        """Test compare_inputs with consistent chains"""
        inputs_data = {
            "file1.xyz": [
                {"valid": True, "elements": ["C", "H", "O"], "raw_chain": "1-2-3"}
            ],
            "file2.xyz": [
                {"valid": True, "elements": ["C", "H", "O"], "raw_chain": "1-2-3"}
            ],
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        assert is_consistent is True
        assert errors == []

    def test_compare_inputs_inconsistent_elements(self):
        """Test compare_inputs with mismatched elements"""
        inputs_data = {
            "file1.xyz": [
                {"valid": True, "elements": ["C", "H", "O"], "raw_chain": "1-2-3"}
            ],
            "file2.xyz": [
                {"valid": True, "elements": ["C", "H", "N"], "raw_chain": "1-2-3"}
            ],
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        assert is_consistent is False
        assert len(errors) == 1
        assert "mismatch" in errors[0]

    def test_compare_inputs_invalid_chain_in_other_file(self):
        """Test compare_inputs when other file has invalid chain"""
        inputs_data = {
            "file1.xyz": [
                {"valid": True, "elements": ["C", "H", "O"], "raw_chain": "1-2-3"}
            ],
            "file2.xyz": [
                {"valid": False, "elements": [], "raw_chain": "1-2-3", "error": "Out of range"}
            ],
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        assert is_consistent is False
        assert len(errors) == 1
        assert "Invalid" in errors[0]

    def test_compare_inputs_skips_invalid_reference(self):
        """Test compare_inputs skips when reference chain is invalid"""
        inputs_data = {
            "file1.xyz": [
                {"valid": False, "elements": [], "raw_chain": "1-2-3", "error": "Error"}
            ],
            "file2.xyz": [
                {"valid": True, "elements": ["C", "H", "O"], "raw_chain": "1-2-3"}
            ],
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        # Should skip invalid reference and return True
        assert is_consistent is True
        assert errors == []

    def test_compare_inputs_multiple_chains(self):
        """Test compare_inputs with multiple chains"""
        inputs_data = {
            "file1.xyz": [
                {"valid": True, "elements": ["C", "H"], "raw_chain": "1-2"},
                {"valid": True, "elements": ["O", "N"], "raw_chain": "3-4"},
            ],
            "file2.xyz": [
                {"valid": True, "elements": ["C", "H"], "raw_chain": "1-2"},
                {"valid": True, "elements": ["O", "N"], "raw_chain": "3-4"},
            ],
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        assert is_consistent is True
        assert errors == []

    def test_compare_inputs_partial_mismatch(self):
        """Test compare_inputs with partial chain mismatch"""
        inputs_data = {
            "file1.xyz": [
                {"valid": True, "elements": ["C", "H"], "raw_chain": "1-2"},
                {"valid": True, "elements": ["O", "N"], "raw_chain": "3-4"},
            ],
            "file2.xyz": [
                {"valid": True, "elements": ["C", "H"], "raw_chain": "1-2"},
                {"valid": True, "elements": ["O", "S"], "raw_chain": "3-4"},  # Different
            ],
        }
        is_consistent, errors = ChainValidator.compare_inputs(inputs_data)
        assert is_consistent is False
        assert len(errors) == 1
