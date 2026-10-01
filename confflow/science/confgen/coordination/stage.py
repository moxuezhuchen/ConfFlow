#!/usr/bin/env python3
"""Coordination generation stage: protocol-exact adapter over pure science.

This module exports the coordination lane's :class:`CoordinationStage`,
which implements the frozen core ``GenerationStage`` protocol (v2) against
the shared dataclasses in ``confflow.science.confgen.model``:

- constructor takes the FULL resolved spec mapping and reads its own
  ``coordination`` section (0-based, never reconverted; a bare section
  mapping is tolerated for pure-science use);
- ``axis == "coordination"``;
- ``estimate(parent, context)`` symbolic preflight with basis,
  ``scope_coverage``, and the enumeration certificate (raw/policy/proof/
  shape/molecular/Burnside layers, exclusions, declared-group completeness);
- ``enumerate_targets(parent, context)`` lazy iterator of stable
  ``GenerationTarget`` records carrying the narrow COMMAND key (center,
  shape, canonical placement, indexed site mapping — no class display ids);
- ``realize(parent, target, context)`` geometric rigid-fragment realization
  with explicit failure accounting (drift is evidence, never success;
  DRIFTED retains the observed canonical key plus geometric evidence for
  engine REALIZED_VIA_DRIFT / OUT_OF_SCOPE routing);
- ``perceive(structure, context)`` donor-geometry audit across all
  registered CN shapes (requested shapes restrict enumeration, never
  observed classification) with measured within-shape and across-shape
  margins and ``verified``/``reported``/``ambiguous`` confidence;
- ``audit_target(structure, target, parent, context)`` hook comparing
  commanded vs re-perceived indexed identity for core parent locks.

Status mapping preserves the coordination-native verdict in evidence:
science ``REALIZED`` -> core ``realized``; ``DRIFTED`` ->
``geometry_failure`` (reason carries the drift detail, structure withheld,
observed key retained); ``UNRESOLVED`` -> ``numerical_failure``.

H_geom suppression lives in :mod:`hgeom` as a helper-only audit until it is
integrated with witnessed target records; core never suppresses without
witnesses, and the synthetic C2 positive control does not imply the engine
suppresses any target geometry.

The single typed-graph authority is the lane-B graph on the context
(``context.graph``); ``context.adjacency`` stays covalent-only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import numpy as np

from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    StructureRecord,
    WorkingRealization,
)
from confflow.science.confgen.model import (
    PerceptionResult as CorePerceptionResult,
)
from confflow.science.confgen.model import (
    RealizationResult as CoreRealizationResult,
)
from confflow.science.confgen.model import (
    StageEstimate as CoreStageEstimate,
)
from confflow.science.topology import inherit_topology_kwargs

from ..graph import (
    CN_SHAPES,
    BindingSite,
    CoordinationSpec,
    DonorBoundProof,
    EdgeType,
    ForbiddenTrans,
    TypedGraph,
)
from .enumeration import (
    canonical_representative,
    command_key,
    enumerate_targets,
    normalize_command_key,
)
from .enumeration import (
    state_matches as _keys_match,
)
from .perception import perceive_donors
from .realization import realize_flexible, realize_target
from .shapes import get_shape, proper_rotation_group

__all__ = [
    "CoordinationStage",
    "NATIVE_STATUSES",
    "HGEOM_HELPER_NOTE",
    "resolve_axis_spec",
    "axis_spec_from_lane_spec",
    "state_matches",
    "adapt_to_core",
]

#: Coordination-native terminal verdicts (preserved inside core evidence).
NATIVE_STATUSES: tuple[str, ...] = ("REALIZED", "DRIFTED", "UNRESOLVED")

#: H_geom integration status: helper-only audit, not engine suppression.
HGEOM_HELPER_NOTE = (
    "H_geom verification (coordination.hgeom) is a helper-only audit until it "
    "is integrated with witnessed target records; core never suppresses "
    "geometry targets without witnesses, and the synthetic C2 positive "
    "control does not imply the engine suppresses any target geometry."
)

#: Section-level coordination tolerances (lane-owned; the frozen
#: coordination_bond_atol / coordination_angle_atol_deg arrive via
#: context.tolerances and govern intra-fragment integrity).
DEFAULT_SECTION_TOLERANCES: dict[str, float] = {
    "realize_tol": 0.45,
    "reaction_tol": 0.25,
    "clash_scale": 0.70,
    "rmsd_tolerance": 0.35,
    "margin_tolerance": 0.05,
    "shape_margin_tolerance": 0.15,
}

_AXIS_SPEC_KEYS = (
    "metal_center",
    "binding_sites",
    "shapes",
    "treatment",
    "constraints",
    "site_group",
    "backend",
    "tolerances",
    "budgets",
    "donor_configuration",
)

#: Realization backend selection (section-level, lane-owned).
BACKEND_CHOICES = ("rigid", "flexible", "rigid_then_flexible")


def _resolve_shapes(spec: CoordinationSpec, shapes: Any) -> tuple[str, ...]:
    if shapes == "auto" or shapes is None:
        return CN_SHAPES[spec.coordination_number]
    if isinstance(shapes, (str, bytes)):
        raise ValueError(f"coordination shapes must be 'auto' or a list, got {shapes!r}")
    names = tuple(str(name) for name in shapes)
    if not names:
        raise ValueError("coordination shapes must name at least one shape")
    return names


def resolve_axis_spec(axis_spec: Mapping[str, Any]) -> CoordinationSpec:
    """Validate a resolved coordination section into a lane-B spec.

    Accepts the resolved 0-based convention (metal_center, site atoms) and
    never reconverts. Unknown keys fail closed. ``shapes`` may be ``"auto"``
    or a list. ``site_group`` optionally declares molecular-orbit generators
    (site-index permutations, already 0-based) for the enumeration
    certificate; without it no molecular accounting is claimed.
    """
    if not isinstance(axis_spec, Mapping):
        raise ValueError("coordination axis_spec must be a mapping")
    unknown = sorted(set(axis_spec) - set(_AXIS_SPEC_KEYS))
    if unknown:
        raise ValueError(f"coordination axis_spec holds unknown keys {unknown}")
    try:
        metal = int(axis_spec["metal_center"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("coordination axis_spec requires metal_center") from exc
    raw_sites = axis_spec.get("binding_sites", [])
    sites: list[BindingSite] = []
    for position, entry in enumerate(raw_sites):
        if not isinstance(entry, Mapping):
            raise ValueError(f"binding_sites[{position}] must be a mapping")
        atoms = tuple(int(a) for a in entry.get("atoms", []))
        sites.append(
            BindingSite(
                id=str(entry.get("id", f"site{position}")),
                kind=str(entry.get("kind", "atom")),
                atoms=atoms,
                hapticity=int(entry.get("hapticity", 1)),
            )
        )
    constraints: list[ForbiddenTrans] = []
    for position, entry in enumerate(axis_spec.get("constraints", []) or []):
        if not isinstance(entry, Mapping):
            raise ValueError(f"constraints[{position}] must be a mapping")
        proof = None
        raw_proof = entry.get("proof")
        if raw_proof is not None:
            if not isinstance(raw_proof, Mapping):
                raise ValueError(f"constraints[{position}].proof must be a mapping")
            pair = raw_proof.get("pair", ["", ""])
            proof = DonorBoundProof(
                pair=(str(pair[0]), str(pair[1])),
                min_distance=raw_proof.get("min_distance"),
                max_distance=raw_proof.get("max_distance"),
                declared_bounds=tuple(
                    (str(name), float(value))
                    for name, value in (raw_proof.get("declared_bounds", []) or [])
                ),
                enforced_by=str(raw_proof.get("enforced_by", "")),
                audit_ref=str(raw_proof.get("audit_ref", "")),
            )
        constraints.append(
            ForbiddenTrans(
                id=str(entry.get("id", f"C{position:02d}")),
                sites=(str(entry["sites"][0]), str(entry["sites"][1])),
                classification=str(entry.get("classification", "REJECTED_BY_POLICY")),
                provenance=str(entry.get("provenance", "")),
                proof=proof,
            )
        )
    treatment = str(axis_spec.get("treatment", "enumerate"))
    if treatment not in ("enumerate", "preserve_input"):
        raise ValueError(f"coordination treatment {treatment!r} is unsupported")
    site_group = axis_spec.get("site_group")
    if site_group is not None:
        if not isinstance(site_group, Mapping):
            raise ValueError("coordination site_group must be a mapping")
        for position, gen in enumerate(site_group.get("generators", []) or []):
            try:
                values = sorted(int(v) for v in gen)
            except (TypeError, ValueError):
                raise ValueError(
                    f"site_group generators[{position}] must permute all site positions"
                ) from None
            if values != list(range(len(sites))):
                raise ValueError(
                    f"site_group generators[{position}] must permute all site positions"
                )
        for position, wit in enumerate(site_group.get("witnesses", []) or []):
            if not isinstance(wit, Mapping) or "mapping" not in wit:
                raise ValueError(
                    f"site_group witnesses[{position}] must map full atom permutations"
                )
            mapping = [int(v) for v in wit["mapping"]]
            if sorted(mapping) != list(range(len(mapping))):
                raise ValueError(f"site_group witnesses[{position}].mapping must be a permutation")
            if not str(wit.get("provenance", "")):
                raise ValueError(f"site_group witnesses[{position}] requires provenance")
    spec = CoordinationSpec(
        metal_center=metal,
        binding_sites=tuple(sites),
        shapes=("auto",),
        treatment=treatment,
        constraints=tuple(constraints),
    )
    return CoordinationSpec(
        metal_center=spec.metal_center,
        binding_sites=spec.binding_sites,
        shapes=_resolve_shapes(spec, axis_spec.get("shapes", "auto")),
        treatment=treatment,
        constraints=spec.constraints,
    )


def axis_spec_from_lane_spec(
    spec: CoordinationSpec,
    *,
    treatment: str | None = None,
    tolerances: Mapping[str, Any] | None = None,
    budgets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a lane-B spec back to a resolved coordination section."""
    return {
        "metal_center": spec.metal_center,
        "binding_sites": [
            {
                "id": site.id,
                "kind": site.kind,
                "atoms": list(site.atoms),
                "hapticity": site.hapticity,
            }
            for site in spec.binding_sites
        ],
        "shapes": list(spec.shapes),
        "treatment": treatment or spec.treatment,
        "constraints": [
            {
                "id": item.id,
                "kind": "FORBIDDEN_TRANS",
                "sites": list(item.sites),
                "classification": item.classification,
                "provenance": item.provenance,
            }
            for item in spec.constraints
        ],
        "tolerances": dict(tolerances or {}),
        "budgets": dict(budgets or {}),
        "donor_configuration": list(spec.site_ids),
    }


def state_matches(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
    context: MolecularContext | None = None,
) -> bool:
    """Compare commanded vs observed coordination keys by indexed identity.

    *context* is accepted for convention validation (keys must already be
    internal 0-based); the comparison itself is the pure indexed-identity
    check in :mod:`enumeration`. Documented hook for core parent locks.
    """
    _ = context
    return _keys_match(expected, observed)


def _lane_graph_from_context(context: MolecularContext) -> TypedGraph:
    """Return the lane-B typed authority carried on the context."""
    graph = context.graph
    if not isinstance(graph, TypedGraph):
        raise ValueError("context graph is not lane-B typed-graph authority")
    return graph


def _section_tolerances(axis_spec: Mapping[str, Any]) -> dict[str, float]:
    options = dict(DEFAULT_SECTION_TOLERANCES)
    for key, value in dict(axis_spec.get("tolerances", {}) or {}).items():
        if key not in options and key != "max_nfev":
            raise ValueError(f"unknown coordination tolerance {key!r}")
        options[key] = float(value)
    return options


def _section_of(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the coordination section from a full resolved mapping."""
    if not isinstance(resolved, Mapping):
        raise ValueError("resolved spec must be a mapping")
    if "coordination" in resolved:
        section = resolved["coordination"]
        if section is None:
            raise ValueError("resolved mapping declares no coordination section")
        if not isinstance(section, Mapping):
            raise ValueError("coordination section must be a mapping")
        return dict(section)
    if "metal_center" in resolved:
        return dict(resolved)
    raise ValueError("mapping holds neither a coordination section nor a bare section")


class CoordinationStage(GenerationStage):
    """Coordination generation stage implementing the core protocol."""

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        section = _section_of(axis_spec)
        self._axis_spec = section
        self._spec = resolve_axis_spec(section)
        self._tolerances = _section_tolerances(section)
        budgets = dict(section.get("budgets", {}) or {})
        self._max_nfev = int(budgets.get("max_nfev", 120))
        self._maxiter = int(budgets.get("maxiter", 400))
        backend = str(section.get("backend", "rigid_then_flexible"))
        if backend not in BACKEND_CHOICES:
            raise ValueError(f"coordination backend {backend!r} must be one of {BACKEND_CHOICES}")
        self._backend = backend
        raw_group = section.get("site_group")
        self._site_generators: tuple[tuple[int, ...], ...] | None = None
        self._site_witnesses: tuple[dict[str, Any], ...] = ()
        if isinstance(raw_group, Mapping) and (raw_group.get("generators") or []):
            self._site_generators = tuple(
                tuple(int(v) for v in gen) for gen in raw_group["generators"]
            )
            self._site_witnesses = tuple(
                {
                    "mapping": tuple(int(v) for v in wit["mapping"]),
                    "provenance": str(wit.get("provenance", "")),
                }
                for wit in (raw_group.get("witnesses", []) or [])
            )

    @property
    def axis(self) -> str:
        """Return the stage axis."""
        return "coordination"

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        """Return the observed-key fields for engine coverage checks."""
        _ = context
        return ("center", "shape", "placement", "sites")

    @property
    def lane_spec(self) -> CoordinationSpec:
        """Return the resolved lane-B coordination spec."""
        return self._spec

    def _check_context(self, context: MolecularContext) -> TypedGraph:
        graph = _lane_graph_from_context(context)
        if graph.n_atoms != len(context.structure.atoms):
            raise ValueError("context graph size disagrees with structure atom count")
        if tuple(context.structure.atoms) != graph.elements:
            raise ValueError("context graph elements disagree with input atom order")
        declared_metal = self._spec.metal_center
        if not 0 <= declared_metal < graph.n_atoms:
            raise ValueError("axis_spec metal_center out of range for context graph")
        donors = set(self._spec.donor_indices)
        coord_neighbors = set(graph.neighbors(declared_metal, EdgeType.COORDINATION))
        if donors and not donors <= coord_neighbors:
            raise ValueError("axis_spec donors disagree with context coordination edges")
        return graph

    def _validated_site_group(self, graph: TypedGraph) -> tuple[Any, list[dict[str, Any]]]:
        """Validate site-group generators against full-atom witnesses.

        Every generator must equal the induced donor action of a supplied
        full-atom witness that itself validates (topology, bond orders,
        binding-site closure, reaction and fragment roles).  A donor-only
        permutation with no backing witness — or a graph-breaking witness —
        fails closed here, never becoming molecular authority.  Generators
        without witnesses are permitted only as a restricted declared
        topological subgroup (topology-level orbits, no stereo, no
        suppression authority).
        """
        from .enumeration import declared_site_group
        from .symmetry import validate_full_witness

        reports: list[dict[str, Any]] = []
        for position, witness in enumerate(self._site_witnesses):
            report = validate_full_witness(
                graph, self._spec, witness["mapping"], witness["provenance"]
            )
            reports.append(report)
            if not report["authority_valid"]:
                raise ValueError(f"site_group witnesses[{position}] breaks typed-graph authority")
        if self._site_generators is None:
            trivial = declared_site_group(self._spec)
            return trivial, reports
        if self._site_witnesses:
            donor_order = list(self._spec.donor_indices)
            backed_actions = []
            for witness in self._site_witnesses:
                action = graph.induced_site_action(witness["mapping"], self._spec.donor_indices)
                if action is None:
                    raise ValueError("site witness donor action not closed")
                # Site-position permutation induced by the atom witness.
                position_of = {donor: pos for pos, donor in enumerate(donor_order)}
                backed_actions.append(tuple(position_of[action[d]] for d in donor_order))
            for gen in self._site_generators:
                if tuple(gen) not in backed_actions:
                    raise ValueError("site_group generator lacks a backing full-atom witness")
        group = declared_site_group(self._spec, site_generators=self._site_generators)
        return group, reports

    def _pipelines(self, context: MolecularContext | None = None) -> list[dict[str, Any]]:
        pipes = [
            enumerate_targets(
                self._spec,
                shape,
                site_generators=self._site_generators,
            )
            for shape in self._spec.shapes
        ]
        if context is not None and self._site_generators is not None:
            graph = _lane_graph_from_context(context)
            group, reports = self._validated_site_group(graph)
            provenances = [wit["provenance"] for wit in self._site_witnesses]
            restricted = not bool(self._site_witnesses)
            for pipe in pipes:
                audit = pipe["orbit_audit"]
                if isinstance(audit, dict):
                    audit["witness_reports"] = [
                        {
                            "authority_valid": r["authority_valid"],
                            "provenance": r["provenance"],
                            "stereo_action": r["stereo_action"],
                            "suppression_authority": r["suppression_authority"],
                        }
                        for r in reports
                    ]
                    audit["authority"] = (
                        "restricted-declared-topological-subgroup"
                        if restricted
                        else "witness-backed-declared-subgroup"
                    )
                    audit["witness_provenance"] = provenances
                    audit["suppression_authority"] = "none (H_geom required)"
        return pipes

    def estimate(self, parent: WorkingRealization, context: MolecularContext) -> CoreStageEstimate:
        """Return the exact symbolic preflight count plus certificate."""
        self._check_context(context)
        if self._spec.treatment == "preserve_input":
            return CoreStageEstimate(
                declared_count=0,
                upper_bound=0,
                exact=True,
                details={
                    "basis": "preserve_input treatment declares no coordination targets",
                    "scope_coverage": "exact",
                    "shapes": list(self._spec.shapes),
                },
            )
        pipes = self._pipelines(context)
        declared = sum(pipe["layers"].shape_classes for pipe in pipes)
        upper = sum(pipe["layers"].raw_assignments for pipe in pipes)
        excluded_summary: dict[str, int] = {}
        for pipe in pipes:
            for item in pipe["excluded"]:
                excluded_summary[item.verdict] = excluded_summary.get(item.verdict, 0) + 1
        certificate = {
            "layers": [pipe["layers"].to_dict() for pipe in pipes],
            "excluded_summary": excluded_summary,
            "site_group": [
                {
                    "shape": pipe["shape"],
                    "site_group": pipe["site_group"].to_dict() if pipe["site_group"] else None,
                    "molecular_orbits": [orbit.to_dict() for orbit in pipe["molecular_orbits"]],
                    "orbit_audit": pipe["orbit_audit"],
                    "molecular_burnside": pipe["molecular_burnside"],
                }
                for pipe in pipes
            ],
        }
        return CoreStageEstimate(
            declared_count=declared,
            upper_bound=upper,
            exact=True,
            details={
                "basis": (
                    "complete proper-rotation groups at declared shapes; exact "
                    "raw/policy/shape layers per shape; molecular orbits only "
                    "where a verified declared site group is supplied"
                ),
                "scope_coverage": "exact",
                "shapes": list(self._spec.shapes),
                "certificate": certificate,
            },
        )

    def enumerate_targets(
        self, parent: WorkingRealization, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        """Yield stable symbolic targets lazily (no geometry generation)."""
        self._check_context(context)
        if self._spec.treatment == "preserve_input":
            return
        ordinal = 0
        for shape_name, pipe in zip(self._spec.shapes, self._pipelines(context)):
            for cls in pipe["shape_classes"]:
                state_value = command_key(
                    self._spec.metal_center,
                    shape_name,
                    list(cls.representative),
                    list(self._spec.site_ids),
                )
                yield GenerationTarget(
                    axis="coordination",
                    target_id=f"coordination:{ordinal:06d}",
                    state_value=state_value,
                    ordinal=ordinal,
                    provenance={
                        "shape": shape_name,
                        "shape_class": cls.id,
                        "group_order": pipe["group_order"],
                        "donor_configuration": list(self._spec.site_ids),
                    },
                )
                ordinal += 1

    def _target_plan(self, target: GenerationTarget) -> tuple[str, tuple[int, ...]]:
        state = normalize_command_key(dict(target.state_value))
        shape_name = state["shape"]
        if shape_name not in self._spec.shapes:
            raise ValueError(f"target shape {shape_name!r} not in stage shapes")
        get_shape(shape_name)
        placement = tuple(int(v) for v in state["placement"])
        if len(placement) != self._spec.coordination_number:
            raise ValueError("target placement incompatible with coordination number")
        group = proper_rotation_group(shape_name)
        return shape_name, canonical_representative(placement, group)

    def _observe(
        self, coordinates: np.ndarray, shape_name: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Perceive one shape; return (observed key, margins record)."""
        result = perceive_donors(
            np.asarray(coordinates, dtype=float),
            self._spec.metal_center,
            self._spec.donor_indices,
            self._spec.site_ids,
            shape_name,
            rmsd_tolerance=self._tolerances["rmsd_tolerance"],
            margin_tolerance=self._tolerances["margin_tolerance"],
        )
        return dict(result.best_key), {
            "shape": shape_name,
            "best_rmsd": result.best_rmsd,
            "margin": result.margin,
            "boundary_flags": list(result.boundary_flags),
            "unambiguous": result.unambiguous,
        }

    def realize(
        self,
        parent: WorkingRealization,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> CoreRealizationResult:
        """Realize one target from the parent geometry with honest accounting."""
        graph = self._check_context(context)
        if target.axis != "coordination":
            return CoreRealizationResult(
                structure=None,
                status="unsupported",
                reason=f"target axis {target.axis!r} is not coordination",
                backend="coordination-stage",
                evidence=({"native_status": "UNRESOLVED"},),
            )
        try:
            shape_name, _ = self._target_plan(target)
        except ValueError as exc:
            return CoreRealizationResult(
                structure=None,
                status="unsupported",
                reason=f"unsupported target: {exc}",
                backend="coordination-stage",
                evidence=({"native_status": "UNRESOLVED"},),
            )
        template = get_shape(shape_name)
        placement = tuple(int(v) for v in dict(target.state_value)["placement"])
        coords = np.array(parent.structure.coordinates, dtype=float)

        def _perceive_class(generated: np.ndarray) -> Any:
            observed, record = self._observe(np.asarray(generated, dtype=float), shape_name)
            return tuple(observed["placement"]), {
                "unambiguous": record["unambiguous"],
                "margin": record["margin"],
            }

        intra_bond = float(context.tolerances.coordination_bond_atol)
        intra_angle = float(context.tolerances.coordination_angle_atol_deg)
        options = {
            "realize_tol": self._tolerances["realize_tol"],
            "intra_bond_tol": intra_bond,
            "intra_angle_tol_deg": intra_angle,
            "reaction_tol": self._tolerances["reaction_tol"],
            "clash_scale": self._tolerances["clash_scale"],
        }
        attempts: list[dict[str, Any]] = []
        natives = []
        if self._backend in ("rigid", "rigid_then_flexible"):
            rigid = realize_target(
                coords,
                graph,
                self._spec.metal_center,
                self._spec.donor_indices,
                self._spec.site_ids,
                placement,
                template,
                target_id=target.target_id,
                perceive=_perceive_class,
                max_nfev=self._max_nfev,
                **options,
            )
            natives.append(rigid)
            attempts.append(_attempt_summary(rigid))
            if rigid.status == "REALIZED" and self._backend == "rigid_then_flexible":
                return self._wrap_native(
                    rigid,
                    shape_name,
                    parent,
                    target,
                    context.adjacency,
                    attempt_records=attempts,
                )
        if self._backend in ("flexible", "rigid_then_flexible"):
            flexible = realize_flexible(
                coords,
                graph,
                self._spec.metal_center,
                self._spec.donor_indices,
                self._spec.site_ids,
                placement,
                template,
                target_id=target.target_id,
                perceive=_perceive_class,
                maxiter=self._maxiter,
                warm_max_nfev=self._max_nfev,
                **options,
            )
            natives.append(flexible)
            attempts.append(_attempt_summary(flexible))
        native = next((item for item in natives if item.status == "REALIZED"), natives[-1])
        return self._wrap_native(
            native, shape_name, parent, target, context.adjacency, attempt_records=attempts
        )

    def _wrap_native(
        self,
        native: Any,
        shape_name: str,
        parent: WorkingRealization,
        target: GenerationTarget,
        adjacency: Any,
        attempt_records: list[dict[str, Any]] | None = None,
    ) -> CoreRealizationResult:
        """Map a native verdict onto the core result, retaining evidence."""
        evidence = dict(native.evidence)
        evidence["native_status"] = native.status
        evidence["native_reason"] = native.reason
        evidence["target_id"] = native.target_id
        if attempt_records is not None:
            evidence["attempt_records"] = list(attempt_records)
        # Retain the observed indexed state for engine drift/out-of-scope
        # routing (geometry itself is withheld on failure per core contract).
        observed_key: dict[str, Any] | None = None
        perceived = evidence.get("perceived_class")
        if isinstance(perceived, (tuple, list)):
            try:
                observed_key = command_key(
                    self._spec.metal_center,
                    shape_name,
                    [int(v) for v in perceived],
                    list(self._spec.site_ids),
                )
            except ValueError:
                observed_key = None
        evidence["observed_key"] = observed_key
        frozen_evidence = _freeze_evidence(evidence)
        if native.status == "REALIZED":
            assert native.structure is not None
            record = StructureRecord(
                id=target.target_id,
                atoms=tuple(parent.structure.atoms),
                coordinates=tuple(tuple(point) for point in native.structure),
                charge=parent.structure.charge,
                multiplicity=parent.structure.multiplicity,
                parent_ids=(parent.structure.id,),
                source_step_id="coordination",
                **inherit_topology_kwargs(parent.structure, adjacency),
            )
            return CoreRealizationResult(
                structure=record,
                status="realized",
                reason=native.reason,
                backend=native.backend,
                evidence=(frozen_evidence,),
            )
        if native.status == "DRIFTED":
            return CoreRealizationResult(
                structure=None,
                status="geometry_failure",
                reason=f"coordination_drift: {native.reason}",
                backend=native.backend,
                evidence=(frozen_evidence,),
            )
        return CoreRealizationResult(
            structure=None,
            status="numerical_failure",
            reason=native.reason,
            backend=native.backend,
            evidence=(frozen_evidence,),
        )

    def perceive(
        self, structure: StructureRecord, context: MolecularContext
    ) -> CorePerceptionResult:
        """Perceive donor geometry across registered CN shapes.

        Requested shapes restrict enumeration, never observed classification:
        every registered shape at the coordination number is fitted, and the
        best/second-best margin is measured across shapes.  An out-of-scope
        best shape is reported (never hidden) with a boundary flag.
        """
        self._check_context(context)
        coords = np.array(structure.coordinates, dtype=float)
        registered = CN_SHAPES[self._spec.coordination_number]
        scored = []
        for shape_name in registered:
            observed, margins = self._observe(coords, shape_name)
            scored.append((margins["best_rmsd"], shape_name, observed, margins))
        scored.sort(key=lambda item: (item[0], item[1]))
        _, best_shape, observed, margins = scored[0]
        second_rmsd = scored[1][0] if len(scored) > 1 else float("inf")
        shape_margin = float(second_rmsd - margins["best_rmsd"])
        flags = list(margins["boundary_flags"])
        if not shape_margin >= self._tolerances["shape_margin_tolerance"]:
            flags.append("SMALL_SHAPE_MARGIN")
        if best_shape not in self._spec.shapes:
            flags.append("OUT_OF_SCOPE_SHAPE")
        if "HIGH_BEST_RMSD" in flags or "SMALL_MARGIN" in flags or "SMALL_SHAPE_MARGIN" in flags:
            confidence = "ambiguous"
        elif flags:
            confidence = "reported"
        else:
            confidence = "verified"
        alternatives = tuple(
            {
                "shape": shape,
                "sites": dict(obs["sites"]),
                "placement": list(obs["placement"]),
                "rmsd": record["best_rmsd"],
            }
            for _, shape, obs, record in scored[1:]
        )
        return CorePerceptionResult(
            best_key=dict(observed),
            alternatives=alternatives,
            confidence=confidence,
            margins={
                "best_rmsd": margins["best_rmsd"],
                "margin_within_shape": margins["margin"],
                "margin_across_shapes": shape_margin,
                "shape": best_shape,
                "second_shape": scored[1][1] if len(scored) > 1 else None,
            },
            boundary_flags=tuple(flags),
        )

    def suppression_for_target(
        self,
        parent: WorkingRealization,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> dict[str, Any] | None:
        """Propose verified suppression for one target (engine hook, helper).

        Returns a verified suppression record, or ``None`` (realize
        normally).  Suppression requires ALL of: ``enumerate`` treatment, no
        combined ring/torsion axes (fail closed until joint action verified),
        a complete declared site group backed by full-atom witnesses, a
        multi-member orbit with the target as a non-representative member,
        declared stereo labels, a fitted proper rotation satisfying the pair
        equation on the parent geometry, unambiguous re-perception with
        margins, induced template action in the proper group, an exact
        parent StateKey stabilizer, and full :func:`verify_hgeom` witnesses.
        The engine owns terminal accounting (a future
        REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY status); this hook only
        proposes, never removes.  Until the engine binds it, suppression
        stays helper-only with zero engine effect — stated, not implied.
        """
        from .hgeom import HGeomKey, induced_template_action, verify_hgeom
        from .shapes import kabsch_proper_rotation

        if self._spec.treatment == "preserve_input":
            return None
        resolved = context.resolved_spec
        if list(resolved.get("rings", []) or []) or list(resolved.get("torsions", []) or []):
            return None
        graph = self._check_context(context)
        try:
            commanded = normalize_command_key(dict(target.state_value))
        except ValueError:
            return None
        shape_name = commanded["shape"]
        if shape_name not in self._spec.shapes:
            return None
        template = get_shape(shape_name)
        group = proper_rotation_group(shape_name)
        pipes = self._pipelines(context)
        pipe = next((p for p in pipes if p["shape"] == shape_name), None)
        if pipe is None or pipe["site_group"] is None or not pipe["site_group"].complete:
            return None
        classes_by_id = {cls.id: cls.representative for cls in pipe.get("shape_classes", [])}
        orbit = next(
            (
                o
                for o in pipe["molecular_orbits"]
                if commanded_key_in_orbit(commanded, o, classes_by_id)
            ),
            None,
        )
        if orbit is None:
            return None
        members = sorted(orbit.members)
        representative_id = members[0]
        rep_target = self._target_by_class(context, parent, pipe, representative_id)
        if rep_target is None or rep_target.target_id == target.target_id:
            return None
        # Full-atom witness backing the orbit step (topology validated).
        witness = self._witness_for_orbit(graph, commanded, pipe)
        if witness is None:
            return None
        sigma = witness["site_perm"]
        atom_perm = witness["mapping"]
        # Fit the proper rotation on parent donor directions (measured R).
        coords = np.array(parent.structure.coordinates, dtype=float)
        center = coords[self._spec.metal_center]
        donors = list(self._spec.donor_indices)
        dirs = np.array([coords[d] - center for d in donors])
        if bool(np.any(np.linalg.norm(dirs, axis=1) < 1e-9)):
            return None
        order = list(range(len(donors)))
        inv = [0] * len(donors)
        for old, new in enumerate(sigma):
            inv[new] = old
        src = np.array([dirs[inv[i]] for i in order])
        fitted = kabsch_proper_rotation(src, dirs)
        pair_disp = float(
            np.max(
                np.linalg.norm(
                    np.array(
                        [
                            coords[atom_perm[i]] - (center + fitted @ (coords[i] - center))
                            for i in range(graph.natoms)
                        ]
                    ),
                    axis=1,
                )
            )
        )
        stereo_labels = _stereo_labels_from_context(context, graph.natoms)
        if stereo_labels is None:
            return None
        perceived = self.perceive(parent.structure, context)
        if perceived.confidence == "ambiguous":
            return None
        observed = dict(perceived.best_key)
        if observed.get("shape") != shape_name:
            return None
        margins = (
            float(perceived.margins["margin_within_shape"]),
            float(perceived.margins["margin_within_shape"]),
        )
        rho = induced_template_action(
            tuple(observed["placement"]), list(sigma), [list(g) for g in group]
        )
        if rho is None:
            return None
        witness_order = _permutation_order(atom_perm)
        key = HGeomKey(
            kind="rotation-permutation",
            mapping=tuple(atom_perm),
            rotation=tuple(tuple(float(v) for v in row) for row in fitted),
            center=tuple(float(v) for v in center),
            order=witness_order,
            scope=tuple(range(graph.natoms)),
            declared_site_action={
                site: self._spec.site_ids[sigma[pos]]
                for pos, site in enumerate(self._spec.site_ids)
            },
            declared_rho=tuple(int(v) for v in rho),
        )
        verdict = verify_hgeom(
            coords,
            key,
            graph,
            self._spec.metal_center,
            donors,
            list(self._spec.site_ids),
            stereo_labels=stereo_labels,
            perceive=lambda c: self._observe(np.asarray(c), shape_name)[0]["placement"],
            perception_margins=margins,
            state_value=dict(observed["sites"]),
            site_permutation=list(sigma),
            template=template,
        )
        if not verdict.suppress_allowed:
            return None
        return {
            "suppressed_target": target.target_id,
            "representative_target": rep_target.target_id,
            "orbit_id": orbit.id,
            "rho": [int(v) for v in rho],
            "site_action": [int(v) for v in sigma],
            "rotation": [[float(v) for v in row] for row in fitted],
            "pair_residual": pair_disp,
            "witness_provenance": witness["provenance"],
            "stereo_centers_audited": verdict.witnesses.get("stereo", {}).get("centers_audited"),
            "closure_order": witness_order,
        }

    def _target_by_class(
        self,
        context: MolecularContext,
        parent: WorkingRealization,
        pipe: dict[str, Any],
        class_id: str,
    ) -> GenerationTarget | None:
        """Find the enumerated target carrying a shape-class id."""
        by_id = {cls.id: cls.representative for cls in pipe.get("shape_classes", [])}
        wanted = by_id.get(class_id)
        if wanted is None:
            return None
        for target in self.enumerate_targets(parent, context):
            state = dict(target.state_value)
            if state.get("shape") == pipe["shape"] and tuple(state.get("placement", ())) == tuple(
                int(v) for v in wanted
            ):
                return target
        return None

    def _witness_for_orbit(
        self, graph: TypedGraph, commanded: Mapping[str, Any], pipe: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Find a validated witness stepping the commanded class off itself."""
        try:
            placement = tuple(int(v) for v in commanded["placement"])
        except (KeyError, TypeError, ValueError):
            return None
        by_id = {cls.id: cls.representative for cls in pipe.get("shape_classes", [])}
        members = set()
        for orbit in pipe.get("molecular_orbits", []):
            reps = {tuple(by_id[m]) for m in orbit.members if m in by_id}
            if placement in reps and len(reps) > 1:
                members = reps
                break
        if not members:
            return None
        donor_order = list(self._spec.donor_indices)
        position_of = {donor: pos for pos, donor in enumerate(donor_order)}
        for witness in self._site_witnesses:
            action = graph.induced_site_action(witness["mapping"], donor_order)
            if action is None:
                continue
            site_perm = tuple(position_of[action[d]] for d in donor_order)
            # Relabel commanded placement by the witness site action.
            inv = [0] * len(site_perm)
            for old, new in enumerate(site_perm):
                inv[new] = old
            image = tuple(placement[inv[i]] for i in range(len(placement)))
            from .enumeration import canonical_representative
            from .shapes import proper_rotation_group

            group = proper_rotation_group(str(commanded.get("shape", self._spec.shapes[0])))
            if (
                canonical_representative(image, group) in members
                and canonical_representative(image, group) != placement
            ):
                return {
                    "site_perm": site_perm,
                    "mapping": list(witness["mapping"]),
                    "provenance": witness["provenance"],
                }
        return None

    def verify_locked(
        self,
        structure: StructureRecord,
        locked_state: Mapping[str, Any],
        context: MolecularContext,
        parent: WorkingRealization | None = None,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Verify a locked coordination ancestor state on fresh geometry.

        Agreed engine hook for inherited-axis locks: re-perceives *structure*
        with real perception (unambiguous required), snaps the canonical
        observed key, and compares the FULL locked identity
        (center/shape/placement/sites) via indexed identity.  Scope is the
        coordination sphere only — metal, donors, and their covalent
        neighbors — plus the declared donor configuration: ring interiors and
        other torsion/ring degrees of freedom are never frozen by the C lock
        (a chair-to-boat ring change with intact donor placement still
        verifies).  Binding radial integrity (every donor-metal distance
        within ``coordination_bond_atol`` of the reference) is enforced, so a
        donor-ray shape match cannot mask a broken coordination distance.
        The reference is the accepted parent realization when supplied,
        else ``context.input_coords`` (recorded in evidence); unchanged
        accepted parents always verify.  Returns ``(ok, snapped_map,
        evidence)``; ``snapped_map`` is ``{}`` when unverifiable.
        """
        from .realization import _bond_angle_deg

        try:
            expected = normalize_command_key(dict(locked_state))
        except ValueError as exc:
            return (
                False,
                {},
                [{"kind": "coordination_lock", "error": f"locked key malformed: {exc}"}],
            )
        graph = self._check_context(context)
        try:
            perceived = self.perceive(structure, context)
        except Exception as exc:
            return (
                False,
                {},
                [
                    {
                        "kind": "coordination_lock",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": f"perception raised {type(exc).__name__}",
                    }
                ],
            )
        observed = dict(perceived.best_key)
        coords = np.array(structure.coordinates, dtype=float)
        if parent is not None:
            reference = np.array(parent.structure.coordinates, dtype=float)
            reference_source = "accepted-parent"
        else:
            reference = np.array(context.input_coords, dtype=float)
            reference_source = "context-input"
        if reference.shape != coords.shape:
            return (
                False,
                {},
                [{"kind": "coordination_lock", "error": "reference geometry mismatch"}],
            )
        donors = list(self._spec.donor_indices)
        metal = self._spec.metal_center
        scope = {metal, *donors}
        for donor in donors:
            scope.update(n for n in graph.neighbors(donor, EdgeType.COVALENT))
        bond_tol = float(context.tolerances.coordination_bond_atol)
        angle_tol = float(context.tolerances.coordination_angle_atol_deg)
        radial_devs = [
            abs(
                float(np.linalg.norm(coords[d] - coords[metal]))
                - float(np.linalg.norm(reference[d] - reference[metal]))
            )
            for d in donors
        ]
        max_radial = float(max(radial_devs)) if radial_devs else 0.0
        bond_devs = [
            abs(
                float(np.linalg.norm(coords[edge.a] - coords[edge.b]))
                - float(np.linalg.norm(reference[edge.a] - reference[edge.b]))
            )
            for edge in graph.edges
            if edge.type is EdgeType.COVALENT and edge.a in scope and edge.b in scope
        ]
        max_bond = float(max(bond_devs)) if bond_devs else 0.0
        angle_devs = []
        for atom in sorted(scope):
            cov = [n for n in graph.neighbors(atom, EdgeType.COVALENT) if n in scope]
            for pos, first in enumerate(cov):
                for second in cov[pos + 1 :]:
                    before = _bond_angle_deg(reference, atom, first, second)
                    after = _bond_angle_deg(coords, atom, first, second)
                    if before != before or after != after:
                        continue
                    angle_devs.append(abs(before - after))
        max_angle = float(max(angle_devs)) if angle_devs else 0.0
        evidence = [
            {
                "kind": "coordination_lock",
                "expected": expected,
                "observed": observed,
                "confidence": perceived.confidence,
                "margins": dict(perceived.margins),
                "reference_source": reference_source,
                "lock_scope_atoms": sorted(scope),
                "max_radial_deviation": max_radial,
                "max_bond_deviation": max_bond,
                "max_angle_deviation_deg": max_angle,
            }
        ]
        if perceived.confidence == "ambiguous":
            evidence.append(
                {
                    "kind": "anomaly",
                    "anomaly": "AMBIGUOUS_KEY",
                    "detail": "locked perception ambiguous",
                }
            )
            return False, {}, evidence
        if not state_matches(expected, observed, context):
            return False, observed, evidence
        if not max_radial <= bond_tol:
            evidence.append(
                {
                    "kind": "anomaly",
                    "anomaly": "RADIAL_DRIFT",
                    "detail": f"donor-metal deviation {max_radial:.4f}A exceeds {bond_tol}A",
                }
            )
            return False, observed, evidence
        if not max_bond <= bond_tol:
            return False, observed, evidence
        if not max_angle <= angle_tol:
            return False, observed, evidence
        return True, observed, evidence

    def audit_target(
        self,
        structure: StructureRecord,
        target: GenerationTarget,
        parent: WorkingRealization,
        context: MolecularContext,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Compare commanded vs re-perceived indexed identity (engine hook).

        Returns ``(ok, canonical_observed_key, evidence)``.  ``ok`` is True
        only when the re-perceived state exactly matches the commanded key;
        otherwise the observed key plus margins ride along so the engine
        records drift (REALIZED_VIA_DRIFT routing) instead of success.
        Ambiguity never verifies: it returns ``ok=False`` with anomaly
        evidence (OUT_OF_SCOPE routing).
        """
        _ = parent
        try:
            commanded = normalize_command_key(dict(target.state_value))
        except ValueError as exc:
            return (
                False,
                {},
                [{"kind": "coordination_audit", "error": f"commanded key malformed: {exc}"}],
            )
        try:
            perceived = self.perceive(structure, context)
        except Exception as exc:
            return (
                False,
                {},
                [
                    {
                        "kind": "coordination_audit",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": f"perception raised {type(exc).__name__}",
                    }
                ],
            )
        observed = dict(perceived.best_key)
        evidence = [
            {
                "kind": "coordination_audit",
                "commanded": commanded,
                "observed": observed,
                "margins": dict(perceived.margins),
                "confidence": perceived.confidence,
            }
        ]
        if perceived.confidence == "ambiguous":
            evidence.append(
                {
                    "kind": "anomaly",
                    "anomaly": "AMBIGUOUS_KEY",
                    "detail": "observed state ambiguous",
                }
            )
            return False, observed, evidence
        if not state_matches(commanded, observed, context):
            return False, observed, evidence
        return True, observed, evidence


def _attempt_summary(native: Any) -> dict[str, Any]:
    """Reduce one backend attempt to a routing-grade summary record."""
    evidence = dict(native.evidence)
    return {
        "backend": native.backend,
        "native_status": native.status,
        "reason": native.reason,
        "max_donor_error": evidence.get("max_donor_error"),
        "max_intra_bond_deviation": evidence.get("max_intra_bond_deviation"),
        "max_intra_angle_deviation_deg": evidence.get("max_intra_angle_deviation_deg"),
        "min_clash_gap": evidence.get("min_clash_gap"),
        "max_reaction_violation": evidence.get("max_reaction_violation"),
        "perception_gate_passed": evidence.get("perception_gate_passed"),
    }


def _permutation_order(perm: Sequence[int]) -> int | None:
    """Return the multiplicative order of a permutation (None beyond 12)."""
    perm_t = tuple(int(v) for v in perm)
    count = len(perm_t)
    current = tuple(range(count))
    for order in range(1, 13):
        current = tuple(current[perm_t[i]] for i in range(count))
        if all(v == i for i, v in enumerate(current)):
            return order
    return None


def _stereo_labels_from_context(
    context: MolecularContext, natoms: int
) -> tuple[str | None, ...] | None:
    """Read declared atom stereo labels from the resolved spec, if present."""
    stereo = context.resolved_spec.get("stereochemistry", {})
    if not isinstance(stereo, Mapping):
        return None
    labels = stereo.get("labels")
    if labels is None:
        return None
    items = list(labels)
    if len(items) != natoms:
        return None
    return tuple(item if item is None else str(item) for item in items)


def commanded_key_in_orbit(
    commanded: Mapping[str, Any], orbit: Any, classes_by_id: dict[str, Any]
) -> bool:
    """Return whether a commanded key's placement sits in the orbit."""
    try:
        placement = tuple(int(v) for v in commanded["placement"])
    except (KeyError, TypeError, ValueError):
        return False
    for member in orbit.members:
        rep = classes_by_id.get(member)
        representative: Sequence[int] | None = None
        if rep is not None:
            if hasattr(rep, "representative"):
                representative = tuple(int(v) for v in rep.representative)
            else:
                representative = tuple(int(v) for v in rep)
        if representative is not None and representative == placement:
            return True
    return False


def _freeze_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce science evidence to JSON-compatible core evidence."""
    frozen = _freeze_value(evidence)
    if not isinstance(frozen, dict):
        raise ValueError("evidence must freeze to a mapping")
    return frozen


def _freeze_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, Mapping):
        return {str(key): _freeze_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_freeze_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def adapt_to_core(argument: Any) -> CoordinationStage:
    """Bind the engine's call to a protocol-exact coordination stage.

    Accepts the FULL resolved spec mapping (constructs from its
    ``coordination`` section, 0-based, never reconverted) or an existing
    :class:`CoordinationStage` instance (returned unchanged).  Never raises
    ImportError: the protocol binding is complete.
    """
    if isinstance(argument, CoordinationStage):
        return argument
    if isinstance(argument, Mapping):
        return CoordinationStage(argument)
    raise TypeError("adapt_to_core requires a full resolved spec mapping or a CoordinationStage")
