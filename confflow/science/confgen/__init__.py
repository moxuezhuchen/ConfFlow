#!/usr/bin/env python3

"""ConfGen v3 core science package (CORE lane ownership).

Eagerly imports core-owned modules only (model, tolerances, planner,
accounting, perception, torsion, engine). Coordination/ring stages are
never imported here -- the engine lazy-loads them when their specs request
those axes, so this package imports cleanly with only core present.
"""

from __future__ import annotations

from confflow.science.confgen import accounting, engine, model, perception, planner, tolerances
from confflow.science.confgen.accounting import (
    AMBIGUOUS_KEY,
    SUPPRESSED_BY_SYMMETRY,
    RunCertificate,
    TargetRecord,
    build_certificate,
    certificate_category_counts,
    enumeration_digest,
    leaf_weight,
    scientific_category,
    stamp_production_results,
    verify_count_equations,
    verify_terminal_equations,
)
from confflow.science.confgen.engine import (
    AtomOrderViolationError,
    ConfgenEngine,
    EngineCancelledError,
    EngineRun,
    InheritedScopeError,
    UnsupportedAxisError,
    combine_state_key,
    thaw_snapshot,
)
from confflow.science.confgen.kernel_records import (
    ComponentStateKey,
    KernelGenerationTarget,
    KernelRun,
    KernelWorkingRealization,
    as_kernel_target,
)
from confflow.science.confgen.model import (
    AXIS_ORDER,
    SCHEMA_VERSION,
    AtomRef,
    ConfgenStateKey,
    EdgeType,
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    OrbitIdentity,
    PerceptionResult,
    RealizationResult,
    StageEstimate,
    TerminalStatus,
    TypedEdge,
    TypedGraph,
    WorkingRealization,
    build_context,
    covalent_adjacency_of,
    edge_kind_of,
)
from confflow.science.confgen.planner import (
    MixedRadixGrid,
    PreflightLimitError,
    PreflightReport,
    TorsionAxis,
    build_typed_graph,
    check_limits,
    deferred_ranges,
    normalize_spec,
    preflight,
    sample_indices,
)
from confflow.science.confgen.tolerances import ConfgenTolerances, resolve_tolerances

__all__ = [
    "AXIS_ORDER",
    "SCHEMA_VERSION",
    "AMBIGUOUS_KEY",
    "SUPPRESSED_BY_SYMMETRY",
    "AtomOrderViolationError",
    "AtomRef",
    "ComponentStateKey",
    "ConfgenEngine",
    "ConfgenStateKey",
    "ConfgenTolerances",
    "EdgeType",
    "EngineCancelledError",
    "EngineRun",
    "GenerationStage",
    "GenerationTarget",
    "InheritedScopeError",
    "InheritedTorsionLock",
    "KernelGenerationTarget",
    "KernelRun",
    "KernelWorkingRealization",
    "MixedRadixGrid",
    "MolecularContext",
    "OrbitIdentity",
    "PerceptionResult",
    "PreflightLimitError",
    "PreflightReport",
    "RealizationResult",
    "RunCertificate",
    "StageEstimate",
    "TargetRecord",
    "TerminalStatus",
    "TorsionAxis",
    "TypedEdge",
    "TypedGraph",
    "UnsupportedAxisError",
    "WorkingRealization",
    "accounting",
    "as_kernel_target",
    "build_certificate",
    "build_context",
    "build_typed_graph",
    "certificate_category_counts",
    "check_inherited_torsion_locks",
    "check_limits",
    "combine_state_key",
    "covalent_adjacency_of",
    "deferred_ranges",
    "edge_kind_of",
    "engine",
    "enumeration_digest",
    "inherited_torsion_locks",
    "leaf_weight",
    "model",
    "normalize_spec",
    "perception",
    "planner",
    "preflight",
    "resolve_tolerances",
    "sample_indices",
    "scientific_category",
    "stamp_production_results",
    "thaw_snapshot",
    "tolerances",
    "verify_count_equations",
    "verify_terminal_equations",
]

__version__ = "3.0.0-core"

# A4d lazy compat (PEP 562): only the three moved torsion names are served
# lazily; all other misses raise AttributeError (never ImportError).
_INHERITED_LAZY_NAMES = frozenset(
    {
        "InheritedTorsionLock",
        "inherited_torsion_locks",
        "check_inherited_torsion_locks",
    }
)


def __getattr__(name: str) -> object:
    """Serve only the three moved torsion compat names lazily."""
    if name not in _INHERITED_LAZY_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib as _il

    mod = _il.import_module("confflow.science.confgen.torsion.inherited")
    try:
        value = getattr(mod, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value
