#!/usr/bin/env python3

"""V4 result-profile contracts.

A result profile turns parser facts (:class:`NativeResult`) plus the original
work-item inputs into normalized domain collections.  Profiles understand
``standard`` / ``path_endpoints`` / ``ensemble`` / ``opaque`` semantics; they
never parse file formats (that is the program adapter's job) and never judge
scientific acceptance (that is the checks' job).

SP passthrough rule (frozen): when the native result carries no geometry and
the declared checks do not require one, the standard profile emits a new
semantic :class:`StructureRecord` whose geometry content is identical to the
input, with ``parent_ids`` set to the input structure id and the lineage root
preserved.  Content identity is unchanged, so reuse is unaffected, while every
structure in a step result carries explicit producer provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic
from ..domain.result import ResultSet
from ..domain.structure import StructureSet
from .native import NativeResult, ResolvedCalculationInputs

__all__ = [
    "GeometrySemantics",
    "ProfileContext",
    "ProfileOutput",
    "ResultProfile",
]


class GeometrySemantics(str, Enum):
    """How the output structures relate to the input structure."""

    PRODUCED = "produced"
    PASSTHROUGH = "passthrough"


@dataclass(frozen=True, slots=True)
class ProfileContext:
    """Everything a result profile may read."""

    work_item_id: str
    step_id: str
    logical_key: str
    profile_name: str
    profile_version: str
    native_result: NativeResult
    inputs: ResolvedCalculationInputs
    discovered_artifacts: ArtifactSet = field(default_factory=ArtifactSet)

    def __post_init__(self) -> None:
        if not isinstance(self.native_result, NativeResult):
            raise TypeError("native_result must be a NativeResult")
        if not isinstance(self.inputs, ResolvedCalculationInputs):
            raise TypeError("inputs must be ResolvedCalculationInputs")
        if not isinstance(self.discovered_artifacts, ArtifactSet):
            raise TypeError("discovered_artifacts must be an ArtifactSet")


@dataclass(frozen=True, slots=True)
class ProfileOutput:
    """Normalized domain collections produced by a result profile."""

    structures: StructureSet = field(default_factory=StructureSet)
    results: ResultSet = field(default_factory=ResultSet)
    artifacts: ArtifactSet = field(default_factory=ArtifactSet)
    geometry_semantics: GeometrySemantics = GeometrySemantics.PRODUCED
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.structures, StructureSet):
            raise TypeError("structures must be a StructureSet")
        if not isinstance(self.results, ResultSet):
            raise TypeError("results must be a ResultSet")
        if not isinstance(self.artifacts, ArtifactSet):
            raise TypeError("artifacts must be an ArtifactSet")
        if not isinstance(self.geometry_semantics, GeometrySemantics):
            raise TypeError("geometry_semantics must be a GeometrySemantics")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


def passthrough_structure_id(logical_key: str) -> str:
    """Return the deterministic output id for passthrough geometry."""
    return f"{logical_key}:structure:passthrough"


def produced_structure_id(logical_key: str, ordinal: int = 0) -> str:
    """Return the deterministic output id for produced geometry."""
    return f"{logical_key}:structure:{ordinal}"


@runtime_checkable
class ResultProfile(Protocol):
    """Semantic normalizer for one result-profile kind."""

    @property
    def name(self) -> str:
        """Return the profile name."""
        ...

    @property
    def contract_version(self) -> str:
        """Return the profile contract version (folded into digests)."""
        ...

    def apply(self, context: ProfileContext) -> ProfileOutput:
        """Normalize parser facts into domain collections."""
        ...
