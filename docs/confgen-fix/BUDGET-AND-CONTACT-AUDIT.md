# Budgeted search and metal-contact audit

The October 2026 paper diagnostic exposed two independent problems: initialization could
consume an entire short search, and the metal radius screen could reject ordinary Rb–C/H
proximity as extra coordination.

## Search scheduling

`ExploreSettings` now bootstraps a small batch instead of performing a fixed second DG
initialization. Initialization and well probes leave optimization calls for feedback rounds.
DG generation is capped by the available optimization quota, and feedback runs in small
batches. `starts` remains a per-target initialization ceiling, rather than a promise to
optimize that many starts before feedback.

`time_limit_seconds` is an optional soft wall limit. The search estimates relaxation cost
from completed batches and checks remaining time between calls. It cannot interrupt one
backend call or guarantee completion before a hard external timeout. The execution worker
uses 90% of its configured outer wall limit for this soft limit, leaving setup/output margin.

`explore(..., checkpoint=callback)` emits audited, deduplicated snapshots after completed
relaxation batches. The worker atomically replaces `checkpoint.json`, containing coordinates,
atom identities, energies, audit evidence and search statistics in one document. It is
registered as a `search_checkpoint` artifact, including after interruption. Checkpoints are
explicitly non-final; `checkpoint_stage` distinguishes restrained from released geometries.
They preserve usable results, but do not implement exact search-state resume. A hard kill
inside a backend batch can still lose that batch; previously written snapshots survive.

Release reports `deferred` if time prevents starting it, or `partial` if only a subset was
processed. Deferred results retain search restraints; partial results contain the passing
released subset. Execution metadata and diagnostics expose these states.

## Metal-contact audit

The original reference-relative radius/distance-drop screen still identifies contacts.
Additional classification uses the typed covalent graph, metal family, carbon saturation,
hydrogen neighbors and local C–H geometry. Saturated carbon and ordinary carbon-bound
hydrogen proximity to alkali/alkaline-earth centers do not alone establish an extra donor.
Very short contacts, undercoordinated/unsaturated carbon, potential hydrides, unknown
hydrogen environments and possible transition-metal agostic contacts remain conservative
rejections. Declared binding-site atoms retain their existing coordination semantics.

Each screened contact carries zero-based metal/atom indices, element, new/reference
distances, cutoff, covalent neighbors, classification and the audit decision. Evidence is
included in audit records and serialized search records; explore also records contact
decisions for rejected optimizations. This is a geometric chemical-context heuristic,
not an electronic-structure determination of bonding or a general organometallic classifier.

The two archived JIGPUK witnesses (seed 11 candidate 1 and seed 29 candidate 2, zero-based)
now pass native auditing, with the previous C/H triggers explained as ordinary proximity.
Compact coordinate/graph extracts are committed as regression fixtures. This replay does
not establish their optimized stability or relative energy.
