# ConfFlow Workflow V3 — Implementation Plan

Status: **PLANNING ONLY — not an implementation.** This document plans the R3
slices so they can be implemented, reviewed and rolled back one at a time. No
runtime, parser, schema, IR, CLI, state or binding code is changed by this plan.

- **Base**: `ebea9d949b82cf0cd3512efb365819f006d21c2f` (accepted RFC head on
  `design/workflow-v3-rfc`), on top of `61e11b4` / `c649f59` / `014e185`.
- **Authoritative design**: `docs/rfc/WORKFLOW_V3_RFC.md` (accepted) +
  `docs/rfc/workflow-v3.schema.draft.json` (draft). This plan **does not change
  the RFC's semantics**; it only clarifies implementation. Where the RFC leaves
  an implementation detail open, this plan chooses one and marks it
  `[clarification]`.
- **Companion plan branch**: `plan/workflow-v3-implementation`.

> **R3 does not make V3 workflow executable.** V3 becomes parseable, validatable,
> upgradeable and inspectable; **running** a V3 workflow stays fail-closed until
> R4 provides state/binding v2 (§7, §24).

---

## 1. Implementation Reality Check

Verified by reading the current code at the base SHA. "Gap" is what must be
added; "Target" is the slice that adds it.

| RFC feature | Current support (code) | Gap | Target |
|---|---|---|---|
| **Stable step id** | none — identity is the V2 name (`canonical_step_name`); IR has `name`/`v2_name` only | `CanonicalStepDefinition.id`, parser, uniqueness/grammar validation, upgrade allocation | R3.1 / R3.2 / R3.3 |
| **label** | none — `name` doubles as label | IR `label`; parser; `config-show` display | R3.1 / R3.2 / R3.5 |
| **explicit inputs** | V2 explicit-DAG + implicit-linear fallback; `normalize_step_inputs`, `build_step_graph`, `topo_order` in `canonical/workflow.py` | V3 requires `inputs` always; V2 fallback must remain untouched | R3.1 / R3.2 |
| **inputs = step-id refs** | refs are V2 canonical names | V3 refs are ids; resolver logic is unchanged (it works on graph keys) | R3.2 / R3.4 |
| **version dispatch** | none — `parse_workflow_mapping` ignores a `schema` key entirely; the V2 JSON schema has **no** `schema` property and `additionalProperties: true`, so historical V2 files have no version field | `detect_schema_version()` + dispatch + "never treat unknown as V2" | R3.2 |
| **checkpoint reference** | `params.chk_from_step` (name or 1-based index) in `step_handlers._resolve_chk_input_dir`; R2 validates it inside `params` | promote to `checkpoint.from_step`; migrate; validate ancestry | R3.2 / R3.3 / R3.4 |
| **strict params** | V2 = open bag; `_known_calc_keys()` (fingerprint.py) and `confgen_known_keys()` (shared/confgen_params.py) already exist | single key registry; V3 schema + validation derive from it; `chk_from_step` leaves `params` | R3.2 / R3.4 |
| **extensions** | V2 unknown *step-level* fields → IR `extensions` (carried, currently unused downstream) | namespaced `ExtensionRegistry` + recognition gate | R3.2 / R3.4 |
| **annotations** | none | IR `annotations`; parser; serializer; never fingerprinted | R3.1 / R3.2 |
| **fragment vs runnable** | R2 split exists: `parse_workflow_mapping` (parseable) vs `validate_workflow_definition` (runnable) | explicit profile value object instead of relying on which function you call | R3.2 |
| **fingerprint boundary** | `canonical_workflow_payload` / `workflow_fingerprint(plan)` (V2, binding v1) | V3 **definition** fingerprint (A) as a pure function; binding v2 stays R4 | R3.1 |
| **recipe identity/rebase** | `recipes.py` is a catalog only; **no instantiate code exists anywhere** | RFC §19.3 algorithm is specified; implementation is an application-layer verb (first consumer = GUI/JobDesk), **out of R3 scope** | post-R3 (marked) |
| **CLI step selector** | `_select_step` by name/1-based index in `config_show.py` and `rerun_failed.py` | V3 id selector | R3.5 |
| **config validate** | R2 `validate_workflow_definition` + `config/cli.py` v1 projection | version-aware dispatch | R3.4 / R3.5 |
| **config-show** | name/index, no graph, no id/label | V3 id/label/inputs/checkpoint display | R3.5 |
| **dry-run** | assumes a single linear chain (`current_input = output_path`), no DAG | V3 graph + execution-order + input-relationship display | R3.5 |
| **export** | keyed by dirname/step_name from `.workflow_state.json`; `OUTPUT_MANIFEST_SCHEMA = output_manifest.v1` | **no V3 change in R3** — step-id export metadata needs state v2 | R4 |

**Two facts that shape the plan**

1. `_known_calc_keys()` lives in `fingerprint.py` and includes `chk_from_step`
   (V2 binds it into the fingerprint). V3 must **exclude** `chk_from_step` from
   `params`. The registry therefore keeps the V2 set verbatim and derives the V3
   set from it (§12).
2. Nothing dispatches on a document version today. A V3 document handed to the
   current engine would be silently read as V2 (its `schema` key ignored). The
   gate in R3.2 exists to make that impossible (§7).

---

## 2. Version dispatch

`[clarification]` — the RFC says "dispatch by `schema`"; the exact table:

| `schema` value in the document | Version |
|---|---|
| absent | **V2** (historical files carry no version field; verified against `schema.py`) |
| `confflow.workflow.v2` | **V2** (explicit) |
| `confflow.workflow.v3` | **V3** |
| any other non-empty value | **error** (`workflow.schema.unsupported`) — never guessed |

- `detect_schema_version(raw) -> Literal["v2","v3"]` lives in `parser.py`.
- The V2 path (`parse_workflow_mapping`, `WorkflowConfig.from_mapping`,
  `load_workflow_model`) is **unchanged**; V2 tests and fingerprints are the proof.
- A V3 document must never reach the V2 adapter. The engine boundary raises the
  execution gate instead (§7).

---

## 3. R3 / R4 boundary and the V3 execution feature gate

`[clarification]` — RFC §16.D says V3 resume needs binding v2 / state v2. Today
state keys are **dirnames** and the binding is `workflow_binding.v1`
(`workflow/state.py`, `canonical/fingerprint.py`), neither of which can be
correct for a V3 document. Therefore R3 chooses **option A from the review**:

```
R3:  parse  · validate · upgrade · config-show · dry-run      ← allowed
R4:  execute · resume · export step-id metadata · binding v2  ← blocked
```

**Gate implementation (R3.2).** A single helper:

```python
# confflow/config/canonical/execution_gate.py
V3_EXECUTION_SUPPORTED = False   # flipped only when R4 lands

def ensure_executable(definition) -> None:
    if definition.source_version == "v3" and not V3_EXECUTION_SUPPORTED:
        raise ConfFlowError(
            "Workflow V3 execution requires state/binding v2 (R4). "
            "V3 documents can be validated, upgraded and inspected, but not run yet."
        )
```

- Called from `build_workflow_plan` (the single planning boundary used by
  `engine.run_workflow`) and from `rerun_failed` (which would write step state).
- **Not** a boolean parameter, environment variable, or string-flag on a public
  API. It is one guarded constant + one call site per execution boundary.
- `config-show` / `dry-run` / `validate` / `upgrade` never call `ensure_executable`.
- Stop gate: a V3 document must be **unable** to write `.workflow_state.json`
  (v1) or reach `CalcArtifactManager`.

---

## 4. Production schema authority (single source of truth)

`[clarification]` — RFC §13 anticipates this; the concrete rule:

**Authoritative owner of "which core params exist" = one registry module.**
`confflow/config/canonical/param_fields.py` (new, R3.2):

```python
# moved verbatim from fingerprint._known_calc_keys() — V2 behaviour preserved
V2_CALC_PARAM_KEYS: frozenset[str]              # includes "chk_from_step"
# V3 core keys: the V2 set minus chk_from_step (promoted to `checkpoint`)
V3_CALC_PARAM_KEYS = V2_CALC_PARAM_KEYS - {"chk_from_step"}
CONFGEN_PARAM_KEYS: frozenset[str]              # == confgen_known_keys()
```

Derived consumers (no second hand-written allow-list anywhere):

| Consumer | How it uses the registry |
|---|---|
| V2 fingerprint `_known_calc_keys()` | becomes `return V2_CALC_PARAM_KEYS` (byte-identical) |
| V3 validation strict-params check | `params.keys() ⊆ V3_CALC_PARAM_KEYS` / `CONFGEN_PARAM_KEYS` |
| V3 JSON Schema `params` | generated: `properties = {k: {} for k in sorted(keys)}`, `additionalProperties: false`; `iprog`/`itask` get enums derived from `ProgramName`/`TaskName` |
| future editor manifest (R7) | generated from the same registry + typed descriptors |

- **Value semantics stay owned by the canonical resolvers**
  (`CalcStepParams.from_params`, `resolve_confgen_params`). The schema enforces
  the **key set**; the resolver enforces **values**. A test asserts the schema
  accepts exactly the documents the validator accepts (`test_v3_schema_agrees_with_validator`).
- The published `confflow.workflow.v2` schema and `workflow_schema_sha256()`
  are **untouched**. V3 schema getters are new (`workflow_json_schema_v3()`,
  `workflow_schema_sha256_v3()`), used only as binding/provenance (RFC §16.B).
- The draft file `docs/rfc/workflow-v3.schema.draft.json` is **not** wired in; the
  production V3 schema is a Python-generated dict, like V2.

---

## 5. Serialization strategy

`[clarification]` — two serializers, one meaning each:

| Serializer | Purpose | Order policy |
|---|---|---|
| **Human YAML** (`canonical/yaml_io.py`, new) | write documents (upgrade output, future save) | `sort_keys=False`, **author step order preserved**, step key order fixed (`id,label,type,enabled,inputs,params,checkpoint,extensions,annotations`) |
| **Canonical JSON** (`serialization.canonical_json`, existing) | fingerprints and contract digests | `sort_keys=True`; steps sorted by the V3 order key |

- The two are **never** conflated: the fingerprint canonicaliser does not rewrite
  the user's YAML, and the YAML writer does not sort.
- Comments are **not** preserved (we do not use a round-trip loader); upgrade
  output is a fresh, comment-free document. Stated so no one expects otherwise.
- Determinism test: `dump(load(dump(doc))) == dump(doc)` byte-for-byte, and
  upgrade twice from the same V2 source byte-for-byte.

---

## 6. Error / diagnostic compatibility

- Reuse R2's `Diagnostic(code, severity, path, message, step_ref)`
  (`canonical/diagnostics.py`). For V3, `step_ref` is the **stable id**; `path`
  still points at the document location (e.g. `steps[2].inputs[0]`).
- The v1 CLI wire shape is **unchanged**: `{schema, valid, workflow_schema_sha256,
  issues:[{path,message}]}`. No `code`/`severity`/`step_ref` on the wire (the
  JobDesk consumer rejects extra members). V3 diagnostics are projected through
  the same `to_v1_issue()`.
- `config validate` becomes version-aware *internally*; the response shape does
  not change (RFC §10.1).

---

## 7. R3 slices

Each slice: goal · files · data model · compatibility · tests first · acceptance ·
commit · stop gate. Slices are ordered; each is independently reviewable and
revertible.

### R3.1 — V3-ready Canonical IR

**Goal.** Let the existing R1 IR represent both a V2-adapted workflow and a future
V3 workflow, **without** a V3 parser. `R3.1` adds fields; it does not populate
them for V2.

**Answers to the required questions.**

1. **Current identity** — `CanonicalStepDefinition.name` (canonical graph key)
   plus `v2_name` (the typed V2 projection name); `predecessors` are canonical
   names. `CanonicalWorkflowDefinition` has `global_config/global_options/steps/
   dependency_mode/predecessors/execution_order/terminal_steps/extensions`.
2. **Add a stable id without breaking V2** — add `id: str | None = None`. For V2
   the adapter leaves it `None`; `to_v2_step()` ignores it. No V2 code path reads
   it, so V2 output is unchanged.
3. **V2 has no native id** — the temporary identity is the existing `name`
   (canonical graph key). `[clarification]` define
   `CanonicalStepDefinition.identity -> str` = `id if id is not None else name`,
   so graph consumers have **one** accessor and never branch on version. For V3,
   `name = id` (the graph key is the id) and `label` carries the display title.
4. **`identity_origin` enum?** — **No.** `source_version` on the definition is
   sufficient and adding an enum would be a third way to describe the same fact.
   `identity` is derived, not stored.
5. **label ← V2 name** — for V2, `label` is populated from the typed name
   (`v2_name`), stripped; empty-after-strip falls back to the generated graph name.
   `[clarification]` — the RFC (§14) says "the V2 name or the generated name"; the
   strip rule only removes whitespace-only names.
6. **V2 generated name survives** — yes: `v2_name` and `name` are untouched; the
   V2 execution projection still emits exactly `{name,type,enabled,params[,inputs]}`.
7. **checkpoint in the IR** — a structured optional field
   `checkpoint_from: str | None`, **not** a legacy raw param. For **V2** the
   adapter leaves it `None` and keeps `params.chk_from_step` verbatim (V2
   fingerprint/execution unchanged). V3 population happens in R3.2.
8. **extensions / annotations in the IR** — `extensions: dict` already exists
   (V2: unknown step-level fields). Add `annotations: dict` (default `{}`) at both
   step and root level. Neither is consumed by the V2 pipeline (verified: nothing
   reads `.extensions` today), so adding them is inert for V2.
9. **Keep `to_v2_execution_shape()` byte-identical** — it must keep returning
   `(global_config, [to_v2_step(), …])` with the same keys/values. The new fields
   are never read by it. A golden test pins V2 fingerprints and plan fields.

**Data model (exact, `[clarification]` on defaults).**

```python
@dataclass(frozen=True)
class CanonicalStepDefinition:
    name: str                       # graph key (V2 canonical name | V3 id)
    type: str
    enabled: bool
    params: dict[str, Any]
    predecessors: tuple[str, ...]   # V2 names | V3 ids
    inputs_declared: bool
    v2_name: str
    id: str | None = None           # NEW
    label: str | None = None        # NEW
    checkpoint_from: str | None = None  # NEW
    raw_inputs: Any = None
    extensions: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)   # NEW

    @property
    def identity(self) -> str:
        return self.id if self.id is not None else self.name
```

```python
@dataclass(frozen=True)
class CanonicalWorkflowDefinition:
    ...                             # existing fields unchanged
    source_version: Literal["v2", "v3"] = "v2"   # NEW
    annotations: dict[str, Any] = field(default_factory=dict)  # NEW
```

**Plus (pure function, no state):** `workflow_definition_fingerprint_v3(definition)`
implementing RFC §16.A (semantics_version + scientific global + per-step
`{id,type,enabled,resolved params,inputs,checkpoint_from,extensions}`, steps
sorted by the V3 order key; excludes label/annotations/order/schema digest/source
version). It is **not** wired into any binding.

**Files.** `canonical/workflow.py` (add fields + `identity` + v3 order key +
fingerprint fn), `canonical/v2_adapter.py` (populate `label`, leave `id=None`,
leave `checkpoint_from=None`).

**Compatibility.** Every R1/R2 test must stay green; V2 fingerprints, plan
fields, dirnames, state and binding unchanged.

**Tests first.**
- all R1 differential tests (`test_canonical_workflow_ir.py`) unchanged
- all V2 characterization (`test_workflow_characterization_v2.py`) unchanged
- new: `test_ir_v3_fields_default_to_none_for_v2`; `test_ir_identity_accessor`
- new: `test_v3_definition_fingerprint_*` for P1–P6 (built from in-memory V3-shaped IR)
- new: golden V2 fingerprint unchanged (`test_v2_durable_digest_golden.py` still green)

**Acceptance.** No new field is visible in any V2 projection; V3 fingerprint is
computable from an in-memory IR.

**Commit.** `feat(config): extend canonical workflow IR for V3 identity`

**Stop gate.** If any V2 fingerprint / plan field / dirname / binding digest
changes → STOP and investigate before continuing.

---

### R3.2 — `workflow.v3` parser + production schema

**Goal.** Parse a V3 document into the IR; publish a generated V3 schema; add the
execution gate. No V3 execution.

**Version dispatch.** `detect_schema_version` (§2) + `load_workflow_definition(config_file)`
(new entry point) that dispatches V2/V3 → `CanonicalWorkflowDefinition`.
`load_workflow_model` stays V2-only.

**Profiles** `[clarification]` — an explicit value object, not a bool:

```python
class ValidationProfile(Enum):
    FRAGMENT = "fragment"   # parseable partial: id optional, user-required params optional
    RUNNABLE = "runnable"   # document profile: id required, core params complete, extensions recognised
```

`parse_v3_document(raw, profile=FRAGMENT)` (structural) and
`validate_workflow_definition(raw, profile=RUNNABLE)` (semantic). The existing
`validate_workflow_definition(raw)` signature gains `profile=RUNNABLE`
(backward-compatible default).

**Production V3 schema** (`schema.py` additions; V2 untouched):
- `WORKFLOW_SCHEMA_VERSION_V3 = "confflow.workflow.v3"`, `workflow_json_schema_v3()`,
  `workflow_schema_sha256_v3()`.
- root closed: `{schema, global, steps, extensions, annotations}`, `additionalProperties:false`.
- step closed: `{id,label,type,enabled,inputs,params,checkpoint,extensions,annotations}`,
  `required:[id,type,inputs]` (document profile), `type∈{calc,confgen}`.
- `params` generated from `param_fields` (key set + `additionalProperties:false`);
  `iprog`/`itask` enums from `ProgramName`/`TaskName`.
- `checkpoint` = `{from_step}` closed; `extensions` propertyNames = namespaced regex.
- Publish **only** as an artifact; the runtime validates via the canonical
  validator (agreement test in §16).

**Extension registry** (new `canonical/extensions.py`):
- `ExtensionSpec(namespace, validate, fingerprint_payload=None)`.
- `ExtensionRegistry` with `register`, `recognised(ns)`, `validate(ns,payload)`,
  `fingerprint_payload(entries)`.
- Producer-owned, **empty by default**: with nothing registered, *every*
  extension namespace is unrecognised → parse preserved, runnable ERROR (R3.2
  behaviour, matching RFC §6.3). No `if namespace == "…"` in the validator.
- Core built-ins may be registered at import in a later phase; R3 registers none.

**Strict params.** Unknown core param = definition ERROR using `param_fields`
(RFC §6.4). `chk_from_step` is **not** a V3 core param (it is `checkpoint`).

**Execution gate.** Add `canonical/execution_gate.py` and call `ensure_executable`
from `build_workflow_plan` and `rerun_failed` (§3). This is what prevents "V3 read
as V2" / "V3 run on v1 state".

**Files.** new `canonical/v3_parser.py`, `canonical/extensions.py`,
`canonical/param_fields.py`, `canonical/execution_gate.py`,
`canonical/yaml_io.py`; modify `canonical/schema.py`, `canonical/parser.py`,
`canonical/validation.py`, `canonical/__init__.py`, `canonical/fingerprint.py`
(import the registry), `workflow/plan.py` + `workflow/rerun_failed.py` (gate call).

**Tests first.** valid linear/branch/fan-in/checkpoint/disabled; duplicate label
allowed; duplicate id rejected; missing id rejected (runnable) / allowed (fragment);
unknown root key rejected; unknown step key rejected; unknown param rejected;
recognised extension accepted; unknown extension parse-ok/runnable-error;
annotation accepted; version dispatch table; V2 documents unaffected; schema
agrees with validator.

**Acceptance.** V3 parses and validates; V2 unchanged; a V3 document cannot reach
the engine (gate raises).

**Stop gate.** If the schema's allowed fields and the validator's allowed fields
are two hand-written lists → STOP (must both derive from `param_fields`/enums).

**Commit.** `feat(config): add Workflow V3 parser and schema`

---

### R3.3 — Deterministic V2 → V3 upgrade

**Goal.** `confflow workflow upgrade` turns a V2 document into a deterministic V3
document. No execution, no state.

**CLI** `[clarification]` — a new top-level subcommand dispatched from
`confflow/cli.py` (`effective_args[0] == "workflow"`), implemented in
`confflow/config/workflow_cli.py`:

```
confflow workflow upgrade <input.yaml> [-o out.yaml]
                          [--check] [--unknown-params={fail|annotations|extensions:<ns>}]
```

- default output: stdout; `-o` writes a file.
- `--check`: report what would change; write nothing.
- `input is already V3` → **refuse** (error, RFC §14: never re-number).
- unknown `schema` value → refuse.

**Algorithm** — exactly RFC §14:
1. ids: document order → `s001, s002, …` (sequential family only).
2. label: V2 typed name (stripped; empty → generated graph name) `[clarification]`.
3. type: `task→calc`, `gen→confgen`.
4. enabled: copied.
5. inputs: explicit-DAG → predecessor names → ids; implicit-linear → step *k*
   `inputs=[id(k-1)]`, first `[]`.
6. checkpoint: `params.chk_from_step` (name → id; numeric → id at that 1-based
   position) → `checkpoint.from_step`; remove `chk_from_step` from params.
7. params: copied; **unknown core key → fail by default**; `--unknown-params=annotations`
   demotes them to `annotations`; `--unknown-params=extensions:<ns>` moves them to
   that namespaced extension (then the document is a runnable ERROR unless `<ns>`
   is registered — reported, not silently ignored).
8. unknown step-level V2 fields → `annotations` (they were inert in V2).
9. `schema: confflow.workflow.v3`; array order preserved.
10. `[clarification]` present alias **values** are canonicalised
    (`iprog: 2`→`orca`, `itask: 1`→`sp`) via the existing normalisers; **absent**
    values stay absent (never materialise a default).

**Serializer.** `yaml_io` (§5): author order preserved, fixed key order, no comments.

**Idempotence.** Same V2 source → byte-identical V3 output. V3 input → refuse.

**Files.** new `canonical/upgrade.py` (pure `upgrade_v2_mapping(raw) -> dict`),
`config/workflow_cli.py`; modify `confflow/cli.py` (dispatch), `canonical/__init__.py`.

**Tests first.** implicit linear; explicit DAG; unnamed step; disabled; `task`/`gen`
aliases; numeric + named `chk_from_step`; unknown params (fail / annotations /
extensions); unknown step-level fields → annotations; deterministic (twice);
V3-input refusal; `>999` steps (`s1000`); alias value canonicalisation; parse the
V3 output through `parse_v3_document` (round trip).

**Acceptance.** `upgrade` output parses and validates as V3; two runs are identical.

**Stop gate.** If upgrade is not byte-deterministic, or its output fails V3
validation → STOP.

**Commit.** `feat(config): add deterministic V2 to V3 upgrade`

---

### R3.4 — V3 graph / reference validation

**Goal.** The runnable-definition validator for V3.

**Checks.** duplicate id; invalid id grammar; id required (runnable); unknown
`inputs` ref; duplicate `inputs` entry; self-loop; cycle; `inputs: []` root;
calc fan-in on the declared graph; disabled basic semantics; `checkpoint` only on
`calc`; `checkpoint.from_step` exists; **strict ancestor** via `inputs`;
extension recognition (registry); strict params; fragment profile relaxations.

**Checkpoint ancestry algorithm** — lives in the **validation layer**, using an
IR graph helper (`ancestors(definition, step_id)`); **never** in a step handler
(RFC §11). Reachability over the `inputs` edges (transitive closure).

**Execution order** — derived at IR construction (as today) with the V3 sort key:
`(0,int(suffix))` for `^s[0-9]+$`, else `(1,id)` (RFC §9). Stored on the IR, same
as V2 (so `plan`/engine code keeps one shape).

**Diagnostics.** Stable codes, e.g. `workflow.v3.duplicate_id`,
`workflow.v3.invalid_id`, `workflow.v3.unknown_input`,
`workflow.v3.duplicate_input`, `workflow.v3.self_loop`,
`workflow.v3.dependency_cycle`, `workflow.v3.checkpoint_target_not_calc`,
`workflow.v3.checkpoint_not_ancestor`, `workflow.calc_fan_in` (reused),
`workflow.v3.params_unknown_key`, `workflow.v3.extension_unknown`.

**R6 boundary (explicit).** R3.4 validates the **declared** graph. The
**effective-dataflow** cardinality after disabled bypass (RFC §12) is **R6**.
R3.4 must not build a typed artifact/dataflow system.

**Files.** modify `canonical/validation.py`, `canonical/workflow.py` (graph helper).

**Tests first.** unknown predecessor; cycle; self-loop; checkpoint non-ancestor;
checkpoint wrong step type; calc fan-in; disabled; root; duplicate inputs;
extension registry; strict params; runnable id required; fragment allows missing id.

**Stop gate.** If the V3 validator and the V3 schema can accept/reject the same
document differently (beyond known profile relaxations) → STOP.

**Commit.** `feat(config): validate Workflow V3 graph references`

---

### R3.5 — CLI-facing integration

**Goal.** Let a user inspect a V3 document. **No execution, no state change.**

- **`config validate`** — version-aware internally; **v1 wire shape unchanged**;
  V3 diagnostics projected to `{path,message}`. `config contract` untouched.
- **`config-show`** (`workflow/config_show.py`) — for V3, display `id`, `label`,
  `type`, `inputs`, `checkpoint`, and the resolved params; keep V2 output as-is.
  `[clarification]` `--step` for V3 selects by **id only** (labels may duplicate).
- **`dry-run`** (`workflow/dry_run.py`) — for V3, print the resolved graph,
  stable ids, labels, execution order and input relationships (the current code
  assumes a linear chain). V2 output unchanged.
- **`rerun_failed`** — V3 blocked by the R3.2 gate.
- **export** — **no change in R3**; step-id export metadata and `output_manifest.v2`
  are R4 (state v2). Explicitly deferred.
- **contract** — unchanged; V3 is **not** advertised (R7).

**Files.** modify `workflow/config_show.py`, `workflow/dry_run.py`,
`config/cli.py`, `confflow/cli.py` (dispatch if needed), plus tests.

**Tests first.** `config validate` V2 exact compatibility; V3 validate; config-show
V2 unchanged / V3 id+label+graph; dry-run V2 unchanged / V3 graph display;
`--step <id>` V3; `--step <name|index>` V2 unchanged; `export` unchanged; a V3
run is blocked with the gate message.

**Stop gate.** If a V3 document can bypass the execution gate and write
`.workflow_state.json` (v1) or launch a calc → STOP.

**Commit.** `feat(cli): expose Workflow V3 inspection commands`

---

## 8. Test matrix

### R3.1 — IR
- unit: new fields default to `None`/`{}` for V2; `identity` accessor; v3 order key
- characterization: all R1 differential + V2 characterization unchanged
- property: P1–P6 (in-memory V3 IR)
- golden: V2 fingerprint/plan (unchanged)

### R3.2 — parser / schema
- valid linear / branch / fan-in / checkpoint / disabled
- duplicate label allowed; duplicate id rejected; missing id (runnable) rejected;
  fragment missing id allowed
- unknown root key rejected; unknown step key rejected; unknown param rejected
- recognised extension accepted; unknown extension parse-ok + runnable-error
- annotation accepted; version dispatch table; V2 unaffected
- schema-agrees-with-validator corpus

### R3.3 — upgrade
- implicit linear; explicit DAG; unnamed step; disabled; `task`/`gen`; numeric
  `chk_from_step`; named `chk_from_step`; unknown-params (fail/annotations/extensions);
  unknown step-level → annotations; deterministic twice; V3-input refusal; `>999`
  steps; alias value canonicalisation; upgrade→parse round trip

### R3.4 — validation
- unknown predecessor; cycle; self-loop; checkpoint non-ancestor; checkpoint wrong
  type; calc fan-in; disabled; root; duplicate inputs; extension registry; strict
  params; runnable-id-required; fragment relaxation

### R3.5 — CLI
- `config validate` V2 exact; V3 validate; config-show V2/V3; dry-run V2/V3;
  `--step` V2 (name/index) vs V3 (id); export unchanged; V3 run blocked

### Property / metamorphic (P1–P8)
| # | Property | Slice |
|---|---|---|
| P1 | reorder V3 steps array → **same** definition fingerprint | R3.1 |
| P2 | rename label → **same** definition fingerprint | R3.1 |
| P3 | change id → **different** definition fingerprint | R3.1 |
| P4 | change an edge → **different** definition fingerprint | R3.1 |
| P5 | change a semantic extension → **different** definition fingerprint | R3.1 |
| P6 | change an annotation → **same** definition fingerprint | R3.1 |
| P7 | upgrade twice from the same V2 source → same V3 document | R3.3 |
| P8 | instantiate the same recipe twice → unique id sets | **out of R3** (application layer) |

P8 rationale: no instantiate code exists in the repo (§1); the RFC §19.3 algorithm
is specified, and its first consumer is an application verb. R3 does not
implement it; the plan marks it for the application layer, tested there.

### Migration golden fixtures
`tests/fixtures/workflow_v3/` with paired V2 input and expected V3:
`upgrade-linear`, `upgrade-dag`, `upgrade-numeric-checkpoint`, plus V3-only
`simple-linear`, `branch`, `checkpoint`, `disabled`, `partial-recipe`.
Golden compares **parsed structure** (not YAML whitespace), plus one serializer
determinism test — no over-binding to formatting.

---

## 9. File change matrix

| File | R3.1 | R3.2 | R3.3 | R3.4 | R3.5 | Reason |
|---|---|---|---|---|---|---|
| `config/canonical/workflow.py` | modify | no-touch | no-touch | modify | no-touch | IR fields, `identity`, v3 order key, `ancestors()` |
| `config/canonical/v2_adapter.py` | modify | no-touch | no-touch | no-touch | no-touch | populate `label`; keep `id/checkpoint_from=None` |
| `config/canonical/parser.py` | no-touch | modify | no-touch | no-touch | no-touch | `detect_schema_version`, dispatch |
| `config/canonical/v3_parser.py` | — | **add** | no-touch | no-touch | no-touch | V3 document → IR |
| `config/canonical/schema.py` | no-touch | modify | no-touch | no-touch | no-touch | V3 schema getters (V2 untouched) |
| `config/canonical/param_fields.py` | no-touch | **add** | no-touch | no-touch | no-touch | single key registry |
| `config/canonical/extensions.py` | no-touch | **add** | no-touch | modify | no-touch | registry + recognition |
| `config/canonical/execution_gate.py` | no-touch | **add** | no-touch | no-touch | no-touch | R3/R4 fail-closed gate |
| `config/canonical/yaml_io.py` | no-touch | **add** | modify | no-touch | no-touch | human YAML serializer |
| `config/canonical/validation.py` | no-touch | modify | no-touch | modify | no-touch | profiles, strict params, graph refs |
| `config/canonical/fingerprint.py` | modify | modify | no-touch | no-touch | no-touch | v3 definition fingerprint; import registry; V2 keys from registry |
| `config/canonical/upgrade.py` | — | — | **add** | no-touch | no-touch | pure V2→V3 |
| `config/canonical/__init__.py` | modify | modify | modify | modify | modify | exports |
| `config/workflow_cli.py` | — | — | **add** | no-touch | no-touch | `workflow upgrade` |
| `config/cli.py` | no-touch | no-touch | no-touch | no-touch | modify | version-aware validate |
| `cli.py` | no-touch | no-touch | modify | no-touch | modify | `workflow` dispatch |
| `workflow/plan.py` | no-touch | modify | no-touch | no-touch | no-touch | gate call |
| `workflow/rerun_failed.py` | no-touch | modify | no-touch | no-touch | no-touch | gate call |
| `workflow/config_show.py` | no-touch | no-touch | no-touch | no-touch | modify | V3 display/select |
| `workflow/dry_run.py` | no-touch | no-touch | no-touch | no-touch | modify | V3 graph display |
| `workflow/export.py` | no-touch | no-touch | no-touch | no-touch | no-touch | deferred to R4 |
| `workflow/state.py`, `canonical/contract.py` | no-touch | no-touch | no-touch | no-touch | no-touch | v1/v2 unchanged (R4) |
| `calc/*`, `blocks/*`, `application/*`, `control*` | no-touch | no-touch | no-touch | no-touch | no-touch | protected core |

---

## 10. Commit plan

| Slice | Commit |
|---|---|
| R3.1 | `feat(config): extend canonical workflow IR for V3 identity` |
| R3.2 | `feat(config): add Workflow V3 parser and schema` |
| R3.3 | `feat(config): add deterministic V2 to V3 upgrade` |
| R3.4 | `feat(config): validate Workflow V3 graph references` |
| R3.5 | `feat(cli): expose Workflow V3 inspection commands` |

Each commit: tests green, reviewable, no dependency on later uncommitted code.
No mega-commit; no push/tag/merge in this plan.

---

## 11. Risk register

| Sev | Risk | Mitigation / test |
|---|---|---|
| **P0** | V2 fingerprint / binding / dirname regression | R3.1 stop gate; golden fingerprint tests; V2 characterization must stay green |
| **P0** | V3 accidentally executed on v1 state identity | R3.2 execution gate at `build_workflow_plan`/`rerun_failed`; R3.5 stop gate; explicit test that V3 cannot write `.workflow_state.json` |
| **P0** | schema / validator truth drift | single `param_fields` registry; `test_v3_schema_agrees_with_validator`; R3.2 stop gate |
| **P0** | checkpoint migration corruption | R3.3 migrates via the exact V2 resolution (name/index); golden `upgrade-numeric-checkpoint`; ancestry validation in R3.4 |
| **P1** | non-deterministic upgrade | byte-determinism test (twice); R3.3 stop gate |
| **P1** | id collision | opaque + collision check; `test_allocator_collision_retries` (application allocator) |
| **P1** | extension registry drift | one registry module; unrecognised → runnable ERROR test |
| **P1** | CLI version ambiguity | dispatch table test; unknown `schema` → error test |
| **P2** | formatting / order churn | structural golden (not whitespace); serializer determinism test |
| **P2** | label presentation | label excluded from fingerprint (P2) |
| **P2** | docs/examples drift | RFC examples validated in CI-adjacent test |

---

## 12. Protected core

Unchanged during R3 (any change requires a separate review): calc execution,
Gaussian/ORCA policy, `CalcArtifactManager`, ConfGen generation algorithm,
`ExecutionService`, control protocol, worker supervision, cancel/pause, remote
execution, artifact/path safety, `workflow_state.v1` behaviour,
`workflow_binding.v1` behaviour, `output_manifest`/`workflow_stats` v1.

---

## 13. Configuration-contract-v2 boundary

R3 keeps `configuration-contract.v1`/`.v2`, `workflow_schema_sha256` (v2),
`editor-manifest.v1`, `recipe-catalog.v1` and `configuration-validation.v1`
**unchanged**. Internal V3 support ≠ published V3 support: V3 is not advertised
to JobDesk until **R7**.

---

## 14. R3 completion definition

R3 COMPLETE means: the V3 production schema exists; a V3 parser exists; the
canonical IR represents V3; V3 runnable-definition validation exists;
deterministic V2→V3 upgrade exists; the CLI can validate / show / dry-run a V3
document; V2 behaviour is unchanged; and **V3 execution remains fail-closed**.
R3 COMPLETE ≠ "V3 workflows run calculations" — that is R4.

---

## 15. R4 handoff contract

**R3 provides R4:** stable step ids in the IR; the V3 semantic **definition**
fingerprint; explicit graph semantics + `ancestors()`; structured
`checkpoint.from_step`; a version-aware parser + validated V3 document; the
`execution_gate` constant to flip.

**R4 must add (not R3):** `workflow_binding.v2`; `workflow_state.v2`;
step-id-keyed state records; `steps/<id>/` directories; the **execution**
fingerprint; V3 engine enablement (flip `V3_EXECUTION_SUPPORTED` with real
support); V3 resume semantics; `output_manifest.v2` / compatible export metadata.

---

## 16. Adversarial review of this plan

| Role | Attack | Resolution |
|---|---|---|
| Maintainer | hidden R3→R4 dependency? | gate + handoff §15; R3 ships inspection only |
| Maintainer | circular slice dependency? | R3.1→R3.2→R3.3→R3.4→R3.5 is a DAG; R3.5 depends on R3.2–R3.4; R3.4 depends on R3.1–R3.2 |
| Maintainer | duplicated source of truth? | `param_fields` registry (§4) + schema-agreement test |
| V2 user | "did my run change?" | V2 stop gates + golden fingerprints + characterization |
| CLI user | "can I run V3 by accident?" | explicit gate error; V3 cannot produce v1 state |
| CLI user | "which `--step` value?" | V2 name/index; V3 id only (labels may duplicate) |
| JobDesk | wire change? | none — v1 validation shape frozen; contract untouched |
| Schema author | schema/parser disagreement | single registry + agreement test; R3.2/R3.4 stop gates |
| Extension author | "my namespace is ignored?" | unrecognised = runnable ERROR (fail closed), never silent |
| Resume/state | "R3 writes state for V3?" | gate blocks execution; export/state explicitly deferred to R4 |
| Upgrade user | "did it renumber my file?" | V3 input refused; deterministic; ids assign-once |

No circular dependency, no R3/R4 leak, no hidden runtime enablement, no duplicated
truth, no irreversible upgrade (it writes a new document; the V2 source is untouched).

---

## 17. Out of scope for this plan

V3 execution, `workflow_state.v2`, `workflow_binding.v2`, step-id directories,
structured calculation, recipe *instantiation runtime*, plugin step types, JobDesk
changes, contract v3, remote-protocol changes.
