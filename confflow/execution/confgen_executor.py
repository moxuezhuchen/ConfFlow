#!/usr/bin/env python3

"""V4 confgen executor: chain-mode torsion conformer generation.

Pure executor: never shells to Gaussian/ORCA, never calls the calculation
pipeline, never imports legacy runner code.  One :class:`WorkItem` (one
seed structure) produces a deterministic torsion-scan ensemble driven by
the step's chain vocabulary.

Science lives in :mod:`confflow.science` (exclusive V4 ownership, no
legacy orchestration transitives): chain language, angle resolution,
rotating-side selection, Rodrigues rotation, ring refusal, and clash
filtering in :mod:`confflow.science.torsion`; bond perception through
the centralised ``core.bonding`` authority via
:mod:`confflow.science.bonds`.  This module only wires those algorithms
to typed V4 domain records.

Step native vocabulary (all explicit, unknown keys fail closed):

- ``chains`` (required): ``list[str]`` of 1-based dash-separated chains.
- ``chain_steps`` / ``chain_angles`` (optional): per-bond steps or
  explicit angle lists in the ``--steps`` / ``--angles`` spellings.
- ``angle_step`` (default 120): grid step in degrees, 1..360.
- ``rotate_side`` (default ``"left"``): ``"left"`` or ``"right"``.
- ``no_rotate`` (optional): ``list[str]`` ``"a-b"`` 1-based pairs
  excluded from rotation (ring-bond exclusions included).
- ``add_bond`` / ``del_bond`` (optional): ``list[str]`` ``"a-b"``
  topology corrections applied to the perceived adjacency.
- ``bond_scale`` (default 1.15), ``clash_threshold`` (default 0.65).
- ``max_conformers`` (optional): deterministic cap; the full grid order
  is shuffled with ``random.Random(sha256(seed:logical_key))`` and
  truncated.  The seed remains the single stochastic authority and is
  recorded in provenance, report, and digest axes.
- ``optimize``: must be absent or false; true fails closed (MMFF legacy).

Identity: members use the frozen
:func:`confflow.execution.output_identity.conformer_output_id` authority
with the grid enumeration ordinal as the stable native member index.
Lineage is single-parent from the seed structure.  Confgen emits
structures only (no energies are invented).  A JSON report artifact is
written into the attempt directory with a ``sha256`` checksum and a
run-relative locator.  The executor never raises.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import time
from collections.abc import Callable, Mapping
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
from ..science.torsion import (
    clashes,
    edge_in_cycle,
    parse_bond_pair,
    parse_chain,
    resolve_angle_lists,
    rotate_atoms_around_bond,
    rotating_side,
    topological_distance_matrix,
)
from .native import NativeErrorCode
from .output_identity import (
    CONFORMER_MEMBER_METADATA_KEY,
    conformer_output_id,
    endpoint_lineage,
)
from .work_item_executor import ItemExecutionContext, _diagnostic

__all__ = ["ConfgenExecutor", "CONFGEN_REPORT_ROLE"]

#: Artifact role for the deterministic confgen report.
CONFGEN_REPORT_ROLE = "ensemble_report"

#: Allowed step-native keys for confgen (unknown keys fail closed).
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
    }
)

_DEFAULT_ANGLE_STEP = 120
_DEFAULT_BOND_SCALE = 1.15
_DEFAULT_CLASH_THRESHOLD = 0.65


class ConfgenExecutor:
    """Chain-mode torsion conformer-generation executor."""

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
                work_item, context, wall_start, monotonic_start,
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
        seed = scientific.seed
        if seed is None or isinstance(seed, bool) or not isinstance(seed, int):
            raise DomainError(
                "confgen requires the explicit integer step seed (single authority)"
            )
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
        n_atoms = len(driving.atoms)
        for chain in chains:
            for index in chain:
                if index < 0 or index >= n_atoms:
                    raise DomainError(
                        f"confgen chain index {index + 1} out of range for {n_atoms} atoms"
                    )
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
            isinstance(max_conformers, bool) or not isinstance(max_conformers, int)
            or max_conformers < 1
        ):
            raise DomainError(
                f"confgen max_conformers must be an integer >= 1, got {max_conformers!r}"
            )
        chain_steps = self._as_str_list(native.get("chain_steps"), "chain_steps", len(chains))
        chain_angles = self._as_str_list(native.get("chain_angles"), "chain_angles", len(chains))
        no_rotate = self._bond_set(native.get("no_rotate", []) or [], "no_rotate")
        add_bond = [self._bond_pair(item) for item in native.get("add_bond", []) or []]
        del_bond = [self._bond_pair(item) for item in native.get("del_bond", []) or []]

        atomic_numbers = [atomic_number(symbol) for symbol in driving.atoms]
        adjacency = perceive_adjacency(atomic_numbers, driving.coordinates, bond_scale=bond_scale)
        for first, second in add_bond:
            self._check_index(first, n_atoms, "add_bond")
            self._check_index(second, n_atoms, "add_bond")
            if second not in adjacency[first]:
                adjacency[first].append(second)
                adjacency[second].append(first)
        for first, second in del_bond:
            self._check_index(first, n_atoms, "del_bond")
            if second in adjacency[first]:
                adjacency[first].remove(second)
                adjacency[second].remove(first)
        adjacency = [sorted(row) for row in adjacency]

        rot_bonds: list[tuple[int, int, list[int]]] = []
        angle_lists: list[list[float]] = []
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
                if right not in adjacency[left]:
                    raise DomainError(
                        f"confgen chain atoms {left + 1}-{right + 1} are not bonded; "
                        "use add_bond or adjust bond_scale"
                    )
                if tuple(sorted((left, right))) in no_rotate:
                    continue
                if edge_in_cycle(adjacency, left, right):
                    raise DomainError(
                        f"confgen chain bond {left + 1}-{right + 1} is a ring bond and "
                        "cannot be rotated independently; exclude it via no_rotate"
                    )
                if rotate_side == "left":
                    near, far = chain[: position + 1], chain[position + 1 :]
                else:
                    near, far = chain[position + 1 :], chain[: position + 1]
                rotating = rotating_side(adjacency, n_atoms, left, right, near, far)
                rot_bonds.append((left, right, rotating))
                angle_lists.append([float(item) for item in per_bond[position]])
        if not rot_bonds:
            raise DomainError("confgen chains selected no rotatable bonds")
        if should_cancel is not None and should_cancel():
            return self._cancelled(work_item, context, wall_start, monotonic_start)

        base = np.asarray(driving.coordinates, dtype=np.float64)
        radii = covalent_radii(atomic_numbers)
        topo = topological_distance_matrix(adjacency)
        grid = list(itertools.product(*angle_lists))
        kept: list[tuple[int, np.ndarray]] = []
        clash_dropped = 0
        for ordinal, combo in enumerate(grid):
            coords = base.copy()
            for (left, right, rotating), angle in zip(rot_bonds, combo):
                rotate_atoms_around_bond(coords, left, right, rotating, float(angle))
            if clashes(coords, radii, topo, clash_threshold):
                clash_dropped += 1
                continue
            kept.append((ordinal, coords))
            if should_cancel is not None and should_cancel():
                return self._cancelled(work_item, context, wall_start, monotonic_start)
        if not kept:
            raise DomainError(
                f"confgen generated no clash-free conformers ({len(grid)} grid points, "
                f"{clash_dropped} clash-dropped)"
            )
        if max_conformers is not None and len(kept) > max_conformers:
            shuffler = random.Random(
                int.from_bytes(
                    hashlib.sha256(f"{seed}:{work_item.logical_key}".encode()).digest()[:8],
                    "big",
                )
            )
            shuffler.shuffle(kept)
            kept = kept[:max_conformers]

        charge = driving.charge
        multiplicity = driving.multiplicity
        overrides = scientific.overrides
        if overrides.get("charge") is not None:
            charge = int(overrides["charge"])
        if overrides.get("multiplicity") is not None:
            multiplicity = int(overrides["multiplicity"])
        parent_ids, lineage_root, group_key = endpoint_lineage(driving)
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
                    metadata=FrozenDict({CONFORMER_MEMBER_METADATA_KEY: ordinal, "seed": int(seed)}),
                )
            )
        artifacts = self._write_report(
            work_item, context, driving, members, int(seed), native,
            grid=len(grid), clash_dropped=clash_dropped,
        )
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
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
                        f"confgen scanned {len(grid)} torsion points, kept "
                        f"{len(members)} ({clash_dropped} clash-dropped)"
                    ),
                    severity=DiagnosticSeverity.INFO,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict({
                        "members": len(members),
                        "seed": int(seed),
                        "grid_points": len(grid),
                        "clash_dropped": clash_dropped,
                    }),
                ),
            ),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    # -- helpers ---------------------------------------------------------

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
        if not isinstance(raw, (list, tuple)) or not all(
            isinstance(item, str) for item in raw
        ):
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
    ) -> ArtifactSet:
        attempt_dir = context.attempt_dir(work_item)
        os.makedirs(attempt_dir, exist_ok=True)
        payload = {
            "seed": seed,
            "driving_id": driving.id,
            "chains": list(native.get("chains", [])),
            "grid_points": grid,
            "clash_dropped": clash_dropped,
            "members": [
                {"id": record.id, "ordinal": record.ordinal,
                 "geometry_digest": record.geometry_digest}
                for record in members
            ],
        }
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
                    NativeErrorCode.NATIVE_INPUT_ERROR, message,
                    step_id=context.step_id, work_item_id=work_item.id,
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
                    NativeErrorCode.CANCELLATION_ERROR, "work item cancelled",
                    step_id=context.step_id,
                    work_item_id=work_item.id, logical_key=work_item.logical_key,
                    details={"confirmed": True},
                ),
            ),
            timing=timing,
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )
