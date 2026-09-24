# ConfFlow Workflow V3 — Request For Comments

Status: **DRAFT — design only, not for implementation yet**
Scope: R3 design phase. This document defines the *intended* Workflow V3 schema and
semantics so the next phases can be implemented against a frozen target. **No V3
runtime, parser, state, or contract code is added by this RFC.**
Base: `014e18572c773770fa49a08c7a2368a402607c47`
(accepted R0–R2 head on `refactor/canonical-workflow-ir`).

> This file and its companions under `docs/rfc/` are inert documentation. They
> are **not** imported by the runtime and **must not** be wired into
> `workflow_schema_sha256` or the configuration contract. See §24.

---

## 1. Motivation

R0–R2 built a single canonical workflow IR (`confflow.config.canonical.workflow`)
and a single validation entry point (`validate_workflow_definition`). The V2
document is still shaped by historical choices that block a reliable GUI, precise
resume, and future extensions:

- a step's **identity is its `name`**, which is also its human label;
- the workflow is **implicitly linear** unless any step declares `inputs`;
- references (`inputs`, `chk_from_step`, `--step`) target **names or list
  positions**;
- the editor manifest addresses step fields by **`/steps/{index}/…`**.

V3 separates identity from presentation, makes the graph explicit and
position-independent, and turns the implicit behaviour into declared semantics.

## 2. Glossary

| Term | Meaning |
|---|---|
| **id** | immutable, machine-facing step identity. Graph/dependency/checkpoint/state/artifact key. |
| **label** | human-facing, editable step title. Presentation only. |
| **inputs** | the geometry/data-flow predecessors of a step, as a list of step ids. |
| **checkpoint** | a distinct reference to an upstream calc step whose checkpoint file is reused. |
| **definition** | the workflow document + its semantics, independent of any run. |
| **runnable** | a definition that satisfies all execution preconditions. |
| **partial document** | a parseable fragment (recipe) that is not yet runnable. |
| **definition fingerprint** | digest of the semantic definition (no run context). |
| **execution fingerprint** | definition fingerprint + run context/resources (the resume binding). |

## 3. V2 constraints / migration facts (verified against the code)

These are facts read from the shipped implementation at the base SHA, not
assumptions. Each cites the owning code.

**Identity concepts today**
1. `name` — from `WorkflowConfig.from_mapping` (`confflow/config/canonical/types.py`): `str(raw_name)` if the config declares one, else a generated `f"{type}_{index}"` (e.g. `confgen_2`).
2. **Canonical graph name** — `canonical_step_name` (`config/canonical/workflow.py`): `str(name).strip()`, falling back to `f"step_{index:02d}"` when the name is empty/whitespace. This can differ from the typed `name` only by surrounding whitespace.
3. **dirname** — `build_step_dir_name_map` (`workflow/step_naming.py`): sanitized name, deduplicated with `_2`, `_3` suffixes; empty names → `step_NN`.
4. **list position** — 1-based document position, used by `chk_from_step` numeric form and `--step <n>`.
5. **editor-side id** — JobDesk addresses step instances by **list index** via `json_pointer: /steps/{index}/params/...` (`config/canonical/editor_manifest.py`). There is no producer-side stable step id.

**Where `name` is identity**
6. Fingerprint binding: `_canonical_step` records `"name"` (`config/canonical/fingerprint.py`).
7. State: `StepRecord.name`; records are keyed by **dirname** (`workflow/engine.py::_initial_workflow_state`, `workflow/state.py`).
8. Calc resume: `manifest.json` stores `step_name`; `validate_reusable_artifact` rejects a manifest whose `step_name` differs (`workflow/resume_validation.py:162`).
9. Output manifest / stats: `terminal_outputs` and workflow stats key on the step **name** (`workflow/presenter.py`, `workflow/engine.py`).
10. `config-show` / `--rerun-failed` select a step by name or 1-based index (`workflow/config_show.py::_select_step`).

**Where list position is a reference**
11. `chk_from_step: 6` → `step_dirs[5]` (`workflow/step_handlers.py::_resolve_chk_input_dir`).
12. `--step 2` (`config_show`), recipe `exposed_fields`/`required_fields` are concept ids (`calc.keyword`), not positions.

**Graph semantics today**
13. `inputs` normalized by `normalize_step_inputs` (string → one entry, list → deduped in declared order). Unknown predecessor or duplicate name → error; `topo_order` detects cycles.
14. **Implicit linear**: if *no* step declares `inputs`, each step depends on its document predecessor. **Explicit DAG**: if *any* step declares `inputs`, the whole workflow is explicit and every step without `inputs` becomes a **root** (`workflow/plan.py` R1 pipeline / `config/canonical/v2_adapter.py`).
15. Roots consume the workflow's external input; a root calc with >1 external input is rejected (run context).

**Checkpoint today**
16. `chk_from_step` lives **inside `params`**; it accepts a step **name** or a **1-based position** (`workflow/step_handlers.py:445`). A non-resolving value is silently ignored at execution but *is* rejected by R2 definition validation (`config/canonical/validation.py`).

**Rename / reorder today**
17. Renaming a step changes: the fingerprint, the state key (dirname, usually), the calc manifest `step_name` (invalidating resume), and the output manifest keys.
18. Reordering the array changes: the implicit-linear chain, numeric `chk_from_step`, `--step <n>`, dirnames for collisions, and the fingerprint's `execution_order`/`predecessors` values.

**Disabled today**
19. `enabled: false` = **pass-through bypass**: the engine sets the step's output to its resolved input and marks it `skipped`; no artifact is produced (`workflow/engine.py:437`, `:312`, `:268`). Terminal collection special-cases `skipped` records (`workflow/engine.py:585`).

**Fan-out / fan-in today**
20. Fan-out (one step → several) and fan-in (several → one) are both supported; only `calc` is restricted to **exactly one** input (definition rule, R2 `calc_input_diagnostics`). `confgen` may take many.

**Terminals today**
21. Explicit DAG: steps that are never a predecessor. Linear: the last step in order (`workflow/plan.py` R1/adapter).

**Extensions today**
22. Unknown *params* keys are preserved and participate in the fingerprint as `semantic["extra"]`. Unknown **step-level** keys are dropped from the execution projection (`as_legacy_shape` keeps only `name/type/enabled/params/inputs`) and do **not** affect the fingerprint.

**Recipes today**
23. A recipe `document` is a **partial V2 fragment** parsed by `parse_workflow_mapping`; it may omit user-required fields (`conformer_search` ships `params: {}` and declares `required_fields: ["confgen.chains"]`). R2 established **parseable ≠ runnable**: `validate_workflow_definition` rejects it until filled.

**Fingerprint today**
24. `canonical_workflow_payload` binds: `workflow_schema`, schema digest, resolved `global` (minus deprecated `resume_from_backups`), each step `{name, type, enabled, params{resolved,chk_from_step,extra}}`, and `dag{mode, predecessors, execution_order, terminal_steps, step_dirnames}`.

---

## 4. V3 design goals

A stable step identity · label/identity separation · explicit-graph-only ·
position-independent dependencies and checkpoint references · lossless V2 reading
· unchanged V2 run/resume · reliable GUI editing · deterministic serialization ·
room for structured calculation (R5). **This RFC implements none of these; it
fixes the target.**

---

## 5. Stable step id

### 5.1 Decision

**Every step has an immutable, workflow-local, author-or-tool-assigned string
`id`.** `label` is separate and non-identifying.

### 5.2 Options considered

| Option | readable | diff-friendly | deterministic | collision-free | resume-safe |
|---|---|---|---|---|---|
| Random UUID (`550e8400-…`) | ✗ | ✗ | ✗ (upgrade churns) | ✓ globally | ✓ |
| Deterministic sequence (`s001`) | ✓ | ✓ | ✓ | ✓ workflow-local | ✓ |
| Slug from label (`opt_freq`) | ✓ | ✓ | ✓ | ✗ (dupes, and renames drift) | ✗ (rename = new id) |
| Opaque short random (`k7p2q9`) | ~ | ~ | ✗ | ✓ | ✓ |

### 5.3 Rules

1. **Charset / length / case** — `id` must match `^[a-z][a-z0-9_]{0,63}$` (lowercase
   only, ≤ 64 chars). Lowercase-only is enforced so `s001` and `S001` can never
   be confused; ids are nonetheless compared case-sensitively.
2. **Uniqueness** — **workflow-local**, not global. Global uniqueness would defeat
   copy/paste and template reuse, and nothing (state, dirs, bindings) is global.
3. **Who assigns** — the workflow author (hand-written YAML) or the tool that
   mutates the document (GUI *add/duplicate*, V2→V3 upgrade). The **allocator**
   is spec'd in §5.4 so every producer agrees.
4. **Generated form** — `s` + counter zero-padded to ≥ 3 digits (`s001`, `s042`,
   `s1000`). Bare YAML scalars starting with a letter never parse as numbers, so
   `id: s001` is unambiguously a string.
5. **Hand-written** — any id matching the charset is accepted; it need not be
   sequential. Tools never renumber an existing id.
6. **Renaming an `id`** — **not a document operation.** Changing identity is
   defined as *delete + create* (§20). There is no "rename id" verb.
7. **Allocation is once-and-persisted.** An id, once written, is never reassigned
   and never reused (even after deletion). This is the invariant that makes
   reorder/copy safe.

### 5.4 The allocator (normative)

```
next_id(existing_ids):
    numeric = [int(i[1:]) for i in existing_ids if re.fullmatch(r"s[0-9]+", i)]
    n = (max(numeric) + 1) if numeric else 1
    return "s" + format(n, "03d")
```

- Deterministic given the current document.
- Monotonic: never reuses a freed number, so a deleted id cannot be
  resurrected by a later add.
- Independent of array order, so adding/moving steps does not shuffle ids.

---

## 6. Step envelope

### 6.1 Terminology decision

V3 uses **`id` + `label`**, not `id` + `name`.

- `name` in V2 means *identity*, so reusing the word for a label would preserve
  the exact conflation V3 exists to remove, and would make migration review
  ("is this `name` an id or a label?") ambiguous.
- `label` also matches the editor manifest's existing per-field `label` concept
  (a human title) — same word, same meaning, different namespace (steps vs
  fields).

### 6.2 Field set (normative)

| Field | Required | Type | Role |
|---|---|---|---|
| `id` | runnable: yes / fragment: no | string (`^[a-z][a-z0-9_]{0,63}$`) | identity (allocated on instantiate for a fragment) |
| `label` | no | string | display title (may duplicate) |
| `type` | yes | `calc` \| `confgen` | step kind |
| `enabled` | no (default `true`) | boolean | bypass switch |
| `inputs` | **yes** | array of step ids (may be `[]`) | data-flow predecessors |
| `params` | no (default `{}`) | object | step-type-specific parameters |
| `checkpoint` | no | `{ from_step: <id> }` | checkpoint reuse reference |
| `extensions` | no | namespaced object | **semantic** producer data |
| `annotations` | no | free object | **non-semantic** presentation data |

- **No other key is allowed on a step.** An unknown key is a definition error
  (typo detection). Out-of-band data must use `extensions` or `annotations`.
- `label` is optional; when absent a consumer displays the `id`.
- `id` is optional to **parse** (so a recipe fragment is legal) but required to
  **run** — a runnable definition must give every step an id. This mirrors the R2
  parseable/runnable split (§19).
- `params` stays the typed container for the step type (see §18 for the R5 plan).

### 6.3 `extensions` vs `annotations` (namespace rule)

- **`extensions`** keys MUST be namespaced: `^[a-z0-9_]+(\.[a-z0-9_]+)+$`
  (≥ 2 dot-separated segments, e.g. `vendor.mopac.basis`). A non-namespaced key
  is an error. Unknown keys are **preserved** and **participate in the
  definition fingerprint** (an unknown producer extension could change what is
  computed, so a resume under a different one must be rejected — fail-safe).
- **`annotations`** keys are free-form and **never** participate in any
  fingerprint or execution decision. They exist so GUI layout / comments /
  colours have a legal home instead of being smuggled into `extensions`.
- Both buckets are preserved by round-trip (parse → serialize → parse is
  lossless modulo key order).

---

## 7. Explicit-graph-only

V3 has **no implicit-linear mode**. The V2 mode switch
(`implicit_linear` vs `explicit`) is a V2-adapter concern only.

- Every step declares `inputs`.
- `inputs: []` is a **root**: it consumes the workflow's external input.
- Because every edge is declared, there is no hidden order semantics and no
  "one `inputs` key flips the whole file" rule.

---

## 8. Root input representation

### Options

- **A. `inputs: []`** — the empty dependency list denotes "workflow input".
- **B. `inputs: ["$input"]`** — a magic pseudo-node.
- **C. a separate `source: workflow_input` field.**

### Decision: **A — `inputs: []`**

- One reference kind: everything in `inputs` is a step id. There is no second
  token namespace to validate and no reserved word to typo (`$ipnut`).
- It already matches V2's explicit-DAG behaviour (`inputs: []` is an explicit
  root), so upgrade is a rename-free projection.
- Multiple external inputs are the run context, not the document: a root step's
  effective input is the workflow's input set (CLI supplies N files). Cardinality
  is an **effective-dataflow** rule (R6), not a per-step marker.
- **Batch is explicitly out of scope.** JobDesk batch = N independent ConfFlow
  launches over N structures (v1 capability contract). V3 does **not** model
  batch as one workflow with N inputs; a single ConfFlow workflow still has one
  external input set.

`inputs` is required, so an author must write `inputs: []` to mean "root". This is
the one intentional verbosity V3 adds in exchange for removing a global mode.

---

## 9. Step order / serialization order

### Decision

**Array order is presentation order only. It has no execution semantics and no
fingerprint effect.**

1. **Reorder changes workflow semantics?** No.
2. **Reorder changes the definition fingerprint?** No — the definition payload is
   canonicalized by `id`.
3. **Does the serializer canonicalize/sort?** No. The serializer **preserves the
   author's array order** so YAML stays human-friendly and diffs stay local.
4. **Author order is presentation metadata.** Execution order is derived purely
   from the graph: topological waves, each wave sorted by the numeric id suffix
   (then lexicographically for non-`sNNN` ids). This makes the execution order a
   function of the graph alone, so two documents that differ only in array order
   produce byte-identical fingerprints and identical run orders.

This removes V2's biggest hidden dependency (V2's linear fallback and its
name-sorted waves both made array order semantic).

---

## 10. `inputs` (data dependency)

- **Always an array of step-id strings.** No scalar-or-list: a single shape is
  easier to validate, serialize, and diff, and fan-in is natural.
- Single predecessor is `inputs: [s001]`. (A scalar `input:` form was rejected;
  see §26.)
- Future typed ports can extend each entry to an object later without changing
  the "list of things" shape.

**Validation split**

| Rule | Layer |
|---|---|
| `inputs` present, is an array, entries are strings | schema |
| entry is a well-formed id token | schema |
| `id` values unique within the workflow | definition |
| referenced id exists | definition |
| no duplicate entries in one `inputs` | definition |
| no self-reference (`s001` in `s001.inputs`) | definition |
| graph is acyclic | definition |
| disabled predecessor allowed | definition (allowed) |
| effective cardinality after bypass | run-context / R6 |

---

## 11. Geometry flow vs checkpoint reference (separation)

`inputs` and `checkpoint` are **different relations** and must never be merged:

- `inputs` — an **artifact data-flow edge**: the predecessor's output geometry is
  this step's input.
- `checkpoint` — a **file-reuse reference**: this calc step starts from another
  calc step's `.chk` file (`%OldChk`), independent of which geometry it consumes.

### Decision

```yaml
- id: s002
  label: Frequency
  type: calc
  inputs: [s001]            # geometry comes from s001's output
  checkpoint: { from_step: s001 }   # and its .chk is reused
  params: { itask: freq, keyword: "B3LYP/6-31G(d)" }
```

`checkpoint.from_step` rules:

| Rule | Layer |
|---|---|
| `checkpoint` only on `type: calc` | definition |
| `from_step` is a well-formed id and exists | definition |
| `from_step` targets a `calc` step | definition |
| `from_step` is a **strict ancestor** via `inputs` (transitive) | definition |
| checkpoint file exists / program compatibility / digest | runtime (R4/R6) |

Restricting `from_step` to an *ancestor* means the referenced step is guaranteed
to execute earlier; siblings/descendants are rejected. Program compatibility is a
runtime check because the program may be inherited from `global` and because
ORCA has no `.chk`.

The V2 field `params.chk_from_step` is **removed** in V3 and replaced by this
field (§14).

---

## 12. Disabled-step semantics

**Normative:** `enabled: false` is a **pass-through bypass**.

- The step produces no artifact and executes nothing.
- Its output equals its **effective resolved input**: the concatenation of its
  predecessors' outputs, or the workflow's external input if it is a root.
- Downstream steps receive that same artifact, so a bypass can widen cardinality.

Given

```
A ─┐
   ├→ B(enabled:false) → C
D ─┘
```

`B` forwards `[A_out, D_out]` to `C`, so `C`'s input cardinality (not its
declared predecessor count) is what must be checked. **`calc` cardinality is
therefore evaluated on the effective dataflow after bypass**, not on the literal
`inputs` list. This is stated here as contract; the implementation of
effective-dataflow cardinality is deferred to **R6** (it is a dataflow engine
concern, not a document-shape concern).

A bypass must not itself become an error merely because it fans in; only the
*consumers* of the forwarded artifact are cardinality-checked.

**Disabled steps are exempt from execution preconditions.** A bypassed step never
executes, so runnable preconditions (`confgen.chains`, `calc.keyword`, calc input
cardinality, checkpoint resolution) apply only to **enabled** steps and to the
effective enabled dataflow. Structural/type checks still apply to every step
(`params` must be an object, `type` must be known, references well-formed), so a
disabled step is *parsed and typed* but not *preconditioned*. This matches V2,
which already exempts a disabled calc from the single-input rule.

> **Known divergence to reconcile (R3.4 / R6).** At the base SHA the canonical
> validator applies the confgen `chains`/`angle_step` and calc `keyword`
> preconditions to **disabled** steps too; only calc fan-in is exempted. The V3
> target above is uniform exemption. Reconciling this is a deliberate behaviour
> change and is **out of scope for the R3 design phase** (no code changes here);
> it is listed so R3.4/R6 do it consciously rather than by accident.

---

## 13. Step `type` and extensions

- `type` accepts **only** canonical values `calc` and `confgen` in V3.
- Legacy aliases `task` (→ `calc`) and `gen` (→ `confgen`) are handled **only**
  by the V2 adapter. They are **not** valid V3 type tokens. Rationale: keeping the
  aliases in V3 would perpetuate two spellings of one type and delay the moment a
  typo (`clac`) is caught.
- An unrecognized `type` is a definition error.
- Third-party / producer-specific behaviour is NOT modelled by inventing step
  types in V3. Unknown semantics go in the namespaced `extensions` bucket; a
  future plugin axis is R6+.

Extension/typo policy (normative):

| Location | Unknown key | Treatment |
|---|---|---|
| root top level | any | **error** (must live in `extensions`/`annotations`) |
| step top level | any | **error** |
| `extensions` | non-namespaced | **error** |
| `extensions` | namespaced | preserved, fingerprinted |
| `annotations` | any | preserved, never fingerprinted |
| `params` | unknown member | preserved; **warning** diagnostic, not an error (step params are versioned by type and may gain members) |

---

## 14. V2 → V3 upgrade (deterministic)

`confflow workflow upgrade` (R3.3) turns a V2 document into a V3 document. It is a
pure function of the input bytes: **no UUIDs, no timestamps, no randomness.**

### Algorithm

Given a V2 `WorkflowConfig` (or its canonical IR):

1. **ids** — walk steps in document order and assign `s001, s002, …` (the §5.4
   allocator over an empty document).
2. **label** — the V2 `name`, or the generated `{type}_{index}` if the V2 step was
   unnamed.
3. **type** — alias-normalize (`task`→`calc`, `gen`→`confgen`).
4. **enabled** — copied.
5. **inputs**:
   - explicit-DAG V2 → each declared predecessor name maps to that name's id, in
     declared order (already deduped by `normalize_step_inputs`);
   - implicit-linear V2 → step *k* gets `[id(step k-1)]`; step 1 gets `[]`.
6. **checkpoint** — if V2 `params.chk_from_step` is set: resolve it exactly as V2
   does (name → id; numeric → the id at that 1-based position) and emit
   `checkpoint: { from_step: <id> }`; then **remove** `chk_from_step` from
   `params`. An unresolvable value is a V2 definition error and upgrade fails.
7. **params** — copied; unknown members preserved (still fingerprinted, matching
   V2 semantics).
8. **extensions/annotations** — V2 unknown *step-level* fields were ignored by
   execution and by the fingerprint, so they become `annotations` (non-semantic).
   A `--to-extensions` flag may promote them under `annotations`→`extensions` only
   if the author explicitly asserts they are semantic; default is `annotations`.
9. **schema** — `confflow.workflow.v3`.
10. **step array order** — preserved.

### Outcomes by input class

| Input | Result |
|---|---|
| implicit linear | `s001 → s002 → …`, first step `inputs: []` |
| explicit DAG | same edges, ids assigned in document order |
| unnamed step | label `{type}_{index}`, id `sNNN` |
| sanitized-name collisions (`A!`/`A?`) | distinct ids, no dirname reliance |
| numeric `chk_from_step` | resolved to `checkpoint.from_step` id |
| disabled step | `enabled: false` preserved |
| `task`/`gen` alias | normalized to `calc`/`confgen` |
| unknown step-level fields | moved to `annotations` |
| unknown params | preserved in `params` |

### Identity stability after upgrade

Ids are written once and never reassigned. **Reordering the file after upgrade
does not change any id**, so the definition fingerprint and resume identity are
stable across cosmetic edits. This is the whole reason upgrade must not use
random ids.

---

## 15. Canonical IR shape

V3 does **not** introduce a second parallel IR. The R1 canonical model is
extended with optional identity + provenance:

```python
@dataclass(frozen=True)
class CanonicalStepDefinition:
    id: str | None            # None for a V2-sourced definition
    name: str                 # canonical graph name (V2) / label fallback (V3)
    label: str
    type: str
    enabled: bool
    params: dict[str, Any]
    predecessors: tuple[str, ...]     # V3: ids; V2: canonical names
    inputs_declared: bool
    v2_name: str
    checkpoint_from: str | None
    extensions: dict[str, Any]
    annotations: dict[str, Any]

@dataclass(frozen=True)
class CanonicalWorkflowDefinition:
    source_version: Literal["v2", "v3"]
    ...  # existing fields, plus extensions/annotations
```

- `source_version` records where the definition came from.
- V2 loader → IR sets `source_version="v2"`, `id=None`, `predecessors` = canonical
  names (unchanged V2 semantics).
- V3 parser → IR sets `source_version="v3"`, `id` populated, `predecessors` = ids.
- Downstream planning keys off `id` when present, else the V2 name, so V2 run
  identity is untouched.

---

## 16. Fingerprint semantics

Two digests, deliberately distinct.

### 16.1 Definition fingerprint (environment-independent)

Includes, canonicalized by id:

- `source_version` (`v2` | `v3`) and the schema identifier + digest;
- resolved `global`;
- per step: `id`, `type`, `enabled`, resolved `params`, `inputs` (by id),
  `checkpoint.from_step`, `extensions`.

Excludes: `label`, `annotations`, **array order**, and (for V2) the legacy `dag.mode`
tag is retained only in the V2 document's own digest.

Rationale for `extensions` inclusion: an unknown producer extension may alter what
is computed, so resume under a changed extension must fail closed. Rationale for
excluding `label`/`annotations`/order: they are presentation and cannot change a
result.

### 16.2 Execution fingerprint (R4)

**definition fingerprint + run context**: external input digests, resolved
resources, executables/runtime settings, and the resolved sandbox/policy inputs.

### 16.3 Resume

**Resume uses the execution fingerprint** (the binding), because resuming must
reject not only a changed definition but also changed inputs/resources. A rename
of a `label` or a reorder of the array must **not** invalidate a resume; changing
an `id` (delete+create) must.

V2's existing binding (`workflow_binding.v1`) is **unchanged** and keeps using the
V2 algorithm for V2 documents. V3 obtains its own binding schema in **R4**
(`workflow_binding.v2`).

---

## 17. State and directory identity (target for R4)

- Step directory: **`<work_dir>/steps/<id>/`**.
- **Machine identity is the id.** The label never appears in the path.
- Renaming `label` changes **no path, no state key, no fingerprint, no resume**.
- `output_manifest.json` records, per terminal, `{ id, label, artifacts }`: `id`
  for machines, `label` for display.
- Resume locates a step by id; a state written for id `s001` is only valid for a
  document whose `s001` still exists.
- New `steps/` grouping keeps run-level artifacts (`output_manifest.json`,
  `.workflow_state.json`, `run_summary.json`) from colliding with step dirs.

This is a target; R4 implements it. V2 keeps its flat `name`-derived layout and
V2 resume semantics (`workflow_state.v1`).

---

## 18. Structured calculation (future R5) — `params` decision

**Decision: `params` remains the single step parameter container.** R5 will add a
reserved sub-namespace `params.theory` (program/task/method/basis/dispersion/
solvent) rather than a new step-level `spec` field. Rationale:

- one container, one place to look; no `params` vs `spec` ambiguity;
- additive and non-breaking (`params.keyword` stays the raw escape hatch);
- when structured fields and `keyword` disagree, **validation errors** and requires
  an explicit reconciliation (R5 defines the compiler), so a silent ignore is
  impossible.

A step-level `spec` was rejected (§26) precisely because it would split the
parameter surface in two.

---

## 19. Recipes and templates

### 19.1 Partial vs runnable (unchanged boundary)

A **recipe document** is a parseable partial definition. It is accepted by the
parser and rejected by runnable-definition validation until the author supplies
the declared `required_fields`. This is exactly the R2 boundary and is preserved.

### 19.2 Ids in recipes

- A recipe **may omit `id`s** (recommended for single-step starters). The
  instantiate step allocates ids.
- A multi-step recipe **may carry `id`s**, but those are **template-local**. On
  instantiation they are **rebased** to the target workflow's fresh ids, and
  every intra-recipe `inputs` / `checkpoint.from_step` reference is rewritten.
- A recipe document must be **self-contained**: every `inputs` /
  `checkpoint.from_step` reference must resolve **within the recipe**. A recipe
  that references a step outside itself is not instantiable and is invalid as a
  recipe. (The shipped `conformer_search` recipe is a single root step, so it is
  trivially self-contained.)

`required_fields` (`confgen.chains`, `calc.program`, `calc.keyword`) and
`exposed_fields` are unchanged, and `build_recipe_catalog()` /
`recipe_catalog_sha256()` are untouched.

---

## 20. Copy / duplicate / template instantiation

| Operation | Copies | Allocates | Notes |
|---|---|---|---|
| **Duplicate step** | `type, params, enabled, checkpoint` | a **new id** | `label` gets a ` (copy)` suffix; `inputs` defaults to the original's `inputs` (branch alongside). Never copies the id. |
| **Insert between** | — | new id | see §21 |
| **Template instantiate** | template steps | **fresh ids for every template-local id** | intra-template refs rewritten; `annotations` carried; ids that were template-local never survive into the target |

**Identity is never copied.** This is the invariant that keeps ids meaningful.

**Rekeying (changing an id) = delete + create**: the old id's state, artifacts and
checkpoint reuse are abandoned, and the new id starts fresh. Tools surface this as
"this will discard the step's previous results", never as a quiet edit.

---

## 21. GUI operation mapping (semantic mutations)

| Operation | Mutation |
|---|---|
| **Add here (root)** | append a step with `id=alloc()`, `inputs: []` |
| **Insert X between A and B** | `X.id=alloc()`, `X.inputs=[A]`; in every step `S` where `B ∈ S.inputs`, replace `B` with `X`; if `B` had `checkpoint.from_step==A`, leave `B.checkpoint` unchanged (still an ancestor) |
| **Branch from A** | `X.id=alloc()`, `X.inputs=[A]`; `A`'s other consumers untouched |
| **Duplicate step S** | `X.id=alloc()`, `X.inputs=S.inputs` (same predecessors), `X.params=S.params`, `X.type=S.type`, `X.enabled=S.enabled`, `X.checkpoint=S.checkpoint`, `X.label=S.label+" (copy)"` |
| **Disable step** | set `S.enabled=false`; **do not** rewrite consumers' `inputs` (bypass is dataflow, §12) |
| **Delete step S** | remove `S`; for every `T` with `S ∈ T.inputs`, replace `S` with `S.inputs` (reconnect); if `S` had one predecessor, that predecessor takes its place; if `S` was a root, consumers become roots (`inputs` loses that entry) |
| **Change input source** | set `S.inputs` to the new id list (validated) |
| **Rename label** | set `S.label`; nothing else changes |

Deleting a step that a `checkpoint.from_step` references is a definition error and
must be blocked or the checkpoint re-pointed by the tool.

---

## 22. Stress scenarios

`FPΔ` = does the **definition fingerprint** change.

| # | Scenario | document shape | identity | edges | validation | FPΔ |
|---|---|---|---|---|---|---|
| S1 | single calc | one step, `inputs: []` | s001 | none | root; run-context cardinality | — |
| S2 | Opt→Freq→SP | 3 calc, `[],[s001],[s002]` | s001..s003 | chain | calc single-input ok | — |
| S3 | atomic Opt+Freq | one calc `itask: opt_freq` | s001 | none | ok | — |
| S4 | ConfGen→Opt→Freq | confgen root → calc → calc | s001..s003 | chain | confgen needs chains; calc ok | — |
| S5 | Opt ├→SP-A └→SP-B | diamond | s001..s003 | fan-out | ok | — |
| S6 | A + B → ConfGen → Calc | two roots → confgen fan-in → calc | s001..s004 | fan-in then chain | confgen fan-in ok; calc single-input ok | — |
| S7 | disabled middle | A → B(off) → C | s001..s003 | chain, bypass | effective dataflow (R6) | — |
| S8 | disabled fan-in | A,D → B(off) → C | s001..s004 | bypass widens C's input | C cardinality on effective flow (R6) | — |
| S9 | checkpoint reuse | A → B(opt) → C(freq, checkpoint.from_step=B) | s001..s003 | chain + checkpoint edge | from_step is calc ancestor | — |
| S10 | rename label | S2 with `label` changed | unchanged | unchanged | ok | **no** |
| S11 | reorder serialization | S2 array permuted | unchanged | unchanged | ok | **no** |
| S12 | duplicate step | S2 + copy of s002 | new s004 | s004.inputs=[s001] | ok | yes |
| S13 | insert between | A→B becomes A→X→B | new id | rewired | ok | yes |
| S14 | delete + reconnect | remove middle, reconnect | remaining ids | rewired | ok | yes |
| S15 | partial recipe | conformer_search | (no ids) | none | parseable, **not runnable** | n/a |
| S16 | V2 unnamed upgrade | V2 `{type: confgen}` steps | s001.. | linear → explicit | ok | new digest |
| S17 | V2 numeric chk upgrade | `chk_from_step: 2` | s00N | checkpoint ref | resolves to id | new digest |
| S18 | template twice | two copies of a 2-step template | s00N..s00N+3 | two disjoint subgraphs | ids rebased, no collision | yes |

---

## 23. Schema prototype examples

The five core examples plus a partial recipe and a V2 before/after live in
`docs/rfc/examples/` and share one field vocabulary:

- `workflow-v3-linear.yaml` (S2)
- `workflow-v3-branched.yaml` (S5)
- `workflow-v3-confgen-fanin.yaml` (S6)
- `workflow-v3-checkpoint.yaml` (S9)
- `workflow-v3-disabled.yaml` (S7)
- `workflow-v3-recipe-partial.yaml` (S15)
- `workflow-v2-to-v3-before.yaml` / `workflow-v2-to-v3-after.yaml` (S16 + S17)

A non-production JSON Schema draft is at
`docs/rfc/workflow-v3.schema.draft.json`.

---

## 24. JSON Schema draft

`docs/rfc/workflow-v3.schema.draft.json` is a **DRAFT ONLY** artifact:

- It is not imported anywhere and is not part of any package data.
- It must **not** be referenced by `workflow_schema_sha256`, the editor manifest,
  the recipe catalog, or the configuration contract. The published
  `confflow.workflow.v2` schema and its digest are unchanged.
- It describes the **document** profile: `id` is optional so a recipe fragment
  still validates, while `type` and `inputs` are required. A **runnable**
  definition additionally requires every step to have an `id` (the parseable vs
  runnable split, §19).
- R3.2 will supersede it with a production schema, at which point the contract
  version bumps under R7.

All example documents under `docs/rfc/examples/` validate against this draft
(the V2 "before" example does not, by construction).

---

## 25. Decision table

| Topic | V2 | V3 decision | Why |
|---|---|---|---|
| Step identity | `name` (also label) | immutable `id` (`sNNN`) | identity must survive rename/reorder |
| Display label | `name` | `label` (optional, may duplicate) | separate presentation from identity |
| Graph | implicit-linear OR explicit | **explicit only**; `inputs` required | remove hidden mode and order coupling |
| Root input | implicit or `inputs: []` | `inputs: []` | one reference kind, no magic token |
| Dependency target | name | step `id` | position/name independent |
| Checkpoint target | `params.chk_from_step` name/index | `checkpoint.from_step` id | distinct relation, stable target |
| Step order | sometimes semantic | presentation only | deterministic, order-free execution |
| Disabled | runtime bypass | **declared pass-through** semantics | schema-level, cardinality on effective flow |
| Directory | name-derived | `steps/<id>/` | machine identity = id |
| Definition fingerprint | v1 (name-based, order-sensitive) | id-based, excludes label/order/annotations | presentation must not affect identity |
| Execution fingerprint | v1 binding | definition + run context (R4) | resume rejects real changes only |
| Extensions | unknown params = semantic; unknown step keys dropped | `extensions` (namespaced, semantic) + `annotations` (non-semantic) | typo detection + round-trip + fingerprint clarity |
| Type tokens | `calc/confgen/task/gen` | `calc/confgen` only | aliases only in V2 adapter |
| Recipes | partial V2 fragment | partial V3 fragment, self-contained, ids rebased on instantiate | parseable ≠ runnable preserved |

---

## 26. Rejected alternatives

| Alternative | Verdict | Why |
|---|---|---|
| Random UUID step ids | **rejected as default** | non-deterministic upgrade churns diffs; unreadable. Allowed only as a hand-written id if the charset permits. |
| List-index step ids | rejected | exactly the V2 problem: reorder changes identity |
| `name` as id | rejected | rename = identity drift; label cannot be free |
| Implicit linear fallback in V3 | rejected | hidden order semantics; the root cause of fragile edits |
| Graph-canvas-specific schema (x/y fields) | rejected | layout is not semantics; belongs to the consumer/`annotations` |
| `$input` pseudo-node | rejected | second token namespace, typo-prone, needless |
| `inputs` scalar-or-list | rejected | two shapes to validate/serialize; fan-in consistency |
| Checkpoint merged into `inputs` | rejected | different relation; merging loses the ancestor rule |
| `label` affecting fingerprint | rejected | rename must be free |
| List order affecting execution | rejected | order must not be identity |
| Eager migration of old workflow state | rejected | V2 runs must keep V2 resume semantics; migration is opt-in per run |
| Deleting V2 support | rejected | JobDesk and existing runs depend on V2 |
| Step-level `spec` alongside `params` | rejected | splits the parameter surface; `params.theory` (R5) is additive |

---

## 27. Compatibility matrix

| Artifact | Status |
|---|---|
| Old V2 YAML | **supported** — V2 adapter unchanged; never silently read as V3 |
| V2 run / `.workflow_state.json` | **stays V2** resume semantics (`workflow_state.v1`); no eager migration |
| V3 workflow | new semantics (`confflow.workflow.v3`) |
| `configuration-contract.v1` | **unchanged** |
| `configuration-contract.v2` | **unchanged** (editor manifest / recipe catalog digests unchanged) |
| `configuration-validation.v1` | **unchanged** (exactly `{schema, valid, workflow_schema_sha256, issues:[{path,message}]}`) |
| Current JobDesk | continues on V2 until it opts into V3 (future work) |

Guarantee: a document is dispatched by its `schema` field. A document lacking
`schema: confflow.workflow.v3` is treated as V2; there is no heuristic promotion.

---

## 28. R3 implementation slices (do not execute yet)

### R3.1 — Extend the canonical IR for stable identity
- files: `config/canonical/workflow.py`, `config/canonical/v2_adapter.py`
- protected: none (IR is new in R0–R2)
- tests: extend `test_canonical_workflow_ir.py`; V2 IR fields unchanged
- acceptance: V2 IR byte-identical; optional `id`/`label`/`checkpoint`/`source_version` present

### R3.2 — Add the `workflow.v3` parser + schema
- files: new `config/canonical/v3_parser.py` (or `parser` extension), `config/canonical/schema.py` (+ v3 schema constant, **not** replacing v2), `docs/rfc/workflow-v3.schema.draft.json` → production
- protected: `load_workflow_model` V2 path must not change
- tests: parse fixtures, unknown-key errors, id charset, `inputs` validation
- acceptance: `confflow.workflow.v3` documents parse to the canonical IR; V2 still works

### R3.3 — `confflow workflow upgrade`
- files: new CLI subcommand; pure function in `config/canonical/v2_adapter.py` or a new `upgrade.py`
- tests: the §14 outcome table; determinism (same bytes twice)
- acceptance: byte-deterministic output; ids stable across re-run

### R3.4 — Canonical graph/reference validation for V3
- files: `config/canonical/validation.py`
- tests: unknown id, dup ref, self-loop, cycle, checkpoint ancestor rule, type tokens
- acceptance: definition validation covers §10/§11; diagnostics use stable codes

### R3.5 — CLI `config-show` / `export` / `--step` on ids
- files: `workflow/config_show.py`, `workflow/rerun_failed.py`, `workflow/dry_run.py`, `config/cli.py`
- protected: V2 selection by name/index must remain for V2 documents
- acceptance: V3 documents select by id; V2 behaviour unchanged

---

## 29. Dependencies on later phases

- **R4 (state/resume)** needs R3's stable id, graph semantics, and the
  definition/execution fingerprint split; implements `workflow_binding.v2`,
  `workflow_state.v2`, `steps/<id>/`.
- **R5 (structured calc)** needs R3's `params` envelope; adds `params.theory`.
- **R6 (capabilities/dataflow)** needs R3's step type and effective-dataflow
  semantics (disabled bypass cardinality).
- **R7 (contract)** publishes the V3 schema/editor manifest/recipe catalog once
  R3/R5/R6 are stable.

---

## 30. Adversarial review

| Role | Concern | Resolution |
|---|---|---|
| CLI user | "Why must I write `inputs: []`?" | One-time verbosity buys order-free, rename-free semantics; upgrade writes it for you. |
| CLI user | "I renamed my step and my resume broke." | Cannot happen in V3: dirname/state/fingerprint use `id`; `label` is free. |
| JobDesk GUI | "I address fields by `/steps/{index}/…`." | R3.2/R3.5 add id-addressed pointers (`/steps/{id}/params/...`); JobDesk adopts at its own pace on V2. |
| JobDesk GUI | "Two steps with the same label are indistinguishable." | UI disambiguates with the id; labels may duplicate by design. |
| Execution engine | "Does array order still matter?" | No: waves are sorted by id; order is presentation. |
| Resume/state | "Copy a step and resume the old results?" | Duplicate always allocates a new id; old id's state is not adopted. |
| Source control | "Will upgrade churn the file every time?" | No: deterministic ids, preserved order, no UUIDs. |
| Template/recipe | "Two instances of one template collide." | Ids are template-local and rebased on instantiate; recipes are self-contained. |
| Plugin/step type | "I need a step type ConfFlow does not know." | Unknown `type` is rejected; semantics go in namespaced `extensions`. A plugin axis is R6+. |
| Security/fail-safe | "Unknown extension could change results silently." | `extensions` participates in the fingerprint, so resume fails closed; `annotations` is the only non-semantic bucket. |

---

## 31. Open-question gate (must all be answered; **no TBD remains**)

1. V3 step identity — a workflow-local immutable `id` string (§5).
2. Who generates it — author or mutating tool via the §5.4 allocator.
3. V2 upgrade id generation — document-order `s001…` (§14).
4. Can `label` duplicate — yes (§6).
5. Does renaming `label` affect execution identity — no (§16, §17).
6. Does step array order have semantics — no (§9).
7. Root input — `inputs: []` (§8).
8. `inputs` references — step ids (§10).
9. `checkpoint` references — `checkpoint.from_step`, a calc ancestor id (§11).
10. Disabled semantics — declared pass-through bypass (§12).
11. Duplicate id allocation — always a new id (§20).
12. Recipe instantiate collisions — template-local ids rebased to fresh ids (§19, §20).
13. Unknown extensions — namespaced `extensions` (semantic) + `annotations` (non-semantic) (§13).
14. Definition fingerprint and label/order — excluded; ids/extensions included (§16).
15. Old V2 state — stays V2; no eager migration (§17, §27).

---

## 32. Out of scope for this RFC

Implementation of V3 runtime, state v2, binding v2, structured calc, plugin step
types, JobDesk changes, and remote-protocol changes. This RFC is a target
specification only.
