# ConfFlow Workflow V3 — Implementation Plan

Status: **PLANNING ONLY — not an implementation.** This document plans the R3
slices so they can be implemented, reviewed and rolled back one at a time. No
runtime, parser, schema, IR, CLI, state, binding or engine code is changed by
this plan.

- **Base**: `a1329cc` (`docs(workflow): plan Workflow V3 implementation`) on
  `plan/workflow-v3-implementation`, built on the accepted RFC head `ebea9d9` and
  the accepted chain `61e11b4` / `c649f59` / `014e185`.
- **Authoritative design**: `docs/rfc/WORKFLOW_V3_RFC.md` (accepted) +
  `docs/rfc/workflow-v3.schema.draft.json` (draft). This plan **does not change
  the RFC's semantics**; it only fixes implementation boundaries and marks
  clarifications `[clarification]`.
- **Review round**: this revision resolves the final implementation-review finding —
  the V3 execution capability guard in `application/execution/workflow_adapter.py`
  is **mandatory**, not optional (plus acceptance tests). It builds on the earlier
  revision that resolved (1) execution gate placement, (2) schema profiles,
  (3) param descriptor authority, (4) V3 validation-response schema hash, and the
  fingerprint slice ordering.

> **R3 makes V3 inspectable, not executable.** V3 can be parsed, schema-validated,
> semantically validated, canonicalised, **planned**, `config-show`n, `dry-run`,
> and upgraded from V2. **Running**, **resuming** and **rerun-failed** stay
> fail-closed until R4 provides state/binding v2 (§3, §14).

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
| **version dispatch** | none — `parse_workflow_mapping` ignores a `schema` key entirely; the V2 JSON schema has **no** `schema` property and `additionalProperties: true`, so historical V2 files carry no version field | `detect_schema_version()` + dispatch + "never treat unknown as V2" | R3.2 |
| **schema profiles** | none — one implicit V2 schema; no notion of a partial document | `SchemaProfile.{DOCUMENT,FRAGMENT}` sharing one `$defs` source | R3.2 |
| **execution capability** | V2-only *de facto*: `WorkflowConfig`/`WorkflowPlan`/`WorkflowStateStore` assume V2 identity; nothing declares which versions may execute | version→capability table + **mandatory** check at every execution side-effect boundary, including the service adapter | R3.2 (module) / R3.5 (wiring) |
| **param descriptor authority** | known keys are scattered: `_known_calc_keys()` in `fingerprint.py` (includes `chk_from_step`), `confgen_known_keys()` in `shared/confgen_params.py`; the V2 JSON schema lists nothing | one structural descriptor registry from which schema/validation derive | R3.2 |
| **checkpoint reference** | `params.chk_from_step` (name or 1-based index) in `step_handlers._resolve_chk_input_dir` | promote to `checkpoint.from_step`; migrate; validate ancestry | R3.2 / R3.3 / R3.4 |
| **strict params** | V2 = open bag | V3 core key set from the descriptor registry; unknown = ERROR | R3.2 / R3.4 |
| **extensions** | V2 unknown *step-level* fields → IR `extensions` (carried, currently unused downstream) | namespaced `ExtensionRegistry` + recognition gate | R3.2 / R3.4 |
| **annotations** | none | IR `annotations`; parser; serializer; never fingerprinted | R3.1 / R3.2 |
| **fragment vs runnable** | R2 split exists (`parse_workflow_mapping` vs `validate_workflow_definition`) | structural profiles + validation profiles, paired | R3.2 |
| **V3 validation response hash** | `config/cli.py` always emits `workflow_schema_sha256()` (the V2 digest) | version-aware digest = the schema that validated *this* document | R3.5 |
| **definition fingerprint** | `canonical_workflow_payload` / `workflow_fingerprint(plan)` (V2, binding v1) | V3 definition fingerprint as a pure function, frozen only after the semantic registry exists | R3.4 |
| **recipe identity/rebase** | `recipes.py` is a catalog only; **no instantiate code exists anywhere** | RFC §19.3 algorithm; implementation is an application-layer verb — **out of R3** | post-R3 |
| **CLI step selector** | `_select_step` by name/1-based index (`config_show.py`, `rerun_failed.py`) | V3 id selector | R3.5 |
| **config-show** | name/index, no graph | V3 id/label/inputs/checkpoint display | R3.5 |
| **dry-run** | assumes a single linear chain (`current_input = output_path`), no DAG | V3 graph display via a V3 plan | R3.5 |
| **export** | keyed by dirname/step_name; `output_manifest.v1` | **no R3 change** (needs state v2) | R4 |

**Four facts that shape the plan**

1. `_known_calc_keys()` (`fingerprint.py`) includes `chk_from_step` and V2 binds it;
   V3 must **exclude** it from `params` and promote it to `checkpoint`. The registry
   keeps the V2 set verbatim and derives the V3 set (§4).
2. Nothing dispatches on a document version: a V3 file handed to the engine today
   has its `schema` key silently ignored and is read as V2. Both the dispatch (R3.2)
   and the execution capability check (§3) exist to prevent that.
3. `initialize_runtime_context` (`workflow/runtime_context.py`) already creates the
   work/failed dirs, copies the config, attaches a log handler and calls
   `failure_tracker.clear_previous()` — the first side effect *inside the engine*.
   The execution check must fire **before** it (§3).
4. **The engine is not the first side effect on the application path.**
   `run_workflow_through_service` (`application/execution/workflow_adapter.py`)
   calls `build_workflow_service` *before* it ever reaches the engine, and that
   constructor performs real service-side side effects:
   `_ensure_state_root` does `root.mkdir(parents=True, exist_ok=True)` and
   `os.chmod`, `StateRoot.ensure_run_paths` creates `v1/runs/<run_id>/{staging,work}`,
   and `SQLiteExecutionRepository(root)` opens/creates the durable DB — all before
   `service.prepare` and long before `engine.run_workflow` (verified: lines
   396/398/400/454/460 of the adapter). An engine-only guard is therefore
   **insufficient**: a V3 attempt would already have created a state root, run
   paths and a SQLite record before the engine could reject it. The adapter needs a
   **mandatory** guard before `build_workflow_service` (§3.3).

---

## 2. Version dispatch

`[clarification]` — the RFC says "dispatch by `schema`"; the exact table:

| `schema` value in the document | Version |
|---|---|
| absent | **V2** (historical files carry no version field; verified against `schema.py`) |
| `confflow.workflow.v2` | **V2** (explicit) |
| `confflow.workflow.v3` | **V3** |
| any other non-empty value | **error** (`workflow.schema.unsupported`) — never guessed |

- `detect_schema_version(raw) -> str` lives in `parser.py`.
- The V2 path (`parse_workflow_mapping`, `WorkflowConfig.from_mapping`,
  `load_workflow_model`) is **unchanged**; V2 tests and fingerprints are the proof.
- Parse support and execution support are **different sets** (§3); a version may be
  parseable but not executable.

---

## 3. Execution capability and the R3/R4 boundary

### 3.1 Principle

`build_workflow_plan` is a **planning** boundary, not an execution
side-effect boundary. V3 must be planable (dry-run needs the graph, resolved
execution order, step inputs and resolved config). The check therefore fires at
every **execution** side effect, never in the planner, and there is more than one
such boundary — the CLI, the application service adapter and the engine itself.

```
Inspection path (ALLOWED in R3)
    V3 document → parse → validate → build WorkflowV3Plan → config-show / dry-run

CLI execution path (BLOCKED)
    V3 args → CLI require_executable ─✗→ [validate_managed_path / lease / makedirs never reached]

Service/API execution path (BLOCKED)
    V3 config → detect version → require_executable ─✗→ [build_workflow_service never reached]
                                                          (no state root, no run paths, no SQLite)

Direct engine path (BLOCKED)
    V3 config → build_workflow_plan (OK) → require_executable ─✗→ [binding / resume validation /
                                                                  initialize_runtime_context /
                                                                  state / step dirs / launch never reached]
```

The three blocking checks must all call the **same** `require_executable` (§3.2);
none may hardcode a version decision.

### 3.2 Version capability model (not a bool, not an env var)

`confflow/config/canonical/execution_versions.py` (new, R3.2):

```python
@dataclass(frozen=True)
class VersionCapability:
    parse: bool
    execute: bool

CAPABILITIES: dict[str, VersionCapability] = {
    "confflow.workflow.v2": VersionCapability(parse=True, execute=True),
    "confflow.workflow.v3": VersionCapability(parse=True, execute=False),  # R4 flips `execute`
}

def can_execute(schema_version: str) -> bool: ...
def require_executable(schema_version: str) -> None:   # raises ConfFlowError with a clear message
```

- One table, extensible to a future `v4` by adding an entry;
  parse-support and execution-support are separate fields.
- `require_executable` raises `ConfFlowError(
  "Workflow <version> execution requires state/binding v2 (R4); it can be parsed,
  validated, planned and inspected, but not run.")`.
- **Exactly one source of truth.** All three guard sites — CLI, service adapter,
  engine — call `require_executable(version)`; none of them inspects `schema`
  itself or special-cases `v3`. R4 enables V3 execution by flipping the `execute`
  flag in `CAPABILITIES`; no temporary `if version == "v3"` is ever removed.

### 3.3 Gate locations (real functions)

All three call `require_executable`; the adapter and engine guards are
**mandatory**, the CLI guard is a fail-fast pre-flight.

1. **Service adapter — MANDATORY (A).**
   `confflow/application/execution/workflow_adapter.py::run_workflow_through_service`
   — the check runs **before** `build_workflow_service(spec, …)` (adapter line 454),
   i.e. before `_ensure_state_root` (line 396, `root.mkdir`), before
   `StateRoot.ensure_run_paths` (line 398, creates `v1/runs/<run_id>/{staging,work}`),
   before `SQLiteExecutionRepository(root)` (line 400), before `service.prepare`
   (line 460), and before every lifecycle/work/control-beacon mutation. This is the
   mandatory safety boundary for the application/service execution path: a V3
   attempt leaves **no** state root, **no** run paths, **no** SQLite DB, **no**
   service record and never invokes the runner.
2. **Engine — MANDATORY (B).** `confflow/workflow/engine.py::run_workflow` —
   immediately after `plan = build_workflow_plan(...)` (line ~169) and **before**
   `build_workflow_binding(plan)`, the resume pre-validation (`_validate_state_*`,
   `_validate_resume_artifacts`) and `initialize_runtime_context`. This protects
   direct engine callers, tests and future integrations that bypass the CLI and the
   service adapter.
3. **CLI — fail-fast (C).** `confflow/cli.py::main`, execution branch — before
   `validate_managed_path(work_dir)` (line 729), `acquire_work_directory_lease`
   (line 731) and `os.makedirs(work_dir)` (line 742), so no work-dir / lease /
   managed-path mutation happens for a V3 run. Defense-in-depth / UX, not the
   authority.
4. **rerun_failed — fail-fast (D).**
   `confflow/workflow/rerun_failed.py::run_rerun_failed` — at entry, before
   `validate_managed_path` (line 115) and the `CalcStepRunner().run(...)` launch
   (line 143). Fail-fast UX only; the authoritative protection is (A)+(B).

**Why (A) is mandatory, not optional.** `build_workflow_service` is a side-effecting
constructor (§1 fact 4). With only (B), a V3 run through the service would create a
state root, run paths and a SQLite execution record before the engine rejects it —
violating "no state before R4". (A) is therefore a required, minimal,
**protected-core** change whose sole purpose is to fail closed *before* service-side
state mutation.

### 3.4 Version discovery (side-effect free)

The adapter must learn the version **without** building any service/state resource.
It reads the config document and dispatches on the `schema` key via the single R3.2
entry point (`detect_schema_version` in `config/canonical/parser.py`, §2) — never a
second `if config["schema"] == ...`. Order is:

1. read the config file (pure I/O, no state);
2. `detect_schema_version(raw)`;
3. an unreadable/invalid/unknown `schema` → error **here**, so a bad document
   cannot pay the cost of creating a state root / SQLite DB / run directory merely
   to fail;
4. `require_executable(version)`;
5. only then `build_workflow_service`.

The same side-effect-free detection feeds the CLI pre-flight (C) and the engine
guard (B, via the plan's `source_version`).

### 3.5 Planner behaviour (V3)

- `build_workflow_plan(...)` becomes **version-dispatching** and returns:
  - **V2** → today's `WorkflowPlan`, byte-for-byte identical;
  - **V3** → a new planning-only `WorkflowV3Plan` (`definition: CanonicalWorkflowDefinition`,
    `input_files`, `original_inputs`, `source_version="v3"`).
- The V3 plan does **not** compute V2 dirnames (`step_dirnames`/`name_to_dirname`
  are R4's id-based dirs) and does **not** call the V2 binding.
- `WorkflowPlan` (V2) is **not** widened to `Optional` fields; the union keeps the
  V2 type exact.
- `[clarification]` both dataclasses carry `source_version`; `require_executable(plan.source_version)`
  is the single guard used by the engine (B) and the service adapter (A).

### 3.6 Behaviour summary

| Operation | V2 | V3 in R3 |
|---|---|---|
| `build_workflow_plan` | works | **works** (returns `WorkflowV3Plan`) |
| `config-show` | works | works |
| `dry-run` | works | **works** (graph, ids, labels, execution order) |
| `run_workflow_through_service` | works | **blocked** by (A) before state root / run paths / SQLite / `service.prepare` / runner |
| `run_workflow` (direct) | works | **blocked** by (B) before binding/runtime/state/dirs/launch |
| `resume` | works | **blocked** before any state read/mutation |
| `rerun-failed` | works | **blocked** before mutation/launch |

---

## 4. Production schema authority — param descriptor registry

`[clarification]` — the RFC (§13) anticipates this; a key set is **not** enough
(it cannot express type, enum, shape, required, version membership, aliases).

`confflow/config/canonical/param_fields.py` (new, R3.2) owns **structural** field
descriptors:

```python
@dataclass(frozen=True)
class ParamFieldDescriptor:
    key: str
    value_kind: Literal["string", "integer", "number", "boolean", "array", "object", "any"]
    enum_source: tuple[str, ...] | None = None   # e.g. ProgramName / TaskName values
    required: bool = False
    v2: bool = True          # present in the V2 parameter vocabulary
    v3: bool = True          # present in the V3 runnable core vocabulary
    aliases: tuple[str, ...] = ()

CALC_PARAM_FIELDS: tuple[ParamFieldDescriptor, ...]
CONFGEN_PARAM_FIELDS: tuple[ParamFieldDescriptor, ...]

def v2_calc_keys() -> frozenset[str]      # == the current _known_calc_keys() (includes chk_from_step)
def v3_calc_keys() -> frozenset[str]      # v2 set minus chk_from_step
def confgen_keys() -> frozenset[str]      # == confgen_known_keys()
def schema_properties(kind: str, profile: SchemaProfile) -> dict[str, Any]
```

**What the registry owns (structural truth):** field names, basic type, enum,
structural shape, version membership (`v2`/`v3`), aliases, and `required` within
a step type.

**What the resolver owns (semantic truth — unchanged):** coercion, defaults,
cross-field rules, TS-specific behaviour, confgen runnable invariants
(non-empty chains, positive `angle_step`), alias-conflict semantics. These stay in
`CalcStepParams.from_params()` and `resolve_confgen_params()`.

**Layering (no duplicate truth):**

```
Param descriptor registry ── structural ──▶ JSON Schema (properties + key set)
                          └─ structural ──▶ validator structural check
Resolver (typed)          ── semantic   ──▶ validator semantic check
Validator = structural (registry) + semantic (resolver)
Schema    = structural (registry)
Future editor manifest (R7) = registry + presentation metadata
```

**Derived consumers:** `fingerprint._known_calc_keys()` becomes
`return v2_calc_keys()` (byte-identical); the V3 schema's `params` is generated with
`additionalProperties: false` and the V3 key set; the validator's strict-params
check uses `v3_calc_keys()`/`confgen_keys()`; R7's manifest extends the same
descriptors.

**V2 compatibility is not tightened.** The descriptors only *describe* V2 keys;
V2 parsing/execution keep their open-bag tolerance. Only V3 makes unknown core
params an ERROR.

---

## 5. Schema profiles (DOCUMENT vs FRAGMENT)

`[clarification]` — a single `ValidationProfile` is insufficient: a runnable
schema requiring `id` would reject a recipe fragment before the validator ever
sees it. Two **structural** profiles are therefore required.

### 5.1 Profiles

| Profile | Used by | `id` | `inputs` | User-required params (`chains`, `keyword`) |
|---|---|---|---|---|
| `SchemaProfile.DOCUMENT` | a complete, runnable Workflow V3 document | **required** | **required** | must be complete (runnable) |
| `SchemaProfile.FRAGMENT` | recipe / template / partial starter | optional | required | may be absent |

### 5.2 One schema source, two profiles

```python
def workflow_v3_schema(profile: SchemaProfile = SchemaProfile.DOCUMENT) -> dict[str, Any]
```

Both profiles are assembled from the **same** `$defs` (`stepBase`, `params`
from the descriptor registry, `checkpoint`, `extensions`, `annotations`); the
DOCUMENT profile differs **only** in `required` (`id`, `type`, `inputs`) and
profile-specific constraints. No second full schema dict is written.

### 5.3 Profile pairing

`SchemaProfile.DOCUMENT` ↔ `ValidationProfile.RUNNABLE`;
`SchemaProfile.FRAGMENT` ↔ `ValidationProfile.FRAGMENT`. Cross pairings
(document schema + fragment validator, fragment schema + runnable validator) are
**not** produced by any code path; a test asserts the pairing.

### 5.4 Digest semantics

- `workflow_schema_sha256_v3()` = the **DOCUMENT** schema digest — the official
  Workflow V3 document contract digest (used by §6 and, later, R7).
- The **fragment** schema digest is **internal only**
  (`workflow_fragment_schema_sha256_v3()`, not published, not exported). R7 decides
  whether a fragment contract is ever published.
- The published `confflow.workflow.v2` schema and `workflow_schema_sha256()` are
  untouched.

---

## 6. V3 validation-response schema hash

`[clarification]` — the field means *"the schema this document was validated
against"*, and the wire shape does not change.

**Checked contract facts** (`config/cli.py`, `docs/configuration-contract-v2.md`,
JobDesk `remote_validation.py`): `_validate_stdin` emits exactly
`{schema, valid, workflow_schema_sha256, issues:[{path,message}]}`; the field's
documented meaning is "the digest is checked against the contract's, so an answer
cannot be silently bound to a different schema"; the JobDesk consumer requires
`payload["workflow_schema_sha256"] == contract.workflow_schema_sha256`.

**Rule (R3.5):** `workflow_schema_sha256` = the digest of the schema that validated
**this** document:

| Document | `workflow_schema_sha256` |
|---|---|
| V2 | `workflow_schema_sha256()` (the V2 digest — **unchanged**) |
| V3 | `workflow_schema_sha256_v3()` (the V3 **DOCUMENT** digest) |

`config validate` validates a V3 document against `SchemaProfile.DOCUMENT` +
`ValidationProfile.RUNNABLE` (a fragment is *not* runnable and is rejected, exactly
as a recipe is today). The wire shape stays `confflow.configuration-validation.v1`.

**JobDesk boundary:** the current consumer is contract-bound to the V2 digest, so a
V3 answer fails its `digest != expected` check with
`ProducerValidationError("…validated against a different schema…")` — a **safe
refusal**, never a false accept. Since contract v2 advertises V2 only, JobDesk
sends V2 documents and is unaffected. Publishing V3 support is R7.

---

## 7. Error / diagnostic compatibility

- Reuse R2's `Diagnostic(code, severity, path, message, step_ref)`
  (`canonical/diagnostics.py`). For V3, `step_ref` is the **stable id**.
- The v1 CLI wire shape is **unchanged** (no `code`/`severity`/`step_ref` on the
  wire). V3 diagnostics are projected through the same `to_v1_issue()`.

---

## 8. Serialization strategy

| Serializer | Purpose | Order policy |
|---|---|---|
| **Human YAML** (`canonical/yaml_io.py`, new) | write documents (upgrade output, future save) | `sort_keys=False`, **author step order preserved**, fixed step key order (`id,label,type,enabled,inputs,params,checkpoint,extensions,annotations`) |
| **Canonical JSON** (`serialization.canonical_json`, existing) | fingerprints and contract digests | `sort_keys=True`; steps sorted by the V3 order key |

- The fingerprint canonicaliser never rewrites the user's YAML; the YAML writer
  never sorts.
- Comments are **not** preserved (no round-trip loader); upgrade output is a fresh,
  comment-free document.
- Determinism: `dump(load(dump(doc))) == dump(doc)` byte-for-byte, and upgrade twice
  from the same V2 source byte-for-byte.

---

## 9. R3 slices

### R3.1 — V3-ready Canonical IR (pure IR only)

**Goal.** The IR can represent a V3 workflow; the V3 parser does not exist yet.

**Scope (narrowed this round):** IR fields + the V2 adapter projection only. **No**
schema, parser, execution gate, or fingerprint freeze.

**Model changes** (`[clarification]` on defaults):

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

@dataclass(frozen=True)
class CanonicalWorkflowDefinition:
    ...                             # existing fields unchanged
    source_version: Literal["v2", "v3"] = "v2"   # NEW
    annotations: dict[str, Any] = field(default_factory=dict)  # NEW
```

- **No `identity_origin` enum**: `source_version` + the derived `identity` accessor
  is sufficient (one fewer way to describe the same fact).
- V2: `id`/`checkpoint_from` stay `None`; `params.chk_from_step` stays verbatim;
  `label` is populated from the typed name (stripped; empty → generated graph name);
  `to_v2_execution_shape()` unchanged.
- `extensions`/`annotations` are carried but not consumed by the V2 pipeline
  (verified: nothing reads `.extensions` today).

**Optional (structural, not frozen):** a private `build_semantic_payload(definition)`
helper that *lists* the semantic fields — a primitive only. **The V3 definition
fingerprint is not implemented or frozen here** (§12).

**Files:** `canonical/workflow.py`, `canonical/v2_adapter.py`.

**Tests first:** R1 differential + V2 characterization unchanged; IR-default tests;
`identity` accessor; golden V2 fingerprint.

**Acceptance:** no new field is visible in any V2 projection.

**Commit:** `feat(config): extend canonical workflow IR for V3 identity`

**Stop gate:** any V2 fingerprint / plan / binding / dirname / state change → STOP.

---

### R3.2 — `workflow.v3` parser + schema profiles + param descriptors

**Goal.** Parse a V3 document into the IR; publish a generated V3 schema with two
profiles; establish the descriptor registry; define the execution-capability
module. **No planner gate.**

**Version dispatch.** `detect_schema_version` (§2) + `load_workflow_definition(config_file)`
dispatching V2/V3 → `CanonicalWorkflowDefinition`. `load_workflow_model` stays V2-only.

**Profiles.** `SchemaProfile.{DOCUMENT,FRAGMENT}` (§5) and
`ValidationProfile.{FRAGMENT,RUNNABLE}`; `parse_v3_document(raw, profile=FRAGMENT)`
(structural) and `validate_workflow_definition(raw, profile=RUNNABLE)` (semantic;
backward-compatible default).

**Production schema** (`schema.py` additions; V2 untouched):
- `WORKFLOW_SCHEMA_VERSION_V3 = "confflow.workflow.v3"`, `workflow_v3_schema(profile)`,
  `workflow_schema_sha256_v3()` (DOCUMENT digest), internal
  `workflow_fragment_schema_sha256_v3()`.
- closed root `{schema, global, steps, extensions, annotations}`; closed step
  `{id,label,type,enabled,inputs,params,checkpoint,extensions,annotations}`;
  DOCUMENT `required:[id,type,inputs]`; `type∈{calc,confgen}`;
  `params` generated from the descriptor registry (`additionalProperties:false`);
  `checkpoint` closed; `extensions` propertyNames = namespaced regex.

**Param descriptors.** `canonical/param_fields.py` (§4); `fingerprint._known_calc_keys()`
returns `v2_calc_keys()` (byte-identical).

**Extension registry.** `canonical/extensions.py`, producer-owned, **empty by
default** → unrecognised namespace = parse-preserved, runnable ERROR; no namespace
string-matching in the validator.

**Execution capability.** `canonical/execution_versions.py` (§3.2) — the module
only. Its call sites are R3.5 (§3.3); R3.2 does **not** guard the planner.

**Files:** new `canonical/v3_parser.py`, `canonical/extensions.py`,
`canonical/param_fields.py`, `canonical/execution_versions.py`, `canonical/yaml_io.py`;
modify `canonical/schema.py`, `canonical/parser.py`, `canonical/validation.py`,
`canonical/fingerprint.py`, `canonical/__init__.py`.

**Tests first:** valid linear/branch/fan-in/checkpoint/disabled; document-vs-fragment
schema (missing id: document rejects, fragment accepts); duplicate label allowed;
duplicate id rejected; unknown root/step key rejected; unknown param rejected;
recognised extension accepted; unknown extension parse-ok + runnable-error; annotation
accepted; dispatch table; V2 unaffected; descriptor→schema-properties equality.

**Acceptance:** V3 parses and validates; V2 unchanged; the schema and the validator
share the descriptor registry; planner is **not** gated.

**Stop gate:** two structural field allow-lists (document/fragment schema cannot
share one `$defs`), or a descriptor registry that does not fully drive the schema →
STOP.

**Commit:** `feat(config): add Workflow V3 parser and schema profiles`

---

### R3.3 — Deterministic V2 → V3 upgrade

**Goal.** `confflow workflow upgrade` turns a V2 document into a deterministic V3
document.

**CLI** `[clarification]`:

```
confflow workflow upgrade <input.yaml> [-o out.yaml]
                          [--check] [--unknown-params={fail|annotations|extensions:<ns>}]
```

Dispatch from `confflow/cli.py` (`effective_args[0] == "workflow"`), implemented in
`confflow/config/workflow_cli.py`. Default output stdout; `-o` writes a file;
`--check` reports without writing; V3 input → **refuse**; unknown `schema` → refuse.

**Algorithm** — exactly RFC §14: ids in document order (`s001…`); label = typed V2
name (stripped; empty → generated graph name); type alias-normalised; `enabled`
copied; inputs (explicit-DAG names→ids; implicit-linear chained); `chk_from_step`
(name/index) → `checkpoint.from_step`; params copied; unknown step-level V2 fields →
`annotations`; `schema: confflow.workflow.v3`; array order preserved; present
`iprog`/`itask` alias values canonicalised, absent values stay absent.

**Migration metadata (RFC §14, "Migration metadata mapping").** All migration
metadata uses the one reserved annotation key `confflow.migration.v2`:
unknown **root- and step-level** V2 fields → `annotations.confflow.migration.v2.
unknown_fields`; `--unknown-params=annotations` → `annotations.confflow.migration.
v2.unknown_params`; the two groups are never mixed and empty groups are omitted.
Unknown V2 fields are **not** promoted to `extensions` (that would make them
semantic). Collision-safe: a source field literally named `annotations` or
`confflow.migration.v2` is preserved inside `unknown_fields`, never merged into the
V3 container.

**Output profile (explicit).** The default upgrade output **must** pass
`SchemaProfile.DOCUMENT` + `ValidationProfile.RUNNABLE`; a partial V3 output is
produced only when the user explicitly asks for a fragment (`--profile fragment`,
R7 may expose it). The default upgrade never emits a partial document.

**Unknown-params routing is explicitly lossy/routing, not semantic-equivalent:**

- default (`fail`): unknown core param → **error**, naming the step and key.
- `--unknown-params=annotations`: the key is demoted to
  `annotations.confflow.migration.v2.unknown_params` (non-semantic); the CLI
  **must** emit a clear warning/report that "this field no longer participates in
  execution semantics" and list each affected step/key.
- `--unknown-params=extensions:<ns>`: the key moves to `extensions[<ns>]` and is
  **not** copied to `annotations`; the namespace must be grammar-valid, and if the
  target producer does not recognise it the document **parses but fails runnable
  validation** — stated in the CLI output.

**Serializer.** `yaml_io` (§8): author order, fixed key order, no comments.

**Files:** new `canonical/upgrade.py` (pure `upgrade_v2_mapping(raw) -> dict`),
`config/workflow_cli.py`; modify `confflow/cli.py`, `canonical/__init__.py`.

**Tests first:** implicit linear; explicit DAG; unnamed; disabled; aliases; numeric +
named `chk_from_step`; unknown-params (fail/annotations/extensions); unknown
step-level → annotations; deterministic twice; V3-input refusal; `>999` steps; alias
value canonicalisation; output passes DOCUMENT+RUNNABLE; the `annotations`/`extensions`
routing warnings are emitted.

**Stop gate:** non-deterministic output, or default output not runnable → STOP.

**Commit:** `feat(config): add deterministic V2 to V3 upgrade`

---

### R3.4 — V3 semantic validation + definition fingerprint freeze

**Goal.** The runnable-definition validator AND the frozen V3 definition fingerprint
(the semantic authority is complete here, so the fingerprint can be frozen safely).

**Checks:** duplicate id; invalid id grammar; id required (runnable); unknown
`inputs` ref; duplicate `inputs`; self-loop; cycle; `inputs: []` root; calc fan-in;
disabled basic semantics; `checkpoint` only on `calc`; `checkpoint.from_step` exists;
**strict ancestor** via `inputs`; extension recognition; strict params; fragment
profile relaxations.

**Checkpoint ancestry** lives in the **validation layer** (IR `ancestors()` helper),
never in a step handler.

**Disabled steps — profile-aware semantic params (RFC §12/§16.A, frozen).**
Required-presence checks that RFC §12 exempts (`confgen.chains`, `calc.keyword`,
calc declared fan-in, checkpoint resolution) apply only to **enabled** steps;
supplied values and non-exempt errors are still validated on disabled steps, and
graph/extension checks apply to every step. The definition fingerprint's per-step
`params` are therefore **profile-aware canonical semantic params**: an absent
exempt field is **omitted** (never synthesised — no sentinel/placeholder), a
supplied one is validated, canonicalised and included. Invariant: any
RUNNABLE-valid definition **must** fingerprint successfully. Canonicalisation and
enabled-only presence checks are separated, so no dummy-keyword work-around is
used.

**Confirmed freeze points (implement exactly):**
- `inputs` is a relation → the payload sorts each step's predecessors by the V3 id
  order key (duplicate entries are still a definition error, not silently deduped).
- Extension recognition applies to **all** steps (disabled included); FRAGMENT
  preserves an unknown-but-valid namespace.
- `checkpoint` declaration/type legality (`calc` only) applies to **all** steps; the
  resolution checks (target exists / not self / strict ancestor) apply only to
  **enabled calc** steps.
- Declared calc fan-in applies to **enabled calc** only; effective/transitive
  cardinality after bypass stays R6.

**Execution order** — derived at IR construction with the V3 order key
(`(0,int(suffix))` for `^s[0-9]+$`, else `(1,id)`), stored on the IR (as V2 does).

**Definition fingerprint (frozen here).** `workflow_definition_fingerprint_v3(definition)`
implementing RFC §16.A: `semantics_version` + scientific global + per-step
`{id,type,enabled,resolved params,inputs,checkpoint_from,extensions}`, steps sorted by
the V3 order key; excludes label/annotations/order/schema digest/source version. It
is **not** wired into any binding (that is R4's `workflow_binding.v2`).

**Slice-order rationale.** The fingerprint enumerates the semantic vocabulary
(strict params from the descriptor registry + extension recognition + checkpoint
semantics). Freezing it before R3.2/R3.4 would freeze a payload whose field set is
not yet authoritative. It could go at the end of R3.2, but the ancestry/disabled
semantics land in R3.4, so R3.4 is the first point where the payload is complete.

**Diagnostics:** stable codes (`workflow.v3.duplicate_id`, `workflow.v3.invalid_id`,
`workflow.v3.unknown_input`, `workflow.v3.duplicate_input`, `workflow.v3.self_loop`,
`workflow.v3.dependency_cycle`, `workflow.v3.checkpoint_target_not_calc`,
`workflow.v3.checkpoint_not_ancestor`, `workflow.calc_fan_in` (reused),
`workflow.v3.params_unknown_key`, `workflow.v3.extension_unknown`).

**R6 boundary.** R3.4 validates the **declared** graph; **effective-dataflow**
cardinality after disabled bypass is R6. R3.4 must not build a typed artifact system.

**Files:** `canonical/validation.py`, `canonical/workflow.py` (ancestors + order key),
`canonical/fingerprint.py` (fingerprint), `canonical/extensions.py`.

**Tests first:** unknown predecessor; cycle; self-loop; checkpoint non-ancestor;
checkpoint wrong type; calc fan-in; disabled; root; duplicate inputs; extension
recognition; strict params; runnable-id-required; fragment relaxation; schema-profile
↔ validation-profile pairing; structural-non-contradiction (§11); **P1–P6**.

**Stop gate:** a shared structural rule where schema and validator contradict → STOP.

**Commit:** `feat(config): validate Workflow V3 semantics`

---

### R3.5 — CLI integration + execution guard wiring

**Goal.** Inspect a V3 document; block V3 execution.

- **`config validate`** — version-aware; schema hash per §6; v1 wire shape unchanged.
- **`config-show`** — V3 shows `id`, `label`, `type`, `inputs`, `checkpoint`, resolved
  params; V2 output unchanged. `[clarification]` `--step` for V3 selects by **id only**.
- **`dry-run`** — V3 builds a `WorkflowV3Plan` and prints the resolved graph, stable
  ids, labels, execution order and input relationships; V2 output unchanged.
- **Execution guard wiring** — `require_executable` called at all four sites in §3.3:
  - **MANDATORY**: the service adapter (`run_workflow_through_service`, before
    `build_workflow_service`) and the engine (`run_workflow`, after planning);
  - **FAIL-FAST**: the CLI execution branch and `rerun_failed`.
  All four call the one `require_executable`; none inspects `schema` itself.
- **export** — **no change** (R4).
- **contract** — unchanged; V3 not advertised (R7).

**Core acceptance tests (the R3/R4 boundary):**

| Call | Expected |
|---|---|
| V3 `build_workflow_plan` | **succeeds** (returns `WorkflowV3Plan`) |
| V3 `dry-run` | **succeeds** |
| V3 `run_workflow_through_service` | fails via (A) **before** `build_workflow_service` — no state root, no run paths, no SQLite, no `service.prepare`, runner **not** called |
| V3 `run_workflow` (direct) | fails via (B) **before** state creation, step-dir creation, artifact cleanup, external launch |
| V3 `resume` | fails **before** any state mutation |
| V3 `rerun-failed` | fails **before** mutation/launch |

Named tests:

- `test_service_adapter_rejects_v3_before_side_effects` — give a valid runnable V3
  config, a **non-existent** temp `state_root`, a temp `work_dir` and a fake
  `workflow_runner`; call `run_workflow_through_service(...)`; assert it raises the
  unsupported-execution-version error **and** that `state_root` still does not exist,
  no SQLite DB file exists, no `v1/runs/<run_id>` paths exist, `work_dir` gained no
  execution dirs, `service.prepare` was not reached and the runner was not called.
- `test_engine_rejects_v3_before_runtime_side_effects` — call `engine.run_workflow`
  directly with a V3 config; assert planning succeeds, (B) fires, and before the
  failure there is **no** binding write, **no** runtime-context init, **no**
  `.workflow_state.json`, **no** step directories, **no** cleanup and **no** launch.
- `test_cli_rejects_v3_execution_before_managed_paths` — CLI `run` on a V3 config
  returns the unsupported-execution-version error and acquires/creates no managed
  runtime resources; `dry-run` on the same config **passes**.
- `test_v2_execution_unchanged` — the four guards are no-ops for V2 (existing
  CLI/engine/service execution tests stay green).

**Files:** modify `workflow/config_show.py`, `workflow/dry_run.py`, `config/cli.py`,
`confflow/cli.py`, `workflow/engine.py`, `workflow/rerun_failed.py`,
`workflow/plan.py` (add the V3 planning branch + `WorkflowV3Plan`);
**`application/execution/workflow_adapter.py` (mandatory minimal protected-core change)**.

**Stop gate:** `build_workflow_plan` blocked for V3, OR V3 dry-run blocked, OR any V3
path that creates a state root / run paths / SQLite record / service record, reaches
`service.prepare`, invokes the runner, writes state / creates step dirs / cleans
artifacts / launches before R4, OR the validation schema-hash semantics contradict
the existing contract, OR V2 execution behaviour changes → STOP.

**Commit:** `feat(cli): expose Workflow V3 inspection commands`

---

## 10. Test matrix

### R3.1 — IR
IR defaults; `identity`; V2 characterization/differential unchanged; golden V2 fingerprint.

### R3.2 — parser / schema / descriptors
- valid linear / branch / fan-in / checkpoint / disabled
- **schema profiles**: document missing id → **schema reject**; fragment missing id →
  **schema accept**; runnable validator on the same fragment → **semantic reject**;
  fragment validator → accept if otherwise valid; profile pairing asserted
- duplicate label allowed; duplicate id rejected; unknown root/step key rejected
- **param descriptors**: descriptor keys ↔ generated schema `properties`; unknown V3
  core param → schema reject **and** validator reject
- **extensions**: valid-namespace-but-unknown → schema accept + parse preserve +
  runnable reject; recognised namespace → schema accept + validator accept
- annotation accepted; dispatch table; V2 unaffected
- **structural non-contradiction** (§11), not full equivalence

### R3.3 — upgrade
implicit linear; explicit DAG; unnamed; disabled; aliases; numeric + named
`chk_from_step`; unknown-params (fail/annotations/extensions) with lossy warnings;
unknown step-level → annotations; deterministic twice; V3-input refusal; `>999` steps;
alias canonicalisation; output passes DOCUMENT+RUNNABLE; round trip.

### R3.4 — validation + fingerprint
unknown predecessor; cycle; self-loop; checkpoint non-ancestor; checkpoint wrong type;
calc fan-in; disabled; root; duplicate inputs; extension registry; strict params;
runnable-id-required; fragment relaxation; **P1–P6**.

### R3.5 — CLI + guard
`config validate` V2 exact; V3 validate + digest per §6; config-show V2/V3; dry-run
V2/V3; `--step` V2 (name/index) vs V3 (id); export unchanged.

Guard-boundary tests (the R3/R4 boundary):

1. V3 `build_workflow_plan` → **PASS**
2. V3 `dry-run` → **PASS**
3. CLI V3 `run` → **BLOCK** before managed paths/lease/makedirs (and V3 dry-run PASS)
4. `run_workflow_through_service` V3 → **BLOCK** before `build_workflow_service`
5. service adapter leaves **no state_root** (pre-existent temp path absent)
6. **no SQLite** DB file
7. **no run paths** / `service.prepare` not reached
8. **runner not called**
9. direct `engine.run_workflow` V3 → **BLOCK** before runtime-context/state/dirs/cleanup/launch
10. V2 execution unchanged (guards are no-ops for V2)

### Property / metamorphic (P1–P8)
| # | Property | Slice |
|---|---|---|
| P1 | reorder V3 steps array → **same** definition fingerprint | R3.4 |
| P2 | rename label → **same** definition fingerprint | R3.4 |
| P3 | change id → **different** definition fingerprint | R3.4 |
| P4 | change an edge → **different** definition fingerprint | R3.4 |
| P5 | change a semantic extension → **different** definition fingerprint | R3.4 |
| P6 | change an annotation → **same** definition fingerprint | R3.4 |
| P7 | upgrade twice from the same V2 source → same V3 document | R3.3 |
| P8 | instantiate the same recipe twice → unique id sets | **out of R3** (application layer) |

### §11 Structural non-contradiction (agreement)
Not "schema acceptance ≡ validator acceptance". Three assertions:

1. A structurally valid core field shape the schema accepts **must not** be rejected
   by the validator for a *structural* reason (no duplicate structural rule).
2. A schema-rejected unknown core param **must** also be validator-rejected.
3. A syntactically valid extension namespace the schema accepts **may** be rejected by
   the runnable validator when the producer's `ExtensionRegistry` does not recognise
   it (semantic knowledge the schema cannot have).

### Migration golden fixtures
`tests/fixtures/workflow_v3/`: `simple-linear`, `branch`, `checkpoint`, `disabled`,
`partial-recipe`, `upgrade-linear`, `upgrade-dag`, `upgrade-numeric-checkpoint`.
Structural golden (not whitespace) + one serializer determinism test.

---

## 12. File change matrix

| File | R3.1 | R3.2 | R3.3 | R3.4 | R3.5 | Reason |
|---|---|---|---|---|---|---|
| `config/canonical/workflow.py` | modify | no-touch | no-touch | modify | no-touch | IR fields, `identity`, order key, `ancestors()` |
| `config/canonical/v2_adapter.py` | modify | no-touch | no-touch | no-touch | no-touch | `label`; keep `id`/`checkpoint_from` `None` |
| `config/canonical/parser.py` | no-touch | modify | no-touch | no-touch | no-touch | `detect_schema_version`, dispatch |
| `config/canonical/v3_parser.py` | — | **add** | no-touch | no-touch | no-touch | V3 → IR |
| `config/canonical/schema.py` | no-touch | modify | no-touch | no-touch | no-touch | V3 schema profiles (V2 untouched) |
| `config/canonical/param_fields.py` | no-touch | **add** | no-touch | no-touch | no-touch | param descriptor registry |
| `config/canonical/extensions.py` | no-touch | **add** | no-touch | modify | no-touch | registry + recognition |
| `config/canonical/execution_versions.py` | no-touch | **add** | no-touch | no-touch | no-touch | version capability table + `require_executable` |
| `config/canonical/yaml_io.py` | no-touch | **add** | modify | no-touch | no-touch | human YAML serializer |
| `config/canonical/validation.py` | no-touch | modify | no-touch | modify | no-touch | profiles, strict params, graph refs |
| `config/canonical/fingerprint.py` | no-touch | modify | no-touch | modify | no-touch | V2 keys from registry; V3 definition fingerprint frozen in R3.4 |
| `config/canonical/upgrade.py` | — | — | **add** | no-touch | no-touch | pure V2→V3 |
| `config/canonical/__init__.py` | modify | modify | modify | modify | modify | exports |
| `config/workflow_cli.py` | — | — | **add** | no-touch | no-touch | `workflow upgrade` |
| `config/cli.py` | no-touch | no-touch | no-touch | no-touch | modify | version-aware validate + digest |
| `cli.py` | no-touch | no-touch | modify | no-touch | modify | `workflow` dispatch; execution pre-flight |
| `workflow/plan.py` | no-touch | no-touch | no-touch | no-touch | modify | V3 planning branch (`WorkflowV3Plan`) |
| `workflow/engine.py` | no-touch | no-touch | no-touch | no-touch | modify | **REQUIRED** `require_executable` after planning (B) |
| `workflow/rerun_failed.py` | no-touch | no-touch | no-touch | no-touch | modify | **REQUIRED** fail-fast pre-flight (D) |
| `workflow/config_show.py` | no-touch | no-touch | no-touch | no-touch | modify | V3 display/select |
| `workflow/dry_run.py` | no-touch | no-touch | no-touch | no-touch | modify | V3 plan display |
| `workflow/export.py` | no-touch | no-touch | no-touch | no-touch | no-touch | deferred to R4 |
| `workflow/state.py`, `canonical/contract.py` | no-touch | no-touch | no-touch | no-touch | no-touch | v1/v2 unchanged (R4) |
| `application/execution/workflow_adapter.py` | no-touch | no-touch | no-touch | no-touch | **modify** | **REQUIRED minimal protected-core change (A)**: `require_executable` before `build_workflow_service` / `_ensure_state_root` / `SQLiteExecutionRepository`. Separate review. |
| `calc/*`, `blocks/*`, `control*` | no-touch | no-touch | no-touch | no-touch | no-touch | protected core |

---

## 13. Commit plan

| Slice | Commit |
|---|---|
| R3.1 | `feat(config): extend canonical workflow IR for V3 identity` |
| R3.2 | `feat(config): add Workflow V3 parser and schema profiles` |
| R3.3 | `feat(config): add deterministic V2 to V3 upgrade` |
| R3.4 | `feat(config): validate Workflow V3 semantics` |
| R3.5 | `feat(cli): expose Workflow V3 inspection commands` |

Each commit: tests green, reviewable, no dependency on later uncommitted code.

---

## 14. Stop gates

| Slice | Stop gate |
|---|---|
| R3.1 | any V2 identity/fingerprint/plan/binding/dirname/state change |
| R3.2 | document/fragment schema cannot share one structural source; param schema and descriptors produce a second field truth |
| R3.3 | upgrade non-deterministic, or default output not runnable |
| R3.4 | a shared structural rule where schema and validator contradict |
| R3.5 | `build_workflow_plan` blocked for V3; V3 dry-run blocked; **V3 service execution creates a state root / SQLite record / run paths before rejection**; `service.prepare` reached; runner invoked; engine reaches runtime-context/state init; any V3 execution path that can write state/artifacts before R4; validation schema-hash semantics contradict the existing contract; V2 execution behaviour changes |

---

## 15. Risk register

| Sev | Risk | Mitigation / test |
|---|---|---|
| **P0** | V2 fingerprint / binding / dirname regression | R3.1 stop gate; golden fingerprints; V2 characterization |
| **P0** | **V3 service adapter creates state root / run paths / SQLite record before the engine-level rejection** | mandatory adapter guard (A) before `build_workflow_service`; `test_service_adapter_rejects_v3_before_side_effects` asserts absent state root, no SQLite, no run paths, `service.prepare` not reached, runner not called; R3.5 stop gate |
| **P0** | V3 executed on v1 state identity | capability check at every real side-effect boundary (§3.3); V3 plan/dry-run-pass + run-blocked tests |
| **P0** | schema / validator truth drift | descriptor registry + structural-non-contradiction tests; R3.2 stop gate |
| **P0** | checkpoint migration corruption | exact V2 resolution + golden fixture + ancestry validation |
| **P1** | non-deterministic upgrade | byte-determinism test; R3.3 stop gate |
| **P1** | fragment rejected by the runnable schema before the fragment profile applies | two schema profiles sharing `$defs` (§5); profile-pairing tests |
| **P1** | id collision | opaque + collision check; allocator retry test |
| **P1** | extension registry drift | one registry; unrecognised → runnable ERROR test |
| **P1** | V3 validation digest misread as V2 by JobDesk | §6 safe-refusal test |
| **P2** | formatting / order churn | structural golden + serializer determinism |
| **P2** | lossy unknown-params routing not surfaced | CLI warnings/report test |

---

## 16. Protected core

Unchanged during R3 (any change requires separate review): calc execution,
Gaussian/ORCA policy, `CalcArtifactManager`, ConfGen generation algorithm,
`ExecutionService`, control protocol, worker supervision, cancel/pause, remote
execution, artifact/path safety, `workflow_state.v1`, `workflow_binding.v1`,
`output_manifest`/`workflow_stats` v1. The one planned protected-core touch is the
**required** minimal pre-flight guard in `application/execution/workflow_adapter.py`
(§3.3 A), flagged for separate review: it adds only a `require_executable` call
before `build_workflow_service`, changes no V2 execution semantics, and exists
solely to fail closed before service-side state mutation.

---

## 17. Configuration-contract-v2 boundary

R3 keeps `configuration-contract.v1`/`.v2`, `workflow_schema_sha256` (v2),
`editor-manifest.v1`, `recipe-catalog.v1` and `configuration-validation.v1`
**unchanged**. Internal V3 support ≠ published V3 support; V3 is advertised in R7.
The V3 validation *answer* uses the same v1 wire shape with the V3 DOCUMENT digest
(§6), which the current JobDesk contract-bound check safely refuses.

---

## 18. R3 completion definition

**R3 COMPLETE — V3 can:** parse, schema-validate (document profile), semantic-validate,
canonicalise, **build a plan**, `config-show`, `dry-run`, and upgrade from V2.

**R3 COMPLETE — V3 cannot:** execute, resume, rerun-failed, write `workflow_state`,
write execution artifacts, use V1 identity for state, emit a V3 run output manifest,
or — crucially — **create a state root, run paths or a SQLite execution record for a
V3 execution attempt** (the service adapter guard (A) rejects before
`build_workflow_service`).

R3 COMPLETE ≠ "V3 runs calculations" — that is R4.

---

## 19. R4 handoff contract

**R3 provides R4:** stable step ids in the IR; the frozen V3 **definition**
fingerprint; explicit graph semantics + `ancestors()`; structured
`checkpoint.from_step`; a version-aware parser + validated V3 document; a V3 planning
branch (`WorkflowV3Plan`); **all four execution guards (A–D) already wired to the
single `require_executable`**, so R4 enables V3 execution by flipping one
`CAPABILITIES[v3].execute` flag — no guard body changes.

**R4 adds (not R3):** `workflow_binding.v2`; `workflow_state.v2`; step-id-keyed state
records; `steps/<id>/` directories; the **execution** fingerprint; V3 engine
enablement (flip `v3.execute`); V3 resume semantics; `output_manifest.v2` /
export metadata.

---

## 20. Adversarial review of this plan

| # | Attack | Resolution |
|---|---|---|
| 1 | Does V3 dry-run trip the execution gate? | No — the gate is after the planner, at the execution side effect (§3.1/§3.3); V3 dry-run builds a `WorkflowV3Plan`. |
| 2 | Can a fragment be rejected by the document schema before the fragment profile applies? | No — two structural profiles sharing `$defs` (§5); fragment validated with `SchemaProfile.FRAGMENT`. |
| 3 | Are there still two field allow-lists (schema vs validator)? | No — both derive from `param_fields` (§4); a test asserts descriptor↔schema equality. |
| 4 | Why does the schema accept an unknown extension the validator rejects? | Explained: namespace *grammar* is structural; *recognition* is producer-semantic (§11, §5). |
| 5 | Which digest does V3 `config validate` return? | The V3 DOCUMENT schema digest (§6). |
| 6 | Can current JobDesk mis-consume a V3 validation answer? | No — contract-bound to the V2 digest → safe refusal, never a false accept (§6). |
| 7 | Can V3 write any `workflow_state` before R4? | No — the check precedes binding/runtime/state (§3.3); asserted by tests. |
| 8 | Is the V3 fingerprint frozen before the semantic registry exists? | No — frozen in R3.4, after descriptors/extensions/ancestry (§R3.4 rationale). |
| 9 | Is upgrade output always Document+Runnable? | Yes by default; a fragment only on explicit request (§R3.3). |
| 10 | Is `--unknown-params=annotations` marked lossy? | Yes — CLI warning/report that the field leaves execution semantics (§R3.3). |
| 11 | Can a V3 run create a state root / SQLite record before the engine rejects it? | No — the service adapter guard (A) fires before `build_workflow_service` (before `_ensure_state_root`/`ensure_run_paths`/`SQLiteExecutionRepository`); asserted by `test_service_adapter_rejects_v3_before_side_effects` (§3.3, §R3.5). |
| 12 | Is the service adapter guard optional? | No — it is **mandatory** (§3.3 A, §12, §16). With only the engine guard, `build_workflow_service` would already have created state before rejection. |
| 13 | Do the four guards duplicate the version rule? | No — all call the single `require_executable`; none inspects `schema` itself; R4 flips one `CAPABILITIES` entry (§3.2). |
| 14 | Does the adapter need to build a service to learn the version? | No — it uses the side-effect-free `detect_schema_version` on the raw document before any service/state resource (§3.4). |

No circular dependency, no R3/R4 leak, no hidden runtime enablement, no duplicated
truth, no irreversible upgrade, and every gate sits at a real side-effect boundary
— the engine **and** the service constructor **and** the CLI/rerun entry points.

---

## 21. Out of scope for this plan

V3 execution, `workflow_state.v2`, `workflow_binding.v2`, step-id directories,
structured calculation, recipe *instantiation runtime*, plugin step types, JobDesk
changes, contract v3, remote-protocol changes.
