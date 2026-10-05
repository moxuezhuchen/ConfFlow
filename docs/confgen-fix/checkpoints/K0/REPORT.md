# K0 clash diagnostic report (measured, R3 frozen)

- BASE: `f3c2eeffd06c83a159f5c7d4c21d1418661f31ab` (R3 frozen, detached rehearsal base)
- Method: A production `realize_cp_target` (clash on); B diagnostic mocks only
  `confflow.science.confgen.ring.realization.clash_pairs -> ([], False)` in B ring scope.
  Leaves reuse real `TorsionStage.realize` integrity+clash (threshold 0.65).
- Context graph binds original input; parent uses R3 real seed; `rotate_side:right` moves
  aryl/methyl side; ring atoms verified immobile (max move <= 1e-6).

## Provenance (frozen private XYZ in test, no tmp/RDKit/file reads at runtime)

- S1 rpdd CREST1 frame0 (22 atoms): private text sha `c4d6ff0ca8016bdf0eb7d02acf2f0582e9d1bcd03fcff9c13f8ef055c29dabb0`
  (full fixture `tests/fixtures/confgen/ring/rpdd/crest_conformers.xyz` sha `2078aca84ff293021553e3046920b9ceb787f7d04193d1f9efdd433705a1c7b1`);
  ring 0-based `[0,1,3,4,5,7]`; torsion 1-based `bond [1,12] rotate_side right`; B0/B3 x 6 angles = 12 leaves.
- S2 methylcyclohexane CREST frame0 (21 atoms): private text sha `b977c19ae39008062b75d58ea176a2ac27c5df739bee59185161ac320ae5d07f`
  (full CREST file `/tmp/crest-q9-batch-monitor-output/raw_mch/crest_conformers.xyz` sha `5db90a3c1eaf3f07039afd2068de6e39a6827a07deee0187e246868e6a57933d`, 6 frames, frame0 used);
  ring 0-based `[1,2,3,4,5,6]`; torsion 1-based `bond [2,1] rotate_side right` (methyl side `[7,8,9]`); 2C+6TB x 3 angles = 24 leaves.
- S3 o-tolyl-cyclohexane `CC1=CC=CC=C1C2CCCCC2` ETKDGv3 seed=20261005 + MMFF500 opt=0 (31 atoms):
  private text sha `b86ffcf10ed8576743a8bba6dae72e7ab281320246d88cbe8565a4c824149c73`;
  ring 0-based `[7,8,9,10,11,12]`; torsion 1-based `bond [8,7] rotate_side right` (aryl side 13 atoms, no ring); 2C+6TB x 6 angles = 48 leaves.

## Summary (target-level vs leaf-level distinguished)

| system | ring_targets | grid_leaves | A_clash_rejected | B_ring_pass | B_leaf_realized | B_leaf_clash | rescuable |
|---|---|---|---|---|---|---|---|
| S1 | 2 | 12 | 0 | 2 | 12 | 0 | 0 |
| S2 | 8 | 24 | 0 | 8 | 24 | 0 | 0 |
| S3 | 8 | 48 | 0 | 8 | 46 | 2 | 0 |
| total | 18 | 84 | 0 | 18 | 82 | 2 | 0 |

- `n_ring_clash_rejected`: whole ring target finally rejects with `clash` (first token). Non-clash fails do not count.
- `rescuable`: A clash reject AND B ring pass AND some B leaf `realized`.
- S3 B-leaf clashes (ring passes, leaf clashes) are NOT rescuable: C0@240, TB5@180 (both `geometry_failure/clash`, threshold 0.65).

## Decision

- SUM rescuable = 0. Honest negative: no evidence that ring-stage full-structure clash kills a
  torsion-resolvable structure on these real R x T grids.
- K1/K2 move to FIX-2; R4 proceeds per PLAN. Zero is a publishable negative result.

## Per target x angle (A reason + clash pairs, B leaf state)

A `pass` means ring realization passed all 7 audits (clash count 0). A `FAIL:<token>` shows final
exception reason; only `clash` counts. B leaf shows real `TorsionStage.realize` status/reason.
Clash pairs are `i-j:dist(limit)` 0-based from ring audit (empty when A passes).

### S1

| form | angle | A | A_clash_pairs | B_leaf |
|---|---|---|---|---|
| B0 | 0 | pass | - | realized/realized |
| B0 | 60 | pass | - | realized/realized |
| B0 | 120 | pass | - | realized/realized |
| B0 | 180 | pass | - | realized/realized |
| B0 | 240 | pass | - | realized/realized |
| B0 | 300 | pass | - | realized/realized |
| B3 | 0 | pass | - | realized/realized |
| B3 | 60 | pass | - | realized/realized |
| B3 | 120 | pass | - | realized/realized |
| B3 | 180 | pass | - | realized/realized |
| B3 | 240 | pass | - | realized/realized |
| B3 | 300 | pass | - | realized/realized |

### S2

| form | angle | A | A_clash_pairs | B_leaf |
|---|---|---|---|---|
| C0 | 0 | pass | - | realized/realized |
| C0 | 120 | pass | - | realized/realized |
| C0 | 240 | pass | - | realized/realized |
| C1 | 0 | pass | - | realized/realized |
| C1 | 120 | pass | - | realized/realized |
| C1 | 240 | pass | - | realized/realized |
| TB0 | 0 | pass | - | realized/realized |
| TB0 | 120 | pass | - | realized/realized |
| TB0 | 240 | pass | - | realized/realized |
| TB1 | 0 | pass | - | realized/realized |
| TB1 | 120 | pass | - | realized/realized |
| TB1 | 240 | pass | - | realized/realized |
| TB2 | 0 | pass | - | realized/realized |
| TB2 | 120 | pass | - | realized/realized |
| TB2 | 240 | pass | - | realized/realized |
| TB3 | 0 | pass | - | realized/realized |
| TB3 | 120 | pass | - | realized/realized |
| TB3 | 240 | pass | - | realized/realized |
| TB4 | 0 | pass | - | realized/realized |
| TB4 | 120 | pass | - | realized/realized |
| TB4 | 240 | pass | - | realized/realized |
| TB5 | 0 | pass | - | realized/realized |
| TB5 | 120 | pass | - | realized/realized |
| TB5 | 240 | pass | - | realized/realized |

### S3

| form | angle | A | A_clash_pairs | B_leaf |
|---|---|---|---|---|
| C0 | 0 | pass | - | realized/realized |
| C0 | 60 | pass | - | realized/realized |
| C0 | 120 | pass | - | realized/realized |
| C0 | 180 | pass | - | realized/realized |
| C0 | 240 | pass | - | geometry_failure/clash |
| C0 | 300 | pass | - | realized/realized |
| C1 | 0 | pass | - | realized/realized |
| C1 | 60 | pass | - | realized/realized |
| C1 | 120 | pass | - | realized/realized |
| C1 | 180 | pass | - | realized/realized |
| C1 | 240 | pass | - | realized/realized |
| C1 | 300 | pass | - | realized/realized |
| TB0 | 0 | pass | - | realized/realized |
| TB0 | 60 | pass | - | realized/realized |
| TB0 | 120 | pass | - | realized/realized |
| TB0 | 180 | pass | - | realized/realized |
| TB0 | 240 | pass | - | realized/realized |
| TB0 | 300 | pass | - | realized/realized |
| TB1 | 0 | pass | - | realized/realized |
| TB1 | 60 | pass | - | realized/realized |
| TB1 | 120 | pass | - | realized/realized |
| TB1 | 180 | pass | - | realized/realized |
| TB1 | 240 | pass | - | realized/realized |
| TB1 | 300 | pass | - | realized/realized |
| TB2 | 0 | pass | - | realized/realized |
| TB2 | 60 | pass | - | realized/realized |
| TB2 | 120 | pass | - | realized/realized |
| TB2 | 180 | pass | - | realized/realized |
| TB2 | 240 | pass | - | realized/realized |
| TB2 | 300 | pass | - | realized/realized |
| TB3 | 0 | pass | - | realized/realized |
| TB3 | 60 | pass | - | realized/realized |
| TB3 | 120 | pass | - | realized/realized |
| TB3 | 180 | pass | - | realized/realized |
| TB3 | 240 | pass | - | realized/realized |
| TB3 | 300 | pass | - | realized/realized |
| TB4 | 0 | pass | - | realized/realized |
| TB4 | 60 | pass | - | realized/realized |
| TB4 | 120 | pass | - | realized/realized |
| TB4 | 180 | pass | - | realized/realized |
| TB4 | 240 | pass | - | realized/realized |
| TB4 | 300 | pass | - | realized/realized |
| TB5 | 0 | pass | - | realized/realized |
| TB5 | 60 | pass | - | realized/realized |
| TB5 | 120 | pass | - | realized/realized |
| TB5 | 180 | pass | - | geometry_failure/clash |
| TB5 | 240 | pass | - | realized/realized |
| TB5 | 300 | pass | - | realized/realized |

## Reproduction

- `tests/v4/test_confgen_k0_clash_diagnostic.py` (4 tests): provenance hashes, thresholds
  (`ConfgenTolerances().clash_threshold==0.65`, `_R3_CLASH_THRESHOLD==0.65`, `TOPO_IGNORE_HOPS==3`),
  mock non-pollution, full 84-row matrix with frozen counts (S3 46 realized / 2 clash).
- Production diff: zero (only 1 new test + 3 docs). No golden/full runs, no installs.

