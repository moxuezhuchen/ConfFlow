"""Workflow Binding V2 — the typed, deeply immutable run binding.

One binding object answers, for one V3 run, the three identity questions of
RFC §16:

- **A** — ``definition_fingerprint``: the frozen Workflow Definition
  Fingerprint (semantic identity), taken verbatim from the plan;
- **C** — ``execution_fingerprint``: the Execution Fingerprint (what exactly
  will run), finalized at the execution site over a
  :class:`~confflow.workflow.execution_context.ResolvedExecutionContextV3`;
- **B** — ``provenance``: the schema/canonicalization/producer binding
  (provenance, not identity), carried for audit and compatibility policy.

The model is deeply immutable: frozen dataclasses all the way down, the
provenance included, and :meth:`WorkflowBindingV2.to_payload` returns a fresh
mapping every time so no caller can mutate a persisted binding through a
reference. ``workflow_binding.v1`` (Workflow-Config-V2 runs) is untouched.

Compatibility policy (frozen PD-1, runtime plan §2/§5) lives in
:func:`compare_workflow_binding_v2`:

- schema **ID** mismatch ⇒ reject (never resume across schema identities);
- schema **digest** change with identical A, identical canonicalization
  version, compatible producer and no dirty provenance ⇒ warn + allow;
- canonicalization-version change ⇒ reject;
- producer identity/version/commit change ⇒ reject; an unknown/missing
  producer field on either side ⇒ reject (provenance integrity is required —
  ``unknown == unknown`` is never treated as safe);
- dirty provenance on either side ⇒ reject (fresh dirty runs are allowed and
  recorded, but never resume-compatible under the initial policy).

``state.input_digests`` is a diagnostic/provenance snapshot only: the stored
C digest is the sole execution-compatibility authority, and the snapshot is
consulted solely to *classify* a C mismatch as inputs-vs-resources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config.canonical import (
    CANONICALIZATION_VERSION,
    WORKFLOW_SCHEMA_VERSION_V3,
    workflow_schema_sha256_v3,
)
from .execution_context import ResolvedExecutionContextV3, workflow_execution_fingerprint_v3
from .plan import WorkflowV3Plan

__all__ = [
    "BindingCompatibility",
    "BindingDiagnostic",
    "BindingProvenanceV2",
    "WorkflowBindingV2",
    "authoritative_provenance",
    "build_workflow_binding_v2",
    "compare_workflow_binding_v2",
]

BINDING_V2_SCHEMA = "confflow.workflow_binding.v2"


# ---------------------------------------------------------------------------
# Typed model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BindingProvenanceV2:
    """The B layer: schema/canonicalization/producer provenance (RFC §16.B)."""

    workflow_schema: str
    workflow_schema_sha256: str
    canonicalization_version: str
    producer_version: str
    producer_commit: str | None
    producer_dirty: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflow_schema": self.workflow_schema,
            "workflow_schema_sha256": self.workflow_schema_sha256,
            "canonicalization_version": self.canonicalization_version,
            "producer_version": self.producer_version,
            "producer_commit": self.producer_commit,
            "producer_dirty": self.producer_dirty,
        }

    @classmethod
    def from_payload(cls, raw: Any) -> BindingProvenanceV2:
        if not isinstance(raw, dict):
            raise ValueError("workflow binding provenance must be an object")
        unknown = sorted(
            set(raw)
            - {
                "workflow_schema",
                "workflow_schema_sha256",
                "canonicalization_version",
                "producer_version",
                "producer_commit",
                "producer_dirty",
            }
        )
        if unknown:
            raise ValueError(
                f"workflow binding provenance has unknown fields: {', '.join(unknown)}"
            )
        return cls(
            workflow_schema=str(raw["workflow_schema"]),
            workflow_schema_sha256=str(raw["workflow_schema_sha256"]),
            canonicalization_version=str(raw["canonicalization_version"]),
            producer_version=str(raw["producer_version"]),
            producer_commit=(
                None if raw.get("producer_commit") is None else str(raw["producer_commit"])
            ),
            producer_dirty=bool(raw["producer_dirty"]),
        )


@dataclass(frozen=True)
class WorkflowBindingV2:
    """The deeply immutable binding of one V3 run (schema, A, C, B)."""

    source_version: str
    definition_fingerprint: str
    execution_fingerprint: str
    provenance: BindingProvenanceV2
    schema: str = BINDING_V2_SCHEMA

    def to_payload(self) -> dict[str, Any]:
        """Return the durable wire payload — a fresh mapping every call."""
        return {
            "schema": self.schema,
            "source_version": self.source_version,
            "definition_fingerprint": self.definition_fingerprint,
            "execution_fingerprint": self.execution_fingerprint,
            "provenance": self.provenance.to_payload(),
        }

    @classmethod
    def from_payload(cls, raw: Any) -> WorkflowBindingV2:
        """Parse and validate the durable payload into the typed model."""
        if not isinstance(raw, dict):
            raise ValueError("workflow binding must be an object")
        unknown = sorted(
            set(raw)
            - {
                "schema",
                "source_version",
                "definition_fingerprint",
                "execution_fingerprint",
                "provenance",
            }
        )
        if unknown:
            raise ValueError(f"workflow binding has unknown fields: {', '.join(unknown)}")
        if raw.get("schema") != BINDING_V2_SCHEMA:
            raise ValueError(
                f"workflow binding schema must be {BINDING_V2_SCHEMA!r}, got {raw.get('schema')!r}"
            )
        provenance = BindingProvenanceV2.from_payload(raw.get("provenance"))
        return cls(
            source_version=str(raw["source_version"]),
            definition_fingerprint=str(raw["definition_fingerprint"]),
            execution_fingerprint=str(raw["execution_fingerprint"]),
            provenance=provenance,
        )


def authoritative_provenance() -> BindingProvenanceV2:
    """Return the running producer's provenance from the official sources.

    ``producer_commit`` may legitimately be ``None`` (a source checkout whose
    build provenance was never injected). That is recorded honestly: the
    compatibility comparator refuses resume whenever a provenance field is
    unknown on either side — ``unknown == unknown`` is never safe.
    """
    import confflow

    from ..__build__ import COMMIT, DIRTY

    return BindingProvenanceV2(
        workflow_schema=WORKFLOW_SCHEMA_VERSION_V3,
        workflow_schema_sha256=workflow_schema_sha256_v3(),
        canonicalization_version=CANONICALIZATION_VERSION,
        producer_version=confflow.__version__,
        producer_commit=COMMIT,
        producer_dirty=bool(DIRTY),
    )


def build_workflow_binding_v2(
    plan: WorkflowV3Plan,
    context: ResolvedExecutionContextV3,
    *,
    provenance: BindingProvenanceV2 | None = None,
) -> WorkflowBindingV2:
    """Build the official immutable binding for one planned, resolved run.

    ``definition_fingerprint`` is taken verbatim from the plan (the single A
    authority — callers cannot supply an arbitrary digest) and
    ``execution_fingerprint`` is computed here from the context. Production
    callers use the authoritative provenance; tests may inject an explicit
    one.
    """
    return WorkflowBindingV2(
        source_version=WORKFLOW_SCHEMA_VERSION_V3,
        definition_fingerprint=plan.definition_fingerprint,
        execution_fingerprint=workflow_execution_fingerprint_v3(plan, context),
        provenance=provenance if provenance is not None else authoritative_provenance(),
    )


# ---------------------------------------------------------------------------
# Compatibility comparison (pure function — R4.4 wires it into resume)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BindingDiagnostic:
    """One compatibility finding at a stable dotted code."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class BindingCompatibility:
    """The layered result of comparing a stored binding to the current one."""

    compatible: bool
    warnings: tuple[BindingDiagnostic, ...] = field(default_factory=tuple)
    errors: tuple[BindingDiagnostic, ...] = field(default_factory=tuple)

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(diagnostic.code for diagnostic in self.errors)

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(diagnostic.code for diagnostic in self.warnings)


def compare_workflow_binding_v2(
    stored: WorkflowBindingV2,
    current: WorkflowBindingV2,
    *,
    stored_input_digests: tuple[str, ...] | None = None,
    current_input_digests: tuple[str, ...] | None = None,
) -> BindingCompatibility:
    """Compare a stored run binding against the current one, layer by layer.

    Trust chain: both bindings must have been built by
    :func:`build_workflow_binding_v2` from RUNNABLE-valid plans — the current
    side's "document revalidates" precondition is guaranteed by that
    construction, not re-checked here.

    Evaluation order (deterministic, per the runtime plan):

    1. structural — binding schema and workflow source version;
    2. **A** — definition fingerprint mismatch ⇒ reject immediately;
    3. **B** hard compatibility — schema ID, canonicalization version,
       producer identity/version/commit, dirty provenance;
    4. **B** schema-digest change ⇒ warning + allow under the frozen
       conditions (same schema ID, same A, same canonicalization, compatible
       producer, no dirty);
    5. **C** — execution fingerprint mismatch ⇒ reject, classified as
       ``C:inputs`` or ``C:resources`` using the *snapshots* when available.

    The snapshot arguments only classify a C mismatch; C equality is the sole
    execution-compatibility authority.
    """
    errors: list[BindingDiagnostic] = []
    warnings: list[BindingDiagnostic] = []

    if stored.schema != current.schema:
        errors.append(
            BindingDiagnostic(
                "binding.schema",
                f"stored binding schema {stored.schema!r} does not match the current "
                f"binding schema {current.schema!r}",
            )
        )
        return BindingCompatibility(False, tuple(warnings), tuple(errors))
    if stored.source_version != current.source_version:
        errors.append(
            BindingDiagnostic(
                "binding.source_version",
                f"stored workflow schema {stored.source_version!r} does not match the "
                f"current workflow schema {current.source_version!r}",
            )
        )
        return BindingCompatibility(False, tuple(warnings), tuple(errors))

    # 2. A — semantic definition identity.
    if stored.definition_fingerprint != current.definition_fingerprint:
        return BindingCompatibility(
            False,
            tuple(warnings),
            (
                BindingDiagnostic(
                    "binding.definition_fingerprint",
                    "the workflow definition changed (A); the stored run belongs to a "
                    "different scientific workflow",
                ),
            ),
        )

    # 3./4. B — provenance compatibility.
    stored_p, current_p = stored.provenance, current.provenance
    b_error = False
    if stored_p.workflow_schema != current_p.workflow_schema:
        errors.append(
            BindingDiagnostic(
                "binding.schema_id",
                f"stored workflow schema identity {stored_p.workflow_schema!r} does not "
                f"match the current {current_p.workflow_schema!r}",
            )
        )
        b_error = True
    if stored_p.canonicalization_version != current_p.canonicalization_version:
        errors.append(
            BindingDiagnostic(
                "binding.canonicalization",
                f"stored canonicalization version {stored_p.canonicalization_version!r} does "
                f"not match the current {current_p.canonicalization_version!r}; digests are "
                "not comparable across canonicalizers",
            )
        )
        b_error = True
    if stored_p.producer_version != current_p.producer_version:
        errors.append(
            BindingDiagnostic(
                "binding.producer_version",
                f"stored producer version {stored_p.producer_version!r} does not match the "
                f"current {current_p.producer_version!r}",
            )
        )
        b_error = True
    if stored_p.producer_commit != current_p.producer_commit:
        if stored_p.producer_commit is None or current_p.producer_commit is None:
            errors.append(
                BindingDiagnostic(
                    "binding.producer_commit",
                    "producer provenance is incomplete (no build commit) on "
                    f"{'the stored run' if stored_p.producer_commit is None else 'the current run'}; "
                    "resume cannot prove runtime identity",
                )
            )
        else:
            errors.append(
                BindingDiagnostic(
                    "binding.producer_commit",
                    f"stored producer commit {stored_p.producer_commit!r} does not match "
                    f"the current {current_p.producer_commit!r}",
                )
            )
        b_error = True
    if stored_p.producer_dirty or current_p.producer_dirty:
        side = "stored run" if stored_p.producer_dirty else "current run"
        errors.append(
            BindingDiagnostic(
                "binding.dirty",
                f"the {side} was produced from a dirty working tree; resume cannot prove "
                "runtime identity under the initial binding policy",
            )
        )
        b_error = True
    if b_error:
        return BindingCompatibility(False, tuple(warnings), tuple(errors))

    # 4. B schema digest — the only conditional-allow path.
    if stored_p.workflow_schema_sha256 != current_p.workflow_schema_sha256:
        warnings.append(
            BindingDiagnostic(
                "binding.schema_digest",
                "the workflow schema document changed (representation only: identity, "
                "definition fingerprint and canonicalization are unchanged); resume is "
                "allowed and the change is recorded",
            )
        )

    # 5./6. C — execution identity; snapshots classify only.
    if stored.execution_fingerprint != current.execution_fingerprint:
        classification = "resources"
        if stored_input_digests is not None and current_input_digests is not None:
            if tuple(stored_input_digests) != tuple(current_input_digests):
                classification = "inputs"
        errors.append(
            BindingDiagnostic(
                f"binding.execution_fingerprint_{classification}",
                "the resolved execution context changed; the stored run cannot be "
                "resumed under this environment",
            )
        )
        return BindingCompatibility(False, tuple(warnings), tuple(errors))

    return BindingCompatibility(True, tuple(warnings), tuple(errors))
