# Producer Intent (confflow.intent.v1)

Simplified authoring that compiles to the existing strict V4 wire.  There
is no second runtime: every intent becomes a strict V4 document verified
by the real `parse_workflow_document` + `compile_workflow`.

Entry points (`confflow.producer.intent`):

- `compile_intent(document=None, *, intent=None, machine_profile=None,
  registry=None) -> dict` — intent v1 or legacy V4 in, strict V4 out.
  Failures raise `IntentCompilationError` (a `ValueError`, carrying
  `step_id`/`field_path`/`diagnostics`).
- `intent_catalog() -> dict` — pure card/preset constants plus the
  accepted `schema_keys`/`step_keys`, default seed policy, `from`
  semantics, `supported_recipes`, and configuration doc identifiers.
  Safe to call from `build_configuration_contract_v4` (no
  compiler/contract recursion).

## Intent schema

Top level: `schema: confflow.intent.v1` (required), `inputs` (optional,
default `structures` many/each_entity, declarations passthrough verbatim
including `topology` patch fields — no guessed chemistry),
`globals` (`charge`/`multiplicity`/`freeze` -> scientific defaults;
`freeze: []` is a valid explicit clear), `recipe` (optional reviewed
`producer.recipes` id as the base chain), `cards` (optional reusable
templates, see below), `role_cards` (optional recipe role mapping, see
below), `recipe_cards` (optional `tspes` normal mode
`{low_level, single_point}`, see below),
`steps` (step intents; required unless a recipe supplies the chain).

Step: `card` (`<type>@v1`, required; mapping refs reject unknown keys),
`id` (optional; mechanical `{cardtype}_{occurrence}` otherwise, explicit
values preserved), `role` (optional advanced override preserved verbatim
into `CalculationModel.role`; the card fixes the reviewed default and
downstream lanes may consume role scientifically, so overriding is
advanced — native keywords are never inspected), `program` (required for
calculation cards; resolved via `programs.registry`), `native`
(required non-empty mapping for calculation/confgen cards; verbatim),
`resources` (scientific per-item), `scheduler` (operational, excluded
from seed/digest), `overrides`/`seed`/`adapter`/`profile`/`checks`/
`check_params`/`recovery`/`recovery_params` (explicit wins over card
defaults), `preset` (transform cards), `from` (semantic predecessor
`<step-id>` / `run:<input>`; default = linear predecessor), `bindings`
(explicit V4 wire; a non-mapping value is rejected, never autowired),
`reuse_checkpoint` (`{step, mode: checkpoint|readfc|rcfc,
allow_method_change?}` — explicit only, strict boolean, wired late via
`producer.checkpoints`), `execution`/`label`.

Non-applicable fields are rejected, never silently dropped (a seed or
program on a transform, a program/role/adapter/profile/checks/recovery
on a confgen, a preset on a calculation).

Cards v1: `opt, sp, freq, opt_freq, ts, ts_freq, irc, goat, qst2, qst3, neb,
confgen, refine, deduplicate`.  Plain `ts`/`qst2`/`qst3` legs expect a
produced geometry (`geometry_required`); only the `*_freq` cards expect
frequency output (`ts_freq` carries `imaginary_frequency_count`
expected 1).  `opt_freq` expects geometry plus frequencies.  Unknown
card/version, unknown program, or bad native override fails closed.

## Recipe mode

`"recipe"` selects a reviewed catalog document as the base chain and
requires explicit `program` + `native` assignments (matched by step id)
for every base calculation step — demo recipe science never leaks into
production and keywords are never transformed from method text.  Missing
or incomplete assignments are rejected.  Analysis steps and native
edges are preserved untouched.  Recipe intents never inherit the
catalog neutral `charge 0 / multiplicity 1` fallback: declare explicit
`globals` (or typed per-input charge/multiplicity) instead.

## Reusable cards + recipe role cards

`cards: {<name>: {card: '<type>@v1', program, native | native_by_role,
resources, ...}}` fixes reusable science once.  A step with a bare
`card: '<name>'` expands the template mechanically (explicit step fields
win; `native` replaces wholesale, never merges); inline `'<type>@v1'`
still works.  Unknown refs, bad types/versions, and chained cycles fail
closed.  A family card uses `native_by_role: {<role>: <native>}` to map
scientific roles to verbatim natives with no keyword editing.

`role_cards: {<role-or-step-id>: '<card-name>'}` maps each reviewed
calculation role (or step id, which wins) onto a reusable card.  Family
variants select the step-id key first, then the role key — so `ts_freq`
gets the `ts_freq` variant/purpose even through the `freq` role key —
and purpose-card defaults (adapter/profile/checks/check_params/recovery)
replace catalog demo values per stage.  Plain-card role mismatches are
refused unless mapped by explicit step id.

`recipe_cards: {low_level: '<family>', single_point: '<sp>'}` (currently
`tspes` only) generates that role mapping mechanically (`ts`/`freq`/`opt`/
`irc` from the low-level family, `sp` shared twice from the single-point
card).  Explicit `role_cards` still win on conflicts.  Provenance records
`named_card`, `role_card`, `family_variant`, and `purpose` alongside the
existing keys (never replacing them).

## Linear bindings (Phase 4)

Omitted `bindings` wire the linear predecessor: head <- the (unique)
default run structure input; step N <- step N-1 `structures`.  `from`
overrides for branches.  Named cards (`qst2`/`qst3`/`neb`) always need
explicit bindings.  Zero/multiple candidates fail instead of guessing.

## Seeds (Phase 2)

`derive_seed(step_id, wire_document) -> int` (public two-argument form):
SHA-256 over JCS canonical bytes of the whole seed-free scientific
workflow identity — all steps' program/native/checks/overrides,
normalized per-item resources (memory folds through the real
`domain.resources.parse_memory_bytes`, so `1GiB == 1024MiB` and omitted
dimensions equal explicit schema defaults), bindings, declared input
identity/topology, global defaults, assigned step ids.  Excluded: all
seeds, scheduler, machine/execution, annotations, labels, input
descriptions, output paths.  Explicit seeds are preserved verbatim and
audited as `explicit` — even on steps that are not stochastically
required (e.g. an uncapped typed ConfGen carrying a user seed);
full-enumeration typed ConfGen without a user seed gets none; legacy/capped ConfGen and GOAT
get derived-or-explicit seeds in strict `confgen.seed` /
`calculation.seed`.  Seeds assign after machine and checkpoint updates,
so checkpoint edges move seeds.  GOAT seeds are workflow identity only
(`seed_scope: workflow_identity_only`) — never injected into
`native.goat` (ORCA `RANDOMSEED` is boolean; installed ORCA 6.1.1 has no
integer RNG seed), so no native determinism is promised.

## Presets (Phase 7)

`refine_default@v1` pins `{rmsd_threshold_angstrom: 0.25, bond_scale:
1.2, heavy_only: False}`; `refine_strict@v1` pins `{0.1, 1.2, False}`;
`dedup_default@v1` pins `{}` (deduplicate takes no native parameters).
A preset whose card does not exactly match the step card is rejected, as
are unknown refine native keys.  No `energy_window` member exists (V4
transforms carry structures only).

## Machine + checkpoints (lanes)

`machine_profile={name,total_cores,total_memory,executable,env,
remote_target,...}` resolves per-step via late
`producer.machine.resolve_machine_resources`; the resolver's canonical
`provenance["operational"]` record drives a real `step.execution`
projection restricted to allowed `ExecutionModel` members
(`binding_id` — explicit step value wins — per-program `executable`
selection, merged `env` with explicit keys winning, canonical `target`,
`sandbox`, `allowed_executables`, `walltime_seconds`).  Missing helper +
non-None profile fails closed.  Machine-resolved schema-default
resources keep seeds and definition digests stable.  `reuse_checkpoint`
calls late `producer.checkpoints.wire_checkpoint_reuse` with the exact
signature after private keys are stripped (no blind `TypeError` retry
that could swallow an explicit science flag).

## Legacy

Strict V4 documents pass through deep-equal and unchanged; a legacy
document that cannot strictly compile (e.g. stochastic step without a
seed) raises `IntentCompilationError`.
