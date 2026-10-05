# R7 DIFF — verify-only benchmark (no science change)

## Rule (PLAN R7, verify-only)

- `phase_defined_q_min = 0.05` is frozen (`ConfgenTolerances`, ClassVar,
  not tunable). The tool only reports the numeric gate: any reference or
  published seed below fails the run; `[0.05, 0.10)` is a REVIEW hint,
  never a rejection threshold. No threshold, solver, production, or
  fixture change in this card.
- Seeds come ONLY from `ConfgenEngine.run().leaves` (one real run per
  case). Same-run observer capture (try/finally around the stage entry
  point, original invoked once and returned unmodified, restored always)
  files solver evidence by target id; only captures matching actual
  published `leaf_target_id`s enter `seed_audits.json`. Root unpublished
  counterexample (published=0, recall must be 0) is a locked test.
- Per published seed the report carries measured CP/Q/rbar plus the real
  solver audit (`conjugation_planarity{value,limit,per_bond,passed}`,
  `ring_angle_drift{rigid/free,passed}`, `puckering_amplitude`,
  `cp_reached`, solver, clash). A missing capture for a published leaf is
  an explicit FAIL, never papered over.
- CP recall is `< 15 deg` per CREST reference against actual published
  seeds (never the math catalog). RMSD recall/redundancy is user-side
  only (`match_after_opt.py`, count-level, proves no physical basins).

## Results (R6 base re-verified; pre-R6 v3 cited with old HEAD)

- rpdd 3/3 (10 targets, 2 published), mch explicit 6/6 (38/38),
  synthetic cyclohexane 2/2 (8/8; R1-canonical math, NOT CREST) —
  re-run on `4e5e36a`, all audit-complete, 0 gaps.
- Q9 dual-track limits retained: mch default 5/6 (B_4), chexene needs
  constrained H, nap input-quality 9/14 → crest 14/14, thf full.
- beta_d_glucopyranose 113/155 default → 127/155 explicit; 28 distorted
  chairs stay beyond 15 deg of any regular ideal. Recorded as a limit;
  no relaxation, no 100% claim.
- 181 references all above the frozen gate; original-input gaps are
  unoptimized-input geometry-gate interceptions, not enumeration gaps.

## Files

- In repo (whitelist, 5 paths): 2 tools + 1 test + this MANIFEST + this
  DIFF. No xyz/csv bulk data in repo; full artifacts live outside
  (`/tmp/fix1r-r7-tool-v3-output`, `/tmp/fix1r-r7-final-output/verified`)
  with SHAs in MANIFEST.json. LOG entries stay with the milestone
  closing card.
