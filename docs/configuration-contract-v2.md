# Configuration contract v2 — producer/consumer compatibility matrix

**Status:** implemented in this branch. This document is the *producer-side* record
of what the JobDesk GUI V2 consumer actually accepts. It was written by reading the
consumer's own strict parser, not from a design brief.

Source of truth for the consumer side (read-only reference, JobDesk V2 commit
`6846b51443e6501d3d11d0e76d5f762349c1ae03`):

| File | Why it is authoritative |
| --- | --- |
| `src/jobdesk_v2/application/editor/contract/parse.py` | the strict byte → verified-value pipeline; every refusal code |
| `src/jobdesk_v2/application/editor/contract/models.py` | how `level`, `source` and `capabilities` are *derived* |
| `src/jobdesk_v2/application/editor/manifest.py` | the accepted field-descriptor vocabulary |
| `src/jobdesk_v2/application/editor/recipes.py` | the accepted recipe vocabulary |
| `tests/fixtures/contract/producer_v2.json` | a level-C document the consumer already accepts |
| `tests/application/test_contract_parsing.py` | the assertions a real producer must satisfy |

Where this document and the JobDesk proposal
(`docs/confflow-contract-v2-proposal.md` in JobDesk) disagree, **the parser and its
tests win** — they are the consumer's actual ABI.

---

## 1. Ownership

| Fact | Owner |
| --- | --- |
| which fields exist, what they are addressed at, what values are legal | ConfFlow (this branch) |
| what ConfFlow itself does when a member is absent (`default`) | ConfFlow |
| how a field looks: label, description, group, order, choice label | either; V2 renders, ConfFlow supplies hints |
| `level` (A/B/C) | **derived by the consumer** from artifact provenance — never published |
| provenance (`producer`, commit, dirty) | the contract **envelope** only, never inside an artifact |

Rule C4, already agreed with the consumer: provenance lives on the envelope. Neither
`editor_manifest` nor `recipe_catalog` carries a `producer` or `contract_key` member,
so each artifact's digest is a function of its content and nothing else.

---

## 2. The canonical hashing authority

There is exactly one canonicalisation in ConfFlow:

```python
# confflow/config/canonical/serialization.py
canonical_json(value)  = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False)
canonical_sha256(value) = sha256(canonical_json(value).encode("utf-8")).hexdigest()
```

`workflow_schema_sha256`, `editor_manifest_sha256` and `recipe_catalog_sha256` all go
through `canonical_sha256`. The consumer mirrors this algorithm byte for byte, so a
digest computed here verifies there.

Two deliberate non-changes (both already recorded as conflicts on the consumer side,
neither belongs in this change):

* `rfc8785` is declared as a dependency but **not** used for canonicalisation.
  Switching to it would be a wire-compatibility change; it is left alone. (C9)
* `workflow_schema_sha256` is redundant with the embedded `workflow_schema`, but the
  consumer's verification chain depends on the digest *claim* existing, so it stays. (C11)

---

## 3. Compatibility matrix — the envelope

`consumer expected member → producer source of truth → producer output path → validation rule`

| Consumer member | Producer source of truth | Producer output path | Validation rule the consumer applies |
| --- | --- | --- | --- |
| `schema` | `CONFIGURATION_CONTRACT_V2_SCHEMA` (`config/canonical/contract.py`) | top level | must be one of `confflow.configuration-contract.v1` / `.v2` |
| `workflow_schema_version` | `WORKFLOW_SCHEMA_VERSION` (`config/canonical/schema.py`) | top level | non-empty **and** `confflow.workflow.v2` |
| `workflow_schema_sha256` | `workflow_schema_sha256()` | top level | `canonical_sha256(workflow_schema) == claim` (case-insensitive) |
| `workflow_schema` | `workflow_json_schema()` (deep copy) | top level | must be a JSON object |
| `editor_manifest` | `build_editor_manifest()` (deep copy) | top level | must be an object; **its own** `schema` must be `confflow.editor-manifest.v1` |
| `editor_manifest_sha256` | `editor_manifest_sha256()` | top level | `canonical_sha256(editor_manifest) == claim`; a missing/blank claim is refused before hashing |
| `editor_manifest_version` | *(not published)* | — | the consumer reads the version from `editor_manifest["schema"]`, not from a top-level member |
| `recipe_catalog` | `build_recipe_catalog()` (deep copy) | top level | must be an object; **its own** `schema` must be `confflow.recipe-catalog.v1` |
| `recipe_catalog_sha256` | `recipe_catalog_sha256()` | top level | `canonical_sha256(recipe_catalog) == claim` |
| `recipe_catalog_version` | *(not published)* | — | read from `recipe_catalog["schema"]` |
| `producer.package` | literal `"confflow"` | `producer.package` | the `producer` object is retained whole; its key set is what the consumer exposes |
| `producer.version` | `confflow.__version__` | `producer.version` | drives the consumer's status line (`"Using ConfFlow contract 2.1.6"`) |
| `producer.commit` | `confflow.__build__.COMMIT` | `producer.commit` | may be `null`; the key must be present |
| `producer.dirty` | `confflow.__build__.DIRTY` | `producer.dirty` | may be `null`; the key must be present |
| `validation_response_schema` | `CONFIGURATION_VALIDATION_SCHEMA` | top level | informational; unchanged from v1 |
| `level` | *(not published — derived)* | — | derived from which artifacts the producer declared: A = schema only, B = schema + manifest, C = schema + manifest + catalog |

`producer` carries exactly four keys. The consumer asserts that key set for its own
fixture (`test_the_gui_never_receives_a_raw_producer_mapping`), so adding a member
here would be a visible contract change, not a harmless extra.

---

## 4. Compatibility matrix — the editor manifest

Envelope members of the manifest document itself:

| Member | Producer source of truth |
| --- | --- |
| `schema` | `EDITOR_MANIFEST_SCHEMA = "confflow.editor-manifest.v1"` |
| `workflow_schema_version` | `WORKFLOW_SCHEMA_VERSION` — **must equal the contract's**, or the consumer restricts editing and says why |
| `step_contexts` | `{"calc": "calc", "confgen": "confgen"}` |
| `fields` | `build_editor_manifest()["fields"]` |

Per-field members, with the consumer's rule for each:

| Field member | Required | Consumer rule |
| --- | --- | --- |
| `field_id` | yes | unique; must be `"<context>.<name>"` and its prefix must equal `context` |
| `context` | yes | one of `global`, `calc`, `confgen` |
| `json_pointer` | yes | must start with `/`; step-scoped fields use the `{index}` placeholder |
| `label` | yes | non-empty (falls back to `field_id` if absent) |
| `description` | no | free text |
| `value_type` | no | `integer` / `number` / `string` / `boolean` / `array` |
| `editor` | no | `select`, `text`, `multiline`, `integer`, `number`, `boolean`, `memory`, `atom_pair`, `string_list` — **no new kinds**, and never a widget class |
| `group` | no | presentation grouping key |
| `level` | no | `basic` or `advanced` (progressive disclosure) |
| `order` | no | integers; fields are sorted by `(order, field_id)` |
| `choices` | select only | required when `editor == "select"`; **forbidden otherwise**; each entry is `{"value", "label"}` |
| `visible_when` | no | leaf `{"field", "equals"|"not_equals"|"in"}` or `{"all"|"any": [...]}`; every referenced field must exist in the same manifest |
| `inherit_from` | no | must name an existing **global** field; forbidden together with `read_only` |
| `default` | no | the producer's own default; see §6 |
| `read_only` | no | forbidden together with `inherit_from` |
| `item_type` | no | `string` or `integer`; **only** valid when `editor == "string_list"` |

---

## 5. Compatibility matrix — the recipe catalog

| Member | Required | Consumer rule |
| --- | --- | --- |
| `schema` | yes | `confflow.recipe-catalog.v1` |
| `label` | no | catalog title |
| `recipes[].id` | yes | non-empty and **unique** across the catalog (a duplicate is a refusal, not a warning) |
| `recipes[].label` | yes | non-empty |
| `recipes[].description` | no | free text |
| `recipes[].category` | no | defaults to `Uncategorised` |
| `recipes[].order` | no | sorted by `(order, id)` |
| `recipes[].document` | yes | must be a mapping that parses as a workflow document; **must contain `global`** and a non-empty `steps` list |
| `recipes[].required_fields` | no | manifest `field_id`s |
| `recipes[].exposed_fields` | no | manifest `field_id`s; must not repeat within one recipe |

A `required_fields`/`exposed_fields` entry the manifest does not describe is a
**warning** on the contract (`contract.recipe_field_unknown`), not a refusal: an
over-specified recipe degrades that recipe only. A real producer should publish none.

---

## 6. Defaults: what "producer owns the default" means on the wire

The consumer must be able to report four origins for a field: `explicit`,
`inherited`, `default`, `unset`. `default` is the one that carries producer meaning.

Rules this branch follows:

1. **A default is derived, never copied.** Every `default` in the manifest is read
   from a ConfFlow constant (`confflow.shared.defaults`,
   `confflow.shared.confgen_params`) or from the typed model that already owns the
   behaviour. See §7 for the field-by-field sources.
2. **A default is never written into a workflow document.** A recipe that pins
   `itask: opt_freq` does not also pin `iprog: orca`, and does not pin
   `cores_per_task: 1`. The producer's defaults stay in the producer.
3. **Changing a producer default must not change any document.** The consumer has a
   regression test for exactly this: flipping the manifest default for
   `calc.program` from `orca` to `g16` changes `editor_manifest_sha256` (and so the
   contract key) while leaving the serialised workflow byte-identical, reporting
   `FieldState(explicit_value=None, effective_value="g16", origin="default")`.

Consequence for this branch: the manifest must **not** bake the dataclass default
into the document, and the dataclass default must not live in two places. See §8.

---

## 7. Field-by-field provenance of every published default

| Field | Default | Where it comes from |
| --- | --- | --- |
| `global.charge` | `0` | `shared.defaults.DEFAULT_CHARGE` |
| `global.multiplicity` | `1` | `shared.defaults.DEFAULT_MULTIPLICITY` |
| `global.cores_per_task` | `1` | `shared.defaults.DEFAULT_CORES_PER_TASK` |
| `global.total_memory` | `"4GB"` | `shared.defaults.DEFAULT_TOTAL_MEMORY` |
| `global.max_parallel_jobs` | `1` | `shared.defaults.DEFAULT_MAX_PARALLEL_JOBS` |
| `global.freeze` | *(none)* | no default: absent means "no atoms frozen" |
| `global.energy_window` | *(none)* | `GlobalOptions.energy_window = None` |
| `global.scan_coarse_step` | `0.1` | `shared.defaults.DEFAULT_SCAN_COARSE_STEP` |
| `global.ts_bond_drift_threshold` | `0.4` | `shared.defaults.DEFAULT_TS_BOND_DRIFT_THRESHOLD` |
| `global.gaussian_path` | *(none)* | read-only; resolved by the compute profile |
| `global.orca_path` | *(none)* | read-only; resolved by the compute profile |
| `calc.program` | `"orca"` | `shared.defaults.DEFAULT_PROGRAM` (hoisted in this branch — see §8) |
| `calc.task` | `"opt_freq"` | `shared.defaults.DEFAULT_TASK` (hoisted in this branch) |
| `calc.keyword` | *(none)* | `CalcStepParams.from_params` requires it; there is no default |
| `calc.cores_per_task` | *(none)* | inherits `global.cores_per_task` |
| `calc.total_memory` | *(none)* | inherits `global.total_memory` |
| `calc.max_parallel_jobs` | *(none)* | inherits `global.max_parallel_jobs` |
| `calc.orca_maxcore` | *(none)* | computed by ConfFlow unless set |
| `calc.energy_window` | *(none)* | inherits `global.energy_window` |
| `calc.ts_bond_atoms` | *(none)* | falls back to the first two frozen atoms |
| `calc.ts_rescue_scan` | `false` | `shared.defaults.DEFAULT_TS_RESCUE_SCAN` |
| `calc.scan_coarse_step` | *(none)* | inherits `global.scan_coarse_step` |
| `calc.ts_bond_drift_threshold` | *(none)* | inherits `global.ts_bond_drift_threshold` |
| `calc.blocks` | *(none)* | free-form extra input |
| `confgen.chains` | *(none)* | required by the confgen step |
| `confgen.angle_step` | `120` | `shared.confgen_params.DEFAULT_CONFGEN_ANGLE_STEP` |
| `confgen.bond_multiplier` | `1.15` | `shared.confgen_params.DEFAULT_CONFGEN_BOND_THRESHOLD` |

Choice sets are derived from the typed literals, so they cannot drift:

| Field | Choices | Source |
| --- | --- | --- |
| `calc.program` | `g16`, `orca` | `typing.get_args(ProgramName)` |
| `calc.task` | `opt`, `sp`, `freq`, `opt_freq`, `ts` | `typing.get_args(TaskName)` |

---

## 8. Known discrepancies carried by this branch

| # | Discrepancy | Effect | Handling |
| --- | --- | --- | --- |
| C14 | The consumer addresses the confgen bond-detection parameter as `confgen.bond_multiplier` → `/steps/{index}/params/bond_multiplier`, but ConfFlow's **canonical** key is `bond_threshold` (`bond_multiplier` is a legacy alias in `CONFGEN_ALIAS_GROUPS`). | The field id and the pointer are the consumer's interface and are already in use by the shipped fallback manifest, so this branch publishes them unchanged rather than renaming an interface. A value written to `bond_multiplier` is accepted by `resolve_confgen_params`, so documents stay valid. | Left as-is for ABI stability; flagged for the maintainer to decide whether the canonical key should win in a future version bump. |
| C15 | `GlobalOptions.iprog` / `.itask` were hardcoded literals in the dataclass while every other option default came from `shared.defaults`. | A producer-owned manifest publishing `calc.program`'s default would have been a **third** copy (manifest, dataclass, `from_mapping`). | Fixed in this branch: `DEFAULT_PROGRAM` / `DEFAULT_TASK` now live in `shared.defaults` and the dataclass reads them. Behaviour identical; regression test added. |

---

## 9. Consumer behaviour for every failure mode

Recorded so the producer can test against the real rules. Every failure below is a
*structured* outcome — the consumer never lets an exception reach the GUI, and it
degrades to its bundled snapshot while keeping the reason.

| Situation | Consumer outcome | Code |
| --- | --- | --- |
| payload is not valid UTF-8 | refused | `contract.parse_failed` |
| payload is not valid JSON, or is not an object | refused | `contract.parse_failed` |
| `schema` is neither v1 nor v2 | refused | `contract.unsupported_version` |
| `workflow_schema_version` missing | refused | `contract.workflow_schema_missing` |
| `workflow_schema_version` unknown | refused | `contract.workflow_schema_unsupported` |
| `workflow_schema` not an object | refused | `contract.parse_failed` |
| digest **absent or blank** | refused — "cannot be verified" | `contract.parse_failed` |
| digest **wrong** | refused — "changed after it was signed" | `contract.hash_mismatch` |
| `editor_manifest.schema` unsupported | refused | `contract.editor_manifest_version_unsupported` |
| `recipe_catalog.schema` unsupported | refused | `contract.recipe_catalog_version_unsupported` |
| duplicate recipe id | refused | `contract.parse_failed` |
| recipe names an unknown field | **warning**, contract still level C, editing still allowed | `contract.recipe_field_unknown` |
| manifest targets a different workflow schema | **warning** + capabilities restricted (editing disabled, Run disabled, reason shown) | `contract.workflow_schema_version_mismatch` |
| no `editor_manifest` member | falls back for the field model; level A or B | `contract.editor_manifest_missing` (info) |
| no `recipe_catalog` member | falls back for recipes; level A or B | `contract.recipe_catalog_missing` (info) |

**Level semantics.** A = schema only (the producer as it was). B = schema + manifest
(editable fields, recipes still from the snapshot). C = schema + manifest + catalog
(nothing is taken from the snapshot). This branch emits C.

---

## 10. v1 compatibility guarantee

`confflow config contract --json` continues to emit `confflow.configuration-contract.v1`
with byte-identical output and the same exit code. v2 is **opt-in** via
`--version 2`. The v1 shape is frozen by a regression test that compares the CLI's
actual bytes against a literal expectation, and `build_configuration_contract` still
means v1.

Going the other way — a consumer that does not understand v2 — is safe by
construction: the v2-only members are additive, and the consumer's parser ignores
unknown top-level members.
