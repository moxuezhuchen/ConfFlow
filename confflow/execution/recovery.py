#!/usr/bin/env python3

"""V4 recovery-policy contracts.

Recovery is explicit: a step declares a recovery profile, the executor
evaluates it after check failures, and any recovery execution is recorded
with diagnostics and provenance.  The old ``if itask == 4: rescue`` trigger
does not exist; neither does rescue after an unconfirmed cancellation.

A recovery policy never launches processes itself.  It plans recovery work
through a :class:`RescueDriver` supplied by the executor, which keeps process
control, cancellation, and artifact staging in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain._immutable import FrozenDict
from ..domain.diagnostics import Diagnostic
from .native import (
    MaterializedNativeInput,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeResult,
    ResolvedCalculationInputs,
)

__all__ = [
    "RecoveryContext",
    "RecoveryDecision",
    "RecoveryExecution",
    "RecoveryPolicy",
    "RescueDriver",
]


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    """Everything a recovery policy may read to decide."""

    profile_name: str
    work_item_id: str
    step_id: str
    logical_key: str
    inputs: ResolvedCalculationInputs
    failed_native_result: NativeResult | None
    failure_diagnostics: tuple[Diagnostic, ...] = ()
    attempt: int = 0
    params: FrozenDict = field(default_factory=FrozenDict)
    cancellation_confirmed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.inputs, ResolvedCalculationInputs):
            raise TypeError("inputs must be ResolvedCalculationInputs")
        if self.failed_native_result is not None and not isinstance(
            self.failed_native_result, NativeResult
        ):
            raise TypeError("failed_native_result must be a NativeResult or None")
        object.__setattr__(self, "failure_diagnostics", tuple(self.failure_diagnostics))
        if not isinstance(self.params, FrozenDict):
            object.__setattr__(self, "params", FrozenDict(self.params))


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """Whether recovery should be attempted, and why."""

    attempt: bool
    reason: str
    details: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, bool):
            raise TypeError("attempt must be a boolean")
        if not self.reason or not isinstance(self.reason, str):
            raise ValueError("reason must be a non-empty string")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(self.details))


@dataclass(frozen=True, slots=True)
class RecoveryExecution:
    """The recorded outcome of one recovery execution."""

    native_result: NativeResult
    execution_result: NativeExecutionResult
    materialized: MaterializedNativeInput
    diagnostics: tuple[Diagnostic, ...] = ()
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.native_result, NativeResult):
            raise TypeError("native_result must be a NativeResult")
        if not isinstance(self.execution_result, NativeExecutionResult):
            raise TypeError("execution_result must be a NativeExecutionResult")
        if not isinstance(self.materialized, MaterializedNativeInput):
            raise TypeError("materialized must be a MaterializedNativeInput")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))


@runtime_checkable
class RescueDriver(Protocol):
    """Process-control hook through which recovery runs native work."""

    def run_native(
        self,
        materialized: MaterializedNativeInput,
        request: NativeExecutionRequest,
        *,
        stage: str,
    ) -> tuple[NativeExecutionResult, NativeResult | None]:
        """Execute one native calculation and parse its result.

        Returns the execution result plus the parsed native result, or
        ``None`` when parsing itself failed.  Cancellation and walltime
        handling match the primary execution path.
        """
        ...


@runtime_checkable
class RecoveryPolicy(Protocol):
    """One explicitly declared recovery profile."""

    @property
    def name(self) -> str:
        """Return the recovery profile name."""
        ...

    @property
    def contract_version(self) -> str:
        """Return the recovery contract version (folded into digests)."""
        ...

    def evaluate(self, context: RecoveryContext) -> RecoveryDecision:
        """Decide whether recovery should be attempted."""
        ...

    def execute(self, context: RecoveryContext, driver: RescueDriver) -> RecoveryExecution | None:
        """Perform recovery work through *driver*.

        Returns the recovery execution, or ``None`` when recovery produced
        nothing usable.  Returning ``None`` is itself a recorded outcome,
        never a silent skip.
        """
        ...
