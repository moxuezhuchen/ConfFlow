#!/usr/bin/env python3

"""V4 confgen executor: conformer-generation ensemble production.

Pure executor: never shells to Gaussian/ORCA, never calls the calculation
pipeline, never imports legacy runner code.

The only science path is the typed v3 scope; a scope without
``schema_version: 3`` fails closed.

Typed v3 scopes (``native.schema_version == 3``) run the
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
import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from ..science.topology import (
    should_persist_working_graph,
)
from .output_identity import (
    CONFORMER_MEMBER_METADATA_KEY,
    conformer_output_id,
    endpoint_lineage,
)
from .work_item_executor import (
    ItemExecutionContext,
    pure_cancelled_result,
    pure_fail_result,
)

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
    """Conformer-generation executor (typed v3 engine)."""

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
        return self._fail(
            work_item,
            context,
            wall_start,
            monotonic_start,
            "confgen requires a typed schema_version 3 scope",
        )

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
        *,
        registry: Any | None = None,
    ) -> WorkItemResult:
        """Run the typed v3 engine path (deterministic unless sampling).

        A4a: optional ``registry`` passthrough to the CORE boundary
        (default ``None`` keeps the existing call form).
        """
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
                driving,
                spec,
                input_key,
                inherited_scope=upstream.get("inherited_scope"),
                registry=registry,
            )
        except ValueError as exc:
            raise DomainError(f"confgen v3 spec rejected: {exc}") from exc
        engine = ConfgenEngine(allow_preserve_input=True, registry=registry)
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
        if published == 0:
            return WorkItemResult(
                work_item_id=work_item.id,
                status=WorkItemStatus.FAILED,
                structures=StructureSet(),
                results=results,
                artifacts=artifacts,
                diagnostics=(
                    self._no_realized_structure_diagnostic(work_item, report),
                    *self._resolved_path_diagnostics(resolved_paths, work_item),
                    *self._warning_diagnostics(path_warnings, work_item),
                    *self._component_diagnostic_warnings(report, work_item),
                ),
                timing=timing,
                recovery=RecoveryInfo(profile="none", attempted=False),
                semantic_digest=work_item.semantic_digest,
            )
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
                *self._component_diagnostic_warnings(report, work_item),
            ),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    @staticmethod
    def _no_realized_structure_diagnostic(
        work_item: WorkItem, report: Mapping[str, Any]
    ) -> Diagnostic:
        """Explain a run that published no structure, from the enumeration ledger.

        Uses the terminal-status vocabulary of the report's ``target_categories``
        (REALIZED / UNRESOLVED / DRIFTED / ...): a run with no REALIZED target
        is a failure, never a completed empty ensemble.
        """
        counts = report.get("counts", {})
        categories = {
            name: int(value)
            for name, value in dict(counts.get("target_categories", {})).items()
            if int(value)
        }
        anomalies = {name: int(value) for name, value in dict(counts.get("anomalies", {})).items()}
        raw = counts.get("raw", "?")
        outcome = ", ".join(f"{name}={value}" for name, value in sorted(categories.items()))
        reason = (
            f"; anomalies: {', '.join(f'{name}={value}' for name, value in sorted(anomalies.items()))}"
            if anomalies
            else ""
        )
        return Diagnostic(
            code="confgen_no_realized_structures",
            message=(
                f"confgen v3 realized no structure: 0 of {raw} raw targets published "
                f"(target outcomes: {outcome or 'none recorded'}{reason}); "
                "see the ensemble report for the per-target ledger"
            ),
            severity=DiagnosticSeverity.ERROR,
            step_id=work_item.step_id,
            work_item_id=work_item.id,
            logical_key=work_item.logical_key,
            details=FrozenDict(
                {
                    "raw_targets": raw,
                    "published": 0,
                    "target_categories": categories,
                    "anomalies": anomalies,
                }
            ),
        )

    @staticmethod
    def _attach_inherited_scope(
        results: ResultSet,
        leaves: list[Any],
        context: Any,
    ) -> ResultSet:
        """Attach resolved scope descriptors for downstream chaining.

        Traverses the registry calling each component's
        ``serialize_inherited_state`` (state slices from the generic key,
        never ``context.input_state_key`` attributes); the v3 wire
        adapter assembles the legacy mapping byte-identically
        (torsions/rings/coordination order, empties, label verbatim).
        Scope lives in domain-semantic ``Provenance`` metadata only.
        """
        import dataclasses

        from confflow.science.confgen.wire_v3 import (
            assemble_inherited_scope,
            from_wire_key,
        )

        from ..domain.result import Provenance

        resolved = context.resolved_spec
        registry = getattr(context, "registry", None)
        if registry is None:
            from confflow.science.confgen.registry import default_registry

            registry = default_registry()
        attached = []
        for record, leaf in zip(results, leaves):
            key = leaf.state_key
            try:
                generic = from_wire_key(key)
                comps = dict(generic.components)
            except Exception:
                # Already generic (defensive; executor leaves are v3).
                try:
                    comps = dict(key.components)
                except Exception:
                    comps = {}
            per: dict[str, Any] = {}
            for descriptor in getattr(registry, "descriptors", ()) or ():
                cid = str(getattr(descriptor, "id", ""))
                if not cid:
                    continue
                if cid in comps:
                    sv: Any = comps[cid]
                else:
                    # Missing slice: replace-merge (coordination) means None,
                    # merge (rings/torsions) means {} (generic via state_merge).
                    try:
                        mode = str(getattr(descriptor, "state_merge", "merge"))
                    except Exception:
                        mode = "merge"
                    sv = None if mode == "replace" else {}
                hook = getattr(descriptor, "serialize_inherited_state", None)
                if not callable(hook):
                    continue
                per[cid] = hook(resolved, sv, context)
            scope = assemble_inherited_scope(per)
            existing = record.provenance
            meta = dict(existing.metadata) if existing is not None and existing.metadata else {}
            meta["inherited_scope"] = scope
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
        _comp_diags = report.get("component_diagnostics") if isinstance(report, Mapping) else None
        if isinstance(_comp_diags, Mapping) and dict(_comp_diags):
            payload["component_diagnostics"] = dict(_comp_diags)
        _warn_rows, _err_rows = ConfgenExecutor._serialized_component_lists(report, work_item)
        if _warn_rows:
            payload["warnings"] = _warn_rows
        if _err_rows:
            payload["errors"] = _err_rows
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

    # -- helpers ---------------------------------------------------------

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

    @staticmethod
    def _component_diagnostic_warnings(
        report: Mapping[str, Any], work_item: WorkItem
    ) -> tuple[Diagnostic, ...]:
        """Expose engine component diagnostics as visible ensemble warnings.

        Generic over component ids (never names one); distorted rows become
        WARNING diagnostics, explicit hook errors become ERROR diagnostics.
        Atom ids are global 0-based (``index_base`` 0, stated in the message
        and details so readers never mistake them for 1-based labels).
        Empty or absent input yields no diagnostics so existing outputs
        stay byte-identical.
        """
        raw = report.get("component_diagnostics") if isinstance(report, Mapping) else None
        if not isinstance(raw, Mapping) or not dict(raw):
            return ()
        found: list[Diagnostic] = []
        for comp_id in sorted(str(k) for k in raw.keys()):
            payload = raw.get(comp_id)
            if not isinstance(payload, Mapping):
                continue
            if "error" in dict(payload):
                found.append(
                    Diagnostic(
                        code="confgen_component_diagnostics_error",
                        message=(
                            f"confgen component {comp_id} diagnostics error: "
                            f"{payload.get('error')}"
                        ),
                        severity=DiagnosticSeverity.ERROR,
                        step_id=work_item.step_id,
                        work_item_id=work_item.id,
                        logical_key=work_item.logical_key,
                        details=FrozenDict(
                            {"component": str(comp_id), "error": str(payload.get("error"))}
                        ),
                    )
                )
                continue
            entries = payload.get("distorted_input")
            if not isinstance(entries, (list, tuple)) or not entries:
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                ring = entry.get("ring_id")
                atom = entry.get("atom")
                observed = entry.get("observed")
                expected = entry.get("expected")
                found.append(
                    Diagnostic(
                        code="confgen_distorted_input",
                        message=(
                            f"confgen distorted input {comp_id}"
                            f"{('.' + str(ring)) if ring is not None else ''}"
                            f" atom {atom} (0-based global, index_base 0):"
                            f" observed {observed} expected {expected}"
                        ),
                        severity=DiagnosticSeverity.WARNING,
                        step_id=work_item.step_id,
                        work_item_id=work_item.id,
                        logical_key=work_item.logical_key,
                        details=FrozenDict(
                            {
                                "component": str(comp_id),
                                "ring_id": ring,
                                "atom": atom,
                                "observed": observed,
                                "expected": expected,
                                "index_base": 0,
                            }
                        ),
                    )
                )
        return tuple(found)

    @staticmethod
    def _serialized_component_lists(
        report: Mapping[str, Any], work_item: WorkItem
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Serialize component warnings/errors for the ensemble artifact.

        Built from the same ``_component_diagnostic_warnings`` objects as
        the runtime diagnostics so artifact and runtime stay consistent.
        WARNING rows and ERROR rows are returned separately; errors stay
        explicitly visible and are never relabeled as normal warnings.
        """
        warn_rows: list[dict[str, Any]] = []
        err_rows: list[dict[str, Any]] = []
        for diag in ConfgenExecutor._component_diagnostic_warnings(report, work_item):
            sev = diag.severity
            sev_name = sev.value if isinstance(sev, DiagnosticSeverity) else str(sev)
            details = dict(diag.details) if isinstance(diag.details, Mapping) else diag.details
            row = {
                "code": str(diag.code),
                "message": str(diag.message),
                "severity": str(sev_name),
                "details": details,
            }
            if str(sev_name) == DiagnosticSeverity.WARNING.value:
                warn_rows.append(row)
            else:
                err_rows.append(row)
        return warn_rows, err_rows

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

    def _fail(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        message: str,
    ) -> WorkItemResult:
        return pure_fail_result(
            work_item,
            step_id=context.step_id,
            wall_start=wall_start,
            monotonic_start=monotonic_start,
            message=message,
        )

    def _cancelled(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
    ) -> WorkItemResult:
        return pure_cancelled_result(
            work_item,
            step_id=context.step_id,
            wall_start=wall_start,
            monotonic_start=monotonic_start,
        )
