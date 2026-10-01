# ConfGen v3 worker interface freeze

Shared interface owner: core worker. Other workers must not edit shared/core modules. Use Python dataclasses and stdlib/NumPy/SciPy; existing StructureRecord and fixed indices remain authority. Keep science independent of orchestration/legacy imports.

## Core API

`ConfgenStateKey(schema_version=3, coordination=None, rings={}, torsions={})`: labeled JSON-compatible state values; `to_dict()`. Orbit identities are separate. Maps immutable or defensively copied. State identity never includes sampling/tolerances/config.

`MolecularContext`: input StructureRecord, typed graph, resolved spec, tolerances, input_state_key. Construction via `build_context(structure, spec, input_state_key=None)`; typed graph supplied by explicit spec or conservative legacy covalent perception. Explicit topology wins. Context immutable scientific authority. Atom indices internal zero-based; workflow inputs document and validate convention.

`WorkingRealization(structure, state_key, parent_realization_id=None, generation_axis=None, locked_axes=(), provenance={})`.

`GenerationTarget`: axis, target_id, state_value, ordinal, provenance. Stage-specific state_value JSON-compatible. Engine combines state_value into complete labeled key.

`StageEstimate`: declared_count, upper_bound, exact, details.

`RealizationResult`: structure or None, status, reason, backend, evidence. Stage implementation reports numerical/geometric failure explicitly; never silently labels drift success.

`PerceptionResult`: best_key (stage-local JSON state or complete key when aggregated), alternatives, confidence, margins, boundary_flags. Ambiguity prohibits suppression/publication of a claimed unambiguous state.

`GenerationStage`: estimate(parent, context), enumerate_targets(parent, context), realize(parent, target, context), perceive(structure, context). Coordinator/core owns full parent audit after each stage; stages support their own local audit. Constructors accept resolved axis spec. Stage enumeration preserves stable order and performs no geometry generation.

`ConfgenEngine.run(context, should_cancel=None)` returns output leaf realizations plus report/certificates and compressed/streamable target records. Explicit unsupported requested generation fails closed. Partial geometry failures stay accounted and may publish audited successes. Zero axes produces one audited preserve-input leaf if explicitly allowed.

## Scope specifications

Internal normalized spec is a JSON-compatible mapping; workflow typed models serialize into it. Top-level: schema_version=3, coordination, rings, torsions, topology, stereochemistry, exclusions, tolerances, limits, sampling, seed. Unknown keys fail closed at typed boundaries.

Coordination: metal_center (0-based), binding_sites [{id,kind='atom',atoms:[index],hapticity=1}], shapes ('auto' or list registered names), treatment, constraints (typed with provenance), automorphism witnesses optional. Multi-metal/hapticity unsupported fail closed. Shape groups contain proper rotations only. Donor configuration preserve_input explicitly reported.

Rings: [{id,atoms:[ordered indices],templates:[names],treatment}]. Support isolated covalent 4/5/6; overlap, fused/bridged/chelate unsupported explicit. Stable anchors/order, substituent frame, parity and integrity audit required.

Torsions: [{id,atoms:[four indices] or bond:[two indices],model='relative_rotation_grid'|'absolute_dihedral_grid'|'chemical',angles or states,treatment,rotate_side}]. Relative grids reference original input and rotation sign. Legacy adapter preserves full-grid geometry/ordinals. Duplicate bond axes fail closed.

Limits: max_declared_states, max_output_structures; exact requested enumeration rejects preflight oversize before realization. Sampling: cap counts sampled leaf targets, seeded whole-index-space without replacement, stable sorted ordinal presentation, aggregated deferred ranges; conditional tree weights required for global uniform sampling. No first-N truncation. Finite integer validation.

## Workers

Core creates shared API first, then writes `/tmp/confgen-v3-workers/core-api-ready.md` with exact imports/signatures. Coordination/ring implement their own pure APIs immediately, then adapters after reading ready note. They can request clarification through `/tmp/confgen-v3-workers/{coordination,ring}-interface-requests.md`; core reviews these. Do not modify another lane's files or expectations. Each worker writes progress and final summaries in its own /tmp note, including test commands/results and material limitations.

Acceptance is scientific, not LOC-based. No fake exact proof, fake stereo verification, or fake H_geom. No hardcoded TS1 output assignments in production; fixtures only in tests. Existing SCINE structures audit but do not substitute for generated targets. Geometry solver is geometric, not energy optimization.
