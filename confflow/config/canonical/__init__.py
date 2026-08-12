"""Additive canonical configuration views for the compatible v2 line.

The existing classes in :mod:`confflow.config.models` remain the characterized
public v2 surface. These views provide the new engine-facing namespace while
delegating all parsing and coercion to that single implementation during the
compatibility period.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..models import CalcStepParams, GlobalOptions, StepConfig, WorkflowConfig


@dataclass(frozen=True)
class CanonicalGlobalOptions:
    """Normalized, environment-independent global workflow options."""

    value: GlobalOptions

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> CanonicalGlobalOptions:
        return cls(GlobalOptions.from_mapping(dict(raw or {})))

    def as_mapping(self) -> dict[str, Any]:
        return dict(self.value.__dict__)


@dataclass(frozen=True)
class CanonicalCalcStepParams:
    """Canonical calculation parameters backed by the v2 parser."""

    value: CalcStepParams

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        global_options: CanonicalGlobalOptions,
    ) -> CanonicalCalcStepParams:
        return cls(CalcStepParams.from_params(dict(raw), global_options.value))

    def as_mapping(self) -> dict[str, Any]:
        return self.value.canonical_dict()


@dataclass(frozen=True)
class CanonicalConfgenStepParams:
    """Normalized confgen parameters with explicit extension preservation."""

    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", MappingProxyType(dict(self.value)))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> CanonicalConfgenStepParams:
        return cls(dict(raw or {}))

    def as_mapping(self) -> dict[str, Any]:
        return dict(self.value)


@dataclass(frozen=True)
class CanonicalStepConfig:
    """Typed step view preserving the public v2 step aliases."""

    value: StepConfig

    @classmethod
    def from_legacy(cls, value: StepConfig) -> CanonicalStepConfig:
        return cls(value)

    @property
    def name(self) -> str:
        return self.value.name

    @property
    def type(self) -> str:
        return self.value.type

    @property
    def enabled(self) -> bool:
        return self.value.enabled

    @property
    def params(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self.value.params))


@dataclass(frozen=True)
class CanonicalWorkflowConfig:
    """Parsed workflow configuration consumed by candidate engine code."""

    global_options: CanonicalGlobalOptions
    steps: tuple[CanonicalStepConfig, ...]
    extensions: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> CanonicalWorkflowConfig:
        parsed = WorkflowConfig.from_mapping(dict(raw))
        return cls(
            global_options=CanonicalGlobalOptions(parsed.global_options),
            steps=tuple(CanonicalStepConfig.from_legacy(step) for step in parsed.steps),
            extensions=MappingProxyType(dict(parsed.raw)),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "global": self.global_options.as_mapping(),
            "steps": [
                {
                    "name": step.name,
                    "type": step.type,
                    "enabled": step.enabled,
                    "params": dict(step.params),
                }
                for step in self.steps
            ],
        }


__all__ = [
    "CanonicalCalcStepParams",
    "CanonicalConfgenStepParams",
    "CanonicalGlobalOptions",
    "CanonicalStepConfig",
    "CanonicalWorkflowConfig",
]
