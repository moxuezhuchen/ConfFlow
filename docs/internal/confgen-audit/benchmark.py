"""Reproduce a scoped conformer-strategy audit using RDKit proxy references.

Run from repository root with PYTHONPATH=. .venv/bin/python this_file --out DIR.
MMFF energies are proxy force-field values, not quantum chemical truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rdkit import Chem, rdBase
from rdkit.Chem import AllChem, rdMolTransforms

from confflow.domain import FrozenDict, StructureRecord, StructureSet
from confflow.domain.resources import ResourceRequest
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.checks_standard import kabsch_aligned_rmsd
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.science.torsion import clashes, topological_distance_matrix


def molecule(smiles):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=20260930) == 0
    assert AllChem.MMFFOptimizeMolecule(mol, mmffVariant="MMFF94s", maxIters=1000) == 0
    return mol


def record(mol):
    return StructureRecord(
        id="seed",
        atoms=tuple(a.GetSymbol() for a in mol.GetAtoms()),
        coordinates=tuple(
            tuple(float(v) for v in row) for row in mol.GetConformer().GetPositions()
        ),
        charge=Chem.GetFormalCharge(mol),
        multiplicity=1,
    )


def generate(mol, native, out, name, seed=42):
    key = "audit:seed"  # fixed across cases so seed comparisons do not change identity
    item = WorkItem(
        id=make_work_item_id(key),
        logical_key=key,
        step_id="audit",
        named_inputs=WorkItemInputs(
            structures=FrozenDict({"structure": StructureSet.of(record(mol))})
        ),
        resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2**28),
        semantic_digest="sha256:" + hashlib.sha256(name.encode()).hexdigest(),
    )
    sci = SimpleNamespace(seed=seed, native=FrozenDict(native), overrides=FrozenDict({}))
    context = ItemExecutionContext(
        step_id="audit",
        scientific=sci,
        scientific_defaults=SimpleNamespace(),
        adapter=None,
        profile=None,
        checks=(),
        recovery=None,
        run_root=str(out / name),
    )
    return ConfgenExecutor().execute(item, context)


def attach(mol, coords):
    copy = Chem.Mol(mol)
    copy.RemoveAllConformers()
    conf = Chem.Conformer(copy.GetNumAtoms())
    for i, p in enumerate(coords):
        conf.SetAtomPosition(i, tuple(float(v) for v in p))
    copy.AddConformer(conf)
    return copy


def energy(mol):
    props = AllChem.MMFFGetMoleculeProperties(mol, mmffVariant="MMFF94s")
    return float(AllChem.MMFFGetMoleculeForceField(mol, props).CalcEnergy())


def unique(coords, heavy, threshold=0.10):
    reps = []
    for xyz in coords:
        selected = np.asarray(xyz)[heavy]
        if not any(kabsch_aligned_rmsd(selected, rep) < threshold for rep in reps):
            reps.append(selected)
    return reps


def torsion(mol, indices):
    return float(rdMolTransforms.GetDihedralDeg(mol.GetConformer(), *indices))


def torsion_basins(mol, torsions):
    anchors = np.array([-60.0, 60.0, 180.0])
    return tuple(
        float(anchors[np.argmin(abs((torsion(mol, t) - anchors + 180) % 360 - 180))])
        for t in torsions
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=Path(tempfile.mkdtemp(prefix="confflow-confgen-audit-"))
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    butane = molecule("CCCC")
    for phase in (0, 30, 60, 180):
        start = Chem.Mol(butane)
        rdMolTransforms.SetDihedralDeg(start.GetConformer(), 0, 1, 2, 3, float(phase))
        for step in (120, 60):
            result = generate(
                start,
                {"chains": ["2-3"], "angle_step": step},
                args.out,
                f"butane_phase{phase}_step{step}",
            )
            mols = [attach(start, r.coordinates) for r in result.structures]
            rows.append(
                {
                    "case": f"butane_phase{phase}_step{step}",
                    "status": result.status.value,
                    "members": len(mols),
                    "torsions_deg": [round(torsion(m, (0, 1, 2, 3)), 3) for m in mols],
                    "raw_mmff_kcal_mol": [round(energy(m), 4) for m in mols],
                }
            )
    for chains in (["2-3"], ["1-2-3-4"], ["2-3", "2-3"]):
        name = "butane_chains_" + "_".join(chains)
        result = generate(butane, {"chains": chains}, args.out, name)
        coords = [r.coordinates for r in result.structures]
        rows.append(
            {
                "case": name,
                "status": result.status.value,
                "members": len(coords),
                "heavy_atom_rmsd_unique_0p1A": len(unique(coords, [0, 1, 2, 3])),
            }
        )
    duplicate_cap = []
    for seed in range(12):
        result = generate(
            butane,
            {"chains": ["2-3", "2-3"], "max_conformers": 2},
            args.out,
            f"cap_seed{seed}",
            seed,
        )
        duplicate_cap.append(
            {
                "seed": seed,
                "members": len(result.structures),
                "heavy_unique": len(
                    unique([r.coordinates for r in result.structures], [0, 1, 2, 3])
                ),
            }
        )
    rows.append({"case": "duplicate_dimensions_cap2_seeds0_to11", "samples": duplicate_cap})
    pentane = molecule("CCCCC")
    reference = Chem.AddHs(Chem.MolFromSmiles("CCCCC"))
    params = AllChem.ETKDGv3()
    params.randomSeed = 20260930
    params.numThreads = 1
    ids = list(AllChem.EmbedMultipleConfs(reference, numConfs=200, params=params))
    optimized = list(
        AllChem.MMFFOptimizeMoleculeConfs(
            reference, numThreads=1, mmffVariant="MMFF94s", maxIters=1000
        )
    )
    min_e = min(e for status, e in optimized if status == 0)
    ts = ((0, 1, 2, 3), (1, 2, 3, 4))
    ref_basins = set()
    for cid, (status, e) in zip(ids, optimized):
        if status == 0 and e <= min_e + 3.0:
            ref_basins.add(
                torsion_basins(attach(reference, reference.GetConformer(cid).GetPositions()), ts)
            )
    for step in (120, 60):
        result = generate(
            pentane, {"chains": ["2-3-4"], "angle_step": step}, args.out, f"pentane_step{step}"
        )
        basins = set()
        converged = 0
        before = []
        after = []
        max_bond_error = 0.0
        for rec in result.structures:
            m = attach(pentane, rec.coordinates)
            before.append(energy(m))
            for b in pentane.GetBonds():
                i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
                max_bond_error = max(
                    max_bond_error,
                    abs(
                        rdMolTransforms.GetBondLength(pentane.GetConformer(), i, j)
                        - rdMolTransforms.GetBondLength(m.GetConformer(), i, j)
                    ),
                )
            if AllChem.MMFFOptimizeMolecule(m, mmffVariant="MMFF94s", maxIters=1000) == 0:
                converged += 1
                after.append(energy(m))
                if energy(m) <= min_e + 3.0:
                    basins.add(torsion_basins(m, ts))
        rows.append(
            {
                "case": f"pentane_step{step}",
                "members": len(result.structures),
                "mmff_converged": converged,
                "reference_embedded": len(ids),
                "reference_minimum_mmff_kcal_mol": min_e,
                "reference_optimized_converged": sum(s == 0 for s, e in optimized),
                "reference_low_energy_torsion_basins": sorted(ref_basins),
                "generated_after_mmff_low_energy_basins": sorted(basins),
                "reference_basin_coverage": len(ref_basins & basins) / len(ref_basins),
                "max_original_bond_length_drift_A": max_bond_error,
                "status": result.status.value,
                "messages": [d.message for d in result.diagnostics],
                "raw_mmff_range_kcal_mol": [min(before), max(before)] if before else None,
                "optimized_mmff_range_kcal_mol": [min(after), max(after)] if after else None,
            }
        )
    alkene = molecule("C/C=C/C")
    result = generate(
        alkene, {"chains": ["2-3"], "chain_angles": "0,90,180"}, args.out, "alkene_double_bond"
    )
    rows.append(
        {
            "case": "alkene_double_bond",
            "status": result.status.value,
            "members": len(result.structures),
            "torsions_deg": [
                round(torsion(attach(alkene, r.coordinates), (0, 1, 2, 3)), 3)
                for r in result.structures
            ],
        }
    )
    ring = molecule("C1CCCCC1")
    result = generate(ring, {"chains": ["1-2-3-4-5-6"]}, args.out, "cyclohexane_ring")
    rows.append(
        {
            "case": "cyclohexane_ring",
            "status": result.status.value,
            "messages": [d.message for d in result.diagnostics],
        }
    )
    disconnected = Chem.Mol(butane)
    extra = Chem.AddHs(Chem.MolFromSmiles("O"))
    AllChem.EmbedMolecule(extra, randomSeed=42)
    conf = extra.GetConformer()
    for i in range(extra.GetNumAtoms()):
        p = np.array(conf.GetAtomPosition(i))
        conf.SetAtomPosition(i, tuple(p + np.array([10.0, 3.0, 2.0])))
    mixed = Chem.CombineMols(disconnected, extra)
    result = generate(
        mixed, {"chains": ["2-3"], "chain_angles": "0,90"}, args.out, "disconnected_water"
    )
    moved = []
    original = mixed.GetConformer().GetPositions()[butane.GetNumAtoms() :]
    for r in result.structures:
        moved.append(
            float(
                np.max(
                    np.linalg.norm(
                        np.asarray(r.coordinates)[butane.GetNumAtoms() :] - original, axis=1
                    )
                )
            )
        )
    rows.append(
        {
            "case": "disconnected_water",
            "status": result.status.value,
            "water_max_atom_displacements_A": moved,
        }
    )
    topo = topological_distance_matrix([[1], [0, 2], [1, 3], [2]])
    severe = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [2.0, 1.4, 0.0], [0.05, 0.0, 0.0]])
    rows.append(
        {
            "case": "clash_direct_1_4_pair",
            "end_atom_distance_A": 0.05,
            "clash_detected": clashes(severe, [0.76] * 4, topo, 0.65),
        }
    )
    for row in rows:
        if row["case"] != "cyclohexane_ring" and "status" in row:
            assert row["status"] == "completed", row
    payload = {
        "rdkit_version": rdBase.rdkitVersion,
        "scope": "V4 ConfgenExecutor; MMFF94s is a proxy, not quantum truth; labeled torsion basin comparison; no exhaustive completeness claim",
        "results": rows,
    }
    (args.out / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
