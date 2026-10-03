#!/usr/bin/env python3

"""V4 structure-transform executor: explicit pure set operations.

Pure executor: never shells to Gaussian/ORCA, never calls the calculation
pipeline, never imports legacy runner code.  One :class:`WorkItem` carries
the whole structure set on the ``structure`` port (``ONE_OR_MORE SINGLE``)
and the step declares ``scientific.transform`` in
``{refine, deduplicate, filter}``.

Science provenance (extracted, not invented)
--------------------------------------------
The geometry comparison science lives in :mod:`confflow.science`
(exclusive V4 ownership, no legacy orchestration transitives):

- bond perception ``d < bond_scale * (r_i + r_j)`` with the 0.4 A minimum
  and GaussView covalent radii via :mod:`confflow.science.bonds` (edge
  over the centralised ``core.bonding`` authority);
- default ``bond_scale = 1.2`` is ``topology.BOND_SCALE_FACTOR``;
- default ``rmsd_threshold_angstrom = 0.25`` is
  ``RefineOptions.threshold``;
- duplicate comparison is :func:`confflow.science.frame_compare.compare_frames`:
  RMSD is only evaluated under a legal element/edge-preserving mapping
  found by the budgeted exact search of
  :mod:`confflow.science.topology_mapping` (so atoms that differ only by
  symmetry-equivalent labels, such as the hydrogens of a methyl group, are
  recognised as duplicates).  This module only wires those algorithms to
  typed V4 domain records.

What is NOT carried over: XYZ file I/O, multiprocessing pools, CLI
progress, energy-window / imaginary-frequency filtering (transform input
ports carry structures only, so ``ewin``/``imag`` have no input data and
are not applied — stated here, not silently), and MMFF optimization.

Kinds:

- ``deduplicate``: collapse content-identical records (equal
  ``geometry_digest``) strictly within one scientific-identity group
  ``(charge, multiplicity, group_key, role)``.  Records differing in any
  of those fields are never collapsed.  The representative is the lowest
  canonical id, so selection is reorder-invariant.
- ``refine``: topology-grouped RMSD dedup.  Same scientific-identity
  grouping as above plus element signature; within a group, candidates
  in canonical id order are compared against retained representatives
  and dropped only on a proven RMSD witness at or below the threshold.
  A candidate is a duplicate when some legal mapping gives an RMSD
  strictly below the threshold.  When the mapping search exhausts its node
  budget (``mapping_budget``, default 1000 nodes per pair) the pair is
  *unresolved*: both structures are kept and the step notes say so.
  ``topology_bonds`` (optional) declares the bonding topology exactly as a
  ConfGen v3 ``topology`` does (``bonds`` or ``add_bond``/``del_bond``, typed
  ``COVALENT``/``COORDINATION``/``FORMING``/``BREAKING`` edges, ``atoms``,
  ``index_base``, plus ``coordination`` and ``bond_scale``) and is built by the
  very function ConfGen uses (``planner.build_typed_graph``); typed
  non-covalent edges must be preserved by every mapping.  Without it the
  topology is perceived from geometry as before.
  Optional ``max_structures`` truncates in id order.
- ``filter``: explicit ``min_atoms`` / ``max_atoms`` / ``max_structures``
  selection in canonical id order.  Unknown native keys fail closed.

All results bind output entities; no native syntax is interpreted beyond
the documented transform-native vocabulary.  The executor never raises:
every failure is a typed ``FAILED`` result.
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
from ..science.bonds import perceive_adjacency
from .native import NativeErrorCode
from .work_item_executor import ItemExecutionContext, _diagnostic

__all__ = ["TransformExecutor", "TRANSFORM_KINDS"]

TRANSFORM_KINDS = ("refine", "deduplicate", "filter")

#: Default RMSD dedup threshold (``RefineOptions.threshold``).
REFINE_DEFAULT_RMSD_THRESHOLD_ANGSTROM = 0.25

#: Default bond-perception scale (``topology.BOND_SCALE_FACTOR``).
REFINE_DEFAULT_BOND_SCALE = 1.2

#: Default mapping-search node budget per compared pair
#: (``topology_mapping.DEFAULT_MAPPING_NODE_BUDGET``; equality is tested).  The
#: science modules are imported lazily inside ``_frame``/``_duplicate_of`` so the
#: worker import closure does not load ``confflow.core`` for non-refine runs.
REFINE_DEFAULT_MAPPING_BUDGET = 1000

#: Allowed step-native keys per kind (unknown keys fail closed).
FILTER_NATIVE_KEYS = frozenset({"max_structures", "min_atoms", "max_atoms"})
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


def _scientific_group(record: StructureRecord) -> tuple[Any, ...]:
    """Return the scientific-identity grouping key for one record.

    Records collapse only within one group: charge, multiplicity,
    group key, role, and element signature must all agree.  Geometry
    content alone never merges distinct scientific entities.
    """
    return (
        record.charge,
        record.multiplicity,
        record.group_key,
        record.role,
        tuple(record.atoms),
    )


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
            out, notes = self._filter(members, native)
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
        """Collapse content-identical records within scientific groups.

        Grouping is ``(charge, multiplicity, group_key, role, elements)``
        plus equal ``geometry_digest``; the lowest canonical id wins, so
        representative selection is invariant under input reordering.
        """
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
        """Topology-grouped RMSD dedup with an explicit threshold.

        Energy-window and imaginary-frequency filtering from the legacy
        refine block have no input data on transform ports (structures
        only) and are therefore not applied.
        """
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
        """Perceive the bond adjacency of one record (central authority)."""
        try:
            numbers = [atomic_number(symbol) for symbol in record.atoms]
        except Exception as exc:
            raise DomainError(f"refine element lookup failed for {record.id}: {exc}") from exc
        try:
            return perceive_adjacency(numbers, record.coordinates, bond_scale=bond_scale)
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
            "coordination",
            "bond_scale",
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
            "schema_version": 3,
            "index_base": raw.get("index_base", 1),
            "topology": {
                key: raw[key] for key in ("bonds", "add_bond", "del_bond", "atoms") if key in raw
            },
        }
        if raw.get("coordination") is not None:
            spec["coordination"] = raw["coordination"]
        if raw.get("bond_scale") is not None:
            spec["tolerances"] = {"bond_scale": raw["bond_scale"]}
        from ..science.confgen.planner import normalize_spec

        try:
            return dict(normalize_spec(spec))
        except (ValueError, TypeError, KeyError) as exc:
            raise DomainError(f"refine topology_bonds is invalid: {exc}") from exc

    @classmethod
    def _frame(
        cls, record: StructureRecord, bond_scale: float, declared: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return the comparison frame (atoms, coordinates, bonding graph) of one record."""
        from ..science.topology_mapping import graph_from_adjacency, with_typed_edges

        if declared is None:
            graph = graph_from_adjacency(record.atoms, cls._adjacency(record, bond_scale))
        else:
            from ..science.confgen.planner import build_typed_graph

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
        """Return the retained duplicate witness for *record*, if proven.

        Proof requires a legal element/edge-preserving mapping under which
        the Kabsch RMSD is strictly below the threshold.  A pair whose
        mapping search runs out of budget is unresolved and is kept, never
        collapsed; anything else (different topology, distinct geometry) is
        kept too.
        """
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
