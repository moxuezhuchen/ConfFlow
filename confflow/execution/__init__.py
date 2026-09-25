#!/usr/bin/env python3

"""V4 execution contracts: capability descriptors, not execution.

This package owns the *capability vocabulary* of the V4 engine: which step
executors exist, which ports they declare, which execution adapters, result
profiles, scientific checks, and recovery profiles are registered, and how a
machine-specific execution binding is described.

It deliberately contains no YAML parsing and no workflow topology.  Nothing in
this package launches a process; V4-1 only validates capabilities and folds
contract versions into semantic digests.
"""

from __future__ import annotations

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
from .registry import (
    ExecutionRegistry,
    RegistryLookupError,
    build_default_registry,
    default_registry,
)

__all__ = [
    "CheckSpec",
    "ExecutionAdapterSpec",
    "ExecutionBinding",
    "ExecutionEnvironment",
    "ExecutionRegistry",
    "ExecutorCapability",
    "ExecutorContract",
    "PortSpec",
    "RecoverySpec",
    "RegistryLookupError",
    "ResultProfileSpec",
    "build_default_registry",
    "default_registry",
]
