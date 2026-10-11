"""Typed declarations for the ConfGen v4 scientific scope.

The confgen step has exactly one generation engine: the DG search
(``dg_seed.py`` -> ``search.py`` -> ``confgen_search_worker.py`` /
``confgen_search_run.py``). The older ring, torsion, path and
coordination-realization engines were removed; a section-less v3 document
used to mean "preserve input" and now a ``schema_version: 3`` document is
rejected outright (see :data:`REMOVED_ENGINES_MESSAGE`).

Only supported controls are exposed, with one explicit atom index
convention. The ``search`` section is optional; an absent section means all
defaults (starts resolve at run time: 8 per coordination class when
``coordination`` is declared, else 400).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

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
    """Declared coordination scope for the DG search (exactly one shape)."""

    metal_center: Index
    binding_sites: list[BindingSiteModel] = Field(min_length=4, max_length=6)
    shapes: list[StrictStr] = Field(min_length=1, max_length=1)
    constraints: list[CoordinationConstraintModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_shapes_and_sites(self) -> CoordinationGenerationSpec:
        if len(set(self.shapes)) != len(self.shapes):
            raise ValueError(f"coordination shapes hold duplicates: {self.shapes!r}")
        allowed = set(_supported_shapes())
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


class ConfgenTopologyModel(ConfgenSpecModel):
    bonds: list[TypedEdgeModel | tuple[Index, Index]] | None = None
    atoms: list[TopologyAtomModel] = Field(default_factory=list)
    add_bond: list[TypedEdgeModel | tuple[Index, Index]] | None = None
    del_bond: list[tuple[Index, Index]] | None = None


class FragmentChargeModel(ConfgenSpecModel):
    """One DG-fragment charge override; ``atom`` uses the model's index_base."""

    atom: StrictInt
    charge: StrictInt


class ConfgenSearchModel(ConfgenSpecModel):
    """DG-search settings: DG starts, restrained xTB relaxation, and audit.

    ``starts`` counts DG starts per coordination class (or in total without
    a metal); ``None`` resolves at run time to 8 per coordination class
    when ``coordination`` is declared, else 400. ``fragment_charges`` atoms
    use the model's ``index_base``.
    """

    starts: StrictInt | None = Field(default=None, ge=1)
    small_ring_torsions: Literal["both", "on", "off"] = "both"
    embed_timeout_seconds: StrictInt = Field(default=120, ge=0)
    max_cycles: StrictInt = Field(default=1000, ge=1)
    bond_scale: StrictFloat = Field(default=1.25, gt=0)
    fragment_charges: list[FragmentChargeModel] = Field(default_factory=list)


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
