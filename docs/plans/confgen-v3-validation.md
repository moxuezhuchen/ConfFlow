# ConfGen v3 validation record

> superseded 2026-10-11: ConfGen now has a single DG search engine (schema_version 4); the ring, torsion, path and coordination-realization engines and their gates described here no longer exist.

Date: 2026-10-01. ConfGen v3 implementation checkpoint.
This record supersedes stale in-progress notes in `/tmp/confgen-v3-workers`.
Completed OpenCode workers were not rerun during final science-gate repair.

## Scientific gates and evidence

| Gate | Evidence |
|---|---|
| G0 fixture authority | All 19 TS1 manifest entries retain their size/SHA256; original ZIP untouched. |
| G1 core/torsion | Lazy mixed-radix parity, seeded index sampling, preflight refusal, atom ordering, stable identity and explicit preserved scope. Periodic double-bins reject at both workflow and science boundaries. |
| G2 coordination identity | Reference shape counts 2/3/20/30; TS1 720 raw → 288 policy-admissible → 12 shape classes → 6 declared-subgroup orbits; independent Burnside and SCINE audit. |
| G3 realization | Monodentate and bidentate controls, pure geometry constrained solver, native key+quality audit. Actual engine synthetic C2 suppression has verified witnesses; asymmetric TS1 retains all 12 targets. |
| G4 ring | Isolated 4/5/6 templates, substituent-frame propagation, parity and measured roundtrip; numerical perturbations retain discrete identity. |
| G5 combination | Real default C/R/T stages yield 36 leaves; exact conditional tree yields 9; failed-parent subtree separates deferred leaves from failed attempts. Both incoming torsion labels survive real ring re-enumeration with 4 leaves each. |
| G6 workflow/JobDesk | BY_SUBJECT state pairing, semantic upstream scope/certificate provenance, typed producer/GUI, editor roundtrip/compile, boundary parity. |

Review corrections preserve established green regressions. F-1 fixes the
axis-local lock contribution, F-2 rejects exact circular aliases without
merging nearby grid points, F-3 labels mixed diagnostic units and serializes
record units, F-4 delegates legacy geometry/capping and guards grid size
before realization. No certificate equation mixes internal attempts and
final leaves. Relative torsions use the persisted minimum-index-neighbor
frame convention, which is distinct from chemical frame selection.

## Validation commands and results

- Science/schema/integration: **261 passed** (208 s).
  `PYTHONPATH= .venv/bin/python -m pytest -q tests/v4/test_confgen_v3_{core,torsion,ring,combination,coordination,schema,integration}.py`
- Additional final core gate checks after count-unit annotation: **9 passed**.
- ConfFlow full mypy: **no issues in 257 source files**.
- `ruff check .`: **passes**. The exact externally supplied golden verifier
  path is excluded in project configuration so its manifest bytes stay
  unchanged. It has 13 original style findings; project source/tests retain
  all existing rules.
- JobDesk boundary/application/GUI ConfGen checks: **91 passed**.
- Isolated wheel-install checks: **11 passed** with build-dependency access;
  restricted-environment failures are tracked separately from regressions.
- Architecture boundary gate: **138 passed**; new science sources included
  in forbidden legacy-import scanning.
- JobDesk `ruff check src tests`: **passes**; new editor modules mypy:
  **no issues in 2 source files**, with the already declared YAML stub
  installed only in a `/tmp` checking environment.
- Broad repository run (excluding inaccessible real Gaussian binary test):
  **4479 passed, 7 skipped, 26 failed**, in 1131.56 s. Every failure was
  investigated and its full affected suite rerun successfully:
  wheel installation **11 passed** with build-dependency access;
  architecture **138 passed**; worker **22 passed**; cross-repo E2E + debt
  **60 passed** (includes 7 new numeric-format guards). These are selective
  corrective reruns, not a claim of a second all-green full-suite run.
  The cross-repo consumer double now handles JCS floating-point formatting
  and UTF-16 key ordering, preserving exact real-byte/digest assertions;
  the worker import gate distinguishes the exact typed molecular graph
  from forbidden compiler/DAG modules. No hash verification was bypassed.
  No failures remain unaccounted for in the affected suites.

## Explicit scope limits

- TS1 production `rigid_then_flexible` treats all 12 targets: **3 REALIZED
  (L00/L04/L05), 9 UNRESOLVED**. Every failure retains attempts, observed-key
  and quality evidence. This is solver/audit failure, not an infeasibility
  proof or a guarantee of low-energy coverage. No SCINE output is reused as
  generated geometry. Optional embedding was considered and not adopted.
- Molecular symmetry certification covers the declared validated subgroup;
  budget-limited searches never claim the complete molecular group.
- Verified geometry suppression is supported in its audited single-axis
  scope. Combined axes, capped sampling and top-level exclusions retain
  targets; joint-stabilizer suppression is conservative/disabled.
- Capped sampling over a genuinely conditional symbolic tree fails closed
  pending weighted-tree sampling. Exact conditional enumeration is supported.
- Donor configuration stays explicitly preserved. General R/S–E/Z assignment
  and symmetry suppression without adequate declared stereo are not claimed.
- Chelate/fused/bridged/macrocyclic rings, multi-metal and general haptic
  realization remain unsupported as declared in the scope.
- Legacy `native.chains` retains its separately versioned numeric and survivor
  cap semantics, now with an explosion guard. It emits structures/report but
  no measured `confgen_state`, so it cannot supply state authority for chaining.
  A future adapter must handle undefined terminal frames and equivalent raw
  grid ordinals honestly; raw ordinal keys are not manufactured.

Logs: `/tmp/confgen-v3-final-{science,regression,mypy,ruff}.log`,
`/tmp/confgen-v3-architecture-final.log`,
`/tmp/confgen-v3-install-escalated.log`, `/tmp/confgen-v3-jobdesk-final.log`,
`/tmp/confgen-v3-cross-repo-final.log`, `/tmp/confgen-v3-worker-final.log`.

## Pre-commit verification on 2026-10-01

Before committing the existing implementation for the input-simplification
work, the following checks were repeated:

- Ruff passed; mypy reported no issues in 257 source files.
- Black's formatting API checked all 47 changed Python files against the
  project's Python 3.10 / 100-column mode after formatting 21 files. The
  externally supplied fixture verifier was excluded and remains byte-identical.
- All 19 fixture manifest size/SHA256 checks passed.
- A V4 regression run completed the seven ConfGen v3 suites and architecture
  boundary suite without failures. The wider run was interrupted during the
  unrelated terminal-arbitration race suite after prolonged lack of output;
  this is not a claim of a completed all-green V4 run. The inaccessible real
  Gaussian differential test was excluded after its collection permission error.
- A separate affected-suite run completed with **127 passed** (21.76 s):
  worker, cross-repository E2E, producer closure, recipes and producer contract.

Logs: `/tmp/confflow-baseline-tests.log`,
`/tmp/confflow-baseline-affected.log`, `/tmp/confflow-baseline-mypy.log`.
