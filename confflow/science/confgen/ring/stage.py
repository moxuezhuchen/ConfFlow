#!/usr/bin/env python3

"""ConfGen v3 ring-lane stage adapter (shared ``GenerationStage`` protocol).

``RingStage`` wraps the pure ring science (templates/perception/realization)
in the frozen core stage API from ``confflow.science.confgen.model``. There
are no fallback/shadow dataclasses: a broken core integration fails closed at
import time. Enumeration is a lazy generator per the frozen
``GenerationStage`` contract (stable order, never a materialized grid;
sampling and deferred ranges are owned by the engine via core planner
helpers, never by this stage).

State identity is canonical (template id + canonical torsion descriptor +
anchor + direction); treatment travels in scope/provenance (axis spec, target
provenance, realization evidence), and measured torsions, confidence,
margins, and boundary flags travel in perception diagnostics. Parent-lock
and fresh-target audits run against measured torsions via the
``verify_locked`` / ``audit_target`` hooks, never against snapped evidence.

Dependencies: stdlib + NumPy + frozen core model/planner/perception only;
no legacy runner imports, no orchestration, no energy optimization.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from typing import Any, cast

import numpy as np

from confflow.domain.structure import StructureRecord
from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    PerceptionResult,
    RealizationResult,
    StageEstimate,
    StageParentProtocol,
)
from confflow.science.confgen.perception import drift_event
from confflow.science.confgen.planner import MixedRadixGrid

from .perception import (
    MARGIN_DEG_DEFAULT,
    MATCH_DEG_DEFAULT,
    REJECT_DEG_DEFAULT,
    commanded_state_dict,
    perceive_ring,
    ring_diagnostics,
    ring_state_dict,
    ring_states_match,
)
from .realization import (
    RingGeometryFailure,
    RingNumericalFailure,
    RingSpec,
    RingTolerances,
    RingUnsupported,
    parse_ring_specs,
    realize_rings,
)
from .templates import TEMPLATES_BY_SIZE, get_template

__all__ = [
    "BACKEND_NAME",
    "RingStage",
]

#: Backend label stamped on every ring realization result.
BACKEND_NAME = "geometric-template-kabsch-v1"


class RingStage(GenerationStage):
    """Ring generation stage over isolated covalent 4/5/6-membered rings.

    Index convention follows the global core rule: the engine passes
    normalized internal-0 sections (used as-is, never converted again);
    a raw workflow section may declare ``index_base: 1`` for one explicit
    1-based to 0-based conversion. No silent mixing.
    """

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        if not isinstance(axis_spec, Mapping):
            raise RingUnsupported("axis_spec must be a mapping")
        index_base = axis_spec.get("index_base", 0)
        if index_base not in (0, 1):
            raise RingUnsupported("ring axis_spec index_base must be 0 or 1")
        raw = axis_spec.get("rings", [])
        specs = parse_ring_specs(raw)
        if index_base == 1:
            # Spec-facing 1-based indices converted once, explicitly, to the
            # internal 0-based convention. No silent mixing.
            specs = tuple(
                RingSpec(
                    id=spec.id,
                    atoms=tuple(atom - 1 for atom in spec.atoms),
                    treatment=spec.treatment,
                    templates=spec.templates,
                )
                for spec in specs
            )
            if any(min(spec.atoms, default=0) < 0 for spec in specs):
                raise RingUnsupported("1-based ring indices must be >= 1")
        self._specs: tuple[RingSpec, ...] = specs
        tol = axis_spec.get("tolerances", {})
        if not isinstance(tol, Mapping):
            raise RingUnsupported("tolerances must be a mapping")
        self._tolerances = RingTolerances(
            ring_bond_atol=float(tol.get("ring_bond_atol", 0.08)),
            substituent_bond_atol=float(tol.get("substituent_bond_atol", 1e-6)),
            clash_threshold=float(tol.get("clash_threshold", 0.65)),
            frame_det_min=float(tol.get("frame_det_min", 1e-8)),
            link_bond_atol=float(tol.get("link_bond_atol", 0.15)),
        )
        self._match_deg = float(tol.get("perception_match_deg", MATCH_DEG_DEFAULT))
        self._margin_deg = float(tol.get("perception_margin_deg", MARGIN_DEG_DEFAULT))
        self._reject_deg = float(tol.get("perception_reject_deg", REJECT_DEG_DEFAULT))

    @property
    def axis(self) -> str:
        """Return the stage axis (``rings``)."""
        return "rings"

    @property
    def specs(self) -> tuple[RingSpec, ...]:
        """Return normalized ring specs in stable (sorted-id) order."""
        return self._specs

    def _enumerated(self) -> tuple[RingSpec, ...]:
        return tuple(spec for spec in self._specs if spec.treatment == "enumerate")

    def _options(self, spec: RingSpec) -> tuple[str, ...]:
        """Return declared enumeration templates for one system (fail closed)."""
        if spec.templates:
            names = spec.templates
        else:
            names = TEMPLATES_BY_SIZE.get(len(spec.atoms), ())
            if not names:
                raise RingUnsupported(f"unsupported_ring_size:{len(spec.atoms)}")
        for name in names:
            try:
                template = get_template(name)
            except KeyError as exc:
                raise RingUnsupported(f"unknown_template:{name}") from exc
            if template.ring_size != len(spec.atoms):
                raise RingUnsupported(f"template_size_mismatch:{name}")
        return tuple(names)

    def estimate(self, parent: StageParentProtocol, context: MolecularContext) -> StageEstimate:
        """Symbolic declared count: product of per-system template options."""
        enumerated = self._enumerated()
        total = 1
        unsupported: list[str] = []
        per_system: dict[str, Any] = {}
        for spec in enumerated:
            try:
                options = self._options(spec)
            except RingUnsupported as exc:
                unsupported.append(f"{spec.id}:{exc}")
                per_system[spec.id] = {"options": 0, "reason": str(exc)}
                total = 0
                continue
            per_system[spec.id] = {"options": len(options), "templates": list(options)}
            total *= len(options)
        if not enumerated:
            total = 1 if self._specs else 0
        details: dict[str, Any] = {
            "basis": "finite declared template product over isolated ring systems; "
            "unsupported systems contribute zero with explicit reasons",
            "scope_coverage": "exact",
            "per_system": per_system,
            "unsupported": unsupported,
            "preserve_input": [spec.id for spec in self._specs if spec.treatment != "enumerate"],
        }
        return StageEstimate(declared_count=total, upper_bound=total, exact=True, details=details)

    def enumerate_targets(
        self, parent: StageParentProtocol, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        """Enumerate symbolic targets lazily in stable order (no geometry).

        A generator over the mixed-radix ordinal space (last system fastest);
        the grid is never materialized. Commanded per-system states carry
        canonical template descriptors; agreement with observed states is
        checked tolerance-aware via ``ring_states_match``, never by exact
        float equality.
        """
        enumerated = self._enumerated()
        option_lists = [self._options(spec) for spec in enumerated]
        if not enumerated:
            yield GenerationTarget(
                axis="rings",
                target_id="rings:000000",
                state_value={},
                ordinal=0,
                provenance={"backend": BACKEND_NAME, "preserved": True},
            )
            return
        grid = MixedRadixGrid([len(options) for options in option_lists])
        for ordinal in grid.iter_indices():
            combo = grid.index_to_combo(ordinal)
            state_value = {
                spec.id: commanded_state_dict(
                    option_lists[position][combo[position]], anchor=spec.atoms[0]
                )
                for position, spec in enumerate(enumerated)
            }
            yield GenerationTarget(
                axis="rings",
                target_id=f"rings:{ordinal:06d}",
                state_value=state_value,
                ordinal=ordinal,
                provenance={
                    "backend": BACKEND_NAME,
                    "treatments": {spec.id: spec.treatment for spec in self._specs},
                },
            )

    def _covalent_graph(self, context: MolecularContext) -> list[list[int]]:
        """Return the covalent neighbour lists for this stage.

        Prefers an explicit ``covalent_adjacency`` on the context (typed-graph
        separation, incoming from core); falls back to ``context.adjacency``.
        Reaction/coordination adjacency is never treated as covalent here.
        """
        adjacency = getattr(context, "covalent_adjacency", None)
        if adjacency is None:
            adjacency = context.adjacency
        return [sorted(set(int(v) for v in row)) for row in adjacency]

    def _coordination_atoms(self, context: MolecularContext) -> frozenset[int]:
        """Return internal 0-based atoms owned by the coordination lane.

        ``context.resolved_spec`` is normalized internal-0 by core, so its
        coordination scope is used as-is and never converted again; the
        typed graph authority (``context.graph.metal_center``) is already
        internal too. These atoms make ring realization return
        ``unsupported`` instead of silently moving another lane's atoms.
        """
        found: set[int] = set()
        graph = getattr(context, "graph", None)
        metal = getattr(graph, "metal_center", None)
        if isinstance(metal, int) and not isinstance(metal, bool) and metal >= 0:
            found.add(metal)
        resolved = context.resolved_spec
        coordination = resolved.get("coordination", None) if isinstance(resolved, Mapping) else None
        if isinstance(coordination, Mapping):
            sites = coordination.get("binding_sites", [])
            if isinstance(sites, (list, tuple)):
                for site in sites:
                    if isinstance(site, Mapping):
                        atoms = site.get("atoms", [])
                        if isinstance(atoms, (list, tuple)):
                            for value in atoms:
                                if (
                                    isinstance(value, int)
                                    and not isinstance(value, bool)
                                    and value >= 0
                                ):
                                    found.add(value)
        return frozenset(found)

    def realize(
        self,
        parent: StageParentProtocol,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        """Realize one target; map every failure to an explicit status/reason."""
        try:
            structure = parent.structure
            coords = np.asarray(structure.coordinates, dtype=float)
            if not np.all(np.isfinite(coords)):
                raise RingNumericalFailure("nonfinite")
            elements = [str(item) for item in structure.atoms]
            graph = self._covalent_graph(context)
            state_value = dict(target.state_value) or {}
            assignment = {
                ring_id: str(entry.get("template"))
                for ring_id, entry in state_value.items()
                if isinstance(entry, Mapping) and "template" in entry
            }
            output = realize_rings(
                coords,
                elements,
                graph,
                self._specs,
                assignment,
                tolerances=self._tolerances,
                coordination_atoms=self._coordination_atoms(context),
            )
        except RingUnsupported as exc:
            return RealizationResult(
                structure=None,
                status="unsupported",
                reason=str(exc) or "unsupported",
                backend=BACKEND_NAME,
                evidence=(),
            )
        except RingNumericalFailure as exc:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason=str(exc) or "numerical_failure",
                backend=BACKEND_NAME,
                evidence=(),
            )
        except RingGeometryFailure as exc:
            return RealizationResult(
                structure=None,
                status="geometry_failure",
                reason=str(exc) or "geometry_failure",
                backend=BACKEND_NAME,
                evidence=(),
            )
        except (ValueError, TypeError) as exc:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason=f"unparseable_input:{exc}",
                backend=BACKEND_NAME,
                evidence=(),
            )
        parent_id = structure.id
        try:
            new_structure = replace(
                structure,
                coordinates=output.coordinates,
                id=f"{parent_id}-rings-{target.ordinal:06d}",
                parent_ids=tuple(structure.parent_ids) + (str(parent_id),),
            )
        except (ValueError, TypeError) as exc:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason=f"structure_rebuild:{exc}",
                backend=BACKEND_NAME,
                evidence=(),
            )
        return RealizationResult(
            structure=new_structure,
            status="realized",
            reason="realized" if not output.preserved else "preserved_input",
            backend=BACKEND_NAME,
            evidence=tuple(dict(audit) for audit in output.audits),
        )

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        """Perceive all ring axes; return the complete axis map keyed by id."""
        coords = np.asarray(structure.coordinates, dtype=float)
        best_key: dict[str, Any] = {}
        alternatives: list[dict[str, Any]] = []
        margins: dict[str, Any] = {}
        flags: list[str] = []
        overall = "reported"
        for spec in self._specs:
            perception = perceive_ring(
                coords[list(spec.atoms)],
                match_deg=self._match_deg,
                ambiguity_margin_deg=self._margin_deg,
                reject_deg=self._reject_deg,
            )
            best_key[spec.id] = ring_state_dict(perception, anchor=spec.atoms[0])
            diagnostics = ring_diagnostics(perception)
            margins[spec.id] = diagnostics["margin_deg"]
            raw_alternatives = cast("list[dict[str, Any]]", diagnostics["alternatives"])
            for entry in raw_alternatives:
                alternatives.append({"ring_id": spec.id, **entry})
            for flag in perception.boundary_flags:
                flags.append(f"{spec.id}:{flag}")
            if perception.confidence == "ambiguous":
                overall = "ambiguous"
        return PerceptionResult(
            best_key=best_key,
            alternatives=tuple(alternatives),
            confidence=overall,
            margins=margins,
            boundary_flags=tuple(flags),
        )

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        """Return ring system ids for engine perception coverage checks."""
        _ = context
        return tuple(spec.id for spec in self._specs)

    def _by_id(self, ring_id: str) -> RingSpec | None:
        """Return the spec for one ring id, or ``None`` when unknown."""
        for spec in self._specs:
            if spec.id == ring_id:
                return spec
        return None

    def _lock_tolerance(self, context: MolecularContext) -> float:
        """Return the frozen ring torsion audit tolerance (degrees)."""
        return float(context.tolerances.ring_torsion_atol_deg)

    def _observed_measured(self, spec: RingSpec, coords: np.ndarray) -> tuple[Any, Any]:
        """Perceive one system; return (perception, measured-state dict).

        The measured-state dict mirrors the canonical key shape but carries
        the ACTUAL measured torsions for tolerance-aware audit comparison.
        """
        perception = perceive_ring(
            np.asarray(coords)[list(spec.atoms)],
            match_deg=self._match_deg,
            ambiguity_margin_deg=self._margin_deg,
            reject_deg=self._reject_deg,
        )
        observed = {
            "template": perception.best_template,
            "torsions": [round(value, 6) for value in perception.torsions_deg],
            "anchor": spec.atoms[0],
            "direction": perception.direction,
        }
        return perception, observed

    def audit_target(
        self,
        structure: StructureRecord,
        target: GenerationTarget,
        parent: StageParentProtocol,
        context: MolecularContext,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Audit a fresh realization against its commanded target (engine hook).

        Returns ``(ok, canonical_measured_map, evidence)``. Every enumerated
        system is re-perceived and its ACTUAL measured torsions are checked
        against the commanded canonical state within
        ``ring_torsion_atol_deg``; evidence carries measured torsions and
        deviations (never snapped before verification). ``ok`` is False on
        any mismatch or ambiguous measurement, and the engine routes that to
        FAILED_DRIFT with observed/out-of-scope evidence.
        """
        _ = parent
        coords = np.asarray(structure.coordinates, dtype=float)
        tolerance = self._lock_tolerance(context)
        commanded_map = dict(target.state_value) or {}
        measured_map: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        ok = True
        for spec in self._enumerated():
            perception, observed = self._observed_measured(spec, coords)
            measured_map[spec.id] = ring_state_dict(perception, anchor=spec.atoms[0])
            commanded = commanded_map.get(spec.id)
            match, match_evidence = (
                ring_states_match(commanded, observed, torsion_atol_deg=tolerance)
                if isinstance(commanded, Mapping)
                else (False, {"reason": "missing_commanded_state"})
            )
            diagnostics = ring_diagnostics(perception)
            if perception.confidence == "ambiguous" or not match:
                ok = False
                payload = drift_event(
                    axis=f"rings.{spec.id}",
                    detail="realized ring state outside audit tolerance",
                    expected=(
                        (commanded or {}).get("template")
                        if isinstance(commanded, Mapping)
                        else None
                    ),
                    measured=match_evidence.get("worst_torsion_deg"),
                    tolerance=tolerance,
                )
                payload["observed"] = measured_map[spec.id]
                payload["measured_torsions"] = diagnostics["measured_torsions"]
                payload["match_evidence"] = dict(match_evidence)
                payload["confidence"] = perception.confidence
                payload["out_of_scope"] = perception.best_template != (
                    commanded.get("template") if isinstance(commanded, Mapping) else None
                )
                evidence.append(payload)
        return ok, measured_map, evidence

    def verify_locked(
        self,
        structure: StructureRecord,
        locked_state: Mapping[str, Any],
        context: MolecularContext,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Verify ancestor ring locks on a descendant geometry (engine hook).

        Re-perceives every locked ring system and checks ACTUAL measured
        torsions against the locked canonical states within
        ``ring_torsion_atol_deg``. Returns ``(ok, snapped_map, evidence)``:
        ``snapped`` carries canonical observed states for drift routing;
        ambiguous measurement returns anomaly evidence so the engine marks
        the descendant ambiguous (never a stale-key publication); unknown
        ring ids return lock-error evidence (also engine-ambiguous).
        """
        coords = np.asarray(structure.coordinates, dtype=float)
        tolerance = self._lock_tolerance(context)
        snapped: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        for ring_id, commanded in locked_state.items():
            spec = self._by_id(str(ring_id))
            if spec is None:
                return (
                    False,
                    {},
                    [
                        {
                            "kind": "lock_error",
                            "axis": f"rings.{ring_id}",
                            "detail": "locked ring id unknown to this stage",
                        }
                    ],
                )
            perception, observed = self._observed_measured(spec, coords)
            snapped[spec.id] = ring_state_dict(perception, anchor=spec.atoms[0])
            if perception.confidence == "ambiguous":
                return (
                    False,
                    {},
                    [
                        {
                            "kind": "anomaly",
                            "anomaly": "AMBIGUOUS_KEY",
                            "axis": f"rings.{spec.id}",
                            "detail": "ancestor ring lock perception ambiguous",
                            "measured_torsions": ring_diagnostics(perception)["measured_torsions"],
                        }
                    ],
                )
            match, match_evidence = ring_states_match(
                commanded, observed, torsion_atol_deg=tolerance
            )
            if not match:
                payload = drift_event(
                    axis=f"rings.{spec.id}",
                    detail="ancestor ring lock drifted",
                    expected=commanded.get("template") if isinstance(commanded, Mapping) else None,
                    measured=match_evidence.get("worst_torsion_deg"),
                    tolerance=tolerance,
                )
                payload["observed"] = snapped[spec.id]
                payload["measured_torsions"] = ring_diagnostics(perception)["measured_torsions"]
                payload["match_evidence"] = dict(match_evidence)
                payload["out_of_scope"] = perception.best_template != (
                    commanded.get("template") if isinstance(commanded, Mapping) else None
                )
                evidence.append(payload)
        if evidence:
            return False, snapped, evidence
        return True, snapped, []
