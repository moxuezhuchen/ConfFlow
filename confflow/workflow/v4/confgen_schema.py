"""Typed declarations for the ConfGen v4 scientific scope.

The confgen step has exactly one generation engine: the DG search
(``dg_seed.py`` -> ``search.py`` -> ``confgen_search_worker.py`` /
``confgen_search_run.py``). The older ring, torsion, path and
coordination-realization engines were removed; a section-less v3 document
used to mean "preserve input" and now a ``schema_version: 3`` document is
rejected outright (see :data:`REMOVED_ENGINES_MESSAGE`).

Only supported controls are exposed, with one explicit atom index
convention. The ``search`` section is optional; an absent section means all
defaults (budget 1500 relaxations, 6 kcal/mol window, input arrangement kept).
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

#: Pinned copies of ``confflow.science.confgen.graph.INPUT_SHAPE`` / ``INPUT_CN_MIN`` (pinned by
#: test): the workflow schema must not import the science package at module import time.
INPUT_SHAPE = "input"
INPUT_CN_MIN = 2

Index = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[float, Field(gt=0, strict=True)]

#: Rejection message for ``schema_version: 3`` documents (canonical text;
#: the executor and the science-level normalizer carry pinned identical
#: copies because they must not import the workflow package, and the
#: producer intent wire reuses this object by import; a test pins all
#: spellings equal).
REMOVED_ENGINES_MESSAGE = (
    "confgen schema_version 3 is no longer supported: the ring, torsion, path "
    "and coordination-realization engines were removed; the confgen step now "
    "runs the DG search engine only, declared with schema_version: 4"
)


def _supported_shapes() -> tuple[str, ...]:
    """Return the registered coordination shape names."""
    from confflow.science.confgen.graph import SUPPORTED_SHAPES

    return tuple(SUPPORTED_SHAPES)


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


class CoordinationGenerationSpec(ConfgenSpecModel):
    """Declared coordination scope for the DG search (exactly one shape).

    ``shapes: ["input"]`` takes the input structure's own metal-donor core as the
    template (arrangement only); it needs at least ``INPUT_CN_MIN`` sites and no
    upper bound. Registered ideal shapes keep 4 to 6 sites.
    """

    metal_center: Index
    binding_sites: list[BindingSiteModel] = Field(min_length=INPUT_CN_MIN)
    shapes: list[StrictStr] = Field(min_length=1, max_length=1)
    constraints: list[CoordinationConstraintModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_shapes_and_sites(self) -> CoordinationGenerationSpec:
        if len(set(self.shapes)) != len(self.shapes):
            raise ValueError(f"coordination shapes hold duplicates: {self.shapes!r}")
        if self.shapes == [INPUT_SHAPE]:
            if len(self.binding_sites) < INPUT_CN_MIN:
                raise ValueError(
                    f"shape {INPUT_SHAPE!r} needs at least {INPUT_CN_MIN} binding sites, "
                    f"got {len(self.binding_sites)}"
                )
        elif not 4 <= len(self.binding_sites) <= 6:
            raise ValueError(
                f"coordination binding_sites must hold 4 to 6 sites for ideal shapes, "
                f"got {len(self.binding_sites)}"
            )
        allowed = set(_supported_shapes()) | {INPUT_SHAPE}
        unknown = [name for name in self.shapes if name not in allowed]
        if unknown:
            raise ValueError(
                f"coordination shapes {unknown!r} are not registered; " f"allowed {sorted(allowed)}"
            )
        seen: set[str] = set()
        for site in self.binding_sites:
            if site.id in seen:
                raise ValueError(f"duplicate coordination binding site id {site.id!r}")
            seen.add(site.id)
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
    formal_charge: StrictInt | None = None


class ConfgenTopologyModel(ConfgenSpecModel):
    bonds: list[TypedEdgeModel | tuple[Index, Index]] | None = None
    reaction_pairs: list[tuple[Index, Index]] | None = None
    atoms: list[TopologyAtomModel] = Field(default_factory=list)
    add_bond: list[TypedEdgeModel | tuple[Index, Index]] | None = None
    del_bond: list[tuple[Index, Index]] | None = None


class FragmentChargeModel(ConfgenSpecModel):
    """One DG-fragment charge override; ``atom`` uses the model's index_base."""

    atom: StrictInt
    charge: StrictInt


class ConfgenSearchModel(ConfgenSpecModel):
    """Iterative-search settings: DG starts, feedback moves, restrained relaxation, audit.

    ``budget`` caps the relaxations of the search phase (the release step adds one per minimum
    near the lowest); ``window_kcal`` is the energy window for parents and for
    the published conformers.  ``arrangement: keep`` searches only the input structure's
    donor arrangement (every class when the input fits none), ``enumerate`` all classes.
    ``release`` re-relaxes the final minima with only reaction-pair restraints.  ``starts``
    counts first-round DG starts per coordination class (in total without a metal); ``None``
    spreads 60 over the classes in scope (at least 2 each), or 60 without a metal.
    ``fragment_charges`` and ``freeze_core`` atoms use the model's ``index_base``.
    ``fixed_states`` lists torsions (four distinct atoms each) whose state is kept; the search
    still explores the other states of undeclared torsions.
    """

    budget: StrictInt | None = Field(default=None, ge=1)
    window_kcal: StrictFloat = Field(default=6.0, gt=0)
    arrangement: Literal["keep", "enumerate"] = "keep"
    release: StrictBool = True
    starts: StrictInt | None = Field(default=None, ge=1)
    small_ring_torsions: Literal["both", "on", "off"] = "both"
    embed_timeout_seconds: StrictInt = Field(default=120, ge=0)
    max_cycles: StrictInt = Field(default=1000, ge=1)
    bond_scale: StrictFloat = Field(default=1.25, gt=0)
    fragment_charges: list[FragmentChargeModel] = Field(default_factory=list)
    freeze_core: list[Index] = Field(default_factory=list)
    fixed_states: list[list[Index]] = Field(default_factory=list)
    ez_policy: Literal["preserve"] = "preserve"

    @model_validator(mode="after")
    def check_freeze_core(self) -> ConfgenSearchModel:
        if len(set(self.freeze_core)) != len(self.freeze_core):
            raise ValueError("search.freeze_core holds duplicate atom indices")
        return self

    @model_validator(mode="after")
    def check_fixed_states(self) -> ConfgenSearchModel:
        seen: set[tuple[int, ...]] = set()
        for quad in self.fixed_states:
            if len(quad) != 4:
                raise ValueError("search.fixed_states entries must hold exactly 4 atom indices")
            if len(set(quad)) != 4:
                raise ValueError(f"search.fixed_states entry {quad} repeats an atom")
            key = tuple(quad)
            if key in seen or key[::-1] in seen:
                raise ValueError(f"search.fixed_states entry {quad} is duplicated")
            seen.add(key)
        return self


class ConfgenToleranceModel(ConfgenSpecModel):
    """Global tolerances; only ``bond_scale`` is read by surviving code."""

    bond_scale: Positive = 1.15


class ConfgenModelV3(ConfgenSpecModel):
    """One typed v4 declaration for the DG-search confgen step.

    The class name is kept (cheap-rename rule): it now declares
    ``schema_version: 4``.
    """

    schema_version: Literal[4] = 4
    index_base: Literal[0, 1] = 1
    seed: StrictInt
    coordination: CoordinationGenerationSpec | None = None
    topology: ConfgenTopologyModel = Field(default_factory=ConfgenTopologyModel)
    tolerances: ConfgenToleranceModel = Field(default_factory=ConfgenToleranceModel)
    search: ConfgenSearchModel | None = None

    @model_validator(mode="after")
    def check_input_arrangement(self) -> ConfgenModelV3:
        """Shape ``input`` keeps the input arrangement; ``enumerate`` is not defined for it."""
        coordination = self.coordination
        if (
            coordination is not None
            and coordination.shapes == [INPUT_SHAPE]
            and self.search is not None
            and self.search.arrangement == "enumerate"
        ):
            raise ValueError(f"shape {INPUT_SHAPE!r} supports arrangement 'keep' only")
        return self

    @model_validator(mode="before")
    @classmethod
    def require_integer_versions(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("schema_version") == 3:
                raise ValueError(REMOVED_ENGINES_MESSAGE)
            for name in ("schema_version", "index_base"):
                if name in value and type(value[name]) is not int:
                    raise ValueError(f"{name} must be an integer")
        return value

    def scientific_native(self) -> dict[str, Any]:
        """Return the typed scope wire."""
        return cast(
            dict[str, Any],
            self.model_dump(mode="json", exclude_none=True),
        )
