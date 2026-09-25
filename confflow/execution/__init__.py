#!/usr/bin/env python3

"""V4 execution contracts and native execution interfaces.

This package owns the *capability vocabulary* of the V4 engine (executors,
adapters, profiles, checks, recovery) plus the native execution interfaces:
program adapters, result profiles, scientific checks, recovery policies, and
the process boundary.  It never parses YAML and never owns workflow topology.
"""

from __future__ import annotations

from .batch import (
    BatchStepExecutor,
    InMemoryReuseStore,
    InMemoryWorkItemRepository,
    ReuseStore,
    StepExecutionRequest,
    WorkItemRepository,
)
from .checks import CHECK_DEFAULTS, CheckContext, CheckOutcome, ScientificCheck
from .contracts import (
    CheckSpec,
    ExecutionAdapterSpec,
    ExecutionBinding,
    ExecutionEnvironment,
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
    RecoverySpec,
    ResultProfileSpec,
)
from .environment import EnvironmentMeasurer, ExecutableIdentity, measure_executable
from .native import (
    GeometryOutput,
    InputFile,
    MaterializedNativeInput,
    NativeError,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeHandle,
    NativeResult,
    NativeStatus,
    ParsedGeometry,
    ProcessSupervisor,
    ProducedFile,
    ProgramAdapter,
    ProgramName,
    ResolvedCalculationInputs,
    StagedArtifact,
)
from .profiles import (
    GeometrySemantics,
    ProfileContext,
    ProfileOutput,
    ResultProfile,
    passthrough_structure_id,
    produced_structure_id,
)
from .recovery import (
    RecoveryContext,
    RecoveryDecision,
    RecoveryExecution,
    RecoveryPolicy,
    RescueDriver,
)
from .registry import (
    ExecutionRegistry,
    RegistryLookupError,
    build_default_registry,
    default_registry,
)
from .work_item_executor import (
    DRIVING_STRUCTURE_PORT,
    ItemExecutionContext,
    WorkItemExecutor,
    check_code,
    error_result,
    sanitize_job_name,
    select_driving_structure,
)

__all__ = [
    "BatchStepExecutor",
    "CHECK_DEFAULTS",
    "CancelOutcome",
    "CheckContext",
    "CheckOutcome",
    "CheckSpec",
    "DRIVING_STRUCTURE_PORT",
    "EnvironmentMeasurer",
    "ExecutableIdentity",
    "ExecutionAdapterSpec",
    "ExecutionBinding",
    "ExecutionEnvironment",
    "ExecutionRegistry",
    "ExecutorCapability",
    "ExecutorContract",
    "GeometryOutput",
    "GeometrySemantics",
    "InMemoryReuseStore",
    "InMemoryWorkItemRepository",
    "InputFile",
    "ItemExecutionContext",
    "MaterializedNativeInput",
    "NativeError",
    "NativeErrorCode",
    "NativeExecutionRequest",
    "NativeExecutionResult",
    "NativeHandle",
    "NativeResult",
    "NativeStatus",
    "ParsedGeometry",
    "ProcessSupervisor",
    "ProducedFile",
    "ProfileContext",
    "ProfileOutput",
    "ProgramAdapter",
    "ProgramName",
    "RecoveryContext",
    "RecoveryDecision",
    "RecoveryExecution",
    "RecoveryPolicy",
    "RescueDriver",
    "ResolvedCalculationInputs",
    "ResultProfile",
    "ReuseStore",
    "ScientificCheck",
    "StagedArtifact",
    "StepExecutionRequest",
    "WorkItemExecutor",
    "WorkItemRepository",
    "check_code",
    "error_result",
    "measure_executable",
    "PortSpec",
    "RecoverySpec",
    "RegistryLookupError",
    "ResultProfileSpec",
    "build_default_registry",
    "default_registry",
    "passthrough_structure_id",
    "produced_structure_id",
    "sanitize_job_name",
    "select_driving_structure",
]
