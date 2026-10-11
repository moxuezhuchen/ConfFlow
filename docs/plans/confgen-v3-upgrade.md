# ConfGen v3 upgrade execution plan

> superseded 2026-10-11: ConfGen now has a single DG search engine (schema_version 4); the ring, torsion, path and coordination-realization engines and their gates described here no longer exist.

Status: implementation, independent-review fixes and final scoped validation completed locally (2026-10-01). See `confgen-v3-validation.md` for current evidence and explicit limits.

## Authority and delivery scope

Implement the user's unified seed generator baseline: Declare → Enumerate → Realize → Perceive → Account, fixed Coordination → Ring → Torsion order, leaf-only publication, immutable input atom indices. No energy search or optimization. Complete P0–P3 and producer contract support for P4; the separate JobDesk checkout must be located before any GUI edits are claimed.

The coordinating agent owns interface freeze, scientific acceptance, integration, and final validation. Parallel implementation runs use OpenCode with the user-requested Muse Spark 1.3 model. Do not silently substitute a different model. Workers own disjoint paths and must report tests, unresolved capabilities, and changed interfaces. No worker commits, pushes, modifies golden expectations to fit production output, or alters frozen output identity authority.

## Golden inventory and limitations

Fixture directory: `tests/fixtures/confgen/coordination/ts1/`.
Original supplied ZIP remains untouched. All 19 manifest entries pass size/SHA256 verification; bundled self-check passes.

- 122 indexed atoms; original TS1 and SCINE F1–F6 geometries.
- Explicit COVALENT / COORDINATION / FORMING topology; O74–C79 remains FORMING.
- Raw assignments 720; policy-admissible raw assignments 288; shape-rotation classes 30 before policy and 12 after policy; topology sigma orbits 6.
- All four forbidden-trans constraints are REJECTED_BY_POLICY, never automatic infeasibility proofs.
- Sigma is element/typed-edge preserving and involutive. Stereo/properness and H_geom remain independently unverified.
- Original input must retain 12 realization targets unless independently verified geometry witnesses authorize suppression.
- Missing goldens to construct: stereo-labelled positive/negative witnesses, C2 geometry, CN4/CN5 monodentate cases, bidentate cases, isolated 4/5/6 rings, combined axis fixture.

## Frozen semantic decisions

1. Labeled StateKey expresses indexed physical state. Canonical orbit identity is separate. Realization target ID and ordinal are separate from orbit key.
2. StateKey contains only state identity. Scope includes treatments, topology/stereo declarations, templates, tolerances, budgets, and sampling. Certificates carry resolved declarations and digests.
3. Count layers are explicit: raw assignments, policy/proof exclusions, shape classes, molecular orbits, realization targets, published leaves.
4. Burnside applies only to an invariant set under a complete declared group; incomplete automorphism search cannot certify complete symmetry accounting.
5. Symbolic enumeration is independent of realized geometry wherever possible. Preflight reports exact counts or conservative upper bounds with basis. No unknown count masquerades as exact.
6. Upstream failed realization leaves descendants explicitly DEFERRED with parent reason; subtree/range accounting avoids expanding all deferred leaves.
7. Each target has one terminal status. REALIZED_VIA_DRIFT and out-of-scope discoveries are evidence/events, not double-counted target terminal statuses.
8. Relative legacy rotation grids differ from absolute four-atom dihedral grids and chemical bins. Perception must use the correct reference and direction.
9. v3 samples raw state indices before geometry. Sampling cap counts attempted targets, not guaranteed successful structures. Legacy capped-output compatibility requires an explicitly versioned adapter path; no silent claim of equivalent subsets.
10. Stable ordinals follow declared enumeration and survive sampling/filtering. ScientificResults use existing production make_result_id authority.
11. preserve_input declares exactly what is preserved per axis and tolerance. Unspecified/unsupported axes must be recorded, not silently advertised as enumerated.
12. H_geom suppression is disabled without key, scope, stereo, proper-fit, closure, induced vertex-action, and transformed geometry audit witnesses. Ambiguity disables suppression.
13. Typed topology is fixed context authority; geometric integrity audit does not reinterpret reaction edges as covalent bonds.
14. Proof bounds require declared hard geometric limits enforced by realization and audit.

## Parallel lanes and ownership

A — Core/torsion: `science/confgen/{model,engine,planner,accounting,perception,tolerances}.py`, `science/confgen/torsion/`, core/torsion tests.
B — Coordination: `science/confgen/{graph.py,coordination/}`, coordination tests and golden audit helpers. Shared graph mapping extraction requires coordinator agreement before editing Refine paths.
C — Ring: `science/confgen/ring/`, ring tests. No edits to core or coordination modules.
Coordinator — workflow schema/compiler/validation, executor, registry, producer contract, integration tests, docs. Worker cross-module edits are requested through interface notes, not made concurrently.

Before lanes start, write shared interface definitions and send each worker the same exact contract. Until then workers may audit and design without conflicting edits.

## Gates

G0: fixture integrity, baseline test results, shared model/target/stage contracts frozen.
G1/P0: full-grid numeric regressions; lazy mixed-radix equivalence; duplicate-axis rejection; absolute/relative semantics; pre-generation sampling reproducibility; raw=sampled+deferred; preflight limit rejects before geometry; deterministic no-seed default; StateKey results and reports.
G2/P1a: proper template groups independently verified; tetrahedral 2 / square planar 3 / TBP 20 / octahedral 30; policy-invariant group; TS1 720→288→12→6; Burnside equality; witnesses and all exclusions auditable; SCINE F1–F6 perception audit.
G3/P1b: monodentate then bidentate geometry; TS1 12-target feasibility report with backend/attempt/reason; no fake successes; stereo and bond checks; verified H_geom positive/negative and closure tests. Decide optional backend based on evidence, within authorized seed-realization scope.
G4/P2: isolated 4/5/6 ring roundtrips, substituent frames, stereochemical parity, degeneracy and ambiguous-boundary tests. Chelate remains explicitly experimental/unsupported until demonstrated.
G5/P3: conditional expansion and full parent locks; drift evidence routing; leaf-only outputs; 36-leaf valid fixture; 5832/648 symbolic preflight cases; failure subtree accounting; stable IDs.
G6/P4: typed producer options and conditional seed validation from P0 onward; upstream result input/pairing; docs and example workflows; JobDesk GUI only if checkout is available.
G7: relevant tests plus repository required checks, regression suite, lint/type checks appropriate to changed code; independent review of failure paths and certificate equations. Completion report lists implemented capabilities and limitations accurately.

## Execution record

- Fixture unpacked under recommended test hierarchy.
- Manifest verification: 19/19 entries pass.
- Fixture self-check: PASS, 720→288→12→6.
- OpenCode binary exists, version 2.0.20. Sandbox startup requires escalation; redirected XDG startup encountered managed-port conflict. Standalone model listing returned no models. Exact requested model is being probed; do not substitute.

- Requested model resolved and probe passed: `opencode-go/muse-spark-1.3-contributor`.
- Baseline: 70 tests passed (scientific regressions, repair executors, schema, producer contract).
- Shared worker API specification: `docs/plans/confgen-v3-interfaces.md`.
- Three implementation task files prepared in `/tmp/confgen-v3-workers/`; launch rejected by automatic approval review because source/context transmission to the external provider needs explicit authorization. No implementation workers launched.

- User explicitly authorized external source/context transmission and local subagent edits. Three OpenCode Muse contributor lanes started successfully.

- Broad baseline: 4234 passed, 7 skipped; 10 baseline release-wheel tests failed because pip scans inaccessible `/opt/g16/bsd` from the inherited Python environment. Gaussian real-binary test excluded because `/opt/g16/g16` is inaccessible during collection. Log `/tmp/confgen-v3-full-baseline.log`.
- Coordinator G0/G2/G3/G4 review notes sent via OpenCode durable steer input: scientific kind/status, lazy iteration, typed context, correct package paths, physical C2 control, rigid fragment realization, ring identity/stereo and removal of duplicate biased sampling.

## Current delivery record (2026-10-01)

The authorized Muse workers completed their disjoint implementation lanes.
The completed integration worker was manually interrupted after delivery;
it was not rerun. Earlier launch/runtime notes above are historical.

- Core: unified C → R(C) → T(C,R) engine; exact conditional leaf accounting,
  lazy index enumeration/sampling, preflight limits, upstream perception locks,
  drift/ambiguity routing, immutable atom ordering, stable leaf ordinals.
- Coordination: six CN4–6 shape registries, proper groups, declared molecular
  subgroup witnesses, independent Burnside accounting, rigid and constrained
  internal-coordinate realization with shared key/quality audit.
- Ring: declared isolated 4/5/6 templates, canonical discrete template keys,
  measured internal-coordinate evidence, substituent frames and parity checks.
- Workflow: typed v3 schema, deterministic-default contract, semantic
  `confgen_state` ScientificResults, inherited scope/certificate provenance,
  leaf-only publication and deterministic gzip audit records.
- Producer and JobDesk: registry-sourced options; typed editor; compiling
  torsion recipe appended at order 120; producer-generated boundary fixtures
  synced into JobDesk with provenance, no golden edits.
- Independent review F-1–F-4 closed: inherited torsion contributions scoped
  to the torsion axis (real two-call ring continuation for both labels);
  wrapped-identical torsion values rejected by schema and core planner;
  mixed diagnostics explicitly labeled and leaf/attempt units authoritative;
  JSONL units present; legacy numeric kernel reused with a pre-generation guard.
- Architecture gate records exact schema/executor scientific authorities;
  new science/confgen sources are also scanned for forbidden legacy imports.
  Worker import scanning distinguishes the exact molecular graph module from
  workflow DAGs. Cross-repo doubles retain strict byte/digest equality with
  float-aware JCS formatting, tested against the real producer.

Scientific and compatibility limits are part of the delivered scope, not
successful realization claims: see the validation record. No commits/pushes.
