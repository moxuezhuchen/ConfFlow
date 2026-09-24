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

| Option | readable | diff-friendly | deterministic | collision-safe | history-safe |
|---|---|---|---|---|---|
| Random UUID (`550e8400-…`) | ✗ | ✗ | ✗ (upgrade churns) | ✓ globally | ✓ |
| Max-suffix counter (`s001`…`max+1`) as the **only** scheme | ✓ | ✓ | ✓ | ✓ at the moment of allocation | ✗ **delete then add reuses a deleted id** |
| Persistent high-water mark (`next_step_number`, stored in doc) | ✓ | ✓ | ✓ | ✓ | ✓ *if the metadata survives* — but the allocator state becomes document state |
| Slug from label (`opt_freq`) | ✓ | ✓ | ✓ | ✗ (dupes; renames drift) | ✗ rename = new id |
| **Short opaque immutable id** (`s_k7m2qvd`) | ~ | ~ | ✗ | ✓ (with collision check) | ✓ |

The previous draft used the max-suffix counter **and** claimed ids are never
reused; that is contradictory — deleting `s003` from `{s001,s002,s003}` makes
`max+1 == 3`, so a new step would be `s003` again. This revision fixes it.

### 5.3 Legal id grammar (schema-level)

An `id` is a string matching:

```
^[a-z][a-z0-9_]{0,63}$
```

- **Case**: lowercase only, enforced by the regex and **never normalised**.
  Uppercase is rejected outright, so `s001`/`S001` cannot both exist.
- **Length**: 1–64 characters.
- **Path safety**: the charset excludes `/`, `\`, `.`, `-`, and whitespace, so
  every id is a legal, unambiguous directory component (RFC §17).
- **Uniqueness**: workflow-local (not global) — nothing in ConfFlow is global.
- **Legal ≠ generated.** The schema defines which ids are *legal*; §5.4 defines
  which ids the *allocator* produces. Hand-written ids need not be allocator-shaped.

This grammar deliberately admits **both** generated families below, plus any
author-chosen token (`opt_a`, `sp2`, …).

### 5.4 Allocator policy (two generated families)

The allocator produces exactly two shapes, and nothing else:

| Family | Regex | Used by | Shape rationale |
|---|---|---|---|
| **sequential** | `^s[0-9]{3,}$` (`s001`, `s042`, `s1000`) | **V2 → V3 upgrade only** | deterministic, diff-friendly migration |
| **opaque** | `^s_[a-z2-7]{8}$` (`s_k7m2qvd`) | **any application-generated step** — GUI/API *add*, *duplicate*, *recipe instantiate* | needs no document state; safe against delete |

Combined "allocator-generated" regex: `^s(?:[0-9]{3,}|_[a-z2-7]{8})$`.

- **Opaque alphabet** is RFC 4648 base32 lowercase: `[a-z2-7]` (26 letters + the
  six digits `2`–`7`, i.e. 32 symbols). It contains no digit `0`/`1`/`8`/`9`, no
  uppercase, and no path-danger characters, so it is copy/paste-friendly and
  unambiguous next to the `s_` prefix.
- **Opaque length** is 8 symbols → 32⁸ = 2⁴⁰ ≈ 1.1 × 10¹² values, but the length
  is a *rendering* choice; safety comes from the collision check below, not from
  the space size.
- **Sequential ids are generated once** by the upgrade command, in document order
  (first step `s001`, second `s002`, …). Sequential ids are **never** produced by
  any other operation — in particular not by the max-suffix rule, which is
  rejected (see §5.6 and §26).

### 5.5 Collision handling (normative)

```
allocate(existing_ids):
    for attempt in range(MAX_ATTEMPTS):        # e.g. 32; unreachable in practice
        candidate = "s_" + base32_lowercase(8)
        if candidate not in existing_ids:
            return candidate
    raise AllocationError(...)                 # never silently reuse
```

- The allocator **must** check the candidate against the document's current id
  set; "the probability is tiny" is not a substitute for checking.
- On collision it redraws. The retry is bounded; exhausting the bound is an error,
  not a silent duplicate.
- Uniqueness is checked against the whole workflow being edited (so a second
  recipe instantiation sees the first instantiation's ids — §22 S23).

### 5.6 "Never reused" — precise semantics

A static YAML document cannot prove that an id never existed before. V3 therefore
states the invariants it **can** hold, and drops the unprovable claim:

1. **Document invariant (verifiable):** the ids in the current document are unique.
   A duplicated id is a definition error (§10).
2. **Application invariant (the allocator's responsibility):** the allocator draws
   only fresh opaque tokens and checks each against the current document, so it
   will not reissue a token already present; and it never produces the sequential
   family, so an add can never reproduce a deleted `sNNN`. Mechanism, not
   probability: opaque-with-collision-check + sequential-only-for-upgrade.
3. **Cooperative layer (application):** undo/redo and session history must not
   deliberately resurrect an id the user removed. This is the application's
   promise, not something a static document can encode.
4. **Not claimed:** global or historical uniqueness. A user who hand-writes an id
   that was used in a past, discarded document is undetectable, and V3 does not
   pretend otherwise. Only current-document uniqueness is enforced.

Consequence: deleting the highest-numbered migrated step and then adding a step
yields an **opaque** id (`s_…`), never the deleted `sNNN` (S19).

### 5.7 Persistent high-water mark — rejected

Storing `next_step_number` in the document would keep pretty sequential ids, but:

- the allocator's state becomes document state (a new field every tool must
  maintain, and a new merge-conflict surface);
- hand-edited YAML can drift the mark or delete it, silently breaking the very
  guarantee it exists to provide;
- it does not survive copy/paste of a step into another document.

The opaque family gets the same safety with no document state, so the
high-water-mark option is rejected (§26).

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
| `params` | no (default `{}`) | object | step-type-specific **core** parameters (strict, §6.4) |
| `checkpoint` | no | `{ from_step: <id> }` | checkpoint reuse reference |
| `extensions` | no | namespaced object | **semantic** producer/plugin data (§6.3) |
| `annotations` | no | free object | **non-semantic** presentation data |

- **No other key is allowed on a step.** An unknown key is a definition error
  (typo detection). Out-of-band data must use `extensions` or `annotations`.
- `label` is optional; when absent a consumer displays the `id`.
- `id` is optional to **parse** (so a recipe fragment is legal) but required to
  **run** — a runnable definition must give every step an id, and the parser must
  **not** silently invent one for a runnable document. This mirrors the R2
  parseable/runnable split (§19).
- `params` carries core semantics only and is **strict** (§6.4); `params` stays
  the typed container for the step type (see §18 for the R5 plan).

### 6.3 `extensions` vs `annotations`

Three layers, with one rule each:

| Layer | Meaning | Unknown key | Fingerprinted |
|---|---|---|---|
| `params` | core semantic parameters | **definition ERROR** (§6.4) | yes |
| `extensions` | namespaced semantic extension | **runnable ERROR** unless recognised (§6.3) | yes |
| `annotations` | non-semantic presentation | preserved | **no** |

**`extensions` namespace rule.** Keys MUST match

```
^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$        # >= 2 dot-separated segments
```

each segment starting with a lowercase letter (e.g. `org.example.feature`,
`vendor.mopac.basis`). A key that is not namespaced is a definition error.

**Unknown extension behaviour (fail closed).**

- **Parse / round-trip:** an unknown namespaced extension is **preserved**
  (parse → serialize → parse is lossless) so a document from a newer producer
  survives a round trip through an older one.
- **Runnable:** an extension namespace the current producer does **not**
  recognise is a **definition ERROR**. The producer cannot guarantee the
  execution semantics of a semantic extension it does not understand, so it must
  refuse to run rather than silently ignore it. Recognised namespaces are
  validated by their owner.
- This preserves the R2 principle **parseable ≠ runnable** (§19): the document is
  never rejected *at parse time*, only at the runnable gate.

**`annotations`** keys are free-form and **never** participate in any fingerprint
or execution decision. They exist so GUI layout / comments / colours have a legal
home instead of being smuggled into `extensions` or `params`.

### 6.4 `params` is strict (fail closed)

For the core step types (`calc`, `confgen`), `params` is a **typed, closed** map:
every member is a parameter ConfFlow knows and validates. An unknown member is a
**definition ERROR**, not a warning:

```yaml
params:
  methd: B3LYP      # ERROR: unknown calc parameter 'methd'
```

Rationale: a misspelled core parameter would otherwise be silently ignored, and
the user would believe `methd` took effect. V2's `params`-as-open-bag (unknown
members preserved as `extra`) is precisely the failure mode V3 removes. The
*warning* level the first draft proposed for unknown params is rejected (§26).

- Extension parameters go in `extensions` (§6.3), never in `params`.
- The **production** typed `params` schema per step type is authored in **R3.2**.
  The `schema.draft.json` in this RFC uses a clearly-marked placeholder for
  `params` and must not be mistaken for the finished strict schema (§24).

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

The first rule constrains the **declaring** step; the remaining rules constrain
the **target**. All of them are definition-layer rules. In particular
"`from_step` targets a `calc` step" is deliberately part of this layer: a
definition that declares checkpoint reuse of a `confgen` step is a definition
error even when that step is a strict ancestor. Whether the target actually
produces a consumable checkpoint artifact (file exists, program compatibility,
digest) is a runtime capability question and stays in R4/R6 — the definition
layer never infers artifact capability beyond the `calc` requirement above.
Disabled steps are exempt from the target rules under the §12 resolution
exemption; the declaring-step rule above still applies to them.

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

> **Reconciled (was a divergence).** At the base SHA the canonical validator
> applies the confgen `chains`/`angle_step` and calc `keyword` preconditions to
> **disabled** steps too; only calc fan-in is exempted. V3's target is the uniform
> exemption above, and R3.4 implements it for V3 (V2 behaviour is untouched).

**Disabled steps exempt from *required-presence*, not from *validity*.** An exempt
field (`confgen.chains`, `calc.keyword`) may be **absent** on a disabled step and
the definition is still runnable-valid. But:

- a **supplied** value is still validated and canonicalised — being disabled never
  hides an invalid value the author actually wrote;
- non-exempt semantic errors (bad enum, malformed extension payload, an invalid
  `total_memory`, …) are still errors on a disabled step;
- graph/reference checks (unknown ref, duplicate ref, self-ref, cycle) and
  extension recognition still apply to every step.

**Disabled steps and the definition fingerprint (normative).** A RUNNABLE-valid
definition **must** be fingerprintable (§16.A): the fingerprint may not fail for a
document the validator accepts. The per-step `params` in the fingerprint payload
are therefore **profile-aware canonical semantic params**:

1. supplied params are canonicalised and included;
2. semantic defaults that are valid independent of enabled-state are applied the
   same way as for an enabled step;
3. any RFC §12 **disabled-exempt** required-presence field that is absent is
   **omitted** from the payload — never synthesised (no `null`, `""`, sentinel,
   fake default or temporary placeholder);
4. a disabled-exempt field that *is* supplied is validated, canonicalised and
   included, so `enabled:false` + `itask: opt` and `enabled:false` +
   `itask: opt` + `keyword: "B3LYP/6-31G*"` fingerprint **differently** — a
   disabled step's explicit configuration is still part of the definition.

This keeps a single resolution path: canonicalisation and enabled-only
required-presence checks are separated, so no `keyword = "__dummy__"`-style
work-around is needed. An enabled step is fully resolved, and any missing
required field is a RUNNABLE error before a fingerprint is ever requested.

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

| Location | Key | Treatment |
|---|---|---|
| root top level | any not in `{schema, global, steps, extensions, annotations}` | **error** |
| step top level | any not in the §6.2 field set | **error** |
| `params` | any unknown **core** member | **definition ERROR** (§6.4) |
| `extensions` | non-namespaced | **definition error** |
| `extensions` | namespaced, **recognised** | validated by its owner, fingerprinted |
| `extensions` | namespaced, **unrecognised** | parse-preserved; **runnable ERROR** (§6.3) |
| `annotations` | any | preserved, never fingerprinted |

---

## 14. V2 → V3 upgrade (deterministic)

`confflow workflow upgrade` (R3.3) turns a V2 document into a V3 document. It is a
pure function of the input bytes: **no UUIDs, no timestamps, no randomness.**

### Algorithm

Given a V2 `WorkflowConfig` (or its canonical IR):

1. **ids** — walk steps in document order and assign the **sequential family**
   `s001, s002, …` (§5.4). Upgrade is the *only* producer of sequential ids.
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
7. **params** — copied. A V2 param member that is **unknown to the core type** is
   **not** silently carried (V3 `params` is strict, §6.4): upgrade **fails** with a
   diagnostic naming the step and key. The author may explicitly route such keys
   with `--unknown-params=annotations` (demote to the non-semantic bucket, below)
   or `--unknown-params=extensions:<ns>` (move to a named extension namespace);
   the default is to fail, matching the V3 runnable rule. This prevents a V2
   `methd:` typo from becoming a silently-inert V3 field.
8. **extensions/annotations** — V2 unknown *step-level* fields were ignored by
   execution **and** by the V2 fingerprint, so they become non-semantic
   `annotations` and never affect anything. (They are **not** routed to
   `extensions`, which would wrongly make them semantic.) V2 unknown *root-level*
   fields are treated the same way, for the same reason. The concrete key path is
   the reserved migration namespace below.
9. **schema** — `confflow.workflow.v3`.
10. **step array order** — preserved.

### Migration metadata mapping (normative)

Upgrade owns one reserved annotation key, **`confflow.migration.v2`**. It carries
migration metadata only and is never used for anything else.

```yaml
annotations:
  confflow.migration.v2:
    unknown_fields:            # <- V2 field-level data (root and step)
      <original-key>: <original-value>
    unknown_params:            # <- only --unknown-params=annotations
      <original-param-key>: <original-value>
```

- **`unknown_fields`** — V2 **root-level** and V2 **step-level** unknown fields,
  preserved verbatim with their exact nested structure. These fields were
  non-semantic in V2 (ignored by execution and by the V2 fingerprint), so they
  stay non-semantic in V3. They are **not** routed to `extensions` (which would
  wrongly promote them to semantic, fingerprinted data).
- **`unknown_params`** — only ever written under `--unknown-params=annotations`.
  An unknown **core `params`** member is demoted here; this is an **explicitly
  lossy** demotion and the CLI must report it as such (below).
- **`unknown_fields` and `unknown_params` are never mixed.**
- **Empty groups are omitted.** An empty `unknown_fields` / `unknown_params` is
  not emitted, an empty `confflow.migration.v2` is not emitted, and an empty
  `annotations` is not emitted (the ordinary omission rule).
- **Collision safety.** A V2 source field literally named `annotations` is just a
  V2 unknown field: it is preserved as
  `unknown_fields["annotations"]`, never merged into the V3 `annotations`
  container. Likewise a V2 field named `confflow.migration.v2` is preserved as
  `unknown_fields["confflow.migration.v2"]` and never collides with the reserved
  key.

**`--unknown-params=extensions:<ns>` is the opposite (semantic) routing.** The
unknown params are placed under `extensions[<ns>]` and **not** copied into
`annotations`, because that choice asserts the data is semantic. The namespace
must satisfy the §6.3 grammar; the document stays structurally valid even when the
current producer does not recognise the namespace, and the CLI must say so (future
runnable validation may reject it). `annotations` and `extensions` therefore keep
their §6.3 meanings: `annotations` = non-semantic, `extensions` = semantic.

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
| unknown step-level fields | `annotations.confflow.migration.v2.unknown_fields` |
| unknown root-level fields | `annotations.confflow.migration.v2.unknown_fields` |
| unknown params (core) | **upgrade error** by default; routable via `--unknown-params=…` |
| `--unknown-params=annotations` | `annotations.confflow.migration.v2.unknown_params` (lossy) |
| `--unknown-params=extensions:<ns>` | `extensions[<ns>]` (semantic routing) |


### Identity stability after upgrade

Ids are written once and never reassigned. **Reordering the file after upgrade
does not change any id**, so the definition fingerprint and resume identity are
stable across cosmetic edits. This is the whole reason upgrade must not use
random ids: it is the one deterministic (sequential) allocation, and every later
edit uses the opaque family (§5.4). Deleting the highest migrated id and adding a
new step therefore yields an opaque id, never the deleted `sNNN` (S19).

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

- `source_version` records where the **document** came from (provenance); it is
  *not* part of the definition semantics (§16).
- V2 loader → IR sets `source_version="v2"`, `id=None`, `predecessors` = canonical
  names (unchanged V2 semantics).
- V3 parser → IR sets `source_version="v3"`, `id` populated, `predecessors` = ids.
- Downstream planning keys off `id` when present, else the V2 name, so V2 run
  identity is untouched.
- The **canonical semantics version** is a separate constant,
  `confflow.workflow-semantics.v3`, owned by the canonical layer (not the
  document) and used by the definition fingerprint (§16).

---

## 16. Fingerprint semantics

Three layers, deliberately distinct. In particular the **definition fingerprint
does not contain any schema digest** — a schema's `description`/documentation
metadata can change without the workflow's scientific semantics changing, and a
definition's identity must not move when only the schema's *representation* moves.

### 16.A Workflow definition fingerprint (environment-independent)

Answers: *"what is the semantics of this workflow?"*

```
payload = {
    "semantics_version": "confflow.workflow-semantics.v3",   # a constant, not the document version
    "global":   <resolved scientific global options>,
    "steps":    [ <per-step semantic record, canonical-sorted by id> ],
}
```

Per-step semantic record: `id`, `type`, `enabled`, resolved `params`, `inputs`
(by id), `checkpoint.from_step`, and `extensions`.

**Excluded** from the definition fingerprint:

- `label`, `annotations` — presentation;
- the **steps array order** — canonicalised by id (§9);
- the **schema identity and digest** — schema representation, not semantics;
- the **source document version** (`v2`/`v3`) and producer/commit metadata;
- any documentation or canonicalizer metadata.

`extensions` is included because a semantic extension can change what is
computed, so a resume under a changed extension must fail closed. The definition
fingerprint is computed over the **canonical semantic payload** with keys sorted
and steps ordered by id; it does not depend on the YAML's byte layout.

**Disabled steps.** Per-step `params` are **profile-aware canonical semantic
params** (§12): an absent disabled-exempt required field (`confgen.chains`,
`calc.keyword`) is omitted rather than synthesised, while a supplied value is
canonicalised and included. A RUNNABLE-valid definition is therefore always
fingerprintable. `checkpoint.from_step` is part of the per-step record whenever it
is declared (a disabled calc's declared checkpoint is still definition
configuration); it is never synthesised.

**`inputs` canonicalisation.** `inputs` is a dependency *relation*, not an ordered
list (§10 — a duplicate entry is a definition error, so order carries no meaning),
so the payload stores each step's predecessors **sorted by the canonical V3 id
order key**. Permuting the `inputs` array in the source therefore cannot move the
fingerprint.

**Step order key.** Steps and ids are ordered by `(0, int(suffix))` for ids
matching `^s[0-9]+$`, otherwise `(1, id)`; this is the single ordering used for the
fingerprint payload.

**Global split (scientific vs execution).** `global` is split by meaning; the
definition fingerprint takes only the scientific members, and the execution
fingerprint takes the rest:

| Class | Members | Goes into |
|---|---|---|
| scientific | `charge`, `multiplicity`, `rmsd_threshold`, `energy_window`, `energy_tolerance`, `noH`, `freeze`, `ts_bond_atoms`, `ts_rescue_scan`, `scan_coarse_step`, `scan_fine_step`, `scan_uphill_limit`, `ts_bond_drift_threshold`, `ts_rmsd_threshold`, `auto_clean`, `force_consistency`, `keyword`, `iprog`, `itask`, `blocks` | definition fingerprint (A) |
| execution | `gaussian_path`, `orca_path`, `cores_per_task`, `total_memory`, `max_parallel_jobs`, `orca_maxcore`, `enable_dynamic_resources`, `delete_work_dir`, `stop_check_interval_seconds`, `sandbox_root`, `input_chk_dir`, `allowed_executables`, `gaussian_write_chk`, `max_wall_time_seconds`, `resume_from_backups` (deprecated/no-op) | execution fingerprint (C) |

Rationale: raising `max_parallel_jobs` or moving `gaussian_path` does not change
the *science* of the workflow, so it must not move the definition identity; it
*does* change what is executed, so it moves the execution identity. A theory
default (`keyword`/`iprog`/`itask`/`blocks`) is scientific, because changing the
default changes a step's effective theory unless the step overrides it (the
inherited value is already visible in the resolved step `params`, but binding the
default too keeps the split robust against resolver changes). R4 finalises the
exact membership from the live `GlobalOptions`; this table is the agreed baseline.

**Per-step execution-class params (frozen).** The resolved per-step `params` are
included in the definition fingerprint **except** parameters classified as
execution-class. The frozen per-step execution-class names are the
execution-class global members above, plus the confgen `workers` knob (the
step-level alias of the global `max_parallel_jobs`):

| Class | Per-step members | Effect |
|---|---|---|
| execution | `gaussian_path`, `orca_path`, `cores_per_task`, `total_memory`, `max_parallel_jobs`, `orca_maxcore`, `enable_dynamic_resources`, `delete_work_dir`, `stop_check_interval_seconds`, `sandbox_root`, `input_chk_dir`, `allowed_executables`, `gaussian_write_chk`, `max_wall_time_seconds`, `resume_from_backups` (deprecated/no-op), `workers` (confgen) | excluded from the definition fingerprint (A); bound into the R4 execution fingerprint (C) |
| scientific | every other resolved per-step param name | included in the definition fingerprint (A) |

Changing any execution-class step param — or omitting it versus supplying its
resolved default — therefore never moves the definition fingerprint; it only
moves the execution identity. A declared `checkpoint.from_step` is **not**
execution-class: it is definition configuration and stays in the payload (even
on a disabled calc, whose resolution checks are §12-exempt). This table is
normative; the implementation keeps exactly one authoritative constant derived
from it (`_EXECUTION_CLASS_STEP_PARAMS` in `canonical/fingerprint.py`) and no
second copy.

### 16.B Schema / canonicalization binding (provenance, not identity)

Answers: *"which schema and canonicalizer interpreted this document?"* Recorded
**separately** from the definition fingerprint:

- workflow schema identifier (`confflow.workflow.v3`);
- workflow schema digest (the `workflow_schema_sha256` of the V3 schema document);
- canonicalization version (the canonical-JSON algorithm version);
- producer identity/version, commit/dirty as appropriate.

This binding travels in the run/provenance record (and, later, the binding
document), never inside the definition fingerprint. A schema-document-only change
therefore changes **B** but not **A** (S22).

### 16.C Resolved execution fingerprint (R4)

Answers: *"what exactly will run, and can a prior result be resumed?"*

```
execution payload = definition semantic payload (A)
                  + run context (external input digests, input cardinality facts)
                  + the execution-class global members (see the A table)
                  + resolved resources / runtime / executable settings
```

### 16.D Resume

**Resume binds the execution fingerprint (C)**, because resuming must reject not
only a changed definition but also changed inputs/resources. Concretely:

- renaming a `label`, or reordering the array → **no** change to A or C → resume
  stays valid;
- changing a graph edge, a semantic param, a `checkpoint.from_step`, a semantic
  `extensions` entry, or an `id` (delete+create) → A (and therefore C) changes →
  resume is rejected;
- changing inputs/resources → C changes → resume is rejected (A unchanged).

V2's existing binding (`workflow_binding.v1`) and its fingerprint algorithm are
**unchanged** and continue to describe V2 documents. V3 gets its own binding
schema in **R4** (`workflow_binding.v2`); the V3 design must never force a change
to the V2 binding.

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

A recipe's step ids fall into exactly three cases:

- **A. omitted** — recommended for single-step starters. A partial parser may use
  **template-local generated handles** internally; the instantiate operation
  allocates real workflow ids.
- **B. explicit template-local ids** (e.g. `r_opt`, `r_freq`) — used only for
  references **inside** the recipe. `r_opt`/`r_freq` are legal id tokens but are
  **not** final workflow identities; instantiate rebases them all.
- **C. retention is forbidden** — a recipe must not assume its template-local ids
  survive. Instantiate **always rebases** by default. The only exception is an
  explicit **clone** operation (§20), which is a distinct verb from instantiate.

A recipe document must be **self-contained**: every `inputs` /
`checkpoint.from_step` reference must resolve **within the recipe**. A recipe that
references a step outside itself is not instantiable and is invalid as a recipe.
(The shipped `conformer_search` recipe is a single root step, so it is trivially
self-contained.)

### 19.3 Recipe instantiation — attachment semantics (normative)

Instantiation into an existing workflow needs an explicit rule for where the
recipe's **roots** connect. The operation is:

```
instantiate_recipe(recipe, attach_roots_to: list[step_id], mode: attach|starter)
```

1. **Rebase ids.** Allocate a fresh workflow id (§5.4 opaque family) for every
   recipe-local step; build an `old → new` map.
2. **Preserve internal edges.** Rewrite every intra-recipe `inputs` and
   `checkpoint.from_step` reference through the map. Internal topology is never
   altered.
3. **Find recipe roots.** A recipe root is a step whose `inputs` is empty *within
   the recipe*.
4. **Attach each root.** For **every** root:
   - `attach_roots_to == []` → `root.inputs = []` (roots stay roots);
   - otherwise → `root.inputs = attach_roots_to` (the **same** list for every root).
5. **Descendants unchanged** — non-root steps keep their (rebased) internal edges.
6. **Validate the resulting graph** — acyclic, references exist. Cardinality of
   the attached roots is checked by the ordinary run-context rule: if a root
   cannot accept the attached fan-in (e.g. a `calc` root receiving two inputs),
   the **result is a validation error**. Instantiation **never** rewrites the
   recipe's topology to "fix" this.
7. **Atomic.** The whole instantiation is one operation, so it is a single undo
   step and cannot leave a half-attached graph.

**Multi-root recipe.** If the recipe has several roots `A`, `B` joining at `C`:

- `attach_roots_to == []` → `A`, `B` remain roots (starter);
- `attach_roots_to == [X]` → `A.inputs = [X]`, `B.inputs = [X]`, and internal
  `C.inputs = [A, B]` is preserved: `X → A`, `X → B`, `A + B → C` (S24).

**Multi-source attachment.** `attach_roots_to == [X, Y]` gives **every** recipe
root `inputs = [X, Y]` (the same attachment set), so a multi-root recipe attaches
all its roots to all sources (S25). If a root cannot legally take the resulting
fan-in, validation reports it; the tool does not silently split the sources
between roots.

**Starter semantics.** From an empty workflow,
`instantiate_recipe(recipe, attach_roots_to=[])` leaves the recipe's roots as
roots.

**"Add workflow here".** With an existing `ConfGen` and a recipe `Opt → Freq`,
the user action *"add this recipe after ConfGen"* is
`instantiate_recipe(recipe, attach_roots_to=[<confgen id>])`, producing
`ConfGen → Opt → Freq` — worked through as before/recipe/after in
`docs/rfc/examples/workflow-v3-recipe-attach.yaml`. Placing the recipe
**independently alongside** the existing steps is the explicit
`attach_roots_to=[]` choice, which makes the recipe roots consume the **workflow
external input** — it is never the default.

`required_fields` (`confgen.chains`, `calc.program`, `calc.keyword`) and
`exposed_fields` are unchanged, and `build_recipe_catalog()` /
`recipe_catalog_sha256()` are untouched.

---

## 20. Copy / duplicate / template instantiation

| Operation | Copies | Allocates | Notes |
|---|---|---|---|
| **Duplicate step** | `type, params, enabled, checkpoint` | a **new opaque id** (§5.4) | `inputs` defaults to the original's `inputs` (branch alongside); never copies the id |
| **Instantiate recipe** | recipe steps | **fresh opaque ids** for every template-local id | attachment semantics in §19.3; intra-recipe refs rebased |
| **Clone workflow** | all steps | retains ids only when the **target is empty** and the user explicitly asks | the single exception to rebasing; not the default |

**Identity is never copied** by duplicate/instantiate. The only operation that may
retain ids is an explicit *clone into an empty document*.

**Duplicate label rule.** A duplicate of a step that has `label: L` gets
`label: "L (copy)"`. A duplicate of a step that has **no** label also has **no**
label (the tool does not invent one) — the two are then distinguished by id, and
the UI shows the id for unlabelled steps.

**Checkpoint on duplicate.** If `checkpoint.from_step` points to a step outside
the copied set (an external predecessor), the reference is **kept as-is** (the
ancestor relationship is preserved). A duplicate of the whole set that would make
`from_step` point **inside** the copied set is rebased in lockstep with the ids;
any resulting self-reference or cycle is rejected by validation (S12, §10).

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
| **Duplicate step S** | `X.id=alloc()`, `X.inputs=S.inputs` (same predecessors), `X.params=S.params`, `X.type=S.type`, `X.enabled=S.enabled`, `X.checkpoint=S.checkpoint`, `X.label = "S.label (copy)"` if S has a label else omitted |
| **Instantiate recipe here** | `instantiate_recipe(recipe, attach_roots_to=<selected ids>)` (§19.3): rebase all recipe ids, preserve internal edges, set every recipe root's `inputs` to `attach_roots_to`, validate, commit atomically |
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
| S11 | reorder serialization | S2 (`A→B→C`) array rewritten to `C,A,B` | unchanged | unchanged (graph from `inputs`) | ok | **no** |
| S12 | duplicate step | S2 + copy of s002 | **new opaque id** (e.g. `s_k7m2qvd`) | `inputs=[s001]` (same predecessors) | ok | yes |
| S13 | insert between | A→B becomes A→X→B | new opaque id | rewired | ok | yes |
| S14 | delete + reconnect | remove middle, reconnect | remaining ids | rewired | ok | yes |
| S15 | partial recipe | conformer_search | (no ids) | none | parseable, **not runnable** | n/a |
| S16 | V2 unnamed upgrade | V2 `{type: confgen}` steps | s001.. | linear → explicit | ok | new digest |
| S17 | V2 numeric chk upgrade | `chk_from_step: 2` | s00N | checkpoint ref | resolves to id | new digest |
| S18 | template twice | two copies of a 2-step recipe | fresh opaque ids per copy | two disjoint subgraphs | rebased, no collision | yes |
| S19 | delete highest migrated id then add | `s001,s002,s003` → delete `s003` → add | new step = **opaque** (`s_…`), never `s003` | ok | allocator collision-checks | yes |
| S20 | misspelled core param | `params: {methd: B3LYP}` | — | — | **runnable ERROR** (`params` strict §6.4) | n/a (invalid) |
| S21 | unknown namespaced semantic extension | `extensions: {org.example.x: …}` | — | — | parse **preserved**; **runnable ERROR** unless recognised (§6.3) | n/a (invalid to run) |
| S22 | schema-doc-only change | same semantics, schema `description` edited | unchanged | unchanged | ok | **A unchanged; binding B changes** (§16.B) |
| S23 | instantiate same recipe twice | two copies into one workflow | 2× fresh opaque ids | refs rebased per copy | all final ids unique | yes |
| S24 | multi-root recipe → one predecessor | recipe `A`,`B` roots join at `C`; attach `[X]` | fresh ids | `X→A`, `X→B`, `A+B→C` | per-root cardinality checked | yes |
| S25 | recipe → two predecessors | attach `[X,Y]` | fresh ids | every root `inputs=[X,Y]` | cardinality validated, **no** topology guessing | yes |

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
- `workflow-v3-recipe-attach.yaml` (§19.3 / "add workflow here": before, recipe, after)
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

The `params` member is a **placeholder** in the draft (`$defs/paramsDraft`): it only
asserts `params` is an object, because the production typed `params` enum per step
type is authored in R3.2. The draft is explicit that the **final** policy is
strict — an unknown core `params` member is a definition error (§6.4) — so the
placeholder must not be read as "unknown params are allowed".

All **runnable** example documents under `docs/rfc/examples/` validate against this
draft. Two examples do **not**, by construction, because they are not runnable
documents: `workflow-v2-to-v3-before.yaml` (V2) and the recipe fragment
`workflow-v3-recipe-partial.yaml` (fragment profile, `id` optional).
`workflow-v3-recipe-attach.yaml` is a three-document file (before / recipe /
after); its fragment document likewise belongs to the fragment profile and its
other two documents validate.

---

## 25. Decision table

| Topic | V2 | V3 decision | Why |
|---|---|---|---|
| Step identity | `name` (also label) | immutable workflow-local `id`, grammar `^[a-z][a-z0-9_]{0,63}$` | identity must survive rename/reorder |
| Step ID allocation | — | **V2 upgrade only** → sequential `sNNN`; **everything else** → collision-checked opaque `s_[a-z2-7]{8}` | deterministic migration + delete-safe edits |
| ID reuse | n/a | allocator must not intentionally reuse a removed id; only *current-document* uniqueness is enforceable | static docs cannot prove history |
| Display label | `name` | `label` (optional, may duplicate) | separate presentation from identity |
| Graph | implicit-linear OR explicit | **explicit only**; `inputs` required | remove hidden mode and order coupling |
| Root input | implicit or `inputs: []` | `inputs: []` | one reference kind, no magic token |
| Dependency target | name | step `id` | position/name independent |
| Checkpoint target | `params.chk_from_step` name/index | `checkpoint.from_step` id | distinct relation, stable target |
| Step order | sometimes semantic | presentation only; serializer preserves author order | deterministic, order-free execution |
| Disabled | runtime bypass | **declared pass-through** semantics | schema-level, cardinality on effective flow |
| Directory | name-derived | `steps/<id>/` | machine identity = id |
| Unknown core params | preserved as `extra` | **definition ERROR** (`params` strict) | a typo must not run silently |
| Semantic extension | unknown params | explicit namespaced `extensions` (fingerprinted) | fail-safe, namespaced |
| Unknown semantic extension | n/a | parse-preserved; **runnable ERROR** unless recognised | producer cannot guarantee unknown semantics |
| Non-semantic metadata | — | `annotations` (never fingerprinted) | legal home for GUI/comments |
| Schema digest | inside the v1 binding | **binding/provenance only**, not in the definition fingerprint | schema representation ≠ workflow semantics |
| Definition fingerprint | v1 (name-based, order-sensitive) | `semantics_version` + semantic payload by id; excludes label/order/annotations/schema digest | presentation must not affect identity |
| Execution fingerprint | v1 binding | definition semantic payload + run context (R4) | resume rejects real changes only |
| Type tokens | `calc/confgen/task/gen` | `calc/confgen` only | aliases only in V2 adapter |
| Recipe instantiation | n/a | rebase ids + **explicit `attach_roots_to`** | embedding into an existing DAG must be unambiguous |
| Recipes | partial V2 fragment | partial V3 fragment, self-contained, ids rebased on instantiate | parseable ≠ runnable preserved |

---

## 26. Rejected alternatives

| Alternative | Verdict | Why |
|---|---|---|
| Random UUID step ids | **rejected as default** | non-deterministic upgrade churns diffs; unreadable. Allowed only as a hand-written id if the charset permits. |
| **`max(existing suffix)+1` with "never reused"** | **rejected** | deletion permits reuse (`{s001,s002,s003}` − `s003` → `max+1 == 3` → `s003` again); the claim is unprovable from a static document. Superseded by two allocation families + collision-checked opaque ids (§5). |
| **Persistent high-water mark (`next_step_number`)** | rejected | allocator state becomes document state: merge-conflict surface, deletable metadata, no copy/paste safety (§5.7). |
| List-index step ids | rejected | exactly the V2 problem: reorder changes identity |
| `name` as id | rejected | rename = identity drift; label cannot be free |
| Implicit linear fallback in V3 | rejected | hidden order semantics; the root cause of fragile edits |
| Graph-canvas-specific schema (x/y fields) | rejected | layout is not semantics; belongs to the consumer/`annotations` |
| `$input` pseudo-node | rejected | second token namespace, typo-prone, needless |
| `inputs` scalar-or-list | rejected | two shapes to validate/serialize; fan-in consistency |
| Checkpoint merged into `inputs` | rejected | different relation; merging loses the ancestor rule |
| `label` affecting fingerprint | rejected | rename must be free |
| List order affecting execution | rejected | order must not be identity |
| **Unknown `params` member → warning** | **rejected** | a misspelled semantic parameter (`methd:`) would run silently; V3 makes unknown core params a hard error (§6.4). |
| **Schema digest inside the definition fingerprint** | **rejected** | a schema `description`/representation change would move workflow identity without any semantic change; schema is binding/provenance (§16.B). |
| **Recipe instantiation without explicit attachment** | **rejected** | embedding a self-contained recipe into an existing DAG is ambiguous (root vs attached); `attach_roots_to` makes it explicit and atomic (§19.3). |
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
| Execution engine | "Does array order still matter?" | No: waves are sorted by id; order is presentation (§9). |
| Resume/state | "Copy a step and resume the old results?" | Duplicate always allocates a new id; old id's state is not adopted (§20). |
| Source control | "Will upgrade churn the file every time?" | No: upgrade is deterministic sequential ids; later edits are opaque but assign-once; order preserved (§14). |
| **ID lifecycle** | "Delete `s003`, add a step — is `s003` reused?" | No: adds use the opaque family, never the sequential one; deletion never frees a number (§5.5, S19). |
| **ID lifecycle** | "Paste a step from another workflow — same id?" | Out of scope of the *document*; a paste is an **add** (fresh opaque id), never a raw id copy. |
| **ID lifecycle** | "Merge conflict on two new steps?" | Two allocators may both pick a token; the merge re-validates current-document uniqueness and the loser is reallocated. |
| **ID lifecycle** | "Run upgrade twice?" | Idempotent on the same source (S16); a document already in V3 is never re-numbered. |
| **ID lifecycle** | "Hand-wrote an id that was used before?" | Undetectable and not claimed; only current-document uniqueness is enforced (§5.6). |
| **Unknown fields** | "Typo in a core param?" | Hard error, not a warning (§6.4, S20). |
| **Unknown fields** | "Newer producer's semantic extension?" | Parse-preserved; runnable error unless this producer recognises the namespace (§6.3, S21). |
| **Unknown fields** | "GUI layout data?" | `annotations`, non-semantic, never fingerprinted (§6.3). |
| **Fingerprint** | "Rename a label?" | No change to definition or execution fingerprint (S10). |
| **Fingerprint** | "Reorder steps?" | No change (S11). |
| **Fingerprint** | "Edit only the schema `description`?" | Definition fingerprint (A) unchanged; binding (B) changes (S22). |
| **Fingerprint** | "Change a graph edge / param / extension / id?" | Definition fingerprint changes → resume rejected (§16.D). |
| **Fingerprint** | "Change inputs or resources?" | Execution fingerprint changes; definition unchanged (§16.C). |
| **Recipe** | "Root / multi-root / nested DAG?" | Internal topology preserved; only roots are attached (§19.3, S24). |
| **Recipe** | "Attach to two predecessors?" | Every root gets the same attachment set; cardinality validated, topology never guessed (S25). |
| **Recipe** | "Instantiate the same recipe twice?" | Two disjoint fresh id sets; collision check sees the first copy (S23). |
| Plugin/step type | "I need a step type ConfFlow does not know." | Unknown `type` is rejected; semantics go in namespaced `extensions`. A plugin axis is R6+. |
| Security/fail-safe | "Unknown extension could change results silently." | `extensions` participates in the fingerprint **and** an unrecognised namespace is a runnable error, so nothing semantic is silently ignored (§6.3). |

---

## 31. Open-question gate (must all be answered; **no TBD remains**)

1. V3 step identity — a workflow-local immutable `id` string (§5).
2. Legal id grammar — `^[a-z][a-z0-9_]{0,63}$` (§5.3).
3. Exact new-step allocator — opaque `^s_[a-z2-7]{8}$`, collision-checked, bounded retry (§5.4–§5.5).
4. V2 upgrade id allocator — sequential `^s[0-9]{3,}$`, document order, the only sequential producer (§5.4, §14).
5. Delete-then-add / id reuse contract — adds never reuse a deleted id; only current-document uniqueness is enforceable (§5.6, S19).
6. Who generates ids / runnable requirement — author or mutating tool; `id` required to run, never auto-generated by the parser for a runnable document (§5.3, §6.2).
7. Unknown core `params` member — definition ERROR (§6.4).
8. Semantic-extension mechanism — namespaced `extensions`, fingerprinted (§6.3).
9. Unknown extension runnable behaviour — parse-preserved; runnable ERROR unless recognised; `annotations` is the non-semantic bucket (§6.3).
10. Canonical semantics version — `confflow.workflow-semantics.v3`, a canonical-layer constant (§15, §16.A).
11. Schema digest in the definition fingerprint — **no**; binding/provenance only (§16.B).
12. Definition fingerprint content — semantics_version + canonical semantic payload by id; excludes label/order/annotations/schema digest/source version (§16.A).
13. Execution fingerprint — definition payload + run context (R4) (§16.C).
14. Can `label` duplicate — yes (§6).
15. Does renaming `label` affect execution identity — no (§16.D, §17).
16. Does step array order have semantics — no; serializer preserves author order; fingerprint canonical-sorts by id (§9).
17. Root input — `inputs: []` (§8).
18. `inputs` references — step ids (§10).
19. `checkpoint` references — `checkpoint.from_step`, a calc ancestor id (§11).
20. Disabled semantics — declared pass-through bypass; effective-dataflow cardinality (R6) (§12).
21. Duplicate id allocation — always a new opaque id; label rule and checkpoint rule defined (§20).
22. Recipe attachment semantics — `instantiate_recipe(recipe, attach_roots_to)` (§19.3).
23. Multi-root recipe attachment — every root receives `attach_roots_to` (§19.3, S24).
24. Multi-source attachment — every root receives the same `[X, Y]` set; cardinality validated, no topology guessing (§19.3, S25).
25. Recipe ×2 collision — fresh ids per copy; collision check spans the whole document (§19.3, S23).
26. Old V2 state — stays V2; no eager migration (§17, §27).

---

## 32. Out of scope for this RFC

Implementation of V3 runtime, state v2, binding v2, structured calc, plugin step
types, JobDesk changes, and remote-protocol changes. This RFC is a target
specification only.
