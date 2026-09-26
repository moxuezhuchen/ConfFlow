# V4 Repair — Environment Identity Report (owner: environment lead)

Scope: `confflow/execution/contracts.py` (`ExecutionEnvironment` + v2 rule),
`confflow/execution/environment.py` (measurement, cache, pure helper),
NEW `tests/v4/test_repair_environment.py`. No C/D files touched; no full
integration; no commit/push. Exact API: `/tmp/confflow-v4-environment-api.md`.

Freeze basis: §7 (environment) + supervision notes §10 (prefix/mtime drift),
§15–§17 (ResultRef producer digest, output-entity generation awareness,
native-mode table leakage — planning only here, no edits to B/C/A files).

## Root cause

`measure_executable` hashed only a 512MiB prefix and folded
`size:mtime` into the content hash, while `ExecutionEnvironment.digest()`
(v1) folded the endpoint `target` locator. Consequences: a modified binary
tail past the cap aliased to the old identity; a relocated byte-identical
binary with a new mtime (copy/rsync/restore) invalidated reuse; two
identical computations on different endpoints never reused; scheduler-adjacent
and path facts sat one step from the scientific axis; pure executors had no
construction (callers passed `None`, and unknown silently compared as an
ordinary value).

## Fix

- Full-content sha256 executable identity; stat kept as provenance + cache
  key only (`stat_key()`), never hashed in. `max_hash_bytes` retained as an
  ignored compat parameter (existing caller passes it; semantics now always
  full-hash, documented).
- Stat-gated `EnvironmentMeasurer` cache: every read re-stats; dev/ino/size/
  mtime/real-path mismatch re-measures, vanished files fail closed. Residual
  alias (in-place rewrite preserving all stat fields) documented; direct
  `measure_executable` bypasses the cache.
- Digest rule v2: `{program, program_version, executable_digest,
  relevant_env}`; `target`/paths/stat/scheduler excluded. v1 kind retained
  as a constant; v1≠v2 textually so old rows `INVALIDATE_ENVIRONMENT`.
- `relevant_env`: caller-declared `str→str` subset via `select_relevant_env`
  (explicit `declared` names; undeclared vars can never leak). No relevance
  table invented here — declarations are follow-up binding/compiler
  contract (dependency D-ENV-1, §Planning).
- `ExecutionEnvironment.unknown(program, reason)` + auto nonce: unknown
  digests are unique per construction — unknown≠verified, unknown≠unknown —
  so strict reuse (`evaluate_reuse` equality, D-owned, untouched) fails
  closed with no consumer change.
- `build_pure_environment(implementation, implementation_version, …)`:
  single construction for pure executors (D/remote), no native probing.

## Tests (11/11 pass)

`tests/v4/test_repair_environment.py`: relocated-identical/mtime equality;
same-size same-path replacement invalidation (+exact full-hash assertion);
520MiB sparse tail-change regression; cache hit-identity + stat-bump
re-measure + content-change invalidation; relevant-env sensitivity;
operational/target/scheduler inertness; pure version/rename sensitivity +
pure/native separation; unknown strict-reuse triple via `evaluate_reuse`;
validation failures; v2 marker; pinned v2 golden
(`sha256:49b77439…8ad07`). Gates on owned files: ruff check + format clean;
mypy clean on both owned modules (one mypy error observed lives in C-owned
`confgen_executor.py:373`, not mine).

## Regression impact on existing tests (for H / lead — not edited)

- WILL FAIL (intended rule change, this report is the evidence):
  `test_digest_axes.py::TestDigestAxisSeparation::test_environment_digest_ignores_absolute_paths`
  (asserts target moves digest) and
  `test_v42_executors.py::TestEnvironmentMeasurement::test_target_moves_environment_digest`.
- NOT MINE (verified by attribution, same run): `test_work_item_digest_golden`
  (B's work-item v2 bump), `TestScientificSensitivity::test_adapter_change_moves_step_digest`
  (A's registry rework: `native_template` currently unregistered).
- PASS under change: `TestEnvironmentBranches/Edges` (16), parity
  relocation/alteration env tests (3).

## Planning (no edits — lead coordinates with owners)

1. **Relevance declarations (D-ENV-1).** Native steps currently fold `{}`:
   binary+version identity only. Binding resolution (C-owned module) / compiler
   must supply per-step declared relevant names (e.g. threading/MKL-style
   numeric flags); `select_relevant_env` is the seam. Do not let D scan
   `os.environ` wholesale.
2. **Pure versions (D/F).** D must pass REAL registry contract versions into
   `build_pure_environment` (confgen/transform/analysis implementations) and
   replace the analysis path's `environment_digest=None` with it; remote (E)
   must use the same constructor target-side. Literals would freeze stale
   pure reuse.
3. **Output-entity generation awareness (supervision §16, C/G/F).**
   `output_identity.py` derives entity IDs from logical_key+role+ordinal
   only; changed producer science at one address can mint the same entity
   for different geometry — the dual of the ResultRef `producer_digest`
   fix (B adds required `producer_digest=WorkItem.semantic_digest` to
   `make_result_id`; C passes actual digest). Lead decision needed: coherent
   immutable output-entity identity (producer semantic generation + stable
   native discriminator; retries same-digest stable), must not confuse
   native image/member index with parser list position. C/G/F coordinate;
   I did not touch `output_identity.py`.
4. **Native-mode table leakage (supervision §17, A/C).** A's
   `_NATIVE_MODES`/`_MODE_PROFILES`/`_goat_seed_conflict` interpret
   ORCA-native keys in the compiler and duplicate adapter mode/seed
   authority; registry must not advertise the bare AnalysisExecutor core
   as item-seam-compatible (F owns the real adapter). Integration must move
   native interpretation into ProgramAdapter-owned facts/diagnostics.
   Noted only; no edits to validation/adapters.
5. **D `_measure_environment` wiring.** D currently calls
   `build_environment(candidate, adapter, target)` without `relevant_env`
   and fails closed on unmeasurable — compatible as-is; lead sequences the
   `relevant_env` threading + pure-path adoption with D's remaining work.

## Status

Environment root fix implemented, tested, formatted, lint/type clean on
owned files. Awaiting full-integration instruction. Nothing committed.
