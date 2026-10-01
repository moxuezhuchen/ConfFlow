# TS1 ConfGen coordination fixture

This package is the fixed-input fixture needed to turn the previously discussed TS1/SCINE coordination benchmark into a reproducible ConfFlow test.

## What is fixed here

- 122-atom TS1 original geometry with fixed atom numbering.
- Six previously generated SCINE/Molassembler geometries F1-F6, preserving the same atom order.
- Raw Gaussian log used as the topology/geometry source.
- Typed topology: COVALENT / COORDINATION / FORMING edges.
- Explicit coordination-site order: N17, N19, O45, O46, O74, O80; Al47 is the metal center.
- Four declared forbidden-trans benchmark constraints.
- Octahedral proper-rotation convention and expected combinatorial counts.
- A full 122-atom typed-graph automorphism witness whose induced donor action is N17<->N19 and O45<->O46 while O74/O80 remain fixed.

## Expected topology-level benchmark

Using the declared constraints and the vertex convention in `benchmark/expected_coordination_benchmark.json`:

- all labeled octahedral placements: **720**
- placements satisfying the four declared forbidden-trans constraints: **288**
- proper-octahedral-rotation orbits: **12**
- after the supplied topology sigma action: **6** orbits, each of size 2

The shorthand used in the design discussion is therefore **30 -> 12 -> 6**, where 30 is the six-distinct-site octahedral count after quotienting all 720 placements by the 24 proper octahedral rotations.

## Critical interpretation

`12 -> 6` is an **abstract/topology-level orbit classification**. It is NOT permission to output only six geometries from the original TS1 input. Geometry suppression requires an independently verified `H_geom`. For the normal nonsymmetrized TS1 input, the implementation golden should expect **12 realization targets** unless `H_geom` proves otherwise.

The supplied sigma witness is verified here for element labels and typed graph edges and is explicitly involutive. Production code must still validate stereochemical parity/properness using the stereo model that ConfGen adopts.

The four 30->12 exclusions are deliberately tagged `REJECTED_BY_POLICY`, not `PROVEN_INFEASIBLE`. When a sound donor-path bound implementation exists, individual constraints may be upgraded only with a machine-verifiable proof.

## File layout

- `structures/ts1_original.xyz` -- original optimized TS1 coordinates.
- `structures/scine_F1.xyz` ... `scine_F6.xyz` -- six existing SCINE geometries.
- `structures/ts1_original_plus_F1-F6.xyz` -- combined seven-frame file.
- `topology/typed_topology.json` -- fixed atom list and typed edge graph.
- `topology/atom_index_roles.csv` -- human-readable numbering/role table.
- `benchmark/coordination_constraints.json` -- constraint provenance and classification.
- `benchmark/expected_coordination_benchmark.json` -- vertex convention, rotations, 12 representatives and 6 topology orbits.
- `benchmark/expected_sigma_witness.json` -- full 122-atom topology automorphism witness.
- `benchmark/scine_F1-F6_expected.json` -- SCINE assignment/index/vertex-map metadata.
- `benchmark/verify_fixture.py` -- dependency-light self-check.
- `source/si-rr-salanal2-r-end-spdd-ts1.log` -- raw Gaussian source log.
- `source/scine_F1-F6_report.txt` and `source/scine_generation_metadata.json` -- prior generation records.

## Suggested ConfFlow destination

`tests/fixtures/confgen/coordination/ts1/`

Run the fixture-only check with:

```bash
python benchmark/verify_fixture.py
```
