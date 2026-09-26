#!/usr/bin/env python3

"""Explicit reuse and compatibility decisions for V4 durable execution (V4-3).

This module is pure: it performs no SQLite, filesystem, or process access. It
compares the digest axes of a freshly computed work item against the durable
record of a stored work item and returns one explicit
:class:`~confflow.persistence.contracts.ReuseDecision`.

Frozen rule order in :func:`evaluate_reuse`:

1. No durable record (``stored``/status missing or unknown) never reuses.
2. ``PENDING`` never reuses.
3. ``RUNNING`` never launches a duplicate: a definitely-dead owner recovers
   the abandoned claim, any other verdict blocks on the uncertain owner.
4. ``CANCELLED`` never auto-relaunches; only an explicit retry may rerun it.
5. Terminal ``COMPLETED``/``FAILED``/``INTERRUPTED`` records run the
   compatibility chain, first mismatch winning: producer provenance,
   environment digest, step semantic digest, bound-input artifact checksums,
   work-item digest, then stored-artifact verification.

Presentation facts (labels, GUI annotations), scheduler width, and absolute
binary paths are not fields of :class:`ReuseInputs`, so they can never
invalidate reuse by construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ..domain._immutable import FrozenDict
from ..domain.canonical import CANONICALIZATION_ID
from .contracts import (
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    ReuseDecision,
    StoredWorkItemStatus,
)

__all__ = [
    "MAX_PROVENANCE_DIFF_KEYS_REPORTED",
    "ReuseInputs",
    "build_producer_provenance",
    "evaluate_reuse",
]

#: Maximum number of differing provenance keys reported in decision details.
MAX_PROVENANCE_DIFF_KEYS_REPORTED: Final[int] = 8

#: Fallback identifier when the caller passes a blank ``work_item_id``.
_UNKNOWN_WORK_ITEM_ID: Final[str] = "unknown"


@dataclass(frozen=True, slots=True)
class ReuseInputs:
    """Digest axes compared by :func:`evaluate_reuse`.

    Parameters
    ----------
    work_item_digest : str
        Digest of the bound work-item content (geometry, inputs, resources).
    step_semantic_digest : str
        Digest of the step definition (native keywords, checks, recovery,
        adapter, profile, seeds).
    environment_digest : str | None
        Digest of the execution environment, or ``None`` when unknown.
    producer_provenance : FrozenDict
        Contract versions (adapter/profile/check/recovery) plus the
        canonicalization id.  Empty means unknown.
    artifact_checksums : tuple[str, ...]
        Sorted bound-input artifact checksums.  Construction sorts them so
        comparison is order-stable; they are compared as sorted tuples,
        never as sets.

    Raises
    ------
    PersistenceError
        Raised when any axis value has the wrong type or is blank.
    """

    work_item_digest: str
    step_semantic_digest: str
    environment_digest: str | None
    producer_provenance: FrozenDict
    artifact_checksums: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate axes and normalize checksums into sorted order."""
        for axis in ("work_item_digest", "step_semantic_digest"):
            value = getattr(self, axis)
            if not isinstance(value, str) or not value.strip():
                raise PersistenceError(f"{axis} must be a non-empty string")
        environment = self.environment_digest
        if environment is not None and (
            not isinstance(environment, str) or not environment.strip()
        ):
            raise PersistenceError("environment_digest must be a non-empty string or None")
        provenance = self.producer_provenance
        if not isinstance(provenance, FrozenDict):
            if isinstance(provenance, Mapping):
                object.__setattr__(self, "producer_provenance", FrozenDict(provenance))
            else:
                raise PersistenceError("producer_provenance must be a FrozenDict")
        checksums = self.artifact_checksums
        if isinstance(checksums, list):
            checksums = tuple(checksums)
        if not isinstance(checksums, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in checksums
        ):
            raise PersistenceError("artifact_checksums must be a tuple of non-empty strings")
        object.__setattr__(self, "artifact_checksums", tuple(sorted(checksums)))


def build_producer_provenance(
    *,
    adapter_version: str,
    profile_version: str,
    check_versions: Mapping[str, str],
    recovery_version: str,
    canonicalization_id: str = CANONICALIZATION_ID,
) -> FrozenDict:
    """Build the single shared producer-provenance shape.

    Parameters
    ----------
    adapter_version : str
        Version of the native program adapter contract.
    profile_version : str
        Version of the result-profile contract.
    check_versions : Mapping[str, str]
        Mapping of check name to check contract version.
    recovery_version : str
        Version of the recovery policy contract.
    canonicalization_id : str
        Canonicalization identifier; defaults to
        :data:`~confflow.domain.canonical.CANONICALIZATION_ID`.

    Returns
    -------
    FrozenDict
        Frozen provenance mapping with exactly the keys ``adapter_version``,
        ``profile_version``, ``check_versions``, ``recovery_version``, and
        ``canonicalization_id``.

    Raises
    ------
    PersistenceError
        Raised when any version value is blank or ``check_versions`` is not
        a string-to-string mapping.
    """
    versions = {
        "adapter_version": adapter_version,
        "profile_version": profile_version,
        "recovery_version": recovery_version,
        "canonicalization_id": canonicalization_id,
    }
    for name, value in versions.items():
        if not isinstance(value, str) or not value.strip():
            raise PersistenceError(f"{name} must be a non-empty string")
    if not isinstance(check_versions, Mapping):
        raise PersistenceError("check_versions must be a mapping of check name to version")
    for key, value in check_versions.items():
        if not isinstance(key, str) or not key.strip():
            raise PersistenceError("check_versions keys must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise PersistenceError("check_versions values must be non-empty strings")
    return FrozenDict(
        {
            "adapter_version": adapter_version,
            "profile_version": profile_version,
            "check_versions": dict(check_versions),
            "recovery_version": recovery_version,
            "canonicalization_id": canonicalization_id,
        }
    )


def _normalize_status(value: StoredWorkItemStatus | str | None) -> StoredWorkItemStatus | None:
    """Coerce *value* to a known status, or return ``None`` when unknown.

    Parameters
    ----------
    value : StoredWorkItemStatus | str | None
        Durable status from the store.  Raw strings are coerced through the
        enum; unrecognized strings (such as ``"pending-missing"``) and other
        unexpected types collapse to ``None`` so the caller fails open to a
        fresh execution instead of reusing blindly.

    Returns
    -------
    StoredWorkItemStatus | None
        The known status, or ``None`` when there is no durable record.
    """
    if value is None or isinstance(value, StoredWorkItemStatus):
        return value
    if isinstance(value, str):
        try:
            return StoredWorkItemStatus(value)
        except ValueError:
            return None
    return None


def _normalize_verdict(value: OwnerVerdict | str | None) -> OwnerVerdict | None:
    """Coerce *value* to a known owner verdict, or return ``None``.

    Parameters
    ----------
    value : OwnerVerdict | str | None
        Liveness verdict for an abandoned ``RUNNING`` claim.  Unrecognized
        values collapse to ``None``, which takes the uncertain-owner path
        and never launches a duplicate.

    Returns
    -------
    OwnerVerdict | None
        The known verdict, or ``None`` when liveness is unproven.
    """
    if value is None or isinstance(value, OwnerVerdict):
        return value
    if isinstance(value, str):
        try:
            return OwnerVerdict(value)
        except ValueError:
            return None
    return None


def _provenance_diff(current: FrozenDict, stored: FrozenDict) -> tuple[str, ...]:
    """Return the sorted union of provenance keys whose values differ.

    Parameters
    ----------
    current : FrozenDict
        Freshly computed producer provenance.
    stored : FrozenDict
        Durably recorded producer provenance.

    Returns
    -------
    tuple[str, ...]
        Sorted differing key names.
    """
    current_map = current.thaw()
    stored_map = stored.thaw()
    differing = sorted(
        key
        for key in set(current_map) | set(stored_map)
        if key not in current_map or key not in stored_map or current_map[key] != stored_map[key]
    )
    return tuple(differing)


def _status_label(value: StoredWorkItemStatus | str | None) -> str | None:
    """Return the durable status name for decision details.

    Parameters
    ----------
    value : StoredWorkItemStatus | str | None
        Raw stored status as passed to :func:`evaluate_reuse`.

    Returns
    -------
    str | None
        Enum value, raw string, or ``None``.
    """
    if isinstance(value, StoredWorkItemStatus):
        return value.value
    if isinstance(value, str):
        return value
    return None


def _verdict_label(value: OwnerVerdict | str | None) -> str | None:
    """Return the owner verdict name for decision details.

    Parameters
    ----------
    value : OwnerVerdict | str | None
        Raw owner verdict as passed to :func:`evaluate_reuse`.

    Returns
    -------
    str | None
        Enum value, raw string, or ``None``.
    """
    if isinstance(value, OwnerVerdict):
        return value.value
    if isinstance(value, str):
        return value
    return None


def evaluate_reuse(
    *,
    current: ReuseInputs,
    stored: ReuseInputs | None,
    stored_status: StoredWorkItemStatus | str | None,
    owner_verdict: OwnerVerdict | str | None = None,
    artifacts_verified: bool = True,
    work_item_id: str = "",
) -> ReuseDecision:
    """Evaluate whether a stored work-item result may be reused.

    Parameters
    ----------
    current : ReuseInputs
        Freshly computed digest axes.
    stored : ReuseInputs | None
        Durably recorded digest axes, or ``None`` when no record exists.
    stored_status : StoredWorkItemStatus | str | None
        Durable status of the stored item.  Unknown raw strings collapse to
        "no durable record" and execute new.
    owner_verdict : OwnerVerdict | str | None
        Liveness verdict for an abandoned ``RUNNING`` claim.
    artifacts_verified : bool
        Whether the stored artifacts passed verification.
    work_item_id : str
        Item this decision applies to; passed through into the decision.  A
        blank value falls back to ``"unknown"`` so every path still returns
        a valid decision.

    Returns
    -------
    ReuseDecision
        Explicit verdict with a non-empty reason and the compared axis
        values in details for every compatibility failure.

    Raises
    ------
    PersistenceError
        Raised when *current* or *stored* has the wrong type.
    """
    if not isinstance(current, ReuseInputs):
        raise PersistenceError("current must be ReuseInputs")
    if stored is not None and not isinstance(stored, ReuseInputs):
        raise PersistenceError("stored must be ReuseInputs or None")
    item_id = (
        work_item_id
        if isinstance(work_item_id, str) and work_item_id.strip()
        else (_UNKNOWN_WORK_ITEM_ID)
    )

    status = _normalize_status(stored_status)
    if stored is None or status is None:
        return ReuseDecision(
            decision=ReuseCode.EXECUTE_NEW,
            reason="no durable record for work item; executing new",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_present": stored is not None,
                    "stored_status": _status_label(stored_status),
                }
            ),
        )
    if status is StoredWorkItemStatus.PENDING:
        return ReuseDecision(
            decision=ReuseCode.EXECUTE_NEW,
            reason="pending never reuses; executing new",
            work_item_id=item_id,
            details=FrozenDict({"stored_status": status.value}),
        )
    if status is StoredWorkItemStatus.RUNNING:
        verdict = _normalize_verdict(owner_verdict)
        details = FrozenDict(
            {"stored_status": status.value, "owner_verdict": _verdict_label(owner_verdict)}
        )
        if verdict is OwnerVerdict.DEFINITELY_DEAD:
            return ReuseDecision(
                decision=ReuseCode.RECOVER_ABANDONED,
                reason="owner_dead: prior owner is definitely dead; recovering abandoned claim",
                work_item_id=item_id,
                details=details,
            )
        if verdict is OwnerVerdict.DEFINITELY_ALIVE:
            return ReuseDecision(
                decision=ReuseCode.BLOCKED_UNCERTAIN_OWNER,
                reason="owner_alive: prior owner is definitely alive; never launch a duplicate",
                work_item_id=item_id,
                details=details,
            )
        return ReuseDecision(
            decision=ReuseCode.BLOCKED_UNCERTAIN_OWNER,
            reason="owner_uncertain: owner liveness is unproven; never launch a duplicate",
            work_item_id=item_id,
            details=details,
        )
    if status is StoredWorkItemStatus.CANCELLED:
        return ReuseDecision(
            decision=ReuseCode.EXECUTE_NEW,
            reason="cancelled work items never auto-relaunch; requires explicit retry",
            work_item_id=item_id,
            details=FrozenDict({"stored_status": status.value, "requires_explicit_retry": True}),
        )

    stored_name = status.value
    if current.producer_provenance != stored.producer_provenance:
        differing = _provenance_diff(current.producer_provenance, stored.producer_provenance)
        reported = differing[:MAX_PROVENANCE_DIFF_KEYS_REPORTED]
        keys_text = ", ".join(reported) if reported else "uncomparable provenance payloads"
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_PROVENANCE,
            reason=f"producer provenance mismatch on {keys_text}; invalidating stored result",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_status": stored_name,
                    "differing_keys": reported,
                    "producer_provenance_current": current.producer_provenance.thaw(),
                    "producer_provenance_stored": stored.producer_provenance.thaw(),
                }
            ),
        )
    if current.environment_digest != stored.environment_digest:
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_ENVIRONMENT,
            reason="execution environment digest mismatch; invalidating stored result",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_status": stored_name,
                    "environment_digest_current": current.environment_digest,
                    "environment_digest_stored": stored.environment_digest,
                }
            ),
        )
    if current.step_semantic_digest != stored.step_semantic_digest:
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_DEFINITION,
            reason="step definition digest mismatch; invalidating stored result",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_status": stored_name,
                    "step_semantic_digest_current": current.step_semantic_digest,
                    "step_semantic_digest_stored": stored.step_semantic_digest,
                }
            ),
        )
    if current.artifact_checksums != stored.artifact_checksums:
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_ARTIFACT,
            reason="bound-input artifact checksums mismatch; invalidating stored result",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_status": stored_name,
                    "artifact_checksums_current": current.artifact_checksums,
                    "artifact_checksums_stored": stored.artifact_checksums,
                }
            ),
        )
    if current.work_item_digest != stored.work_item_digest:
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_INPUT,
            reason="work-item input digest mismatch; invalidating stored result",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_status": stored_name,
                    "work_item_digest_current": current.work_item_digest,
                    "work_item_digest_stored": stored.work_item_digest,
                }
            ),
        )
    if not artifacts_verified:
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_ARTIFACT,
            reason="stored artifacts failed verification",
            work_item_id=item_id,
            details=FrozenDict(
                {
                    "stored_status": stored_name,
                    "artifacts_verified": False,
                    "artifact_checksums_current": current.artifact_checksums,
                    "artifact_checksums_stored": stored.artifact_checksums,
                }
            ),
        )
    if status is StoredWorkItemStatus.COMPLETED:
        return ReuseDecision(
            decision=ReuseCode.REUSE,
            reason="every compatibility axis matches; reusing stored result",
            work_item_id=item_id,
            details=FrozenDict({"stored_status": stored_name}),
        )
    return ReuseDecision(
        decision=ReuseCode.RETRY_FAILED,
        reason="every compatibility axis matches; retrying failed attempt",
        work_item_id=item_id,
        details=FrozenDict({"stored_status": stored_name}),
    )
