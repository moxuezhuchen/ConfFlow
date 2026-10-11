#!/usr/bin/env python3

"""V4 structure-transform executor: explicit pure set operations.

Pure executor: never shells to Gaussian/ORCA, calls no calculation pipeline, imports no
legacy runner code. One WorkItem carries the whole set on the ``structure`` port
(``ONE_OR_MORE SINGLE``); the step declares ``scientific.transform`` in ``{refine, deduplicate, filter}``.
Deduplicate collapses equal ``geometry_digest`` strictly within
``(charge, multiplicity, group_key, role)``; differing records are never
collapsed; lowest canonical id wins so selection is reorder-invariant.
Refine is topology-grouped RMSD dedup (plus element signature), dropped
only on a proven witness at/below threshold; budget-exhausted pairs are
unresolved and both kept with a note; typed non-covalent edges are
preserved by every mapping. Filter is id-ordered atom-count/energy
selection; unknown native keys fail closed. Never raises: every failure
is a typed ``FAILED`` result.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.elements import atomic_number
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from ..science.topology import resolve_working_adjacency
from .energy_filter import (
    ENERGY_PARAM_KEYS,
    parse_energy_params,
    select_by_energy,
    uses_energy_params,
)
from .work_item_executor import (
    ItemExecutionContext,
    pure_cancelled_result,
    pure_fail_result,
)

__all__ = ["TransformExecutor", "TRANSFORM_KINDS"]

TRANSFORM_KINDS = ("refine", "deduplicate", "filter")

#: Default RMSD dedup threshold (``RefineOptions.threshold``).
REFINE_DEFAULT_RMSD_THRESHOLD_ANGSTROM = 0.25

#: Default bond-perception scale — the ConfGen perception default
#: (``tolerances.bond_scale``, ``confgen_schema.ConfgenToleranceModel``).
#: Explicit scales and persisted working topologies retain their precedence.
REFINE_DEFAULT_BOND_SCALE = 1.15

#: Default mapping-search node budget per compared pair
#: (``topology_mapping.DEFAULT_MAPPING_NODE_BUDGET``; equality is tested).  The
#: science modules are imported lazily inside ``_frame``/``_duplicate_of`` so the
#: worker import closure does not load ``confflow.core`` for non-refine runs.
REFINE_DEFAULT_MAPPING_BUDGET = 1000

#: Allowed step-native keys per kind (unknown keys fail closed).
#: Filter energy keys (N3) read energies/frequencies from the bound results
#: port; atom-count keys keep their structure-only meaning.
FILTER_NATIVE_KEYS = frozenset(
    {"max_structures", "min_atoms", "max_atoms"} | set(ENERGY_PARAM_KEYS)
)
REFINE_NATIVE_KEYS = frozenset(
    {
        "rmsd_threshold_angstrom",
        "bond_scale",
        "heavy_only",
        "max_structures",
        "mapping_budget",
        "topology_bonds",
    }
)


def _topology_identity(record: StructureRecord) -> tuple[Any, ...]:
    """Return the topology half of the scientific-identity grouping key."""
    patch = record.topology_patch
    patch_key = None
    if patch is not None and not patch.is_empty:
        patch_key = (
            tuple(patch.add_edges),
            tuple(patch.delete_edges),
        )
    graph_key = None
    if record.working_topology is not None:
        graph_key = tuple(tuple(row) for row in record.working_topology)
    return (patch_key, graph_key)


def _scientific_group(record: StructureRecord) -> tuple[Any, ...]:
    """Return the scientific-identity grouping key for one record."""
    return (
        record.charge,
        record.multiplicity,
        record.group_key,
        record.role,
        tuple(record.atoms),
        _topology_identity(record),
    )


_CONNECTIVITY_NOTE_LIMIT = 8


def _graph_bonds(frame: Mapping[str, Any]) -> frozenset[tuple[int, int]]:
    """Return the covalent bonds (0-based index pairs) of one comparison frame."""
    adjacency = frame["graph"].adjacency
    return frozenset(
        (int(first), int(second))
        for first, row in enumerate(adjacency)
        for second in row
        if int(first) < int(second)
    )


def _bond_labels(bonds: Sequence[tuple[int, int]], atoms: Sequence[str]) -> str:
    """Render bonds as 1-based ``C2-C5`` labels, capped for readability."""
    labels = [f"{atoms[a]}{a + 1}-{atoms[b]}{b + 1}" for a, b in sorted(bonds)]
    if len(labels) > _CONNECTIVITY_NOTE_LIMIT:
        extra = len(labels) - _CONNECTIVITY_NOTE_LIMIT
        labels = [*labels[:_CONNECTIVITY_NOTE_LIMIT], f"... (+{extra} more)"]
    return "[" + ", ".join(labels) + "]"


def _connectivity_notes(
    ordered: Sequence[StructureRecord], frames: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Report structures whose bonding graph differs from the majority of their group."""
    bond_sets = {record.id: _graph_bonds(frames[record.id]) for record in ordered}
    counts: dict[frozenset[tuple[int, int]], int] = {}
    first_seen: dict[frozenset[tuple[int, int]], str] = {}
    for record in ordered:
        bonds = bond_sets[record.id]
        counts[bonds] = counts.get(bonds, 0) + 1
        first_seen.setdefault(bonds, record.id)
    if len(counts) < 2:
        return []
    reference = min(counts, key=lambda bonds: (-counts[bonds], first_seen[bonds]))
    notes: list[str] = []
    for record in ordered:
        bonds = bond_sets[record.id]
        if bonds == reference:
            continue
        notes.append(
            f"connectivity of {record.id} differs from the majority bonding graph of its group "
            f"({counts[reference]} of {len(ordered)} structures): "
            f"gained {_bond_labels(sorted(bonds - reference), record.atoms)}, "
            f"lost {_bond_labels(sorted(reference - bonds), record.atoms)}"
        )
    return notes


class TransformExecutor:
    """Pure explicit structure-set transformation executor."""

    def execute(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one transform work item (never raises)."""
        wall_start = time.time()
        monotonic_start = time.monotonic()
        try:
            return self._run(work_item, context, wall_start, monotonic_start, should_cancel)
        except DomainError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except Exception as exc:  # fail closed; executors never raise into batch
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"transform internal failure: {exc}",
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
        kind = scientific.transform
        if kind not in TRANSFORM_KINDS:
            raise DomainError(
                f"unknown structure transform kind {kind!r}; allowed {list(TRANSFORM_KINDS)}"
            )
        members = self._inputs(work_item)
        if should_cancel is not None and should_cancel():
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        native = dict(scientific.native)
        if kind == "deduplicate":
            if native:
                raise DomainError(
                    f"transform deduplicate takes no native parameters, got {sorted(native)}"
                )
            out, notes = self._deduplicate(members)
        elif kind == "filter":
            results = work_item.named_inputs.results.get("results", ResultSet())
            out, notes = self._filter(members, native, results)
        else:
            out, notes = self._refine(members, native)
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=StructureSet(tuple(out)),
            results=ResultSet(),
            artifacts=ArtifactSet(),
            diagnostics=(
                Diagnostic(
                    code=f"transform_{kind}_completed",
                    message=f"transform {kind} kept {len(out)} of {len(members)} structures",
                    severity=DiagnosticSeverity.INFO,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict(
                        {
                            "kind": kind,
                            "kept": len(out),
                            "dropped": len(members) - len(out),
                            "notes": list(notes),
                        }
                    ),
                ),
            ),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    @staticmethod
    def _inputs(work_item: WorkItem) -> list[StructureRecord]:
        sets = work_item.named_inputs.structures
        if "structure" not in sets or len(sets["structure"]) < 1:
            raise DomainError(
                "transform requires a non-empty 'structure' input set "
                f"(ONE_OR_MORE SINGLE); observed ports {sorted(sets)}"
            )
        return list(sets["structure"])

    # -- deduplicate --------------------------------------------------------

    @staticmethod
    def _deduplicate(
        members: list[StructureRecord],
    ) -> tuple[list[StructureRecord], list[str]]:
        """Collapse content-identical records within scientific groups."""
        best: dict[tuple[Any, ...], StructureRecord] = {}
        notes: list[str] = []
        for record in sorted(members, key=lambda item: item.id):
            key = (_scientific_group(record), record.geometry_digest)
            if key in best:
                notes.append(
                    f"dropped duplicate {record.id} of {best[key].id} "
                    f"(identical {record.geometry_digest})"
                )
                continue
            best[key] = record
        ordered = sorted(best.values(), key=lambda item: item.id)
        return ordered, notes

    # -- refine ---------------------------------------------------------------

    def _refine(
        self,
        members: list[StructureRecord],
        native: Mapping[str, Any],
    ) -> tuple[list[StructureRecord], list[str]]:
        """Topology-grouped RMSD dedup with an explicit threshold."""
        unknown = sorted(set(native) - REFINE_NATIVE_KEYS)
        if unknown:
            raise DomainError(
                f"transform refine got unknown native keys {unknown}; "
                f"allowed {sorted(REFINE_NATIVE_KEYS)}"
            )
        threshold = float(
            native.get("rmsd_threshold_angstrom", REFINE_DEFAULT_RMSD_THRESHOLD_ANGSTROM)
        )
        bond_scale = float(native.get("bond_scale", REFINE_DEFAULT_BOND_SCALE))
        heavy_only = native.get("heavy_only", False)
        mapping_budget = native.get("mapping_budget", REFINE_DEFAULT_MAPPING_BUDGET)
        max_structures = native.get("max_structures")
        if not math.isfinite(threshold) or threshold < 0:
            raise DomainError(
                f"refine rmsd_threshold_angstrom must be finite and >= 0, got {threshold!r}"
            )
        if not math.isfinite(bond_scale) or bond_scale <= 0:
            raise DomainError(f"refine bond_scale must be a positive number, got {bond_scale!r}")
        declared = self._declared_topology(native)
        if (
            isinstance(mapping_budget, bool)
            or not isinstance(mapping_budget, int)
            or mapping_budget < 0
        ):
            raise DomainError(
                f"refine mapping_budget must be an integer >= 0, got {mapping_budget!r}"
            )
        if not isinstance(heavy_only, bool):
            raise DomainError(f"refine heavy_only must be a boolean, got {heavy_only!r}")
        if max_structures is not None and (
            isinstance(max_structures, bool)
            or not isinstance(max_structures, int)
            or max_structures < 0
        ):
            raise DomainError(
                f"refine max_structures must be an integer >= 0, got {max_structures!r}"
            )
        groups: dict[tuple[Any, ...], list[StructureRecord]] = {}
        for record in members:
            groups.setdefault(_scientific_group(record), []).append(record)
        out: list[StructureRecord] = []
        notes: list[str] = []
        for group in groups.values():
            ordered = sorted(group, key=lambda item: item.id)
            frames = {record.id: self._frame(record, bond_scale, declared) for record in ordered}
            notes.extend(_connectivity_notes(ordered, frames))
            retained: list[StructureRecord] = []
            for record in ordered:
                witness = self._duplicate_of(
                    record,
                    frames[record.id],
                    retained,
                    frames,
                    threshold,
                    bool(heavy_only),
                    int(mapping_budget),
                    notes,
                )
                if witness is not None:
                    other, rmsd = witness
                    notes.append(
                        f"dropped {record.id} as duplicate of {other.id} "
                        f"(rmsd {rmsd:.4f} A < {threshold:g} A)"
                    )
                    continue
                retained.append(record)
            out.extend(retained)
        out.sort(key=lambda item: item.id)
        if max_structures is not None and len(out) > max_structures:
            dropped = [record.id for record in out[max_structures:]]
            notes.append(f"truncated to {max_structures}: dropped {dropped}")
            out = out[:max_structures]
        return out, notes

    @staticmethod
    def _adjacency(record: StructureRecord, bond_scale: float) -> list[list[int]]:
        """Resolve the intended bond adjacency of one record."""
        try:
            numbers = [atomic_number(symbol) for symbol in record.atoms]
        except Exception as exc:
            raise DomainError(f"refine element lookup failed for {record.id}: {exc}") from exc
        try:
            return resolve_working_adjacency(
                record, numbers, record.coordinates, bond_scale=bond_scale
            )
        except DomainError as exc:
            raise DomainError(f"refine working topology failed for {record.id}: {exc}") from exc
        except ValueError as exc:
            raise DomainError(f"refine bond perception failed for {record.id}: {exc}") from exc

    @staticmethod
    def _declared_topology(native: Mapping[str, Any]) -> dict[str, Any] | None:
        """Resolve ``topology_bonds`` into the normalised ConfGen spec, or ``None``."""
        raw = native.get("topology_bonds")
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise DomainError(f"refine topology_bonds must be a mapping, got {raw!r}")
        allowed = {
            "index_base",
            "bonds",
            "add_bond",
            "del_bond",
            "atoms",
            "bond_scale",
            "coordination",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise DomainError(
                f"refine topology_bonds got unknown members {unknown}; allowed {sorted(allowed)}"
            )
        if "bond_scale" in native:
            raise DomainError(
                "refine got both bond_scale and topology_bonds; with a declared topology the "
                "perception scale is topology_bonds.bond_scale (default: the ConfGen default)"
            )
        spec: dict[str, Any] = {
            "schema_version": 4,
            "index_base": raw.get("index_base", 1),
            "topology": {
                key: raw[key] for key in ("bonds", "add_bond", "del_bond", "atoms") if key in raw
            },
        }
        if raw.get("coordination") is not None:
            spec["coordination"] = raw["coordination"]
        if raw.get("bond_scale") is not None:
            spec["tolerances"] = {"bond_scale": raw["bond_scale"]}
        from ..science.confgen.search_spec import normalize_search_spec

        try:
            return dict(normalize_search_spec(spec))
        except (ValueError, TypeError, KeyError) as exc:
            raise DomainError(f"refine topology_bonds is invalid: {exc}") from exc

    @classmethod
    def _frame(
        cls,
        record: StructureRecord,
        bond_scale: float,
        declared: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the comparison frame (atoms, coordinates, bonding graph) of one record."""
        from ..science.topology_mapping import graph_from_adjacency, with_typed_edges

        if declared is None:
            graph = graph_from_adjacency(record.atoms, cls._adjacency(record, bond_scale))
        else:
            from ..science.confgen.topology import build_typed_graph

            try:
                adjacency, typed = build_typed_graph(record, declared["topology"], declared)
            except (ValueError, TypeError, KeyError) as exc:
                raise DomainError(f"refine topology_bonds does not fit {record.id}: {exc}") from exc
            graph = with_typed_edges(
                graph_from_adjacency(record.atoms, adjacency),
                [
                    (edge.a, edge.b, edge.type.value)
                    for edge in typed.edges
                    if edge.type.value != "COVALENT"
                ],
            )
        return {
            "atoms": list(record.atoms),
            "coords": np.asarray(record.coordinates, dtype=np.float64),
            "graph": graph,
        }

    @staticmethod
    def _duplicate_of(
        record: StructureRecord,
        frame: dict[str, Any],
        retained: Sequence[StructureRecord],
        frames: Mapping[str, dict[str, Any]],
        threshold: float,
        heavy_only: bool,
        mapping_budget: int,
        notes: list[str],
    ) -> tuple[StructureRecord, float] | None:
        """Return the retained duplicate witness for *record*, if proven."""
        from ..science.frame_compare import compare_frames

        for other in retained:
            verdict = compare_frames(
                frame,
                frames[other.id],
                threshold=threshold,
                heavy_only=heavy_only,
                node_budget=mapping_budget,
            )
            if verdict.status == "duplicate" and verdict.witness_rmsd is not None:
                return other, float(verdict.witness_rmsd)
            if verdict.status == "unresolved":
                notes.append(
                    f"kept {record.id}: comparison with {other.id} is unresolved ({verdict.reason})"
                )
        return None

    # -- filter ---------------------------------------------------------------

    @staticmethod
    def _filter(
        members: list[StructureRecord],
        native: Mapping[str, Any],
        results: ResultSet | None = None,
    ) -> tuple[list[StructureRecord], list[str]]:
        unknown = sorted(set(native) - FILTER_NATIVE_KEYS)
        if unknown:
            raise DomainError(
                f"transform filter got unknown native keys {unknown}; "
                f"allowed {sorted(FILTER_NATIVE_KEYS)}"
            )
        ordered = sorted(members, key=lambda record: record.id)
        kept = list(ordered)
        notes: list[str] = []
        min_atoms = native.get("min_atoms")
        max_atoms = native.get("max_atoms")
        if min_atoms is not None:
            if isinstance(min_atoms, bool) or not isinstance(min_atoms, int) or min_atoms < 1:
                raise DomainError(f"filter min_atoms must be an integer >= 1, got {min_atoms!r}")
            before = len(kept)
            kept = [record for record in kept if len(record.atoms) >= min_atoms]
            notes.append(f"min_atoms dropped {before - len(kept)}")
        if max_atoms is not None:
            if isinstance(max_atoms, bool) or not isinstance(max_atoms, int) or max_atoms < 1:
                raise DomainError(f"filter max_atoms must be an integer >= 1, got {max_atoms!r}")
            before = len(kept)
            kept = [record for record in kept if len(record.atoms) <= max_atoms]
            notes.append(f"max_atoms dropped {before - len(kept)}")
        if uses_energy_params(native):
            params = parse_energy_params(native)
            kept, energy_notes = select_by_energy(
                kept, results if results is not None else ResultSet(), params
            )
            notes.extend(energy_notes)
        max_structures = native.get("max_structures")
        if max_structures is not None:
            if (
                isinstance(max_structures, bool)
                or not isinstance(max_structures, int)
                or max_structures < 0
            ):
                raise DomainError(
                    f"filter max_structures must be an integer >= 0, got {max_structures!r}"
                )
            kept = kept[: int(max_structures)]
            notes.append(f"truncated to {len(kept)}")
        return kept, notes

    # -- outcomes ---------------------------------------------------------------

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
