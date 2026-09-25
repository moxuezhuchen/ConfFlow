#!/usr/bin/env python3

"""ConfFlow V4 domain model.

This package is the dependency-free semantic core of the V4 workflow engine.
It must not import ``confflow.core``, ``confflow.config``, ``confflow.calc``,
``confflow.workflow``, ``confflow.blocks``, or any V2/V3 runtime module; the
architecture gate in ``tests/v4`` enforces that rule.

Identity model
--------------
- entity identity: :attr:`StructureRecord.id`, :attr:`ArtifactRef.id`,
  :attr:`WorkItem.id`
- content identity: :attr:`StructureRecord.geometry_digest`,
  :attr:`ScientificResult.value_digest`, work-item semantic digests
- logical identity: :attr:`WorkItem.logical_key`

Digest axes are separated by construction: geometry digests exclude metadata
and provenance, and scheduler-only settings never enter scientific digests.
"""

from __future__ import annotations

from ._immutable import FrozenDict, freeze_value, thaw_value
from .artifact import ArtifactLocator, ArtifactRef, ArtifactSet, LocatorKind
from .binding import (
    Binding,
    BindingSet,
    BindingSource,
    Cardinality,
    Pairing,
    PartialConsumption,
    PortKind,
    PortSelector,
    SelectorKind,
    SourceKind,
)
from .canonical import (
    CANONICALIZATION_ID,
    canonical_json_bytes,
    canonical_sha256,
    normalize,
    typed_digest,
)
from .completion import (
    CompletionMode,
    CompletionPolicy,
    PartialOutputPolicy,
    StepStatus,
    WorkItemStatus,
    evaluate_step_status,
)
from .diagnostics import Diagnostic, DiagnosticSeverity, diagnostic_sort_key
from .elements import ELEMENT_SYMBOLS, atomic_number, canonical_element_symbol, is_known_element
from .errors import (
    CanonicalizationError,
    DomainError,
    ElementSymbolError,
    InvalidArtifactError,
    InvalidBindingError,
    InvalidCompletionPolicyError,
    InvalidResourceError,
    InvalidResultError,
    InvalidStructureError,
    InvalidWorkItemError,
    PublicationError,
)
from .publication import (
    PUBLICATION_ORDER,
    PublicationStage,
    PublicationTracker,
    verify_step_publication,
)
from .resources import OnFailure, ResourceRequest, SchedulerPolicy, parse_memory_bytes
from .result import Provenance, ResultSet, ScientificResult
from .retention import RetentionClass, may_garbage_collect
from .step_result import StepProvenance, StepResult
from .stochastic import SeedPolicy, seed_payload, seed_required, validate_seed
from .structure import GEOMETRY_DIGEST_KIND, Coordinates, StructureRecord, StructureSet
from .units import (
    CANONICAL_UNITS,
    UNIT_QUANTITIES,
    QuantityKind,
    Unit,
    is_canonical_unit,
)
from .work_item import (
    WORK_ITEM_DIGEST_KIND,
    RecoveryInfo,
    ResultError,
    Timing,
    WorkItem,
    WorkItemInputs,
    WorkItemResult,
    make_work_item_id,
    work_item_semantic_digest,
)

__all__ = [
    # Errors
    "DomainError",
    "CanonicalizationError",
    "ElementSymbolError",
    "InvalidStructureError",
    "InvalidArtifactError",
    "InvalidResultError",
    "InvalidBindingError",
    "InvalidWorkItemError",
    "InvalidCompletionPolicyError",
    "InvalidResourceError",
    "PublicationError",
    # Canonicalization
    "CANONICALIZATION_ID",
    "canonical_json_bytes",
    "canonical_sha256",
    "normalize",
    "typed_digest",
    # Immutability
    "FrozenDict",
    "freeze_value",
    "thaw_value",
    # Elements and units
    "ELEMENT_SYMBOLS",
    "atomic_number",
    "canonical_element_symbol",
    "is_known_element",
    "CANONICAL_UNITS",
    "UNIT_QUANTITIES",
    "QuantityKind",
    "Unit",
    "is_canonical_unit",
    # Structure
    "GEOMETRY_DIGEST_KIND",
    "Coordinates",
    "StructureRecord",
    "StructureSet",
    # Artifact
    "ArtifactLocator",
    "ArtifactRef",
    "ArtifactSet",
    "LocatorKind",
    "RetentionClass",
    "may_garbage_collect",
    # Results
    "Provenance",
    "ResultSet",
    "ScientificResult",
    # Bindings
    "Binding",
    "BindingSet",
    "BindingSource",
    "Cardinality",
    "Pairing",
    "PartialConsumption",
    "PortKind",
    "PortSelector",
    "SelectorKind",
    "SourceKind",
    # Resources
    "OnFailure",
    "ResourceRequest",
    "SchedulerPolicy",
    "parse_memory_bytes",
    # Completion
    "CompletionMode",
    "CompletionPolicy",
    "PartialOutputPolicy",
    "StepStatus",
    "WorkItemStatus",
    "evaluate_step_status",
    # Diagnostics
    "Diagnostic",
    "DiagnosticSeverity",
    "diagnostic_sort_key",
    # Work items
    "WORK_ITEM_DIGEST_KIND",
    "RecoveryInfo",
    "ResultError",
    "Timing",
    "WorkItem",
    "WorkItemInputs",
    "WorkItemResult",
    "make_work_item_id",
    "work_item_semantic_digest",
    # Step results
    "StepProvenance",
    "StepResult",
    # Publication
    "PUBLICATION_ORDER",
    "PublicationStage",
    "PublicationTracker",
    "verify_step_publication",
    # Stochastic
    "SeedPolicy",
    "seed_payload",
    "seed_required",
    "validate_seed",
]
