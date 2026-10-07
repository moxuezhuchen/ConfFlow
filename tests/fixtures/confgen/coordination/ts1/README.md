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
- `source/si-rr-salanal2-r-end-spdd-ts1.log` -- raw Gaussian source log (external; not in working tree, see retrieval below).
- `source/scine_F1-F6_report.txt` and `source/scine_generation_metadata.json` -- prior generation records.

## External source log retrieval

`source/si-rr-salanal2-r-end-spdd-ts1.log` (4581599 bytes,
sha256 `5908d7fc95ad9b59dc0be3d2c7482f7360513bdc548d20479fc5307dbe2ff060`)
is kept out of the working tree. No test or tool reads it at runtime;
`structures/ts1_original.xyz` already carries the derived geometry and
`benchmark/verify_fixture.py` skips the missing external entry with a
`SKIP` notice (verifies sha256 when the file is present). To retrieve:

```bash
git show d5a40ae:tests/fixtures/confgen/coordination/ts1/source/si-rr-salanal2-r-end-spdd-ts1.log > /tmp/ts1-source.log
sha256sum /tmp/ts1-source.log  # must equal the sha256 above
```

Alternate copy with identical bytes:
`ts1_confgen_golden_fixture.zip` →
`ts1_confgen_golden_fixture/source/si-rr-salanal2-r-end-spdd-ts1.log`
(e.g. `python3 -c "import zipfile;open('/tmp/ts1-source.log','wb').write(zipfile.ZipFile('ts1_confgen_golden_fixture.zip').read('ts1_confgen_golden_fixture/source/si-rr-salanal2-r-end-spdd-ts1.log'))"`).

## Suggested ConfFlow destination

`tests/fixtures/confgen/coordination/ts1/`

Run the fixture-only check with:

```bash
python benchmark/verify_fixture.py
```
