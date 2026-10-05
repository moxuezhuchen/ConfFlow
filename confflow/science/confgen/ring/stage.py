#!/usr/bin/env python3

"""ConfGen v3 ring-lane stage adapter (shared ``GenerationStage`` protocol).

``RingStage`` wraps the pure ring science (CP forms/perception/realization)
in the frozen core stage API from ``confflow.science.confgen.model``. There
are no fallback/shadow dataclasses: a broken core integration fails closed at
import time. Enumeration is a lazy generator per the frozen
``GenerationStage`` contract (stable order, never a materialized grid;
sampling and deferred ranges are owned by the engine via core planner
helpers, never by this stage).

State identity is canonical R4 (form family + index + anchor + direction);
treatment travels in scope/provenance (axis spec, target provenance,
realization evidence), and measured CP/torsions, confidence, margins, and
boundary flags travel in perception diagnostics. Parent-lock and
fresh-target audits run against measured CP via ``verify_locked`` /
``audit_target`` hooks (CP distance for n=5/6, sign+q gate for n=4), never
against snapped evidence.

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

from .forms import (
    alias_templates_to_forms,
    constrained_forms,
    default_forms,
    expand_form_tokens,
    form_name,
)
from .perception import (
    MARGIN_DEG_DEFAULT,
    MATCH_DEG_DEFAULT,
    REJECT_DEG_DEFAULT,
    perceive_ring,
    ring_diagnostics,
    ring_state_dict,
    ring_states_match,
)
from .puckering import CanonicalForm, canonical_forms
from .realization import (
    RingGeometryFailure,
    RingNumericalFailure,
    RingSpec,
    RingTolerances,
    RingUnsupported,
    parse_ring_specs,
)

__all__ = [
    "BACKEND_NAME",
    "RingStage",
]

#: Backend label stamped on every ring realization result.
BACKEND_NAME = "cp-constrained-v2"

#: Component-local CP thresholds (degrees, n=5/6); not global tolerances.
_CP_MATCH_DEG = 15.0
_CP_MARGIN_DEG = 8.0
_CP_REJECT_DEG = 35.0


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
                    forms=spec.forms,
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
        # v4 priority: record whether clash_threshold was explicitly present
        # in the raw mapping (an explicit .65 is explicit, not omitted).
        self._stage_clash_explicit = "clash_threshold" in tol
        self._match_deg = float(tol.get("perception_match_deg", MATCH_DEG_DEFAULT))
        self._margin_deg = float(tol.get("perception_margin_deg", MARGIN_DEG_DEFAULT))
        self._reject_deg = float(tol.get("perception_reject_deg", REJECT_DEG_DEFAULT))

    def _r3_tolerances(self, context: MolecularContext) -> Any:
        """Explicit clash passthrough for R3 (default stays 0.65).

        R3 already honors a ``ConfgenTolerances`` clash override; R4 must
        not drop an explicitly declared threshold. Priority: an explicit
        raw-stage value (key present, including an explicit .65) always
        wins; only when the stage omits the key is the pipeline
        ``context.tolerances.clash_threshold`` used. No new source is
        invented; all other solver/numeric gates stay frozen.
        """
        if self._stage_clash_explicit:
            explicit = float(self._tolerances.clash_threshold)
        else:
            explicit = float(context.tolerances.clash_threshold)
        return replace(context.tolerances, clash_threshold=explicit)

    @property
    def axis(self) -> str:
        """Return the stage axis (``rings``)."""
        return "rings"

    #: A3 dispatch hooks (engine-read; component owns its literals).
    check_bond_integrity: bool = False
    carries_inherited_locks: bool = False

    def preserved_entries(self, resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return ring ``preserve_input`` entries (delegates to scope)."""
        from confflow.science.confgen.ring.scope import preserved_entries as _entries

        return _entries(resolved)

    def report_section(self, resolved: Mapping[str, Any]) -> dict[str, Any] | None:
        """Ring contributes no report section."""
        from confflow.science.confgen.ring.scope import report_section as _section

        return _section(resolved)

    def describe_scope(self, resolved: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Return the ring scope slice (delegates to scope)."""
        from confflow.science.confgen.ring.scope import describe_scope as _describe

        return _describe(resolved)

    def fallback_lock(
        self,
        locked_state: Mapping[str, Any],
        structure: Any,
        context: MolecularContext,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any], Any] | None:
        """Verify ring ancestor locks without a stage hook (owns matcher)."""
        from confflow.science.confgen.ring.scope import fallback_lock as _fallback

        return _fallback(self, locked_state, structure, context)

    @property
    def specs(self) -> tuple[RingSpec, ...]:
        """Return normalized ring specs in stable (sorted-id) order."""
        return self._specs

    def _enumerated(self) -> tuple[RingSpec, ...]:
        return tuple(spec for spec in self._specs if spec.treatment == "enumerate")

    @staticmethod
    def _canonical_solver_order(atoms: tuple[int, ...] | list[int]) -> tuple[list[int], int, bool]:
        """Canonical solver traversal for one ring (R4, no global resort).

        Starts at the lowest global atom number: rotate the declared list
        left until it leads, then compare the forward tuple with
        ``[first] + reversed(rest)`` and take the lexicographically smaller
        as ``solver_atoms``. Returns ``(solver_atoms, shift, reverse)``
        where ``shift`` is left-shifts from declared to the rotated
        (pre-reverse) order and ``reverse`` flags the mirror pick. The
        combination order matches ``puckering.relabel`` (shift first, then
        reverse), so ``relabel(form, shift, reverse)`` maps a declared
        target to its canonical-solver target for the same physical state.
        """
        seq = [int(a) for a in atoms]
        n = len(seq)
        if n == 0:
            return [], 0, False
        m = min(seq)
        pos = seq.index(m)
        rotated = seq[pos:] + seq[:pos]
        shift = int(pos % n)
        forward = tuple(rotated)
        reversed_cand = tuple([rotated[0]] + list(reversed(rotated[1:])))
        if reversed_cand < forward:
            return list(reversed_cand), shift, True
        return list(forward), shift, False

    def _base_forms(self, spec: RingSpec) -> tuple[CanonicalForm, ...]:
        """Return declared base forms for one system (fail closed, no geometry)."""
        n = len(spec.atoms)
        if n not in (4, 5, 6):
            raise RingUnsupported(f"unsupported_ring_size:{n}")
        try:
            if spec.forms:
                return expand_form_tokens(list(spec.forms), n)
            if spec.templates:
                return alias_templates_to_forms(list(spec.templates), n)
            return default_forms(n)
        except KeyError as exc:
            raise RingUnsupported(str(exc)) from exc

    def _pinned_for_spec(
        self, spec: RingSpec, parent: StageParentProtocol, context: MolecularContext
    ) -> set[tuple[int, int]]:
        """Return R2 pinned global pairs restricted to this ring (best effort)."""
        try:
            from .rigid_units import analyze_rigid_units

            structure = parent.structure
            coords = np.asarray(structure.coordinates, dtype=float)
            elements = [str(a) for a in structure.atoms]
            graph = self._covalent_graph(context)
            analysis = analyze_rigid_units(coords, elements, graph, list(spec.atoms))
            pinned: set[tuple[int, int]] = set()
            ring_set = frozenset(int(a) for a in spec.atoms)
            for unit in analysis.units:
                for pair in unit.pinned_bonds:
                    a, b = int(pair[0]), int(pair[1])
                    if a in ring_set and b in ring_set:
                        pinned.add((a, b) if a < b else (b, a))
            return pinned
        except Exception:
            return set()

    def _forms_for(
        self, spec: RingSpec, parent: StageParentProtocol, context: MolecularContext
    ) -> tuple[CanonicalForm, ...]:
        """Combine base and constrained forms in stable canonical order."""
        base = list(self._base_forms(spec))
        # Explicit requests already include everything; constrained only adds
        # when the base came from defaults (spec has neither forms nor templates).
        # When the user explicitly declares forms/templates, honor exactly that
        # set (plus nothing) so precise selectors stay precise.
        if spec.forms or spec.templates:
            return tuple(base)
        n = len(spec.atoms)
        pinned = self._pinned_for_spec(spec, parent, context)
        extra = list(constrained_forms(n, pinned, [int(a) for a in spec.atoms]))
        seen: dict[str, CanonicalForm] = {}
        for form in base + extra:
            key = form_name(form) if not (form.family == "P" and n in (5, 6)) else "P_0"
            if key not in seen:
                seen[key] = form
        # Stable canonical order.
        order = {form_name(f): i for i, f in enumerate(canonical_forms(n))}
        order["P_0"] = -1

        def _key(f: CanonicalForm) -> tuple[int, str]:
            nm = form_name(f) if not (f.family == "P" and n in (5, 6)) else "P_0"
            return (order.get(nm, 9999), nm)

        return tuple(sorted(seen.values(), key=_key))

    def _options(self, spec: RingSpec) -> tuple[str, ...]:
        """Return declared enumeration precise names (fail closed, symbolic).

        Kept for backward-compatible callers; new code prefers _base_forms.
        """
        return tuple(form_name(f) for f in self._base_forms(spec))

    def estimate(self, parent: StageParentProtocol, context: MolecularContext) -> StageEstimate:
        """Symbolic declared count: product of per-system form options."""
        enumerated = self._enumerated()
        total = 1
        unsupported: list[str] = []
        per_system: dict[str, Any] = {}
        for spec in enumerated:
            try:
                options = self._forms_for(spec, parent, context)
            except RingUnsupported as exc:
                unsupported.append(f"{spec.id}:{exc}")
                per_system[spec.id] = {"options": 0, "reason": str(exc)}
                total = 0
                continue
            per_system[spec.id] = {
                "options": len(options),
                "forms": [form_name(f) for f in options],
            }
            total *= len(options)
        if not enumerated:
            total = 1 if self._specs else 0
        details: dict[str, Any] = {
            "basis": "finite declared CP-form product over isolated ring systems; "
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
        """Enumerate symbolic targets lazily in stable order (no solving).

        A generator over the mixed-radix ordinal space (last system fastest);
        the grid is never materialized. Commanded per-system states carry
        R4 form identity; agreement with observed states is checked via CP
        distance (n=5/6) or sign gate (n=4), never by float equality.
        """
        enumerated = self._enumerated()
        option_lists = [self._forms_for(spec, parent, context) for spec in enumerated]
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
            state_value = {}
            for position, spec in enumerate(enumerated):
                form = option_lists[position][combo[position]]
                state_value[spec.id] = {
                    "form": form.family,
                    "index": form.index,
                    "anchor": spec.atoms[0],
                    "direction": "as_given",
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

    def _form_for_assignment(self, spec: RingSpec, entry: Mapping[str, Any]) -> CanonicalForm:
        """Resolve commanded state_value entry to a CanonicalForm (fail closed)."""
        n = len(spec.atoms)
        if "form" in entry:
            family = str(entry.get("form"))
            try:
                index = int(entry.get("index", 0))
            except (TypeError, ValueError) as exc:
                raise RingUnsupported(f"bad form index:{entry!r}") from exc
            if family == "flat":
                raise RingUnsupported("flat is not realizable")
            if family == "P" and n in (5, 6):
                from .forms import _synthetic_planar

                if index != 0:
                    raise RingUnsupported(f"unknown form P:{index}")
                return _synthetic_planar(n)
            for form in canonical_forms(n):
                if form.family == family and form.index == index:
                    return form
            raise RingUnsupported(f"unknown_form:{family}:{index}")
        # Legacy template-shaped entry (alias rewrite period).
        if "template" in entry:
            try:
                forms = alias_templates_to_forms([str(entry.get("template"))], n)
            except KeyError as exc:
                raise RingUnsupported(str(exc)) from exc
            if len(forms) != 1:
                raise RingUnsupported(f"ambiguous_template:{entry.get('template')!r}")
            return forms[0]
        raise RingUnsupported(f"missing_form:{spec.id}")

    def _explicit_p(self, spec: RingSpec) -> bool:
        """Whether the spec explicitly declares planar P."""
        for token in list(spec.forms):
            if str(token) in ("P", "P_0"):
                return True
        for token in list(spec.templates):
            if str(token) in ("planar_4", "planar_5"):
                return True
        return False

    def realize(
        self,
        parent: StageParentProtocol,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        """Realize one target via CP-constrained solving (R3+R4)."""
        try:
            from .realization import realize_cp_target
            from .rigid_units import analyze_rigid_units

            structure = parent.structure
            coords = np.asarray(structure.coordinates, dtype=float)
            if not np.all(np.isfinite(coords)):
                raise RingNumericalFailure("nonfinite")
            elements = [str(item) for item in structure.atoms]
            graph = self._covalent_graph(context)
            coord_atoms = self._coordination_atoms(context)
            state_value = dict(target.state_value) or {}
            # Preserve-input systems: measure and report unchanged.
            working = np.asarray(coords, dtype=float).copy()
            states: list[dict[str, object]] = []
            audits: list[dict[str, object]] = []
            preserved = True
            all_ring_atoms = (
                frozenset().union(*(frozenset(s.atoms) for s in self._specs))
                if self._specs
                else frozenset()
            )
            for spec in self._specs:
                others = set(all_ring_atoms) - set(spec.atoms)
                if spec.treatment == "preserve_input":
                    perception = perceive_ring(
                        working[list(spec.atoms)],
                        match_deg=self._match_deg,
                        ambiguity_margin_deg=self._margin_deg,
                        reject_deg=self._reject_deg,
                    )
                    states.append(ring_state_dict(perception, anchor=spec.atoms[0]))
                    audits.append(
                        {
                            "ring_id": spec.id,
                            "treatment": spec.treatment,
                            "preserved": True,
                            "diagnostics": ring_diagnostics(perception),
                        }
                    )
                    continue
                if spec.id not in state_value:
                    raise RingNumericalFailure(f"missing_assignment:{spec.id}")
                entry = state_value[spec.id]
                if not isinstance(entry, Mapping):
                    raise RingNumericalFailure(f"missing_assignment:{spec.id}")
                form = self._form_for_assignment(spec, entry)
                # R4 canonical solver traversal (root direction): solver sees
                # atoms starting at the lowest global id, forward vs reversed
                # lexicographically minimal. Original declared traversal,
                # enumeration StateKey, anchor/direction and pre-publish
                # perception all stay in the declared order; only the R3
                # solver call (and its R2 lock regeneration) uses canonical.
                solver_atoms, shift, reverse = self._canonical_solver_order(spec.atoms)
                if form.family == "P" and len(spec.atoms) in (5, 6):
                    solver_form = form
                else:
                    try:
                        from .puckering import relabel as _relabel

                        solver_form = _relabel(form, shift=shift, reverse=reverse)
                    except (ValueError, KeyError, AssertionError) as exc:
                        raise RingUnsupported(f"relabel:{exc}") from exc
                solver_spec = replace(spec, atoms=tuple(int(a) for a in solver_atoms))
                # Rigid units regenerated in solver order from the same
                # original working geometry (locks' p/n consistent).
                try:
                    rigid = analyze_rigid_units(working, elements, graph, list(solver_atoms))
                except ValueError as exc:
                    raise RingNumericalFailure(f"rigid_units:{exc}") from exc
                # Coordination overlap stays unsupported (lane ownership).
                if frozenset(int(a) for a in spec.atoms) & coord_atoms:
                    raise RingUnsupported("coordination_overlap")
                pre = working.copy()
                try:
                    full_new, _rst, audit = realize_cp_target(
                        working,
                        elements,
                        graph,
                        solver_spec,
                        solver_form,
                        rigid_units=rigid,
                        tolerances=self._r3_tolerances(context),
                        additional_starts=(),
                    )
                except (RingGeometryFailure, RingNumericalFailure, RingUnsupported) as exc:
                    # Attach audit when available; engine maps to statuses.
                    raise exc
                full_new = np.asarray(full_new, dtype=float)
                # Cross-system protection: no other ring atom may have moved.
                moved = [
                    int(a) for a in others if float(np.linalg.norm(full_new[a] - working[a])) > 1e-9
                ]
                if moved:
                    raise RingGeometryFailure(f"cross_system_damage:{moved[0]}")
                # Linking-bond audit (acyclic links to other ring systems).
                worst_link = 0.0
                worst_pair: tuple[int, int] | None = None
                for atom in spec.atoms:
                    for nb in graph[int(atom)]:
                        if nb not in others:
                            continue
                        old_len = float(np.linalg.norm(pre[int(atom)] - pre[int(nb)]))
                        new_len = float(np.linalg.norm(full_new[int(atom)] - full_new[int(nb)]))
                        drift = abs(new_len - old_len)
                        if drift > worst_link:
                            worst_link = drift
                            worst_pair = (int(atom), int(nb))
                audit = dict(audit)
                audit["treatment"] = spec.treatment
                # Canonical-solver provenance (do not misclaim original target):
                # R3 solved solver_form in solver_atoms order; the declared
                # StateKey below stays in the original traversal.
                audit["declared_atoms"] = [int(a) for a in spec.atoms]
                audit["solver_atoms"] = [int(a) for a in solver_atoms]
                audit["traversal_shift"] = int(shift)
                audit["traversal_reverse"] = bool(reverse)
                audit["declared_form"] = f"{form.family}:{form.index}"
                audit["solver_form"] = f"{solver_form.family}:{solver_form.index}"
                audit["link_bond_drift"] = round(worst_link, 6)
                if worst_pair is not None:
                    audit["link_bond_pair"] = list(worst_pair)
                if worst_link > float(self._tolerances.link_bond_atol):
                    raise RingGeometryFailure(f"link_bond:{worst_link:.4f}")
                working = full_new
                states.append(
                    {
                        "form": form.family,
                        "index": form.index,
                        "anchor": spec.atoms[0],
                        "direction": "as_given",
                    }
                )
                audits.append(audit)
                preserved = False
            output_coords = tuple(tuple(float(v) for v in row) for row in working)
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
            audit_payload: dict[str, object] = {}
            try:
                audit_payload = dict(getattr(exc, "audit", {}))
            except Exception:
                audit_payload = {}
            evidence = (audit_payload,) if audit_payload else ()
            return RealizationResult(
                structure=None,
                status="geometry_failure",
                reason=str(exc) or "geometry_failure",
                backend=BACKEND_NAME,
                evidence=evidence,
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
                coordinates=output_coords,
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
            reason="realized" if not preserved else "preserved_input",
            backend=BACKEND_NAME,
            evidence=tuple(dict(audit) for audit in audits),
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
            # Explicit-P special: flat n=5/6 with P declared reports P_0.
            if perception.best_form is None and self._explicit_p(spec):
                best_key[spec.id] = {
                    "form": "P",
                    "index": 0,
                    "anchor": spec.atoms[0],
                    "direction": "as_given",
                }
            else:
                best_key[spec.id] = ring_state_dict(perception, anchor=spec.atoms[0])
                if perception.confidence == "ambiguous":
                    overall = "ambiguous"
            diagnostics = ring_diagnostics(perception)
            margins[spec.id] = diagnostics["margin_deg"]
            raw_alternatives = cast("list[dict[str, Any]]", diagnostics["alternatives"])
            for entry in raw_alternatives:
                alternatives.append({"ring_id": spec.id, **entry})
            for flag in perception.boundary_flags:
                # Explicit-P flat is not a boundary for its own spec.
                if flag == "flat" and self._explicit_p(spec):
                    continue
                flags.append(f"{spec.id}:{flag}")
            # Flat without explicit P forces overall ambiguous.
            if perception.best_form is None and not self._explicit_p(spec):
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
        """Return the R4 CP audit tolerance (degrees, n=5/6)."""
        return float(_CP_MATCH_DEG)

    def _observed_measured(self, spec: RingSpec, coords: np.ndarray) -> tuple[Any, Any]:
        """Perceive one system; return (perception, form-state observed)."""
        perception = perceive_ring(
            np.asarray(coords)[list(spec.atoms)],
            match_deg=self._match_deg,
            ambiguity_margin_deg=self._margin_deg,
            reject_deg=self._reject_deg,
        )
        observed = ring_state_dict(perception, anchor=spec.atoms[0])
        return perception, observed

    def audit_target(
        self,
        structure: StructureRecord,
        target: GenerationTarget,
        parent: StageParentProtocol,
        context: MolecularContext,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Audit a fresh realization against its commanded target (R4 CP).

        Returns ``(ok, canonical_measured_map, evidence)``. Every enumerated
        system is re-perceived and its CP form is checked against the
        commanded form (exact family/index/anchor/direction); evidence
        carries measured CP and distances. ``ok`` is False on any mismatch
        or ambiguous measurement, and the engine routes that to FAILED_DRIFT.
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
                ring_states_match(commanded, observed)
                if isinstance(commanded, Mapping)
                else (False, {"reason": "missing_commanded_state"})
            )
            diagnostics = ring_diagnostics(perception)
            if perception.confidence == "ambiguous" or not match:
                ok = False
                payload = drift_event(
                    axis=f"rings.{spec.id}",
                    detail="realized ring state outside CP audit tolerance",
                    expected=(
                        f"{(commanded or {}).get('form')}:{(commanded or {}).get('index')}"
                        if isinstance(commanded, Mapping)
                        else None
                    ),
                    measured=diagnostics.get("distance_deg"),
                    tolerance=tolerance,
                )
                payload["observed"] = measured_map[spec.id]
                payload["measured_cp"] = diagnostics["measured_cp"]
                payload["measured_torsions"] = diagnostics["measured_torsions"]
                payload["match_evidence"] = dict(match_evidence)
                payload["confidence"] = perception.confidence
                payload["out_of_scope"] = perception.best_template != (
                    f"{commanded.get('form')}_{commanded.get('index')}"
                    if isinstance(commanded, Mapping) and "form" in commanded
                    else (commanded.get("template") if isinstance(commanded, Mapping) else None)
                )
                evidence.append(payload)
        return ok, measured_map, evidence

    def verify_locked(
        self,
        structure: StructureRecord,
        locked_state: Mapping[str, Any],
        context: MolecularContext,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Verify ancestor ring locks via CP distance (R4).

        Re-perceives every locked ring system and checks the measured CP
        form against the locked form (exact family/index/anchor/direction;
        perception already gates CP distance <=15 deg for reported states).
        Returns ``(ok, snapped_map, evidence)``: ambiguous measurement
        returns anomaly evidence; unknown ring ids return lock-error.
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
                            "measured_cp": ring_diagnostics(perception)["measured_cp"],
                            "measured_torsions": ring_diagnostics(perception)["measured_torsions"],
                        }
                    ],
                )
            match, match_evidence = ring_states_match(commanded, observed)
            if not match:
                payload = drift_event(
                    axis=f"rings.{spec.id}",
                    detail="ancestor ring lock drifted",
                    expected=(
                        f"{commanded.get('form')}:{commanded.get('index')}"
                        if isinstance(commanded, Mapping) and "form" in commanded
                        else (commanded.get("template") if isinstance(commanded, Mapping) else None)
                    ),
                    measured=ring_diagnostics(perception).get("distance_deg"),
                    tolerance=tolerance,
                )
                payload["observed"] = snapped[spec.id]
                payload["measured_cp"] = ring_diagnostics(perception)["measured_cp"]
                payload["match_evidence"] = dict(match_evidence)
                payload["out_of_scope"] = perception.best_template != (
                    f"{commanded.get('form')}_{commanded.get('index')}"
                    if isinstance(commanded, Mapping) and "form" in commanded
                    else (commanded.get("template") if isinstance(commanded, Mapping) else None)
                )
                evidence.append(payload)
        if evidence:
            return False, snapped, evidence
        return True, snapped, []
