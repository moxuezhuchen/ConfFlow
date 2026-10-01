# Phase 5+6 Authoring Helpers: Machine Resources and Checkpoint Reuse

Producer authoring helpers that produce the existing strict V4 wire.
They use the current resource, graph, artifact, and Gaussian authorities;
the checkpoint helper validates the assembled document with the V4 compiler.

- `confflow.producer.machine.resolve_machine_resources`
- `confflow.producer.checkpoints.wire_checkpoint_reuse`

Both are re-exported from `confflow.producer`.

## `resolve_machine_resources(profile, resources, scheduler=None)`

Turns a per-item scientific request plus a machine capacity profile into
strict V4 `resources`/`scheduler` wire mappings plus a canonical provenance
record. Returns `{"resources", "scheduler", "provenance"}`.

- **Capacity.** `profile` requires `name`, `total_cores` (integer `>= 1`),
  and the `"total_memory"` key (integer bytes or a suffixed string parsed
  by the `parse_memory_bytes` authority). The key is a plain string and is
  never bound to a Python variable, per the architecture vocabulary gate.
- **Request.** Both `cores_per_item` and `memory_per_item` are required and
  must resolve positive. Absent dimensions are never filled from scientific
  defaults.
- **Width.** Automatic `max_parallel_items = min(floor(cores), floor(bytes))`.
  An oversized request (zero slots) fails instead of clamping. An explicit
  scheduler override must be positive and within the capacity limit;
  `on_failure` (`continue`/`fail_fast`) is preserved verbatim.
- **Operational schema (coordinated authoring/UI surface).** `executable`
  accepts a plain path string, `None`, or a per-program mapping of program
  names to paths. `binding_id` names the execution binding. The endpoint
  aliases `remote`, `remote_target`, and `target` canonicalize to `target`
  and conflict with each other when they disagree. `env`, `sandbox`,
  `allowed_executables`, and `walltime_seconds` are validated. All
  operational fields travel in `provenance` only.
- **Digest contract.** Returned `resources` are scientific (they move step
  and definition digests). Scheduler width, `on_failure`, and every
  operational field are digest-inert provenance.

## `wire_checkpoint_reuse(document, target, source, mode="checkpoint", *, allow_method_change=False)`

Adds one semantic checkpoint edge to a copied strict V4 document: the target
gains a `checkpoint` input binding fed by the source `artifacts` output with
the `checkpoint` role selector, promised with binding cardinality `one`
(which the graph authority permits on the `optional` checkpoint port). At
assembly the `one` contract requires exactly one subject-matched artifact, so
a missing checkpoint fails through the existing `artifact_flow` guard
(`artifact_subject_missing`) instead of silently running without restart data.
Pairing is left to the port contract (`by_subject`): no path or order
guessing.

- **Endpoints.** Both steps must be enabled Gaussian `calculation` steps on
  the `standard` execution adapter (plain checkpoint reuse or an IRC route on
  it). ORCA (no checkpoint input vocabulary), `named_structures`/QST shapes,
  and QST route items are refused.
- **Source record.** The source must resolve native `write_chk` to true, and
  the helper writes it explicitly into the copied document (digest-covered).
  Sources and targets that hand-manage `%Chk`/`%OldChk` in native `link0` are refused:
  the adapter renders those lines itself.
- **Modes (labels).** `checkpoint` wires restart data only and leaves the
  target route verbatim. `readfc` inserts `ReadFC` into the target `Opt`
  route item only (Opt label). `rcfc` inserts the `RCFC` vote into the
  target `IRC` route item only (IRC label) — RCFC is never appended to Opt
  routes or naked keywords. An already-present exact `ReadFC`/`RCFC` option
  is accepted idempotently (a user card may already spell it); opposed
  force-constant intent (`CalcFC`/`CalcAll` in the edited item, explicit IRC
  direction votes against `RCFC`) is refused.
- **Compatibility (no chemistry guessing).** The helper keeps each native
  method verbatim and records the relationship in the digest-covered binding.
  Refusals decidable without guessing: explicit charge/spin mismatches,
  route keywords differing beyond the managed `Opt`/`IRC`/`Freq`/`SP` items, and
  native scientific payloads (basis/ECP/extra sections, `modredundant`, atom
  mapping — everything except helper-managed `keyword`/`write_chk`/`link0`)
  differing at all. Unsupported method families refuse through the energy
  semantics authority. Deferred to runtime (documented, not guessed):
  structure atom counts (`by_subject` pairing plus the executor atom gate
  own them), deeper method equivalence, and file presence (staging plus the
  `one` guard own it). A user-intended method change passes only with the
  explicit advanced override `allow_method_change=True`, recorded in the
  authoring-layer provenance annotation on the target step (annotations are
  digest-excluded by design; the binding stays digest-covered).
  Charge and spin mismatches remain errors even with this method override.
  Bound structure state takes precedence over run defaults: a source step
  changing charge/spin to `1/2` passes those values to an ordinary downstream
  consumer even when globals say `0/1`. Unknown concrete state is accepted
  only when the target demonstrably inherits the source's state through
  structure bindings without overriding that field. An explicit target
  override against an unknown source is refused; run defaults alone cannot
  prove the state of imported structures.
- **Validation.** The full wired document compiles through the existing
  compiler: cycles, unknown steps, and every other semantic rule reject
  exactly as hand-written documents do.

## Provenance versions

- Machine resolution: `confflow.producer.machine.v1`
- Checkpoint reuse: `confflow.producer.checkpoints.v1`
