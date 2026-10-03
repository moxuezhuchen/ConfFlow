# C4.3 checkpoint (legacy native path retired)

* `contract.json`, `contract.full.json`, `boundary.full.json`: digests and full bytes of the contract and boundary after C4.3.
* Against `checkpoints/IS.5`: the boundary is byte-identical; the contract differs only by the removal of the `confgen.native` manifest field,
  the `confgen.native` entry of `native_escape_hatches.blocks`, the schema definitions `ConfgenModel` and `LegacyPathDeclarationModel`, and the
  collapse of `StepModel.confgen` to the typed v3 model alone (all remaining manifest fields keep their content and order).
* Engine reports are unchanged from `checkpoints/IS.5` (TS1 and all report files compared byte for byte).
* `engine_reports/`: the 6 engine reports written by the typed v3 path tests added in C4.3a (`test_confgen_paths_typed.py`); new files only, the B0.1 baseline and `checkpoints/IS.5/engine_reports` are untouched. The only previously captured report that disappears is the legacy `TestLegacyRegressions::test_v3_filenames_vs_legacy_compat`, removed with its test.
