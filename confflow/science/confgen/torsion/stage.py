#!/usr/bin/env python3

"""ConfGen v3 torsion generation stage (CORE lane).

Wraps the :mod:`confflow.science.torsion` primitives (chain language, angle
resolution, Rodrigues rotation, BFS side selection, ring refusal, clash
rule) WITHOUT rewriting them. New code here is measurement (dihedral
setpoints/deltas), spec resolution, and audit wiring only.

Sign convention (documented, tested): a commanded angle equals the measured
dihedral change of the deterministic axis frame versus the reference
geometry (input for perception; immediate parent for fresh-target audit).
The moving fragment rotates right-handed about the bond axis pointing AWAY
from it. This differs from the legacy executor's axis direction by sign;
bit-parity with legacy grids lives in :mod:`torsion.legacy`, and one test
pins the exact relationship (stage coords == legacy coords at negated
angles on acyclic systems).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import numpy as np

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.science.bonds import covalent_radii
from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    PerceptionResult,
    RealizationResult,
    StageEstimate,
    StageParentProtocol,
)
from confflow.science.confgen.planner import MixedRadixGrid, TorsionAxis, resolve_torsion_axes
from confflow.science.confgen.torsion.measure import measure_dihedral, wrap_degrees
from confflow.science.topology import inherit_topology_kwargs
from confflow.science.torsion import (
    clashes,
    edge_in_cycle,
    rotate_atoms_around_bond,
    rotating_side,
    topological_distance_matrix,
)

__all__ = ["BACKEND_NAME", "TorsionStage"]

#: Backend label stamped on every torsion realization result.
BACKEND_NAME = "geometric-rodrigues-v3"

_DEGENERATE_AXIS_NORM = 1e-12


class TorsionStage(GenerationStage):
    """Torsion generation stage over the resolved torsion group.

    The constructor takes the FULL resolved spec mapping (0-based, per the
    frozen index convention) and reads ``spec["torsions"]``. Enumeration is
    a lazy joint grid over enumerate-axes (stable declaration order, last
    axis fastest); preserve_input axes contribute factor 1 and are reported
    in estimate details and engine scope.
    """

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        if not isinstance(axis_spec, Mapping):
            raise ValueError("torsion axis_spec must be the resolved spec mapping")
        entries = axis_spec.get("torsions", [])
        if not isinstance(entries, (list, tuple)):
            raise ValueError("resolved spec torsions must be a list")
        self._entries = tuple(dict(entry) for entry in entries)
        # Read-only provenance of path-expanded axes, used only to word a refusal.
        self._paths_resolved = axis_spec.get("paths_resolved")
        # Structure-independent validation now (ranges re-checked with counts).
        resolve_torsion_axes(self._entries, index_base=0)

    @property
    def axis(self) -> str:
        """Return the stage axis (``torsions``)."""
        return "torsions"

    #: A3 dispatch hooks (engine-read; component owns its literals).
    check_bond_integrity: bool = True
    carries_inherited_locks: bool = True

    def preserved_entries(self, resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return torsion ``preserve_input`` entries (delegates to scope)."""
        from confflow.science.confgen.torsion.scope import preserved_entries as _entries

        return _entries(resolved)

    def report_section(self, resolved: Mapping[str, Any]) -> dict[str, Any] | None:
        """Torsion contributes no report section."""
        from confflow.science.confgen.torsion.scope import report_section as _section

        return _section(resolved)

    def describe_scope(self, resolved: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Return the torsion scope slice (delegates to scope)."""
        from confflow.science.confgen.torsion.scope import describe_scope as _describe

        return _describe(resolved)

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        """Return enumerate-axis ids for perception coverage checks."""
        return tuple(axis.axis_id for axis in self._enumerate_axes(context))

    # -- internal resolution --------------------------------------------

    def _axes(self, context: MolecularContext) -> tuple[TorsionAxis, ...]:
        """Resolve axes with atom-count range checks (fail closed)."""
        return resolve_torsion_axes(
            self._entries, n_atoms=len(context.structure.atoms), index_base=0
        )

    def _terminal_origin(self, axis_id: str, terminal_atom: int) -> str:
        """Name the path declaration key that produced a terminal endpoint.

        Wording only: it reads the resolved-path audit (``paths_resolved``) the
        planner already attached to the spec and never feeds a state key, digest
        or report.  Returns ``""`` for axes that did not come from ``paths``.
        """
        resolved = self._paths_resolved
        if not isinstance(resolved, Mapping):
            return ""
        sources: list[str] = []
        for rotor in resolved.get("rotors", ()) or ():
            if isinstance(rotor, Mapping) and rotor.get("id") == axis_id:
                sources = [str(item) for item in rotor.get("sources", ()) or ()]
        atom = terminal_atom + 1
        keys: list[str] = []
        for declared in resolved.get("declared_paths", ()) or ():
            if not isinstance(declared, Mapping) or str(declared.get("source")) not in sources:
                continue
            label = str(declared["source"]).replace("$.", "confgen.", 1)
            for key in ("start", "end"):
                if declared.get(key) == atom:
                    keys.append(f"{label}.{key} (atom {atom})")
        if not keys:
            return ""
        return f"{' and '.join(keys)} is a terminal atom with no measurable dihedral frame; "

    def _frame_for(self, axis: TorsionAxis, context: MolecularContext) -> tuple[int, int, int, int]:
        """Resolve the identity frame, refusing unmeasurable terminal bonds.

        Relative labels use the minimum-index neighbor at each bond end;
        this frame is persisted for chained audits. It is an atom-index
        convention, not a chemically canonical substituent frame.
        """
        first, second = axis.bond
        near = [n for n in context.adjacency[first] if n != second]
        far = [n for n in context.adjacency[second] if n != first]
        if not near or not far:
            terminal = first if not near else second
            raise ValueError(
                f"{self._terminal_origin(axis.axis_id, terminal)}"
                f"torsion axis {axis.axis_id!r}: bond {first + 1}-{second + 1} "
                "has no measurable dihedral frame (terminal pair)"
            )
        return (min(near), first, second, min(far))

    def _frames(
        self, context: MolecularContext, axes: Sequence[TorsionAxis] | None = None
    ) -> dict[str, tuple[int, int, int, int]]:
        """Resolve measurable dihedral frames for relative-style axes."""
        return {
            axis.axis_id: self._frame_for(axis, context)
            for axis in (self._axes(context) if axes is None else axes)
            if axis.frame is None
        }

    def _validated(self, context: MolecularContext) -> tuple[TorsionAxis, ...]:
        """Resolve axes plus bonded/ring/frame checks (fail closed pre-geometry)."""
        axes = self._axes(context)
        frames: dict[str, tuple[int, int, int, int]] | None = None
        for axis in axes:
            if axis.treatment != "enumerate":
                continue
            first, second = axis.bond
            if second not in context.adjacency[first]:
                raise ValueError(
                    f"torsion axis {axis.axis_id!r}: bond {first + 1}-{second + 1} "
                    "is not bonded; use add_bond or adjust bond_scale"
                )
            if edge_in_cycle(context.adjacency, first, second):
                raise ValueError(
                    f"torsion axis {axis.axis_id!r}: bond {first + 1}-{second + 1} "
                    "is a ring bond and cannot be rotated independently"
                )
        frames = self._frames(context, [axis for axis in axes if axis.treatment == "enumerate"])
        for axis in axes:
            if axis.frame is None and axis.treatment == "enumerate":
                _ = frames[axis.axis_id]  # raises when unmeasurable
        return axes

    def _enumerate_axes(self, context: MolecularContext) -> tuple[TorsionAxis, ...]:
        """Return enumerate-treatment axes in declaration order."""
        return tuple(axis for axis in self._axes(context) if axis.treatment == "enumerate")

    def _sizes(self, context: MolecularContext) -> list[int]:
        """Return per-axis value counts (chemical counts its states)."""
        return [len(axis.values) for axis in self._enumerate_axes(context)]

    # -- protocol ---------------------------------------------------------

    def estimate(self, parent: StageParentProtocol, context: MolecularContext) -> StageEstimate:
        """Symbolic joint-grid count (exact, complete declared group)."""
        axes = self._validated(context)
        enumerated = [axis for axis in axes if axis.treatment == "enumerate"]
        sizes = [len(axis.values) for axis in enumerated]
        total = 1
        for size in sizes:
            total *= size
        details = {
            "basis": (
                "complete declared torsion group: "
                f"{len(enumerated)} enumerate axes with counts {sizes}; "
                f"{len(axes) - len(enumerated)} preserve_input"
            ),
            "scope_coverage": "exact",
            "per_axis": {axis.axis_id: len(axis.values) for axis in enumerated},
            "preserve_input": [axis.axis_id for axis in axes if axis.treatment != "enumerate"],
        }
        return StageEstimate(
            declared_count=total, upper_bound=total, exact=True, details=FrozenDict(details)
        )

    def _label(self, axis: TorsionAxis, position: int) -> Any:
        """Return the JSON state label for one axis value position."""
        if axis.model == "chemical":
            return axis.state_names[position]
        return float(axis.values[position])

    def _target_at(self, ordinal: int, axes: Sequence[TorsionAxis]) -> GenerationTarget:
        """Build the joint target for one mixed-radix ordinal."""
        grid = MixedRadixGrid([len(axis.values) for axis in axes])
        combo = grid.index_to_combo(ordinal)
        state_value = {
            axis.axis_id: self._label(axis, combo[position]) for position, axis in enumerate(axes)
        }
        return GenerationTarget(
            axis="torsions",
            target_id=f"torsions:{ordinal:06d}",
            state_value=FrozenDict(state_value),
            ordinal=ordinal,
            provenance=FrozenDict({"backend": BACKEND_NAME}),
        )

    def target_by_ordinal(
        self, parent: StageParentProtocol, ordinal: int, context: MolecularContext
    ) -> GenerationTarget:
        """Fetch one joint target lazily by ordinal (engine sampling path)."""
        axes = self._validated(context)
        enumerated = [axis for axis in axes if axis.treatment == "enumerate"]
        return self._target_at(ordinal, enumerated)

    def enumerate_targets(
        self, parent: StageParentProtocol, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        """Yield joint targets lazily in stable ordinal order (no geometry)."""
        axes = self._validated(context)
        enumerated = [axis for axis in axes if axis.treatment == "enumerate"]
        if not enumerated:
            yield GenerationTarget(
                axis="torsions",
                target_id="torsions:000000",
                state_value=FrozenDict({}),
                ordinal=0,
                provenance=FrozenDict({"backend": BACKEND_NAME, "preserved": True}),
            )
            return
        grid = MixedRadixGrid([len(axis.values) for axis in enumerated])
        for ordinal in grid.iter_indices():
            yield self._target_at(ordinal, enumerated)

    # -- realization ------------------------------------------------------

    def _applied_angles(
        self, axes: Sequence[TorsionAxis], state_value: Mapping[str, Any]
    ) -> dict[str, float]:
        """Resolve commanded state labels to applied angles (fail closed)."""
        applied: dict[str, float] = {}
        for axis in axes:
            if axis.axis_id not in state_value:
                raise ValueError(f"target lacks torsion axis {axis.axis_id!r}")
            label = state_value[axis.axis_id]
            if axis.model == "chemical":
                if label not in axis.state_names:
                    raise ValueError(
                        f"target torsion {axis.axis_id!r} names unknown state {label!r}"
                    )
                applied[axis.axis_id] = float(axis.values[axis.state_names.index(label)])
            else:
                if isinstance(label, bool) or not isinstance(label, (int, float)):
                    raise ValueError(
                        f"target torsion {axis.axis_id!r} must carry an angle, got {label!r}"
                    )
                applied[axis.axis_id] = float(label)
        return applied

    def _side(self, axis: TorsionAxis, context: MolecularContext) -> list[int]:
        """Return the rotating atom indices for one axis (BFS side selection)."""
        first, second = axis.bond
        n_atoms = len(context.structure.atoms)
        if axis.rotate_side == "left":
            near, far = [first], [second]
        else:
            near, far = [second], [first]
        return rotating_side(context.adjacency, n_atoms, first, second, near, far)

    def realize(
        self,
        parent: StageParentProtocol,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        """Apply joint rotations to the parent geometry (geometric solver)."""
        from confflow.domain.elements import atomic_number

        axes = self._validated(context)
        enumerated = [axis for axis in axes if axis.treatment == "enumerate"]
        applied = self._applied_angles(enumerated, dict(target.state_value))
        parent_coords = np.asarray(parent.structure.coordinates, dtype=np.float64)
        coords = parent_coords.copy()
        for axis in enumerated:
            first, second = axis.bond
            pivot_coords = coords[first] - coords[second]
            if float(np.linalg.norm(pivot_coords)) < _DEGENERATE_AXIS_NORM:
                return RealizationResult(
                    structure=None,
                    status="numerical_failure",
                    reason="degenerate_axis",
                    backend=BACKEND_NAME,
                    evidence=(
                        FrozenDict({"axis": axis.axis_id, "detail": "zero-length bond axis"}),
                    ),
                )
            angle = applied[axis.axis_id]
            if axis.frame is not None:
                # Absolute setpoint: rotate the moving side by the wrapped
                # residual so re-measurement equals the command.
                current = measure_dihedral(coords, *axis.frame)
                delta = wrap_degrees(angle - current)
                rotating = self._side(axis, context)
                if axis.rotate_side == "left":
                    pivot, second = second, first  # axis points away from p-side
                else:
                    pivot, second = first, second
                rotate_atoms_around_bond(coords, pivot, second, rotating, delta)
            else:
                # Relative rotation: commanded angle equals measured frame
                # change (axis points away from the moving fragment).
                rotating = self._side(axis, context)
                if axis.rotate_side == "left":
                    pivot, second = second, first
                else:
                    pivot, second = first, second
                rotate_atoms_around_bond(coords, pivot, second, rotating, angle)
        if not np.all(np.isfinite(coords)):
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="nonfinite",
                backend=BACKEND_NAME,
                evidence=(FrozenDict({"detail": "realized coordinates not finite"}),),
            )
        from confflow.science.confgen.perception import max_bond_length_deviation

        integrity = max_bond_length_deviation(coords, parent_coords, context.adjacency)
        if integrity > float(context.tolerances.bond_length_atol):
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="integrity",
                backend=BACKEND_NAME,
                evidence=(
                    FrozenDict(
                        {
                            "detail": "rigid-rotation integrity outside tolerance",
                            "measured": float(integrity),
                        }
                    ),
                ),
            )
        try:
            numbers = [atomic_number(symbol) for symbol in parent.structure.atoms]
            radii = covalent_radii(numbers)
            topo = topological_distance_matrix(context.adjacency)
        except ValueError as exc:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="perception_setup",
                backend=BACKEND_NAME,
                evidence=(FrozenDict({"detail": f"clash setup failed: {exc}"}),),
            )
        threshold = float(context.tolerances.clash_threshold)
        if clashes(coords, radii, topo, threshold):
            # Geometric admissibility failure -- never an infeasibility proof.
            return RealizationResult(
                structure=None,
                status="geometry_failure",
                reason="clash",
                backend=BACKEND_NAME,
                evidence=(
                    FrozenDict(
                        {
                            "detail": "non-bonded clash above declared threshold",
                            "threshold": threshold,
                        }
                    ),
                ),
            )
        record = StructureRecord(
            id=f"{parent.structure.id}/torsions:{target.ordinal:06d}",
            atoms=tuple(parent.structure.atoms),
            coordinates=tuple((float(x), float(y), float(z)) for x, y, z in coords.tolist()),
            charge=parent.structure.charge,
            multiplicity=parent.structure.multiplicity,
            parent_ids=(parent.structure.id,),
            lineage_root_id=parent.structure.lineage_root_id,
            role="confgen-torsion",
            ordinal=int(target.ordinal),
            group_key=parent.structure.group_key,
            metadata=FrozenDict(
                {"axis": "torsions", "ordinal": int(target.ordinal), "backend": BACKEND_NAME}
            ),
            **inherit_topology_kwargs(parent.structure, context.adjacency),
        )
        return RealizationResult(
            structure=record,
            status="realized",
            reason="realized",
            backend=BACKEND_NAME,
            evidence=(
                FrozenDict({"applied": dict(applied), "max_bond_deviation": float(integrity)}),
            ),
        )

    # -- perception ---------------------------------------------------------

    def _measured_map(
        self,
        coords: np.ndarray,
        reference: np.ndarray,
        context: MolecularContext,
        axes: Sequence[TorsionAxis] | None = None,
        *,
        skip_missing: bool = False,
    ) -> tuple[dict[str, float], dict[str, Any], list[str]]:
        """Measure axes (complete map keyed by id).

        ``skip_missing`` omits unmeasurable preserve-treatment axes with a
        boundary flag instead of raising (enumerate axes always raise: their
        measurability is validated pre-geometry).
        """
        resolved = self._validated(context) if axes is None else axes
        measured: dict[str, float] = {}
        skipped: list[str] = []
        for axis in resolved:
            try:
                if axis.frame is not None:
                    measured[axis.axis_id] = float(measure_dihedral(coords, *axis.frame))
                    continue
                frame = self._frame_for(axis, context)
                delta = wrap_degrees(
                    measure_dihedral(coords, *frame) - measure_dihedral(reference, *frame)
                )
                measured[axis.axis_id] = float(delta)
            except ValueError:
                if skip_missing and axis.treatment != "enumerate":
                    skipped.append(f"unmeasured_preserve:{axis.axis_id}")
                    continue
                raise
        from confflow.science.confgen.perception import max_bond_length_deviation

        margins: dict[str, Any] = dict(measured)
        margins["max_bond_deviation"] = float(
            max_bond_length_deviation(coords, reference, context.adjacency)
        )
        return measured, margins, skipped

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        """Perceive the complete torsion state by geometry measurement.

        ALL axes are measured (enumerate and preserve_input): relative axes
        report the frame-dihedral change versus the input reference
        (re-perceivable, never the commanded key); absolute axes report
        measured setpoints. Unmeasurable structures are `ambiguous`.
        """
        coords = np.asarray(structure.coordinates, dtype=np.float64)
        reference = np.asarray(context.input_coords, dtype=np.float64)
        if coords.shape != reference.shape or not np.all(np.isfinite(coords)):
            return PerceptionResult(
                best_key=FrozenDict({}),
                confidence="ambiguous",
                margins=FrozenDict({"detail": "nonfinite or reshaped coordinates"}),
                boundary_flags=("unmeasurable",),
            )
        try:
            measured, margins, skipped = self._measured_map(
                coords, reference, context, self._validated(context), skip_missing=True
            )
        except ValueError as exc:
            return PerceptionResult(
                best_key=FrozenDict({}),
                confidence="ambiguous",
                margins=FrozenDict({"detail": f"measurement failed: {exc}"}),
                boundary_flags=("unmeasurable",),
            )
        flags: list[str] = list(skipped)
        axes = {axis.axis_id: axis for axis in self._validated(context)}
        for axis_id, value in measured.items():
            axis = axes[axis_id]
            if axis.frame is not None and abs(abs(value) - 180.0) <= 2.0:
                flags.append(f"near_periodic_boundary:{axis_id}")
        return PerceptionResult(
            best_key=FrozenDict(dict(measured)),
            confidence="reported",
            margins=FrozenDict(margins),
            boundary_flags=tuple(flags),
        )

    def audit_target(
        self,
        structure: StructureRecord,
        target: GenerationTarget,
        parent: StageParentProtocol,
        context: MolecularContext,
    ) -> tuple[bool, dict[str, float], list[dict[str, Any]]]:
        """Audit a fresh realization against its commanded target.

        Returns (ok, measured, evidence). Relative deltas are measured versus
        the immediate PARENT geometry (fresh-application check); absolute
        axes versus their setpoints. Mismatches are drift evidence for the
        engine equation (FAILED_DRIFT), never silent acceptance. Evidence
        carries the observed map plus snapped/out-of-scope routing.
        """
        from confflow.science.confgen.perception import drift_event

        axes = self._validated(context)
        enumerated = [axis for axis in axes if axis.treatment == "enumerate"]
        applied = self._applied_angles(enumerated, dict(target.state_value))
        coords = np.asarray(structure.coordinates, dtype=np.float64)
        parent_coords = np.asarray(parent.structure.coordinates, dtype=np.float64)
        measured, _, _ = self._measured_map(coords, parent_coords, context, enumerated)
        tolerance = float(context.tolerances.dihedral_atol_deg)
        evidence: list[dict[str, Any]] = []
        ok = True
        for axis in enumerated:
            deviation = abs(wrap_degrees(measured[axis.axis_id] - applied[axis.axis_id]))
            if deviation > tolerance:
                ok = False
                snapped = self._snap(axis, measured[axis.axis_id], tolerance)
                payload = drift_event(
                    axis=f"torsions.{axis.axis_id}",
                    detail="realized torsion state outside audit tolerance",
                    expected=applied[axis.axis_id],
                    measured=measured[axis.axis_id],
                    tolerance=tolerance,
                )
                payload["observed"] = float(measured[axis.axis_id])
                payload["snapped"] = snapped
                payload["out_of_scope"] = snapped is None
                evidence.append(payload)
        return ok, measured, evidence

    def _snap(self, axis: TorsionAxis, measured: float, tolerance: float) -> Any:
        """Snap a measurement to the declared vocabulary, if within tolerance."""
        if axis.model == "chemical":
            best: Any = None
            best_dev = tolerance
            for name, angle in zip(axis.state_names, axis.values):
                deviation = abs(wrap_degrees(measured - angle))
                if deviation <= best_dev:
                    best, best_dev = name, deviation
            return best
        best_angle: Any = None
        best_dev = tolerance
        for angle in axis.values:
            deviation = abs(wrap_degrees(measured - angle))
            if deviation <= best_dev:
                best_angle, best_dev = float(angle), deviation
        return best_angle

    def verify_locked(
        self,
        structure: StructureRecord,
        locked_state: Mapping[str, Any],
        context: MolecularContext,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Verify ancestor torsion locks on a descendant geometry.

        Re-measures every locked axis versus the INPUT reference (frames are
        fixed by the covalent graph) and compares against the ancestor's
        commanded labels. Any deviation outside tolerance is lock drift
        (strict: the engine marks the descendant DRIFTED, never publishes
        with a stale parent key). Returns (ok, snapped_map, evidence) with
        snapped/out-of-scope routing per axis.
        """
        from confflow.science.confgen.perception import drift_event

        axes = {axis.axis_id: axis for axis in self._validated(context)}
        coords = np.asarray(structure.coordinates, dtype=np.float64)
        reference = np.asarray(context.input_coords, dtype=np.float64)
        measured, _, _ = self._measured_map(coords, reference, context, list(axes.values()))
        tolerance = float(context.tolerances.dihedral_atol_deg)
        ok = True
        snapped: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        for axis_id, label in locked_state.items():
            axis = axes.get(str(axis_id))
            if axis is None:
                ok = False
                evidence.append(
                    {
                        "kind": "lock_error",
                        "axis": f"torsions.{axis_id}",
                        "detail": "locked axis unknown to torsion stage",
                    }
                )
                continue
            if axis.model == "chemical":
                if label not in axis.state_names:
                    ok = False
                    evidence.append(
                        {
                            "kind": "lock_error",
                            "axis": f"torsions.{axis_id}",
                            "detail": f"locked label {label!r} not a declared state",
                        }
                    )
                    continue
                expected = float(axis.values[axis.state_names.index(label)])
            else:
                expected = float(label)
            deviation = abs(wrap_degrees(measured[axis.axis_id] - expected))
            if deviation <= tolerance:
                snapped[axis.axis_id] = label
            else:
                ok = False
                snap = self._snap(axis, measured[axis.axis_id], tolerance)
                payload = drift_event(
                    axis=f"torsions.{axis.axis_id}",
                    detail="ancestor torsion lock coupled by descendant motion",
                    expected=expected,
                    measured=measured[axis.axis_id],
                    tolerance=tolerance,
                )
                payload["observed"] = float(measured[axis.axis_id])
                payload["snapped"] = snap
                payload["out_of_scope"] = snap is None
                evidence.append(payload)
                if snap is not None:
                    snapped[axis.axis_id] = snap
        return ok, snapped, evidence

    def preserved_state(
        self, structure: StructureRecord, context: MolecularContext
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resolve preserve-treatment axes to discrete key entries.

        Measures each preserved axis versus the input reference and snaps
        to the declared vocabulary (angles or chemical names). Snapped
        entries merge into the realization key (real perception, discrete
        identity, locked downstream like any ancestor). Unsnappable axes
        are omitted with anomaly evidence (inherited entries pass through).
        """
        axes = [axis for axis in self._validated(context) if axis.treatment != "enumerate"]
        if not axes:
            return {}, []
        coords = np.asarray(structure.coordinates, dtype=np.float64)
        reference = np.asarray(context.input_coords, dtype=np.float64)
        measured, _, skipped = self._measured_map(
            coords, reference, context, axes, skip_missing=True
        )
        tolerance = float(context.tolerances.dihedral_atol_deg)
        snapped: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        for axis in axes:
            if axis.axis_id not in measured:
                evidence.append(
                    {
                        "kind": "anomaly",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": f"preserved axis {axis.axis_id!r} unmeasurable",
                    }
                )
                continue
            snap = self._snap(axis, measured[axis.axis_id], tolerance)
            if snap is None:
                evidence.append(
                    {
                        "kind": "anomaly",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": f"preserved axis {axis.axis_id!r} outside declared vocabulary",
                        "observed": float(measured[axis.axis_id]),
                    }
                )
                continue
            snapped[axis.axis_id] = snap
        return snapped, evidence
