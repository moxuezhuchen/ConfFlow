#!/usr/bin/env python3
"""Generate the hydrogen-bearing fixtures and the ``h_*`` equivalence cases.

Run once from the exec-cf-is repo root::

    python3 docs/refactor/paths_equivalence/make_fixtures_h.py

Writes ``fixtures_h.json`` (atoms, coordinates, bonds for n-butane,
1-propanol, propylamine and CHFClBr, embedded deterministically with RDKit)
and merges the derived ``hydrogen_cases`` into ``cases.json``.  Afterwards
every other step reads the JSON files only -- RDKit is not needed again.
"""

from __future__ import annotations

import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem

HERE = Path(__file__).resolve().parent

#: SMILES for the four fixtures.  Heavy atoms come first in RDKit's atom
#: order; ``AddHs`` appends the hydrogens after them.
MOLECULES = {
    "butane": "CCCC",
    "propanol": "CCCO",
    "propylamine": "CCCN",
    "chiral": "C(F)(Cl)Br",
}


def _embed(smiles: str) -> dict:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise RuntimeError(f"RDKit could not parse {smiles!r}")
    mol = Chem.AddHs(mol)
    if AllChem.EmbedMolecule(mol, randomSeed=7) != 0:
        raise RuntimeError(f"RDKit could not embed {smiles!r}")
    AllChem.MMFFOptimizeMolecule(mol)
    atoms = [atom.GetSymbol() for atom in mol.GetAtoms()]
    coordinates = [
        [float(v) for v in mol.GetConformer().GetAtomPosition(index)]
        for index in range(mol.GetNumAtoms())
    ]
    bonds = sorted(
        [bond.GetBeginAtomIdx() + 1, bond.GetEndAtomIdx() + 1] for bond in mol.GetBonds()
    )
    heavy_atoms = [index + 1 for index, symbol in enumerate(atoms) if symbol != "H"]
    # Degree-1 heavy atoms: neighbours restricted to heavy atoms.
    terminal_heavy_atoms = []
    for atom in heavy_atoms:
        heavy_neighbours = []
        for begin, end in bonds:
            if begin == atom and end in heavy_atoms:
                heavy_neighbours.append(end)
            if end == atom and begin in heavy_atoms:
                heavy_neighbours.append(begin)
        if len(heavy_neighbours) == 1:
            terminal_heavy_atoms.append(atom)
    return {
        "smiles": smiles,
        "atoms": atoms,
        "coordinates": coordinates,
        "bonds": bonds,
        "heavy_atoms": heavy_atoms,
        "terminal_heavy_atoms": terminal_heavy_atoms,
    }


def main() -> int:
    fixtures = {name: _embed(smiles) for name, smiles in MOLECULES.items()}
    document = {
        "fixtures": fixtures,
        "selfcheck": {
            "rigid": fixtures["propanol"],
            "chiral": fixtures["chiral"],
            "dihedral": fixtures["butane"],
        },
    }
    (HERE / "fixtures_h.json").write_text(
        json.dumps(document, sort_keys=True, indent=1) + "\n", encoding="utf-8"
    )

    # Derive the hydrogen cases from the actual bond tables (never guessed).
    def molecule_case(name: str) -> dict:
        data = fixtures[name]
        return {
            "record": {
                "id": name,
                "atoms": data["atoms"],
                "coordinates": data["coordinates"],
            },
            "bonds": data["bonds"],
        }

    def terminals(name: str) -> tuple[int, int]:
        heavy = fixtures[name]["terminal_heavy_atoms"]
        if len(heavy) != 2:
            raise RuntimeError(f"{name}: expected exactly 2 terminal heavy atoms, got {heavy}")
        return heavy[0], heavy[1]

    def terminal_with_symbol(name: str, symbol: str) -> int:
        atoms = fixtures[name]["atoms"]
        heavy = fixtures[name]["terminal_heavy_atoms"]
        matches = [atom for atom in heavy if atoms[atom - 1] == symbol]
        if len(matches) != 1:
            raise RuntimeError(f"{name}: expected exactly one terminal {symbol}, got {matches}")
        return matches[0]

    propanol_oh = terminal_with_symbol("propanol", "O")
    propanol_methyl = next(
        atom for atom in fixtures["propanol"]["terminal_heavy_atoms"] if atom != propanol_oh
    )
    amine_n = terminal_with_symbol("propylamine", "N")
    amine_methyl = next(
        atom for atom in fixtures["propylamine"]["terminal_heavy_atoms"] if atom != amine_n
    )
    butane_start, butane_end = terminals("butane")
    if fixtures["propanol"]["atoms"][propanol_oh - 1] != "O":
        raise RuntimeError("propanol: terminal heavy atom is not the OH oxygen")
    if fixtures["propylamine"]["atoms"][amine_n - 1] != "N":
        raise RuntimeError("propylamine: terminal heavy atom is not the NH2 nitrogen")

    hydrogen_cases = [
        {
            "case_id": "h_butane_terminal",
            **molecule_case("butane"),
            "native": {
                "paths": [
                    {
                        "start": butane_start,
                        "end": butane_end,
                        "move": "end",
                        "angles": [0.0, 120.0, 240.0],
                    }
                ]
            },
        },
        {
            "case_id": "h_butane_terminal_bare",
            **molecule_case("butane"),
            "native": {"paths": [{"start": butane_start, "end": butane_end, "move": "end"}]},
        },
        {
            "case_id": "h_propanol_oh",
            **molecule_case("propanol"),
            "native": {
                "paths": [
                    {
                        "start": propanol_oh,
                        "end": propanol_methyl,
                        "move": "end",
                        "angles": [0.0, 120.0, 240.0],
                    }
                ]
            },
        },
        {
            "case_id": "h_propanol_oh_bare",
            **molecule_case("propanol"),
            "native": {"paths": [{"start": propanol_oh, "end": propanol_methyl, "move": "end"}]},
        },
        {
            "case_id": "h_propylamine_nh2",
            **molecule_case("propylamine"),
            "native": {
                "paths": [
                    {
                        "start": amine_n,
                        "end": amine_methyl,
                        "move": "end",
                        "angles": [0.0, 120.0, 240.0],
                    }
                ]
            },
        },
        {
            "case_id": "h_propylamine_nh2_bare",
            **molecule_case("propylamine"),
            "native": {"paths": [{"start": amine_n, "end": amine_methyl, "move": "end"}]},
        },
        {
            "case_id": "h_butane_internal",
            **molecule_case("butane"),
            "native": {
                "paths": [{"start": 2, "end": 3, "move": "end", "angles": [0.0, 120.0, 240.0]}]
            },
        },
        {
            "case_id": "h_propanol_internal",
            **molecule_case("propanol"),
            "native": {
                "paths": [{"start": 2, "end": 3, "move": "end", "angles": [0.0, 120.0, 240.0]}]
            },
        },
        {
            "case_id": "h_propylamine_internal",
            **molecule_case("propylamine"),
            "native": {
                "paths": [{"start": 2, "end": 3, "move": "end", "angles": [0.0, 120.0, 240.0]}]
            },
        },
    ]

    cases_path = HERE / "cases.json"
    cases_document = json.loads(cases_path.read_text(encoding="utf-8"))
    cases_document["hydrogen_cases"] = hydrogen_cases
    cases_path.write_text(
        json.dumps(cases_document, sort_keys=True, indent=1) + "\n", encoding="utf-8"
    )
    print(f"fixtures_h.json written; {len(hydrogen_cases)} hydrogen cases merged into cases.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
