from typing import Any, Dict, List, Tuple

from rdkit import Chem

from .generator import _parse_chain

class ChainValidator:
    """Validator for chain consistency across multiple input molecules."""

    def __init__(self, chains: List[str]):
        """
        Args:
            chains: List of chain strings (e.g., ["1-2-3", "4-5-6"])
        """
        self.raw_chains = chains
        # Pre-parse chains to 0-based indices
        self.parsed_chains = [_parse_chain(c) for c in chains] if chains else []

    def validate_mol(self, mol: Chem.Mol, filename: str) -> List[Dict[str, Any]]:
        """
        Validates chains against a single molecule and extracts chain features.
        
        Returns:
            List of dicts, one per chain, containing:
            - 'indices': parsed 0-based indices
            - 'elements': list of atomic symbols
            - 'connected': bool (adjacent atoms bonded)
            - 'valid': bool
            - 'error': str (if any)
        """
        n_atoms = mol.GetNumAtoms()
        results = []

        for i, chain_indices in enumerate(self.parsed_chains):
            chain_info = {
                "chain_id": i,
                "raw_chain": self.raw_chains[i],
                "indices": chain_indices,
                "elements": [],
                "connected": True,
                "valid": True,
                "error": None
            }
            
            # Check indices bounds
            if any(idx >= n_atoms for idx in chain_indices):
                chain_info["valid"] = False
                chain_info["error"] = f"Indices out of range (max {n_atoms-1})"
                results.append(chain_info)
                continue

            # Extract elements
            try:
                elements = [mol.GetAtomWithIdx(idx).GetSymbol() for idx in chain_indices]
                chain_info["elements"] = elements
            except Exception as e:
                chain_info["valid"] = False
                chain_info["error"] = str(e)
                results.append(chain_info)
                continue
            
            # Check connectivity: adjacent atoms must have bonds
            for j in range(len(chain_indices) - 1):
                a = chain_indices[j]
                b = chain_indices[j + 1]
                if mol.GetBondBetweenAtoms(int(a), int(b)) is None:
                    chain_info["connected"] = False
                    chain_info["valid"] = False
                    chain_info["error"] = f"未成键: {a+1}-{b+1}"
                    break
            
            results.append(chain_info)
            
        return results

    @staticmethod
    def compare_inputs(inputs_data: Dict[str, List[Dict[str, Any]]]) -> Tuple[bool, List[str]]:
        """
        Compares chain features across multiple inputs.
        
        Args:
            inputs_data: Dict mapping filename -> list of chain info dicts
            
        Returns:
            (is_consistent, error_messages)
        """
        if not inputs_data:
            return True, []
            
        filenames = list(inputs_data.keys())
        if len(filenames) < 2:
            return True, []
            
        ref_file = filenames[0]
        ref_chains = inputs_data[ref_file]
        
        errors = []
        is_consistent = True

        for i, ref_chain in enumerate(ref_chains):
            if not ref_chain["valid"]:
                 # If reference itself is invalid, we might skip consistency check or report it
                 # Validation of single file should happen before calling compare_inputs ideally, 
                 # but we handle it gracefully here.
                 continue

            ref_elements = ref_chain["elements"]
            
            for other_file in filenames[1:]:
                other_chains = inputs_data[other_file]
                if i >= len(other_chains):
                    # Should not happen if all files parsed same chains list
                    continue
                    
                other_chain = other_chains[i]
                
                if not other_chain["valid"]:
                    is_consistent = False
                    errors.append(f"Chain {ref_chain['raw_chain']} in {other_file}: Invalid ({other_chain['error']})")
                    continue
                
                if other_chain["elements"] != ref_elements:
                    is_consistent = False
                    errors.append(
                        f"Chain {ref_chain['raw_chain']} mismatch:\n"
                        f"  - {ref_file}: {'-'.join(ref_elements)}\n"
                        f"  - {other_file}: {'-'.join(other_chain['elements'])}"
                    )

        return is_consistent, errors
