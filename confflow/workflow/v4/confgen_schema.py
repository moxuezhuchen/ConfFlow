"""Typed declarations for the ConfGen v3 scientific scope.

Legacy native block remains a separate schema; declarations expose
only supported controls with one explicit atom index convention.
Vocabularies/defaults come from component ``schema_constants()``
(never solver imports): coordination shapes, ring templates, torsion
wrapping, global tolerances (core lane), coordination section tolerances.
Deeper geometric validation stays lane-owned (normalization and stage
construction); the typed boundary fails closed early on ambiguous
declarations.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

Index = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[float, Field(gt=0, strict=True)]
Treatment = Literal["enumerate", "preserve_input"]


def _coordination_section_defaults() -> dict[str, float]:
    """Return lane-owned coordination section tolerance defaults."""
    from confflow.science.confgen.registry import build_default_registry

    for _d in build_default_registry().descriptors:
        if _d.id == "coordination" and _d.schema_constants is not None:
            return dict(dict(_d.schema_constants()).get("section_tolerances", {}))
    # Fallback (same authority, direct constants path; never stage).
    from confflow.science.confgen.coordination.constants import DEFAULT_SECTION_TOLERANCES

    return dict(DEFAULT_SECTION_TOLERANCES)


def _global_tolerance_defaults() -> dict[str, float]:
    """Return core-owned global tolerance defaults."""
    from confflow.science.confgen.tolerances import ConfgenTolerances

    defaults = ConfgenTolerances()
    return {
        "bond_length_atol": defaults.bond_length_atol,
        "dihedral_atol_deg": defaults.dihedral_atol_deg,
        "clash_threshold": defaults.clash_threshold,
        "bond_scale": defaults.bond_scale,
        "parent_lock_atol_deg": defaults.parent_lock_atol_deg,
        "ring_bond_atol": defaults.ring_bond_atol,
        "substituent_bond_atol": defaults.substituent_bond_atol,
        "frame_det_min": defaults.frame_det_min,
        "ring_angle_atol_deg": defaults.ring_angle_atol_deg,
        "ring_torsion_atol_deg": defaults.ring_torsion_atol_deg,
        "coordination_bond_atol": defaults.coordination_bond_atol,
        "coordination_angle_atol_deg": defaults.coordination_angle_atol_deg,
    }


def _supported_shapes() -> tuple[str, ...]:
    """Return registered coordination shape names (via descriptor)."""
    from confflow.science.confgen.registry import build_default_registry

    for _d in build_default_registry().descriptors:
        if _d.id == "coordination" and _d.schema_constants is not None:
            return tuple(dict(_d.schema_constants()).get("shapes", ()))
    from confflow.science.confgen.graph import SUPPORTED_SHAPES

    return tuple(SUPPORTED_SHAPES)


def _template_sizes() -> dict[str, int]:
    """Return ring template name -> ring size (via descriptor)."""
    from confflow.science.confgen.registry import build_default_registry

    for _d in build_default_registry().descriptors:
        if _d.id == "rings" and _d.schema_constants is not None:
            return dict(dict(_d.schema_constants()).get("template_sizes", {}))
    from confflow.science.confgen.ring.constants import TEMPLATES_BY_SIZE

    return {name: size for size, names in TEMPLATES_BY_SIZE.items() for name in names}


def _form_sizes() -> dict[str, list[int]]:
    """Return ring precise-form name -> supporting sizes (via descriptor)."""
    from confflow.science.confgen.registry import build_default_registry

    for _d in build_default_registry().descriptors:
        if _d.id == "rings" and _d.schema_constants is not None:
            raw = dict(dict(_d.schema_constants()).get("form_sizes", {}))
            out: dict[str, list[int]] = {}
            for _k, _v in raw.items():
                if isinstance(_v, (list, tuple, set)):
                    out[str(_k)] = sorted(int(_x) for _x in _v)
                else:
                    out[str(_k)] = [int(_v)]
            return out
    from confflow.science.confgen.ring.forms import FORM_NAMES_BY_SIZE

    _acc: dict[str, set[int]] = {}
    for _size, _names in FORM_NAMES_BY_SIZE.items():
        for _name in _names:
            _acc.setdefault(str(_name), set()).add(int(_size))
    for _p in ("P", "P_0"):
        _acc.setdefault(_p, set()).update([4, 5, 6])
    return {name: sorted(sizes) for name, sizes in sorted(_acc.items())}


def _forms_for_size(n: int) -> tuple[str, ...]:
    """Return precise-form names valid for ring size n (component authority)."""
    from confflow.science.confgen.registry import build_default_registry

    for _d in build_default_registry().descriptors:
        if _d.id == "rings" and _d.schema_constants is not None:
            by_size = dict(dict(_d.schema_constants()).get("forms_by_size", {}))
            names = tuple(by_size.get(str(int(n)), ()))
            if int(n) in (5, 6) and "P_0" not in names:
                names = tuple(list(names) + ["P_0"])
            return names
    from confflow.science.confgen.ring.forms import FORM_NAMES_BY_SIZE

    names = tuple(FORM_NAMES_BY_SIZE.get(int(n), ()))
    if int(n) in (5, 6) and "P_0" not in names:
        names = tuple(list(names) + ["P_0"])
    return names


def _form_families(n: int) -> set[str]:
    """Return family selectors valid for ring size n (component authority)."""
    fams: set[str] = set()
    for _name in _forms_for_size(int(n)):
        if "_" in _name:
            fams.add(_name.rsplit("_", 1)[0])
        else:
            fams.add(_name)
    if int(n) in (5, 6):
        fams.add("P")
    if int(n) == 4:
        fams.add("B")
    return fams


def _wrap_degrees(angle: float) -> float:
    """Wrap an angle to (-180, 180] (via torsion descriptor, stdlib only)."""
    from confflow.science.confgen.registry import build_default_registry

    for _d in build_default_registry().descriptors:
        if _d.id == "torsions" and _d.schema_constants is not None:
            _fn = dict(_d.schema_constants()).get("wrap_degrees")
            if callable(_fn):
                return cast(float, _fn(angle))
    from confflow.science.confgen.torsion.constants import wrap_degrees as _local

    return _local(angle)


class ConfgenSpecModel(BaseModel):
    """Fail closed on unknown fields and nonfinite numeric declarations."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BindingSiteModel(ConfgenSpecModel):
    id: StrictStr = Field(min_length=1)
    kind: Literal["atom"] = "atom"
    atoms: list[Index] = Field(min_length=1, max_length=1)
    hapticity: StrictInt = Field(default=1, ge=1, le=1)


class CoordinationConstraintModel(ConfgenSpecModel):
    """Declared trans exclusions are policies; no unverified proof input."""

    id: StrictStr = Field(min_length=1)
    kind: Literal["FORBIDDEN_TRANS"] = "FORBIDDEN_TRANS"
    sites: tuple[StrictStr, StrictStr]
    classification: Literal["REJECTED_BY_POLICY"] = "REJECTED_BY_POLICY"
    provenance: StrictStr = Field(min_length=1)


class DonorConfigurationModel(ConfgenSpecModel):
    """Declared donor configuration: preserved input, never enumerated.

    The list form names the declared donor site ids (lane serialization);
    both forms declare — never enumerate — the donor set. Geometry audit
    rejects silent donor flips at the stage; the executor re-checks donor
    identity on chained runs.
    """

    treatment: Literal["preserve_input"] = "preserve_input"


class SiteGroupModel(ConfgenSpecModel):
    """Declared topological subgroup for molecular-orbit accounting.

    Generators are SITE POSITION permutations (positions into
    ``binding_sites``, convention-free — not atom indices). This certifies
    exactly the declared subgroup scope and nothing more: never full
    molecular symmetry, never stereo/improper action, never suppression
    authority (H_geom suppression stays a separate, stricter proof gate).
    ``provenance`` names the witness source (e.g. a fixture sigma audit)
    so the claim stays auditable.
    """

    generators: list[list[StrictInt]] = Field(min_length=1)
    provenance: StrictStr | None = None
    scope: Literal["declared_topological_subgroup"] = "declared_topological_subgroup"


class CoordinationTolerancesModel(ConfgenSpecModel):
    """Lane-owned section tolerances; defaults track the stage authority."""

    realize_tol: Positive = 0.45
    reaction_tol: Positive = 0.25
    clash_scale: Positive = 0.70
    rmsd_tolerance: Positive = 0.35
    margin_tolerance: Positive = 0.05
    shape_margin_tolerance: Positive = 0.15


class CoordinationBudgetModel(ConfgenSpecModel):
    """Lane-owned realization budgets; defaults track the stage authority."""

    max_nfev: StrictInt = Field(default=120, ge=1)
    maxiter: StrictInt = Field(default=400, ge=1)


class CoordinationGenerationSpec(ConfgenSpecModel):
    metal_center: Index
    binding_sites: list[BindingSiteModel] = Field(min_length=4, max_length=6)
    shapes: Literal["auto"] | list[StrictStr] = "auto"
    treatment: Treatment = "enumerate"
    constraints: list[CoordinationConstraintModel] = Field(default_factory=list)
    donor_configuration: DonorConfigurationModel | list[StrictStr] = Field(
        default_factory=DonorConfigurationModel
    )
    backend: Literal["rigid", "flexible", "rigid_then_flexible"] = "rigid_then_flexible"
    site_group: SiteGroupModel | None = None
    tolerances: CoordinationTolerancesModel = Field(default_factory=CoordinationTolerancesModel)
    budgets: CoordinationBudgetModel = Field(default_factory=CoordinationBudgetModel)

    @model_validator(mode="after")
    def check_shapes_and_sites(self) -> CoordinationGenerationSpec:
        shapes = self.shapes
        if isinstance(shapes, list):
            if not shapes:
                raise ValueError("coordination shapes must name at least one shape")
            if len(set(shapes)) != len(shapes):
                raise ValueError(f"coordination shapes hold duplicates: {shapes!r}")
            allowed = set(_supported_shapes())
            unknown = [name for name in shapes if name not in allowed]
            if unknown:
                raise ValueError(
                    f"coordination shapes {unknown!r} are not registered; "
                    f"allowed {sorted(allowed)} or 'auto'"
                )
        seen: set[str] = set()
        for site in self.binding_sites:
            if site.id in seen:
                raise ValueError(f"duplicate coordination binding site id {site.id!r}")
            seen.add(site.id)
        if self.site_group is not None:
            positions = list(range(len(self.binding_sites)))
            for index, generator in enumerate(self.site_group.generators):
                if any(position < 0 for position in generator):
                    raise ValueError(
                        f"coordination site_group generators[{index}] holds negative positions"
                    )
                if sorted(generator) != positions:
                    raise ValueError(
                        f"coordination site_group generators[{index}] must permute "
                        f"all site positions {positions}"
                    )
        if isinstance(self.donor_configuration, list) and len(set(self.donor_configuration)) != len(
            self.donor_configuration
        ):
            raise ValueError("coordination donor_configuration holds duplicate site ids")
        return self


class RingGenerationSpec(ConfgenSpecModel):
    id: StrictStr = Field(min_length=1)
    atoms: list[Index] = Field(min_length=4, max_length=6)
    templates: list[StrictStr] = Field(default_factory=list)
    forms: list[StrictStr] = Field(default_factory=list)
    treatment: Treatment = "enumerate"

    @model_validator(mode="after")
    def check_templates(self) -> RingGenerationSpec:
        if self.templates and self.forms:
            raise ValueError(
                f"ring {self.id!r} declares both 'templates' and 'forms'; declare exactly one"
            )
        if self.templates:
            if len(set(self.templates)) != len(self.templates):
                raise ValueError(f"ring {self.id!r} templates hold duplicates")
            registry = _template_sizes()
            for name in self.templates:
                size = registry.get(name)
                if size is None:
                    raise ValueError(
                        f"ring {self.id!r} template {name!r} is not registered; "
                        f"allowed {sorted(registry)}"
                    )
                if size != len(self.atoms):
                    raise ValueError(
                        f"ring {self.id!r} template {name!r} fits size {size}, "
                        f"not ring size {len(self.atoms)}"
                    )
        if self.forms:
            if len(set(self.forms)) != len(self.forms):
                raise ValueError(f"ring {self.id!r} forms hold duplicates")
            n = len(self.atoms)
            precise = set(_forms_for_size(n))
            families = _form_families(n)
            for name in self.forms:
                # Explicit-P specials (5/6 planar, outside 20/38) stay
                # authorized; run side (forms.py) resolves them identically.
                if name in ("P", "P_0") and n in (5, 6):
                    continue
                if name in precise:
                    continue
                if name in families:
                    continue
                raise ValueError(
                    f"ring {self.id!r} form {name!r} is not registered for size {n}; "
                    f"allowed precise {sorted(precise)} or family {sorted(families)}"
                )
        return self


class TorsionGenerationSpec(ConfgenSpecModel):
    id: StrictStr = Field(min_length=1)
    bond: tuple[Index, Index] | None = None
    atoms: tuple[Index, Index, Index, Index] | None = None
    model: Literal["relative_rotation_grid", "absolute_dihedral_grid", "chemical"]
    angles: list[Annotated[float, Field(strict=True)]] | None = None
    states: dict[StrictStr, Annotated[float, Field(strict=True)]] | None = None
    treatment: Treatment = "enumerate"
    rotate_side: Literal["left", "right"] = "left"

    @model_validator(mode="after")
    def check_model_shape(self) -> TorsionGenerationSpec:
        if self.model == "relative_rotation_grid":
            if self.bond is None:
                raise ValueError("relative_rotation_grid requires 'bond'")
            if self.atoms is not None:
                raise ValueError("relative_rotation_grid takes 'bond', not 'atoms'")
            if self.states is not None:
                raise ValueError("relative_rotation_grid takes 'angles', not 'states'")
            if self.treatment == "enumerate" and not self.angles:
                raise ValueError(
                    "relative_rotation_grid with treatment 'enumerate' requires "
                    "a non-empty 'angles' grid"
                )
        elif self.model == "absolute_dihedral_grid":
            if self.atoms is None:
                raise ValueError("absolute_dihedral_grid requires 'atoms' (four indices)")
            if self.bond is not None:
                raise ValueError("absolute_dihedral_grid takes 'atoms', not 'bond'")
            if self.states is not None:
                raise ValueError("absolute_dihedral_grid takes 'angles', not 'states'")
            if self.treatment == "enumerate" and not self.angles:
                raise ValueError(
                    "absolute_dihedral_grid with treatment 'enumerate' requires "
                    "a non-empty 'angles' grid"
                )
        else:  # chemical: opt-in named states on a four-atom absolute frame
            if self.atoms is None:
                raise ValueError(
                    "chemical torsion requires a four-atom absolute 'atoms' frame "
                    "so named states carry real gauche/anti meaning; "
                    "'bond'-only chemical axes are rejected"
                )
            if self.bond is not None:
                raise ValueError("chemical torsion takes 'atoms', not 'bond'")
            if not self.states:
                raise ValueError(
                    "chemical torsion is opt-in and requires an explicit "
                    "non-empty 'states' map of name -> angle"
                )
            if self.angles is not None:
                raise ValueError("chemical torsion takes 'states', not 'angles'")
        if self.bond is not None and self.bond[0] == self.bond[1]:
            raise ValueError("torsion 'bond' must name two distinct atoms")
        if self.atoms is not None and len(set(self.atoms)) != 4:
            raise ValueError("torsion 'atoms' must hold four distinct atoms")
        if self.angles is not None:
            for left in range(len(self.angles)):
                for right in range(left + 1, len(self.angles)):
                    if _wrap_degrees(self.angles[left] - self.angles[right]) == 0.0:
                        raise ValueError(
                            f"torsion {self.id!r} angles hold a periodic duplicate: "
                            f"{self.angles[left]!r} and {self.angles[right]!r} name "
                            "the identical physical state (one state, one key)"
                        )
        return self


class PathDeclarationModel(ConfgenSpecModel):
    """Phase 0 input simplification: one endpoint-pair rotor declaration.

    Endpoints are ALWAYS user-facing 1-based atom numbers (independent of
    the step ``index_base``; the planner converts them explicitly with
    base 1, never guessed). ``move`` is REQUIRED and exactly
    ``start``/``end`` (intent is never inferred). Every consecutive bond of the resolved bridge-only
    path becomes a relative-rotation rotor. Sampling is explicit: exactly
    one of ``angles``/``step`` may be given; a bare declaration only
    defaults in the legacy native mode (typed scopes fail closed without
    explicit sampling so the scientific grid stays declared).
    """

    start: StrictInt = Field(ge=1)
    end: StrictInt = Field(ge=1)
    move: Literal["start", "end"]
    angles: list[Annotated[float, Field(strict=True)]] | None = None
    step: StrictInt | None = Field(default=None, ge=1, le=360)
    id: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def check_path_shape(self) -> PathDeclarationModel:
        if self.start == self.end:
            raise ValueError("path 'start' and 'end' must name two distinct atoms")
        if self.angles is not None and self.step is not None:
            raise ValueError("path declares both 'angles' and 'step'; declare exactly one")
        if self.angles is not None:
            if not self.angles:
                raise ValueError("path 'angles' must hold at least one angle")
            for left in range(len(self.angles)):
                for right in range(left + 1, len(self.angles)):
                    if _wrap_degrees(self.angles[left] - self.angles[right]) == 0.0:
                        raise ValueError(
                            f"path angles hold a periodic duplicate: "
                            f"{self.angles[left]!r} and {self.angles[right]!r} name "
                            "the identical physical state (one state, one key)"
                        )
        return self


class TypedEdgeModel(ConfgenSpecModel):
    atoms: tuple[Index, Index]
    kind: Literal["COVALENT", "COORDINATION", "FORMING", "BREAKING"] = "COVALENT"
    bond_order: Annotated[float, Field(gt=0, le=4, strict=True)] | None = None
    provenance: StrictStr = "explicit"


class TopologyAtomModel(ConfgenSpecModel):
    index: Index
    label: StrictStr | None = None
    role: StrictStr = ""
    stereo: StrictStr | None = None


class ConfgenTopologyModel(ConfgenSpecModel):
    bonds: list[TypedEdgeModel | tuple[Index, Index]] | None = None
    atoms: list[TopologyAtomModel] = Field(default_factory=list)
    add_bond: list[TypedEdgeModel | tuple[Index, Index]] | None = None
    del_bond: list[tuple[Index, Index]] | None = None


class ConfgenLimits(ConfgenSpecModel):
    max_declared_states: StrictInt = Field(default=10000, ge=1)
    max_output_structures: StrictInt = Field(default=10000, ge=1)


class SamplingSpec(ConfgenSpecModel):
    """Cap sampled targets before geometry; the step seed is sole authority."""

    cap: StrictInt = Field(ge=1)


class FragmentChargeModel(ConfgenSpecModel):
    """One DG-fragment charge override; ``atom`` uses the model's index_base."""

    atom: StrictInt
    charge: StrictInt


class ConfgenSearchModel(ConfgenSpecModel):
    """DG-search mode: DG starts, restrained xTB relaxation, and audit.

    ``starts`` counts DG starts per coordination class (or in total without
    a metal). ``fragment_charges`` atoms use the model's ``index_base``.
    """

    starts: StrictInt = Field(default=8, ge=1)
    small_ring_torsions: Literal["both", "on", "off"] = "both"
    embed_timeout_seconds: StrictInt = Field(default=120, ge=0)
    max_cycles: StrictInt = Field(default=1000, ge=1)
    bond_scale: StrictFloat = Field(default=1.25, gt=0)
    fragment_charges: list[FragmentChargeModel] = Field(default_factory=list)


class ConfgenToleranceModel(ConfgenSpecModel):
    """Global tolerances; defaults track the core science authority."""

    bond_length_atol: Positive = 1e-6
    dihedral_atol_deg: Positive = 1.0
    clash_threshold: Positive = 0.65
    bond_scale: Positive = 1.15
    parent_lock_atol_deg: Positive = 1.0
    ring_bond_atol: Positive = 0.08
    substituent_bond_atol: Positive = 1e-6
    frame_det_min: Positive = 1e-8
    ring_angle_atol_deg: Positive = 5.0
    ring_torsion_atol_deg: Positive = 10.0
    coordination_bond_atol: Positive = 0.05
    coordination_angle_atol_deg: Positive = 3.0


class ConfgenExclusionModel(ConfgenSpecModel):
    """Declared exclusions are recorded policy, never verified proof.

    An optional ``proof`` mapping is a policy annotation (proof_id required)
    until a sound backend proof enforcement exists; it never authorizes
    automatic infeasibility claims.
    """

    axis: Literal["coordination", "rings", "torsions"]
    match: dict[str, Any] = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)
    proof: dict[str, Any] | None = None

    @model_validator(mode="after")
    def check_proof(self) -> ConfgenExclusionModel:
        if self.proof is not None and not self.proof.get("proof_id"):
            raise ValueError("exclusion proof must carry a non-empty proof_id")
        return self


class ConfgenModelV3(ConfgenSpecModel):
    """One typed v3 declaration, independent of legacy native configuration."""

    schema_version: Literal[3] = 3
    index_base: Literal[0, 1] = 1
    coordination: CoordinationGenerationSpec | None = None
    rings: list[RingGenerationSpec] = Field(default_factory=list)
    torsions: list[TorsionGenerationSpec] = Field(default_factory=list)
    paths: list[PathDeclarationModel] = Field(default_factory=list)
    strict_path_bond_check: StrictBool = False
    topology: ConfgenTopologyModel = Field(default_factory=ConfgenTopologyModel)
    stereochemistry: dict[str, Any] = Field(default_factory=dict)
    exclusions: list[ConfgenExclusionModel] = Field(default_factory=list)
    tolerances: ConfgenToleranceModel = Field(default_factory=ConfgenToleranceModel)
    limits: ConfgenLimits = Field(default_factory=ConfgenLimits)
    sampling: SamplingSpec | None = None
    search: ConfgenSearchModel | None = None
    seed: StrictInt | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def require_integer_versions(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for name in ("schema_version", "index_base"):
                if name in value and type(value[name]) is not int:
                    raise ValueError(f"{name} must be an integer")
        return value

    @model_validator(mode="after")
    def check_scope(self) -> ConfgenModelV3:
        if self.sampling is not None and self.seed is None:
            raise ValueError(
                "sampling.cap requires an explicit top-level seed "
                "(the seed is the sole stochastic authority)"
            )
        if self.search is not None:
            combined = [
                name
                for name, occupied in (
                    ("rings", bool(self.rings)),
                    ("torsions", bool(self.torsions)),
                    ("paths", bool(self.paths)),
                    ("sampling", self.sampling is not None),
                )
                if occupied
            ]
            if combined:
                raise ValueError(
                    "search must not be combined with "
                    + ", ".join(combined)
                    + "; declare exactly one confgen generation mode"
                )
            if self.seed is None:
                raise ValueError(
                    "search requires an explicit top-level seed "
                    "(the seed is the sole stochastic authority)"
                )
        seen: set[str] = set()
        for entry in (*self.rings, *self.torsions):
            if entry.id in seen:
                raise ValueError(f"duplicate confgen axis id {entry.id!r}")
            seen.add(entry.id)
        return self

    def scientific_native(self) -> dict[str, Any]:
        """Return the typed scope wire; orchestration overrides stay separate."""
        return cast(
            dict[str, Any],
            self.model_dump(mode="json", exclude={"overrides"}, exclude_none=True),
        )
