#!/usr/bin/env python3

"""V4 confgen executor: conformer-generation ensemble production.

Pure executor: never shells to Gaussian/ORCA, never calls the calculation
pipeline, never imports legacy runner code.

Two versioned science paths share this executor (never mixed):

- Legacy ``native.chains`` torsion scans keep their explicitly versioned
  behavior bit-for-bit: full-grid ordinals and coordinates, the
  ``max_conformers`` survivor shuffle-truncate subset (see
  ``confflow.science.confgen.torsion.legacy.legacy_cap_v1``), and the
  always-required seed. Science lives in :mod:`confflow.science.torsion`.
- Typed v3 scopes (``native.schema_version == 3``) run the
  Declare -> Enumerate -> Realize -> Perceive -> Account engine over the
  normalized wire in ``ScientificDefinition.native``: resolve inputs to a
  typed spec/context, expand the conditional C->R->T tree, publish
  leaf-only structures, stamp ``confgen_state`` results, and write the
  ``ensemble_report`` plus canonical gzip JSONL target records. The v3 path
  is deterministic by default; a seed is required only when ``sampling``
  requests a capped subset.

Identity: members use the frozen
:func:`confflow.execution.output_identity.conformer_output_id` authority
with the stable leaf ordinal as the native member index. Lineage is
single-parent from the seed structure. Confgen emits structures and state
results only (no energies are invented, no C or C-R intermediates are
published). Artifacts carry run-relative locators plus checksums. The
executor never raises.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.elements import atomic_number
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from ..science.bonds import covalent_radii, perceive_adjacency
from ..science.confgen.torsion.legacy import (
    legacy_cap_v1,
    legacy_oriented_grid_geometries,
    legacy_rotatable_bonds,
)
from ..science.confgen.torsion.paths import (
    CanonicalRotor,
    PathDeclarationError,
    PathResolutionError,
    build_chain_rotors,
    canonical_grid_size,
    canonicalize_rotors,
    parse_path_declarations,
    resolve_paths,
)
from ..science.topology import (
    check_spec_patch_conflict,
    resolve_working_adjacency,
    should_persist_working_graph,
)
from ..science.torsion import (
    parse_bond_pair,
    parse_chain,
    resolve_angle_lists,
    topological_distance_matrix,
)
from .native import NativeErrorCode
from .output_identity import (
    CONFORMER_MEMBER_METADATA_KEY,
    conformer_output_id,
    endpoint_lineage,
)
from .work_item_executor import ItemExecutionContext, _diagnostic

__all__ = [
    "ConfgenExecutor",
    "CONFGEN_REPORT_ROLE",
    "CONFGEN_TARGETS_ROLE",
    "CONFGEN_STATE_KIND",
    "CONFGEN_STATE_PORT",
]

#: Artifact role for the deterministic confgen report.
CONFGEN_REPORT_ROLE = "ensemble_report"

#: Artifact role for the canonical gzip JSONL target records.
CONFGEN_TARGETS_ROLE = "ensemble_targets"

#: Scientific result kind carrying one published labeled StateKey value.
CONFGEN_STATE_KIND = "confgen_state"

#: Optional result input port carrying an upstream chained StateKey.
CONFGEN_STATE_PORT = "confgen_state"

#: Allowed step-native keys for the legacy chains path (unknown keys fail closed).
#: Phase 0 adds ``paths`` (endpoint-pair rotor declarations resolved on the
#: final working topology) plus the ``strict_path_bond_check`` toggle. Any
#: declared ``paths`` entry opts the work item into the strict
#: canonicalization contract (dedup/conflict rules); pure-``chains`` items
#: keep bit-for-bit legacy behavior.
ALLOWED_NATIVE_KEYS = frozenset(
    {
        "chains",
        "chain_steps",
        "chain_angles",
        "angle_step",
        "rotate_side",
        "no_rotate",
        "add_bond",
        "del_bond",
        "bond_scale",
        "clash_threshold",
        "max_conformers",
        "optimize",
        "paths",
        "strict_path_bond_check",
    }
)

_DEFAULT_ANGLE_STEP = 120
_DEFAULT_BOND_SCALE = 1.15
_DEFAULT_CLASH_THRESHOLD = 0.65

#: Pre-geometry grid guard for the legacy chains path. Tracks
#: ``ConfgenLimits.max_declared_states`` (asserted by test, never copied
#: blindly): a legacy grid above this refuses before geometry, mirroring
#: the v3 preflight limit discipline on this versioned path.
_LEGACY_MAX_DECLARED_STATES = 10000


class _GridCancelled(Exception):
    """Internal signal: a cancellation probe fired mid-grid (never escapes)."""


def _thaw_path_resolution(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Deep-thaw an optional resolved-spec mapping for JSON report output."""
    if value is None:
        return None
    thaw = getattr(value, "thaw", None)
    if callable(thaw):
        thawed = thaw()
        return dict(thawed) if isinstance(thawed, Mapping) else dict(value)
    return dict(value)


class ConfgenExecutor:
    """Conformer-generation executor (legacy chains + typed v3 engine)."""

    def execute(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one confgen work item and return its result (never raises)."""
        wall_start = time.time()
        monotonic_start = time.monotonic()
        try:
            return self._run(work_item, context, wall_start, monotonic_start, should_cancel)
        except DomainError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except ValueError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except Exception as exc:  # fail closed; executors never raise into batch
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"confgen internal failure: {exc}",
            )

    def _run(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        scientific = context.scientific
        native = dict(scientific.native)
        if native.get("schema_version") == 3:
            return self._run_v3(work_item, context, wall_start, monotonic_start, should_cancel)
        return self._run_legacy(work_item, context, wall_start, monotonic_start, should_cancel)

    # ------------------------------------------------------------------
    # Typed v3 path
    # ------------------------------------------------------------------

    def _run_v3(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        """Run the typed v3 engine path (deterministic unless sampling)."""
        from confflow.science.confgen.accounting import stamp_production_results
        from confflow.science.confgen.engine import (
            AtomOrderViolationError,
            ConfgenEngine,
            EngineCancelledError,
        )
        from confflow.science.confgen.model import ConfgenStateKey, build_context

        scientific = context.scientific
        seed = scientific.seed
        raw_seed = dict(scientific.native).get("seed")
        if seed is not None and raw_seed is not None and int(raw_seed) != int(seed):
            raise DomainError(
                "confgen v3 seed conflict: step seed "
                f"{seed!r} disagrees with native seed {raw_seed!r}; "
                "the top-level step seed is the sole authority"
            )
        if seed is None:
            seed = raw_seed
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise DomainError(f"confgen v3 seed must be an integer or null, got {seed!r}")
        native = dict(scientific.native)
        sampling = native.get("sampling") or {}
        cap = sampling.get("cap") if isinstance(sampling, Mapping) else None
        if cap is not None and seed is None:
            raise DomainError(
                "confgen v3 sampling cap requires an explicit top-level seed "
                "(the seed is the sole stochastic authority)"
            )
        self._reject_freeze(work_item, context)
        driving = self._driving(work_item)
        input_key, upstream = self._chained_input_state(work_item, driving)
        spec = dict(native)
        if seed is not None:
            spec["seed"] = int(seed)
        # Inherited scope travels to the agreed CORE hook: the engine builds
        # audited locks for carried axes from these descriptors and fails
        # closed (INHERITED_STATE_SCOPE_MISSING) on anything unauditable.
        # InheritedScopeError is a ValueError and fails closed below.
        try:
            science_context = build_context(
                driving, spec, input_key, inherited_scope=upstream.get("inherited_scope")
            )
        except ValueError as exc:
            raise DomainError(f"confgen v3 spec rejected: {exc}") from exc
        engine = ConfgenEngine(allow_preserve_input=True)
        try:
            run = engine.run(science_context, should_cancel=should_cancel)
        except EngineCancelledError:
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        except AtomOrderViolationError as exc:
            raise DomainError(f"confgen v3 fatal: {exc}") from exc
        report = run.report_json()
        certificate = report.get("certificate", {})
        if not certificate.get("equations_ok", False):
            raise DomainError(
                f"confgen v3 certificate equations failed: {certificate.get('basis')}"
            )
        if report.get("realization", {}).get("proof_contradictions"):
            raise DomainError(
                "confgen v3 proof contradiction: a drifted state matches a "
                "proof-backed exclusion; the run cannot be presented complete"
            )
        published_ids = {
            record.target_id
            for record in run.target_records
            if record.status.value == "published_leaf"
        }
        for leaf in run.leaves:
            leaf_target = leaf.provenance.get("leaf_target_id")
            if leaf_target not in published_ids:
                raise DomainError(
                    f"confgen v3 leaf {leaf_target!r} is not a verified published "
                    "target; refusing to publish unverified state"
                )
            if not isinstance(leaf.state_key, ConfgenStateKey):
                raise DomainError("confgen v3 leaf carries no labeled state key")
            if tuple(leaf.structure.atoms) != tuple(driving.atoms):
                raise DomainError(
                    "confgen v3 leaf breaks the atomic invariant: atom sequence "
                    "differs from the driving structure"
                )

        charge = driving.charge
        multiplicity = driving.multiplicity
        overrides = scientific.overrides
        if overrides.get("charge") is not None:
            charge = int(overrides["charge"])
        if overrides.get("multiplicity") is not None:
            multiplicity = int(overrides["multiplicity"])
        parent_ids, lineage_root, group_key = endpoint_lineage(driving)
        persist_graph = should_persist_working_graph(driving)
        inherit_patch = driving.topology_patch if persist_graph else None
        inherit_graph = (
            tuple(tuple(int(v) for v in row) for row in science_context.adjacency)
            if persist_graph
            else None
        )
        ordered = sorted(run.leaves, key=lambda leaf: int(leaf.provenance.get("leaf_ordinal", 0)))
        members: list[StructureRecord] = []
        for leaf in ordered:
            ordinal = int(leaf.provenance.get("leaf_ordinal", 0))
            metadata: dict[str, Any] = {CONFORMER_MEMBER_METADATA_KEY: ordinal}
            if seed is not None:
                metadata["seed"] = int(seed)
            if upstream["result_id"] is not None:
                metadata["input_confgen_state"] = upstream["result_id"]
            members.append(
                StructureRecord(
                    id=conformer_output_id(work_item.logical_key, ordinal),
                    atoms=tuple(leaf.structure.atoms),
                    coordinates=tuple(
                        (float(x), float(y), float(z)) for x, y, z in leaf.structure.coordinates
                    ),
                    charge=charge,
                    multiplicity=multiplicity,
                    parent_ids=parent_ids,
                    lineage_root_id=lineage_root,
                    source_step_id=work_item.step_id,
                    source_work_item_id=work_item.id,
                    role="conformer",
                    ordinal=ordinal,
                    group_key=group_key,
                    metadata=FrozenDict(metadata),
                    topology_patch=inherit_patch,
                    working_topology=inherit_graph,
                )
            )
        restated = self._restate_leaves(run.leaves, members)
        results = stamp_production_results(
            restated,
            step_id=work_item.step_id,
            work_item_id=work_item.id,
            producer_digest=work_item.semantic_digest,
            certificate_digest=report["certificate"]["digest"],
            upstream_state_id=upstream["result_id"],
        )
        results = self._attach_inherited_scope(results, restated, science_context)
        artifacts = self._write_report_v3(
            work_item,
            context,
            driving,
            members,
            results,
            run,
            report,
            seed=int(seed) if seed is not None else None,
            upstream=upstream,
            path_resolution=science_context.resolved_spec.get("paths_resolved"),
        )
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        published = len(members)
        resolved_paths = science_context.resolved_spec.get("paths_resolved")
        path_warnings: Sequence[str] = (
            tuple(resolved_paths.get("warnings", ())) if isinstance(resolved_paths, Mapping) else ()
        )
        info_details: dict[str, Any] = {
            "members": published,
            "seed": int(seed) if seed is not None else None,
            "certificate": report["certificate"]["digest"],
            "raw_targets": report["counts"].get("raw"),
        }
        if isinstance(resolved_paths, Mapping):
            info_details["path_rotors"] = len(resolved_paths.get("rotors", ()))
            info_details["path_raw_states"] = resolved_paths.get("raw_cartesian_size")
            info_details["path_warnings"] = len(path_warnings)
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=StructureSet(tuple(members)),
            results=results,
            artifacts=artifacts,
            diagnostics=(
                Diagnostic(
                    code="confgen_completed",
                    message=(
                        f"confgen v3 published {published} leaf structures "
                        f"({report['counts'].get('raw', '?')} raw targets, "
                        f"{report['counts'].get('deferred_sampled_out', 0)} sampled out)"
                    ),
                    severity=DiagnosticSeverity.INFO,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict(info_details),
                ),
                *self._resolved_path_diagnostics(resolved_paths, work_item),
                *self._warning_diagnostics(path_warnings, work_item),
            ),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    @staticmethod
    @staticmethod
    def _attach_inherited_scope(
        results: ResultSet,
        leaves: list[Any],
        context: Any,
    ) -> ResultSet:
        """Attach resolved scope descriptors for downstream chaining.

        The certificate digest and upstream result id already ride in the
        core stamp's provenance (passed as stamp arguments); this step adds
        only ``inherited_scope`` — resolved scope/reference descriptors for
        every axis entry of each leaf's key, in the exact format the CORE
        ``build_context`` hook reads: torsion defining refs (model,
        bond/atoms, rotate_side, chemical states) plus the measurable frame
        and, for relative grids, the absolute frame value snapshotted on
        this run's input reference geometry; ring atom lists; the
        coordination metal/donor refs. Original relative labels are
        preserved verbatim here; downstream locks derive from the snapshotted
        reference, never from rewritten labels. The production ``result_id``
        is untouched. Scope lives in domain-semantic ``Provenance`` metadata
        only — never in the StateKey value, never in structure metadata.

        Relative frames mirror the documented torsion-stage frame rule
        (minimum-index neighbors); the mirror is marked interim in each
        descriptor until CORE owns frame resolution end to end.
        """
        import dataclasses

        import numpy as np

        from confflow.science.confgen.torsion.measure import measure_dihedral

        from ..domain.result import Provenance

        resolved = context.resolved_spec
        torsion_entries = {
            str(entry.get("id")): entry
            for entry in (resolved.get("torsions", []) or [])
            if isinstance(entry, Mapping)
        }
        ring_entries = {
            str(entry.get("id")): entry
            for entry in (resolved.get("rings", []) or [])
            if isinstance(entry, Mapping)
        }
        coordination = resolved.get("coordination")
        adjacency = context.adjacency
        input_coords = np.asarray(context.input_coords, dtype=float)

        def _relative_frame(bond: Any) -> list[int] | None:
            first, second = int(bond[0]), int(bond[1])
            near = [n for n in adjacency[first] if n != second]
            far = [n for n in adjacency[second] if n != first]
            if not near or not far:
                return None
            return [min(near), first, second, min(far)]

        def _torsion_scope(axis_id: str, label: Any) -> dict[str, Any]:
            entry = torsion_entries.get(axis_id, {})
            model = str(entry.get("model", ""))
            bond = entry.get("bond")
            atoms = entry.get("atoms")
            frame: Any = None
            reference_value: Any = None
            if atoms is not None:
                frame = [int(a) for a in atoms]
            elif bond is not None:
                frame = _relative_frame(bond)
                if frame is not None:
                    try:
                        reference_value = float(measure_dihedral(input_coords, *frame))
                    except ValueError:
                        reference_value = None
            descriptor: dict[str, Any] = {
                "model": model,
                "bond": [int(b) for b in bond] if bond is not None else None,
                "atoms": [int(a) for a in atoms] if atoms is not None else None,
                "frame": frame,
                "rotate_side": str(entry.get("rotate_side", "left")),
                "states": dict(entry.get("states", {}) or {}),
                "label": label,
            }
            if model == "relative_rotation_grid":
                descriptor["frame_rule"] = "torsion-stage-_frames-mirror(interim)"
                descriptor["reference_frame_value"] = reference_value
            return descriptor

        coord_scope: Any = None
        if isinstance(coordination, Mapping):
            coord_scope = {
                "metal_center": int(coordination.get("metal_center", -1)),
                "donor_atoms": sorted(
                    int(a)
                    for site in (coordination.get("binding_sites", []) or [])
                    if isinstance(site, Mapping)
                    for a in (site.get("atoms", []) or [])
                ),
                "shapes": coordination.get("shapes", "auto"),
            }

        attached = []
        for record, leaf in zip(results, leaves):
            key = leaf.state_key
            torsions = {
                str(axis_id): _torsion_scope(str(axis_id), label)
                for axis_id, label in dict(key.torsions).items()
            }
            rings = {
                str(ring_id): {
                    "atoms": [
                        int(a) for a in (ring_entries.get(str(ring_id), {}).get("atoms") or [])
                    ],
                    "label": (dict(label) if isinstance(label, Mapping) else label),
                }
                for ring_id, label in dict(key.rings).items()
            }
            coordination_scope: Any = None
            if key.coordination is not None:
                coordination_scope = dict(coord_scope) if coord_scope is not None else {}
                coordination_scope["label"] = (
                    dict(key.coordination)
                    if isinstance(key.coordination, Mapping)
                    else key.coordination
                )
            existing = record.provenance
            meta = dict(existing.metadata) if existing is not None and existing.metadata else {}
            meta["inherited_scope"] = {
                "torsions": torsions,
                "rings": rings,
                "coordination": coordination_scope,
            }
            if existing is not None:
                provenance = dataclasses.replace(existing, metadata=FrozenDict(meta))
            else:
                provenance = Provenance(program="confflow-confgen-v3", metadata=FrozenDict(meta))
            attached.append(dataclasses.replace(record, provenance=provenance))
        return ResultSet(tuple(attached))

    @staticmethod
    def _restate_leaves(leaves: Any, members: list[StructureRecord]) -> list[Any]:
        """Pair engine leaves with output structures for result stamping."""
        from confflow.science.confgen.model import WorkingRealization

        restated = []
        by_ordinal = {int(m.ordinal): m for m in members if m.ordinal is not None}
        for leaf in leaves:
            ordinal = int(leaf.provenance.get("leaf_ordinal", 0))
            restated.append(
                WorkingRealization(
                    structure=by_ordinal[ordinal],
                    state_key=leaf.state_key,
                    parent_realization_id=leaf.parent_realization_id,
                    generation_axis=leaf.generation_axis,
                    locked_axes=tuple(leaf.locked_axes),
                    provenance=leaf.provenance,
                )
            )
        return restated

    @staticmethod
    def _reject_freeze(work_item: WorkItem, context: ItemExecutionContext) -> None:
        """Fail closed on unsupported frozen-atom declarations."""
        overrides = context.scientific.overrides
        try:
            override_freeze = overrides.get("freeze")
        except Exception:
            override_freeze = None
        if override_freeze:
            raise DomainError("confgen does not support freeze overrides; declare an empty freeze")
        defaults = getattr(context, "scientific_defaults", None)
        if defaults is not None and getattr(defaults, "freeze", None):
            raise DomainError(
                "confgen does not support frozen atoms; the run-level freeze "
                "default must be empty for confgen steps"
            )

    def _chained_input_state(
        self, work_item: WorkItem, driving: StructureRecord
    ) -> tuple[Any | None, dict[str, Any]]:
        """Resolve the optional upstream confgen_state result (strict).

        The ``confgen_state`` result port is matched by subject at assembly;
        here the selection is strict: zero or one result, exactly one
        candidate whose subject is exactly the driving structure id
        (``None`` or mismatched subjects are rejected, never guessed), kind
        ``confgen_state``, value parsed as a labeled StateKey. The value
        alone carries state authority (never metadata); the upstream
        certificate digest is inherited from semantic result provenance
        metadata alongside the key. Returns ``(input_key, upstream)`` where
        upstream records the inherited result identity and certificate.
        """
        from confflow.science.confgen.model import ConfgenStateKey

        empty = {
            "result_id": None,
            "value_digest": None,
            "certificate_digest": None,
            "inherited_scope": None,
        }
        try:
            available = work_item.named_inputs.results
        except Exception:
            return None, empty
        if CONFGEN_STATE_PORT not in available:
            return None, empty
        candidates = available[CONFGEN_STATE_PORT]
        if len(candidates) == 0:
            return None, empty
        if len(candidates) != 1:
            raise DomainError(
                f"confgen requires at most one '{CONFGEN_STATE_PORT}' result per "
                f"work item for subject {driving.id!r}; got {len(candidates)} "
                "(narrow with an explicit result selector)"
            )
        record = candidates[0]
        if record.kind != CONFGEN_STATE_KIND:
            raise DomainError(
                f"confgen '{CONFGEN_STATE_PORT}' port carries kind {record.kind!r}; "
                f"expected {CONFGEN_STATE_KIND!r}"
            )
        if record.subject_structure_id != driving.id:
            raise DomainError(
                f"confgen '{CONFGEN_STATE_PORT}' subject "
                f"{record.subject_structure_id!r} does not exactly match driving "
                f"structure {driving.id!r}; by-subject chaining requires identity"
            )
        try:
            key = ConfgenStateKey.from_dict(dict(record.value))
        except ValueError as exc:
            raise DomainError(f"confgen chained state key rejected: {exc}") from exc
        provenance = getattr(record, "provenance", None)
        metadata = getattr(provenance, "metadata", None)
        try:
            meta = dict(metadata) if metadata else {}
        except Exception:
            meta = {}
        return (
            key,
            {
                "result_id": record.result_id,
                "value_digest": record.value_digest,
                "certificate_digest": meta.get("certificate_digest"),
                "inherited_scope": meta.get("inherited_scope"),
            },
        )

    def _write_report_v3(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        driving: StructureRecord,
        members: list[StructureRecord],
        results: ResultSet,
        run: Any,
        report: dict[str, Any],
        *,
        seed: int | None,
        upstream: Mapping[str, Any],
        path_resolution: Mapping[str, Any] | None = None,
    ) -> ArtifactSet:
        attempt_dir = context.attempt_dir(work_item)
        os.makedirs(attempt_dir, exist_ok=True)
        leaf_rows = []
        for record, result in zip(members, results):
            leaf_rows.append(
                {
                    "id": record.id,
                    "ordinal": record.ordinal,
                    "geometry_digest": record.geometry_digest,
                    "result_id": result.result_id,
                    "state_key": dict(result.value),
                }
            )
        payload = {
            "schema_version": 3,
            "seed": seed,
            "driving_id": driving.id,
            "input_confgen_state": upstream.get("result_id"),
            "input_state_digest": upstream.get("value_digest"),
            "input_certificate_digest": upstream.get("certificate_digest"),
            "inherited_scope": ("verified" if upstream.get("result_id") is not None else "none"),
            "counts": report["counts"],
            # Unit-exact sections, passed through verbatim: the legacy
            # `counts` rollup mixes attempt and leaf units, so consumers
            # must prefer `leaf_certificate` (final-leaf outcomes, unit 1)
            # and `attempt_ledger` (issued attempts, unit 2) for unit-exact
            # accounting. Present only on engine runs (absent on the
            # preserve-input path, which carries no issued attempts).
            "count_units": {
                "leaf_certificate": "final-leaf outcomes only (unit 1)",
                "attempt_ledger": "issued attempts (unit 2)",
                "counts": "legacy mixed-unit rollup; prefer the sections above",
            },
            "leaf_certificate": report.get("leaf_certificate"),
            "attempt_ledger": report.get("attempt_ledger"),
            "inherited": report.get("inherited"),
            "suppression": report.get("suppression"),
            "enumeration": report["enumeration"],
            "realization": report["realization"],
            "certificate": report["certificate"],
            "sampling": report["sampling"],
            "scope": report["scope"],
            "path_resolution": _thaw_path_resolution(path_resolution),
            "drift_events": report["drift_events"],
            "members": leaf_rows,
        }
        report_name = "ensemble_report.json"
        report_path = os.path.join(attempt_dir, report_name)
        try:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            with open(report_path, "rb") as handle:
                report_checksum = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            raise DomainError(f"confgen report write failed: {exc}") from exc
        targets_name = "confgen_states.jsonl.gz"
        targets_path = os.path.join(attempt_dir, targets_name)
        try:
            from confflow.science.confgen.accounting import record_unit

            child_ids = {
                record.parent_target_id
                for record in run.target_records
                if record.parent_target_id is not None
            }
            buffer = io.BytesIO()
            with gzip.GzipFile(
                filename="", mode="wb", compresslevel=9, mtime=0, fileobj=buffer
            ) as gz:
                for record in run.target_records:
                    row = {
                        "target_id": record.target_id,
                        "axis": record.axis,
                        "ordinal": int(record.ordinal),
                        "status": record.status.value,
                        "reason": record.reason,
                        "unit": record_unit(record, record.target_id in child_ids),
                        "state_value": (
                            dict(record.state_value)
                            if not hasattr(record.state_value, "thaw")
                            else record.state_value.thaw()
                        ),
                        "complete_key": (
                            None
                            if record.complete_key is None
                            else (
                                dict(record.complete_key)
                                if not hasattr(record.complete_key, "thaw")
                                else record.complete_key.thaw()
                            )
                        ),
                        "parent_target_id": record.parent_target_id,
                    }
                    gz.write(
                        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode(
                            "utf-8"
                        )
                    )
            with open(targets_path, "wb") as handle:
                handle.write(buffer.getvalue())
            with open(targets_path, "rb") as handle:
                targets_checksum = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            raise DomainError(f"confgen targets write failed: {exc}") from exc
        for path in (report_path, targets_path):
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise DomainError(f"confgen artifact {path!r} was not persisted")
        prefix = context.run_relative_prefix(work_item)
        refs = []
        for name, role, checksum in (
            (report_name, CONFGEN_REPORT_ROLE, report_checksum),
            (targets_name, CONFGEN_TARGETS_ROLE, targets_checksum),
        ):
            locator_path = f"{prefix}/{name}" if prefix else name
            metadata: dict[str, Any] = {"seed": seed}
            if upstream.get("result_id") is not None:
                metadata["input_confgen_state"] = upstream["result_id"]
            refs.append(
                ArtifactRef(
                    id=f"{work_item.id}/{name}",
                    role=role,
                    locator=ArtifactLocator.run_relative(locator_path),
                    checksum=checksum,
                    producer_step_id=work_item.step_id,
                    producer_work_item_id=work_item.id,
                    subject_structure_id=driving.id,
                    metadata=FrozenDict(metadata),
                )
            )
        return ArtifactSet(tuple(refs))

    # ------------------------------------------------------------------
    # Legacy chains path (explicitly versioned behavior, bit-for-bit)
    # ------------------------------------------------------------------

    def _run_legacy(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        scientific = context.scientific
        seed = scientific.seed
        if seed is None or isinstance(seed, bool) or not isinstance(seed, int):
            raise DomainError("confgen requires the explicit integer step seed (single authority)")
        driving = self._driving(work_item)
        native = dict(scientific.native)
        unknown = sorted(set(native) - ALLOWED_NATIVE_KEYS)
        if unknown:
            raise DomainError(
                f"confgen got unknown native keys {unknown}; allowed {sorted(ALLOWED_NATIVE_KEYS)}"
            )
        if native.get("optimize") is True:
            raise DomainError(
                "confgen optimize=true is unsupported in V4 (MMFF legacy); "
                "declare chains without pre-optimization"
            )
        n_atoms = len(driving.atoms)
        angle_step = native.get("angle_step", _DEFAULT_ANGLE_STEP)
        if isinstance(angle_step, bool) or not isinstance(angle_step, int):
            raise DomainError(f"confgen angle_step must be an integer, got {angle_step!r}")
        if angle_step <= 0 or angle_step > 360:
            raise DomainError(f"confgen angle_step must be in 1..360, got {angle_step!r}")
        rotate_side = str(native.get("rotate_side", "left"))
        if rotate_side not in ("left", "right"):
            raise DomainError(f"confgen rotate_side must be 'left' or 'right', got {rotate_side!r}")
        try:
            bond_scale = float(native.get("bond_scale", _DEFAULT_BOND_SCALE))
            clash_threshold = float(native.get("clash_threshold", _DEFAULT_CLASH_THRESHOLD))
        except (TypeError, ValueError) as exc:
            raise DomainError(f"confgen numeric parameter malformed: {exc}") from exc
        if not math.isfinite(bond_scale) or bond_scale <= 0:
            raise DomainError(
                f"confgen bond_scale must be a positive number, got {native.get('bond_scale')!r}"
            )
        if not math.isfinite(clash_threshold) or clash_threshold <= 0:
            raise DomainError(
                "confgen clash_threshold must be a positive number, "
                f"got {native.get('clash_threshold')!r}"
            )
        max_conformers = native.get("max_conformers")
        if max_conformers is not None and (
            isinstance(max_conformers, bool)
            or not isinstance(max_conformers, int)
            or max_conformers < 1
        ):
            raise DomainError(
                f"confgen max_conformers must be an integer >= 1, got {max_conformers!r}"
            )
        strict_raw = native.get("strict_path_bond_check", False)
        if type(strict_raw) is not bool:
            raise DomainError(
                "confgen strict_path_bond_check must be a boolean, " f"got {strict_raw!r}"
            )
        chain_steps_raw = native.get("chain_steps")
        chain_angles_raw = native.get("chain_angles")
        no_rotate = self._bond_set(native.get("no_rotate", []) or [], "no_rotate")
        add_bond = [self._bond_pair(item) for item in native.get("add_bond", []) or []]
        del_bond = [self._bond_pair(item) for item in native.get("del_bond", []) or []]

        raw_paths = native.get("paths", None)
        if raw_paths is not None:
            # Phase 0 strict mode: any declared paths list opts this work
            # item into the canonicalization contract (paths resolve on the
            # driving geometry with the pure resolver; chains, when also
            # declared, canonicalize with them instead of legacy overlap).
            if not isinstance(raw_paths, (list, tuple)) or not raw_paths:
                raise DomainError(
                    "confgen paths must be a non-empty list of " "{start, end, move} declarations"
                )
            raw_chains_opt = native.get("chains", None)
            if raw_chains_opt is None:
                chains_opt: list[list[int]] = []
            else:
                if not isinstance(raw_chains_opt, (list, tuple)) or not raw_chains_opt:
                    raise DomainError(
                        "confgen chains must be a non-empty list of 1-based "
                        "dash-separated atom chains"
                    )
                try:
                    chains_opt = [parse_chain(item) for item in raw_chains_opt]
                except ValueError as exc:
                    raise DomainError(f"confgen {exc}") from exc
                for chain in chains_opt:
                    for index in chain:
                        if index < 0 or index >= n_atoms:
                            raise DomainError(
                                f"confgen chain index {index + 1} out of range "
                                f"for {n_atoms} atoms"
                            )
            return self._run_legacy_paths(
                work_item,
                context,
                wall_start,
                monotonic_start,
                should_cancel,
                scientific=scientific,
                seed=int(seed),
                driving=driving,
                native=native,
                chains=chains_opt,
                angle_step=angle_step,
                rotate_side=rotate_side,
                chain_steps_raw=chain_steps_raw,
                chain_angles_raw=chain_angles_raw,
                no_rotate=no_rotate,
                add_bond=add_bond,
                del_bond=del_bond,
                bond_scale=bond_scale,
                clash_threshold=clash_threshold,
                max_conformers=max_conformers,
                strict_path_bond_check=bool(strict_raw),
            )
        raw_chains = native.get("chains")
        if not isinstance(raw_chains, (list, tuple)) or not raw_chains:
            raise DomainError(
                "confgen requires explicit 'chains' (1-based dash-separated atom chains); "
                "automatic rotatable-bond detection does not exist"
            )
        try:
            chains = [parse_chain(item) for item in raw_chains]
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        for chain in chains:
            for index in chain:
                if index < 0 or index >= n_atoms:
                    raise DomainError(
                        f"confgen chain index {index + 1} out of range for {n_atoms} atoms"
                    )
        chain_steps = self._as_str_list(native.get("chain_steps"), "chain_steps", len(chains))
        chain_angles = self._as_str_list(native.get("chain_angles"), "chain_angles", len(chains))

        atomic_numbers = [atomic_number(symbol) for symbol in driving.atoms]
        adjacency = self._working_adjacency(
            atomic_numbers,
            driving.coordinates,
            bond_scale,
            add_bond,
            del_bond,
            n_atoms,
            structure=driving,
        )

        rot_bond_count = 0
        excluded_bonds = 0
        full_angle_lists: list[list[float]] = []
        for chain_index, chain in enumerate(chains):
            try:
                per_bond = resolve_angle_lists(
                    len(chain) - 1,
                    chain_steps[chain_index] if chain_steps else None,
                    chain_angles[chain_index] if chain_angles else None,
                    angle_step,
                )
            except ValueError as exc:
                raise DomainError(f"confgen {exc}") from exc
            for position, (left, right) in enumerate(zip(chain, chain[1:])):
                rot_bond_count += 1
                if right not in adjacency[left]:
                    raise DomainError(
                        f"confgen chain atoms {left + 1}-{right + 1} are not bonded; "
                        "use add_bond or adjust bond_scale"
                    )
                if tuple(sorted((left, right))) in no_rotate:
                    # Excluded bonds hold a single 0.0 grid point: ordinals
                    # stay identical to the versioned legacy grid.
                    full_angle_lists.append([0.0])
                    excluded_bonds += 1
                    continue
                # Ring-bond refusal now comes from the science helper below
                # (fail closed even for no_rotate-listed ring bonds: refusal,
                # never a silent skip).
                full_angle_lists.append([float(item) for item in per_bond[position]])
        if excluded_bonds >= rot_bond_count:
            raise DomainError("confgen chains selected no rotatable bonds")
        if should_cancel is not None and should_cancel():
            return self._cancelled(work_item, context, wall_start, monotonic_start)

        total = math.prod(len(angles) for angles in full_angle_lists)
        if total > _LEGACY_MAX_DECLARED_STATES:
            raise DomainError(
                f"confgen legacy grid declares {total} points above the "
                f"pre-geometry limit {_LEGACY_MAX_DECLARED_STATES}; split the "
                "chains or use a v3 sampling cap instead"
            )
        base = np.asarray(driving.coordinates, dtype=np.float64)
        # Science-owned legacy grid: identical ordinals/coordinates to the
        # versioned legacy executor (chain-aware sides, cumulative
        # application, post-geometry clash filter). Never routed through the
        # v3 TorsionStage, whose sign convention differs by design. The
        # oriented adapter below is the same numeric loop with caller-supplied
        # bonds, so pure-legacy geometry is unchanged.
        try:
            legacy_bonds = legacy_rotatable_bonds(chains, adjacency, rotate_side)
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        try:
            kept, clash_dropped = self._realize_grid_members(
                driving_coordinates=base,
                adjacency=adjacency,
                atomic_numbers=atomic_numbers,
                clash_threshold=clash_threshold,
                rot_bonds=[
                    (int(left), int(right), list(rotating))
                    for left, right, rotating in legacy_bonds
                ],
                angle_lists=full_angle_lists,
                total=total,
                should_cancel=should_cancel,
            )
        except _GridCancelled:
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        return self._complete_legacy_grid(
            work_item,
            context,
            wall_start,
            monotonic_start,
            driving=driving,
            seed=int(seed),
            native=native,
            scientific=scientific,
            total=total,
            kept=kept,
            clash_dropped=clash_dropped,
            max_conformers=max_conformers,
            summary_prefix=f"confgen scanned {total} torsion points",
            extra_details={},
            report_extra=None,
            extra_diagnostics=(),
            working_graph=(
                [list(row) for row in adjacency] if should_persist_working_graph(driving) else None
            ),
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _working_adjacency(
        atomic_numbers: list[int],
        coordinates: Any,
        bond_scale: float,
        add_bond: list[tuple[int, int]],
        del_bond: list[tuple[int, int]],
        n_atoms: int,
        *,
        structure: StructureRecord | None = None,
    ) -> list[list[int]]:
        """Build the final working topology (perception + corrections).

        Shared by the pure-legacy and Phase 0 paths modes so both resolve on
        the identical working graph.  A record-authoritative graph (a
        nonempty ``topology_patch`` or a persisted ``working_topology``)
        wins verbatim: simultaneous native ``add_bond``/``del_bond``
        corrections fail closed instead of silently overriding it.
        Pure-legacy records keep bit-for-bit the legacy construction below.
        """
        if structure is not None and should_persist_working_graph(structure):
            check_spec_patch_conflict(
                {"add_bond": list(add_bond), "del_bond": list(del_bond)},
                structure,
                where="confgen legacy",
            )
            return resolve_working_adjacency(
                structure, atomic_numbers, coordinates, bond_scale=bond_scale
            )
        adjacency = perceive_adjacency(atomic_numbers, coordinates, bond_scale=bond_scale)
        for first, second in add_bond:
            ConfgenExecutor._check_index(first, n_atoms, "add_bond")
            ConfgenExecutor._check_index(second, n_atoms, "add_bond")
            if second not in adjacency[first]:
                adjacency[first].append(second)
                adjacency[second].append(first)
        for first, second in del_bond:
            ConfgenExecutor._check_index(first, n_atoms, "del_bond")
            if second in adjacency[first]:
                adjacency[first].remove(second)
                adjacency[second].remove(first)
        return [sorted(row) for row in adjacency]

    def _prepare_path_rotors(
        self,
        *,
        driving: StructureRecord,
        native: Mapping[str, Any],
        chains: list[list[int]],
        angle_step: int,
        rotate_side: str,
        chain_steps_raw: Any,
        chain_angles_raw: Any,
        no_rotate: set[tuple[int, int]],
        add_bond: list[tuple[int, int]],
        del_bond: list[tuple[int, int]],
        bond_scale: float,
        strict_path_bond_check: bool,
    ) -> dict[str, Any]:
        """Compile path (+ optional chain) declarations to canonical rotors.

        Preparation only: parses declarations, resolves paths on the driving
        working topology with the pure resolver, builds declared-grid chain
        descriptors via the shared science helper, canonicalizes
        (conflict-before-exclusion), and guards the final canonical task
        count pre-geometry. No geometry runs here. Returns rotors plus the
        working topology, radii inputs, declaration/canonical sizes, and the
        raw resolution for reporting.
        """
        n_atoms = len(driving.atoms)
        chain_steps = self._as_str_list(chain_steps_raw, "chain_steps", len(chains))
        chain_angles = self._as_str_list(chain_angles_raw, "chain_angles", len(chains))
        try:
            parsed = parse_path_declarations(
                list(native.get("paths") or []),
                index_base=1,
                default_step=angle_step,
                source_prefix="native.paths",
            )
        except (PathResolutionError, ValueError) as exc:
            raise DomainError(f"confgen {exc}") from exc
        atomic_numbers = [atomic_number(symbol) for symbol in driving.atoms]
        adjacency = self._working_adjacency(
            atomic_numbers,
            driving.coordinates,
            bond_scale,
            add_bond,
            del_bond,
            n_atoms,
            structure=driving,
        )
        try:
            radii = covalent_radii(atomic_numbers)
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        try:
            resolution = resolve_paths(
                parsed,
                adjacency,
                n_atoms=n_atoms,
                coords=[tuple(point) for point in driving.coordinates],
                radii=list(radii),
                strict_bond_check=strict_path_bond_check,
                topology_extra={
                    "add_bond": sorted(f"{a + 1}-{b + 1}" for a, b in add_bond),
                    "del_bond": sorted(f"{a + 1}-{b + 1}" for a, b in del_bond),
                    "bond_scale": bond_scale,
                },
            )
        except (PathResolutionError, PathDeclarationError) as exc:
            raise DomainError(f"confgen {exc}") from exc
        per_chain: list[list[list[float]]] = []
        for chain_index, chain in enumerate(chains):
            try:
                per_chain.append(
                    resolve_angle_lists(
                        len(chain) - 1,
                        chain_steps[chain_index] if chain_steps else None,
                        chain_angles[chain_index] if chain_angles else None,
                        angle_step,
                    )
                )
            except ValueError as exc:
                raise DomainError(f"confgen {exc}") from exc
        try:
            chain_rotors = build_chain_rotors(
                chains, adjacency, per_chain, rotate_side, source_prefix="chains"
            )
        except PathResolutionError as exc:
            raise DomainError(f"confgen {exc}") from exc
        declared_chain = 1
        for lists in per_chain:
            for angles in lists:
                declared_chain *= len(angles)
        declared_size = int(resolution.raw_cartesian_size) * declared_chain
        try:
            rotors = canonicalize_rotors(
                [*resolution.rotors, *chain_rotors], excluded_bonds=sorted(no_rotate)
            )
        except PathResolutionError as exc:
            raise DomainError(f"confgen {exc}") from exc
        if not rotors:
            raise DomainError("confgen paths selected no rotatable bonds")
        if all(
            rotor.angles == (0.0,) and "excluded:no_rotate" in rotor.sources for rotor in rotors
        ):
            raise DomainError("confgen chains selected no rotatable bonds")
        # Arbitrary-precision final canonical task count BEFORE any geometric
        # generation or allocation (Python ints are unbounded). This guards
        # compute; max_conformers stays a post-geometry survivor cap only.
        canonical_size = canonical_grid_size(rotors)
        if canonical_size > _LEGACY_MAX_DECLARED_STATES:
            raise DomainError(
                f"confgen paths grid declares {canonical_size} states "
                f"(declared {declared_size}) above the pre-geometry limit "
                f"{_LEGACY_MAX_DECLARED_STATES}; split the paths or narrow "
                "the angle grids"
            )
        return {
            "rotors": rotors,
            "resolution": resolution,
            "adjacency": adjacency,
            "atomic_numbers": atomic_numbers,
            "declared_size": declared_size,
            "canonical_size": canonical_size,
        }

    @staticmethod
    def _resolved_path_diagnostics(
        payload: Mapping[str, Any] | None, work_item: WorkItem
    ) -> tuple[Diagnostic, ...]:
        """Expose the resolver's audited chains without another graph traversal."""
        if payload is None:
            return ()
        symbols = payload.get("atom_symbols", ())
        diagnostics: list[Diagnostic] = []
        for ordinal, declaration in enumerate(payload.get("declared_paths", ()), 1):
            route = declaration["route"]
            chain = "-".join(f"{atom}({symbols[atom - 1]})" for atom in route)
            bonds = ", ".join(f"{first}-{second}" for first, second in zip(route, route[1:]))
            move = declaration["move"]
            diagnostics.append(
                Diagnostic(
                    code="confgen_path_resolved",
                    message=(
                        f"Path P{ordinal}: {chain}; move {move} endpoint "
                        f"{declaration[move]}; rotors: {bonds}"
                    ),
                    severity=DiagnosticSeverity.INFO,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict(
                        {
                            "source": declaration["source"],
                            "route": list(route),
                            "move": move,
                            "topology_digest": payload["topology_digest"],
                        }
                    ),
                )
            )
        return tuple(diagnostics)

    @staticmethod
    def _warning_diagnostics(
        warnings: Sequence[str], work_item: WorkItem
    ) -> tuple[Diagnostic, ...]:
        """Expose resolver warnings as stable WARNING_SHORT_BOND diagnostics."""
        found: list[Diagnostic] = []
        for note in warnings:
            found.append(
                Diagnostic(
                    code="warning_short_bond",
                    message=note,
                    severity=DiagnosticSeverity.WARNING,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict({"warning": "short_bond"}),
                )
            )
        return tuple(found)

    def _realize_grid_members(
        self,
        *,
        driving_coordinates: Any,
        adjacency: list[list[int]],
        atomic_numbers: list[int],
        clash_threshold: float,
        rot_bonds: list[tuple[int, int, list[int]]],
        angle_lists: list[list[float]],
        total: int,
        should_cancel: Callable[[], bool] | None,
    ) -> tuple[list[tuple[int, np.ndarray]], int]:
        """Run the shared legacy grid pipeline (one existing runtime only).

        Consumes the oriented numeric adapter (cumulative Rodrigues
        application in declared order, row-major ordinals, post-geometry
        clash filter) and returns ``(kept, clash_dropped)``. Both the pure
        chains mode and the strict paths mode flow through here, so charge,
        lineage, capping, and report behaviors cannot diverge.
        """
        try:
            radii = covalent_radii(atomic_numbers)
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        topo = topological_distance_matrix(adjacency)
        try:
            stream = legacy_oriented_grid_geometries(
                np.asarray(driving_coordinates, dtype=np.float64),
                rot_bonds,
                angle_lists,
                radii=radii,
                topo=topo,
                clash_threshold=clash_threshold,
            )
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        kept: list[tuple[int, np.ndarray]] = []
        for ordinal, coords in stream:
            kept.append((ordinal, coords))
            if should_cancel is not None and should_cancel():
                raise _GridCancelled()
        return kept, total - len(kept)

    def _build_conformer_members(
        self,
        *,
        driving: StructureRecord,
        kept: list[tuple[int, np.ndarray]],
        seed: int,
        work_item: WorkItem,
        scientific: Any,
        working_graph: list[list[int]] | None = None,
    ) -> list[StructureRecord]:
        """Build output members preserving atom order, lineage, and charge.

        Members inherit the driving record's intended topology: the patch
        plus the working graph resolved exactly once on the driving
        geometry (never re-perceived on moved output coordinates).
        Pure-legacy members (``working_graph=None``) gain no new content.
        """
        charge = driving.charge
        multiplicity = driving.multiplicity
        overrides = scientific.overrides
        if overrides.get("charge") is not None:
            charge = int(overrides["charge"])
        if overrides.get("multiplicity") is not None:
            multiplicity = int(overrides["multiplicity"])
        parent_ids, lineage_root, group_key = endpoint_lineage(driving)
        inherit_patch = driving.topology_patch if working_graph is not None else None
        inherit_graph = (
            tuple(tuple(int(v) for v in row) for row in working_graph)
            if working_graph is not None
            else None
        )
        members: list[StructureRecord] = []
        for ordinal, coords in sorted(kept, key=lambda item: item[0]):
            members.append(
                StructureRecord(
                    id=conformer_output_id(work_item.logical_key, ordinal),
                    atoms=tuple(driving.atoms),
                    coordinates=tuple(
                        (float(x), float(y), float(z)) for x, y, z in coords.tolist()
                    ),
                    charge=charge,
                    multiplicity=multiplicity,
                    parent_ids=parent_ids,
                    lineage_root_id=lineage_root,
                    source_step_id=work_item.step_id,
                    source_work_item_id=work_item.id,
                    role="conformer",
                    ordinal=ordinal,
                    group_key=group_key,
                    metadata=FrozenDict(
                        {CONFORMER_MEMBER_METADATA_KEY: ordinal, "seed": int(seed)}
                    ),
                    topology_patch=inherit_patch,
                    working_topology=inherit_graph,
                )
            )
        return members

    def _complete_legacy_grid(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        *,
        driving: StructureRecord,
        seed: int,
        native: Mapping[str, Any],
        scientific: Any,
        total: int,
        kept: list[tuple[int, np.ndarray]],
        clash_dropped: int,
        max_conformers: int | None,
        summary_prefix: str,
        extra_details: dict[str, Any],
        report_extra: dict[str, Any] | None,
        extra_diagnostics: tuple[Diagnostic, ...] = (),
        working_graph: list[list[int]] | None = None,
    ) -> WorkItemResult:
        """Finish one legacy grid run: cap, members, report, result."""
        if not kept:
            raise DomainError(
                f"confgen generated no clash-free conformers ({total} grid points, "
                f"{clash_dropped} clash-dropped)"
            )
        if max_conformers is not None and len(kept) > max_conformers:
            # Versioned survivor cap (post-geometry shuffle-truncate-restore):
            # different science from v3 pre-geometry sampling, never mixed.
            kept = legacy_cap_v1(
                kept,
                seed=int(seed),
                logical_key=work_item.logical_key,
                cap=max_conformers,
                key=lambda item: item[0],
            )
        members = self._build_conformer_members(
            driving=driving,
            kept=kept,
            seed=int(seed),
            work_item=work_item,
            scientific=scientific,
            working_graph=working_graph,
        )
        artifacts = self._write_report(
            work_item,
            context,
            driving,
            members,
            int(seed),
            native,
            grid=total,
            clash_dropped=clash_dropped,
            path_resolution=report_extra,
        )
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        details: dict[str, Any] = {
            "members": len(members),
            "seed": int(seed),
            "grid_points": total,
            "clash_dropped": clash_dropped,
        }
        details.update(extra_details)
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=StructureSet(tuple(members)),
            results=ResultSet(),
            artifacts=artifacts,
            diagnostics=(
                Diagnostic(
                    code="confgen_completed",
                    message=(
                        f"{summary_prefix}, kept {len(members)} " f"({clash_dropped} clash-dropped)"
                    ),
                    severity=DiagnosticSeverity.INFO,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict(details),
                ),
                *extra_diagnostics,
            ),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    def _run_legacy_paths(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
        *,
        scientific: Any,
        seed: int,
        driving: StructureRecord,
        native: Mapping[str, Any],
        chains: list[list[int]],
        angle_step: int,
        rotate_side: str,
        chain_steps_raw: Any,
        chain_angles_raw: Any,
        no_rotate: set[tuple[int, int]],
        add_bond: list[tuple[int, int]],
        del_bond: list[tuple[int, int]],
        bond_scale: float,
        clash_threshold: float,
        max_conformers: int | None,
        strict_path_bond_check: bool,
    ) -> WorkItemResult:
        """Run the Phase 0 strict paths mode (paths [+ chains]).

        Version behavior: declaring ``paths`` opts into the strict
        canonicalization contract (unordered-bond dedup, direction/sampling
        conflicts fail closed, ring crossings refused, raw Cartesian size
        guarded pre-geometry). Pure-``chains`` items never enter this method
        and keep bit-for-bit legacy behavior. Numerics reuse the legacy
        Rodrigues application and clash filter through the oriented adapter
        (per-rotor moving sides); the typed-v3 stage is never involved.
        Resolution runs per work item on the driving structure (which may be
        an upstream product), so upstream-supplied geometry resolves with the
        same pure resolver; submission-time document checks stay advisory.
        """
        prep = self._prepare_path_rotors(
            driving=driving,
            native=native,
            chains=chains,
            angle_step=angle_step,
            rotate_side=rotate_side,
            chain_steps_raw=chain_steps_raw,
            chain_angles_raw=chain_angles_raw,
            no_rotate=no_rotate,
            add_bond=add_bond,
            del_bond=del_bond,
            bond_scale=bond_scale,
            strict_path_bond_check=strict_path_bond_check,
        )
        rotors: list[CanonicalRotor] = prep["rotors"]
        resolution = prep["resolution"]
        total: int = prep["canonical_size"]
        if should_cancel is not None and should_cancel():
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        try:
            kept, clash_dropped = self._realize_grid_members(
                driving_coordinates=np.asarray(driving.coordinates, dtype=np.float64),
                adjacency=prep["adjacency"],
                atomic_numbers=prep["atomic_numbers"],
                clash_threshold=clash_threshold,
                rot_bonds=[
                    (rotor.ordered[0], rotor.ordered[1], list(rotor.moving)) for rotor in rotors
                ],
                angle_lists=[list(rotor.angles) for rotor in rotors],
                total=total,
                should_cancel=should_cancel,
            )
        except _GridCancelled:
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        except ValueError as exc:
            raise DomainError(f"confgen {exc}") from exc
        path_resolution = self._path_resolution_payload(
            native,
            driving,
            rotors,
            resolution,
            declared_size=prep["declared_size"],
            canonical_size=total,
            strict_path_bond_check=strict_path_bond_check,
        )
        return self._complete_legacy_grid(
            work_item,
            context,
            wall_start,
            monotonic_start,
            driving=driving,
            seed=int(seed),
            native=native,
            scientific=scientific,
            total=total,
            kept=kept,
            clash_dropped=clash_dropped,
            max_conformers=max_conformers,
            summary_prefix=(
                f"confgen scanned {total} torsion points over {len(rotors)} path rotors"
            ),
            extra_details={
                "path_rotors": len(rotors),
                "topology_digest": resolution.topology_digest,
                "path_warnings": len(resolution.warnings),
            },
            report_extra=path_resolution,
            extra_diagnostics=(
                *self._resolved_path_diagnostics(path_resolution, work_item),
                *self._warning_diagnostics(resolution.warnings, work_item),
            ),
            working_graph=(
                [list(row) for row in prep["adjacency"]]
                if should_persist_working_graph(driving)
                else None
            ),
        )

    @staticmethod
    def _path_resolution_payload(
        native: Mapping[str, Any],
        driving: StructureRecord,
        rotors: Sequence[CanonicalRotor],
        resolution: Any,
        *,
        declared_size: int,
        canonical_size: int,
        strict_path_bond_check: bool,
    ) -> dict[str, Any]:
        """Build the JSON-serializable path-resolution audit record.

        Per-source routes come from the resolver authority
        (``resolution.declared_paths``), never re-derived. ``raw_cartesian_size``
        is the executed canonical task count (post-dedup, post-exclusion);
        ``declared_cartesian_size`` preserves the pre-dedup declaration space.
        """
        return {
            "driving_note": "resolved per work item on the driving structure",
            "driving_id": driving.id,
            "driving_geometry_digest": driving.geometry_digest,
            "atom_symbols": list(driving.atoms),
            "paths": [dict(item) for item in (native.get("paths") or [])],
            "declared_paths": [
                {
                    "source": item.source,
                    "start": item.raw_start,
                    "end": item.raw_end,
                    "move": item.move,
                    "route": [atom + 1 for atom in item.route],
                    "angles": list(item.angles),
                    "index_base": 1,
                }
                for item in resolution.declared_paths
            ],
            "strict_path_bond_check": bool(strict_path_bond_check),
            "topology_digest": resolution.topology_digest,
            "declared_cartesian_size": int(declared_size),
            "raw_cartesian_size": int(canonical_size),
            "warnings": list(resolution.warnings),
            "rotors": [
                {
                    "bond": [rotor.bond[0] + 1, rotor.bond[1] + 1],
                    "ordered": [rotor.ordered[0] + 1, rotor.ordered[1] + 1],
                    "moving_atom": rotor.moving_atom + 1,
                    "moving": [atom + 1 for atom in rotor.moving],
                    "fixed": [atom + 1 for atom in rotor.fixed],
                    "angles": list(rotor.angles),
                    "sources": list(rotor.sources),
                    "model": rotor.model,
                    "index_base": 1,
                }
                for rotor in rotors
            ],
        }

    @staticmethod
    def _driving(work_item: WorkItem) -> StructureRecord:
        sets = work_item.named_inputs.structures
        if "structure" in sets and len(sets["structure"]) == 1:
            record = sets["structure"][0]
            assert isinstance(record, StructureRecord)
            return record
        raise DomainError(
            "confgen requires exactly one 'structure' input per work item; "
            f"observed ports {sorted(sets)}"
        )

    @staticmethod
    def _as_str_list(raw: Any, key: str, n_chains: int) -> list[str] | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
            raise DomainError(f"confgen {key} must be a string or a list of strings")
        if len(raw) not in (1, n_chains):
            raise DomainError(f"confgen {key} count must be 1 or match chain count {n_chains}")
        items = list(raw)
        return items if len(items) == n_chains else items * n_chains

    @staticmethod
    def _bond_set(raw: Any, key: str) -> set[tuple[int, int]]:
        if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
            raise DomainError(f"confgen {key} must be a list of 'a-b' strings")
        try:
            return {tuple(sorted(parse_bond_pair(item))) for item in raw}  # type: ignore[misc]
        except ValueError as exc:
            raise DomainError(f"confgen {key}: {exc}") from exc

    @staticmethod
    def _bond_pair(item: Any) -> tuple[int, int]:
        if not isinstance(item, str):
            raise DomainError(f"confgen bond overrides must be 'a-b' strings, got {item!r}")
        try:
            return parse_bond_pair(item)
        except ValueError as exc:
            raise DomainError(f"confgen bond override: {exc}") from exc

    @staticmethod
    def _check_index(index: int, n_atoms: int, key: str) -> None:
        if index < 0 or index >= n_atoms:
            raise DomainError(f"confgen {key} index {index + 1} out of range for {n_atoms} atoms")

    def _write_report(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        driving: StructureRecord,
        members: list[StructureRecord],
        seed: int,
        native: Mapping[str, Any],
        *,
        grid: int,
        clash_dropped: int,
        path_resolution: dict[str, Any] | None = None,
    ) -> ArtifactSet:
        attempt_dir = context.attempt_dir(work_item)
        os.makedirs(attempt_dir, exist_ok=True)
        payload = {
            "seed": seed,
            "driving_id": driving.id,
            "chains": list(native.get("chains", [])),
            "grid_points": grid,
            "clash_dropped": clash_dropped,
            "limits": {"max_declared_states": _LEGACY_MAX_DECLARED_STATES},
            "members": [
                {
                    "id": record.id,
                    "ordinal": record.ordinal,
                    "geometry_digest": record.geometry_digest,
                }
                for record in members
            ],
        }
        if path_resolution is not None:
            payload["path_resolution"] = dict(path_resolution)
        name = "confgen_report.json"
        path = os.path.join(attempt_dir, name)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            with open(path, "rb") as handle:
                checksum = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            raise DomainError(f"confgen report write failed: {exc}") from exc
        prefix = context.run_relative_prefix(work_item)
        locator_path = f"{prefix}/{name}" if prefix else name
        ref = ArtifactRef(
            id=f"{work_item.id}/{name}",
            role=CONFGEN_REPORT_ROLE,
            locator=ArtifactLocator.run_relative(locator_path),
            checksum=checksum,
            producer_step_id=work_item.step_id,
            producer_work_item_id=work_item.id,
            subject_structure_id=driving.id,
            metadata=FrozenDict({"seed": seed}),
        )
        return ArtifactSet((ref,))

    def _fail(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        message: str,
    ) -> WorkItemResult:
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.FAILED,
            diagnostics=(
                _diagnostic(
                    NativeErrorCode.NATIVE_INPUT_ERROR,
                    message,
                    step_id=context.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                ),
            ),
            timing=timing,
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    def _cancelled(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
    ) -> WorkItemResult:
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.CANCELLED,
            diagnostics=(
                _diagnostic(
                    NativeErrorCode.CANCELLATION_ERROR,
                    "work item cancelled",
                    step_id=context.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details={"confirmed": True},
                ),
            ),
            timing=timing,
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )
