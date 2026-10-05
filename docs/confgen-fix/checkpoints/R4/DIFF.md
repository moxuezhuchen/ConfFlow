# R4 DIFF — CP forms switch (stage record, not final green)

- Base `ccf167f227d21322d1a251591440e75f48123538`, R4 commit `27e046c138090e68d54b2d0a711b392c7af0a3d8`, frozen patch sha256 `52f9425141f306bec77298dc1bbeeeae759319c99af9ed8138c4a62491776ed7`.
- Root stage: v3 218 tests + v4 11 tests passed, collect 4951, evidence `/tmp/fix1r-r4-v4-output/ROOT-STAGE-ACCEPTANCE.json`. Full milestone full/golden pending.
- Scope: 93 engine reports = 21 ring-allowed-change + 72 must-stay-same. Actual: 13 changed, 8 same (byte-identical to baseline), 72 non-ring unchanged by mapping `/tmp/fix1r-r4-baseline-prep-output/mapping_93.csv`. Never claim all 21 changed.
- Field direction (all 13): ring basis text template-product → CP-form product; `state_key.rings` `{template,torsions}` → `{form,index}` (anchor/direction retained); `evidence_sha256` + certificate digests follow; targets/leaves counts move per `/tmp/fix1r-r4-root-report-diff-summary.json` (13 entries). Six reports keep leaf/target counts yet still byte-change via the above fields.
- contract/full effect (4 paths, `/tmp/fix1r-r4-root-contract-diff-paths.json`): `/confgen/ring_forms_by_size`, `/contract_digest`, `/workflow_schema/$defs/RingGenerationSpec/properties/forms`, `/workflow_schema_sha256` — forms fields + hashes only. `boundary.full.json` (`ee811b99…`) unchanged; TS1 default/flexible/rigid unchanged at stage; `current-jd-contract` keeps FIX1A bytes. Final full/golden pending, not final green.
- R5/R4G add only illegal-input/diagnostic regressions; existing golden unchanged (R5 23 reports 0 diff; R4G 23 byte-equal).

## 13 changed (ring basis + StateKey + digests)

- `tests_v4_test_confgen_v3_combination.py_test_combination_36_leaves_real_stages__0.json`
- `tests_v4_test_confgen_v3_combination.py_test_combination_36_leaves_real_stages__1.json`
- `tests_v4_test_confgen_v3_combination.py_test_combination_adversarial_tamper_caught__0.json`
- `tests_v4_test_confgen_v3_combination.py_test_combination_perturbation_key_invariance__0.json`
- `tests_v4_test_confgen_v3_combination.py_test_combination_perturbation_key_invariance__1.json`
- `tests_v4_test_confgen_v3_combination.py_test_combination_real_perception_locks__0.json`
- `tests_v4_test_confgen_v3_core.py_test_chained_ring_enumeration_preserves_only_torsion_axis_locks__1.json`
- `tests_v4_test_confgen_v3_core.py_test_chained_ring_enumeration_preserves_only_torsion_axis_locks__2.json`
- `tests_v4_test_confgen_v3_core.py_test_engine_damaged_geometry_never_publishes__0.json`
- `tests_v4_test_confgen_v3_core.py_test_engine_ring_torsion_control_locks_hold__0.json`
- `tests_v4_test_confgen_v3_core.py_test_engine_rings_only_run__0.json`
- `tests_v4_test_confgen_v3_core.py_test_pinned_coordination_ring_damage_is_drift__0.json`
- `tests_v4_test_confgen_v3_integration.py_TestExecutorV3_test_nested_run_rows_carry_units__0.json`

## 8 ring-allowed but byte-identical (sha-verified vs baseline)

- `tests_v4_test_confgen_v3_core.py_test_conditional_exact_nine_not_twelve__0.json`
- `tests_v4_test_confgen_v3_core.py_test_conditional_failed_top_parent_defers_exact_subtree__0.json`
- `tests_v4_test_confgen_v3_core.py_test_conditional_failure_defers_exact_subtree__0.json`
- `tests_v4_test_confgen_v3_core.py_test_inherited_adversarial_rotation_is_drift_never_stale__1.json`
- `tests_v4_test_confgen_v3_core.py_test_inherited_adversarial_rotation_is_drift_never_stale__2.json`
- `tests_v4_test_confgen_v3_core.py_test_lock_reference_parent_uses_accepted_parent_geometry__0.json`
- `tests_v4_test_confgen_v3_core.py_test_lock_reference_parent_uses_accepted_parent_geometry__1.json`
- `tests_v4_test_confgen_v3_core.py_test_suppression_combined_axes_realize_normally__0.json`

## External evidence

- `/tmp/fix1r-r4-root-checkpoint/` (26 files + MANIFEST self `83353f9ba5c8957f7def878fc694cb38796876db0d71c25a58be74d6aa136168`, 26/26 sha recomputed OK).
- `/tmp/fix1r-r4-root-report-diff-summary.json` (13 entries), `/tmp/fix1r-r4-baseline-prep-output/mapping_93.csv` (93 rows), `/tmp/fix1r-final-root-expected-baseline-MANIFEST.json` (104 files; FIX1A baseline bytes kept, R4 overlay = 13 engine + accepted contract).
