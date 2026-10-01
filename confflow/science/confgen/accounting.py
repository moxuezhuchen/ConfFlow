#!/usr/bin/env python3

"""ConfGen v3 failure accounting (CORE lane).

Target records with single terminal statuses, deferred-range compression
for unsampled and parent-failed subtrees, terminal-equation verification,
run certificates over explicit count layers, and production result
stamping via the existing ``make_result_id`` authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from confflow.domain._immutable import FrozenDict
from confflow.science.confgen.model import TerminalStatus, WorkingRealization

if TYPE_CHECKING:  # typing only; runtime import stays lazy inside the function
    from confflow.domain.result import ResultSet

__all__ = [
    "AMBIGUOUS_KEY",
    "SCIENTIFIC_TARGET_CATEGORIES",
    "SUPPRESSED_BY_SYMMETRY",
    "RunCertificate",
    "TargetRecord",
    "attempt_ledger_counts",
    "build_certificate",
    "certificate_category_counts",
    "compress_ranges",
    "count_ranges",
    "deferred_range_record",
    "enumeration_digest",
    "leaf_category_counts",
    "leaf_weight",
    "record_unit",
    "scientific_category",
    "stamp_production_results",
    "verify_count_equations",
    "verify_terminal_equations",
]

#: User-visible scientific certificate target categories. Internal engine
#: states (EXPANDED/PUBLISHED_LEAF) never replace these in certificates.
SCIENTIFIC_TARGET_CATEGORIES: tuple[str, ...] = (
    "REALIZED",
    "DRIFTED",
    "UNRESOLVED",
    "DEFERRED",
    "REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY",
)

#: Suppression category: emitted only for engine-bound verified-symmetry
#: records (single-axis runs with a complete witness record:
#: representative, full parent stabilizer, element witnesses, closure
#: proof). Counted explicitly, never hidden; zero when the hook is
#: unbound or refuses.
SUPPRESSED_BY_SYMMETRY: str = "REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"

#: Perception anomaly: the stage-local state could not be unambiguously
#: measured. Carried as evidence, never a second terminal status.
AMBIGUOUS_KEY: str = "AMBIGUOUS_KEY"


@dataclass(frozen=True, slots=True)
class TargetRecord:
    """One accounted target: exactly one terminal status plus evidence.

    Deferred leaves are stored compressed: one record per deferred RANGE
    (``target_id`` ``"<axis>:range:<start>-<end>"``) instead of one record
    per leaf, so subtree accounting never expands deferred leaves.
    """

    target_id: str
    axis: str
    ordinal: int
    state_value: Mapping[str, Any] = field(default_factory=FrozenDict)
    complete_key: Mapping[str, Any] | None = None
    status: TerminalStatus = TerminalStatus.EXPANDED
    reason: str = "pending"
    evidence: tuple[Mapping[str, Any], ...] = ()
    parent_target_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id:
            raise ValueError("target_id must be a non-empty string")
        if not isinstance(self.axis, str) or not self.axis:
            raise ValueError("axis must be a non-empty string")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be an integer >= 0")
        if not isinstance(self.status, TerminalStatus):
            raise ValueError(f"status must be a TerminalStatus, got {self.status!r}")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string")
        if not isinstance(self.state_value, FrozenDict):
            object.__setattr__(self, "state_value", FrozenDict(dict(self.state_value)))
        if self.complete_key is not None and not isinstance(self.complete_key, FrozenDict):
            object.__setattr__(self, "complete_key", FrozenDict(dict(self.complete_key)))
        object.__setattr__(
            self, "evidence", tuple(FrozenDict(dict(item)) for item in self.evidence)
        )


def compress_ranges(indices: Sequence[int]) -> tuple[tuple[int, int], ...]:
    """Compress sorted unique indices into ascending inclusive ranges."""
    chosen = sorted(int(index) for index in indices)
    if len(set(chosen)) != len(chosen):
        raise ValueError("indices must be unique")
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for index in chosen:
        if start is None or previous is None:
            start, previous = index, index
        elif index == previous + 1:
            previous = index
        else:
            ranges.append((start, previous))
            start, previous = index, index
    if start is not None and previous is not None:
        ranges.append((start, previous))
    return tuple(ranges)


def count_ranges(ranges: Sequence[tuple[int, int]]) -> int:
    """Return the leaf count covered by inclusive ranges."""
    total = 0
    for start, end in ranges:
        if end < start:
            raise ValueError(f"invalid range ({start}, {end})")
        total += end - start + 1
    return total


def deferred_range_record(
    *,
    axis: str,
    start: int,
    end: int,
    status: TerminalStatus,
    reason: str,
    parent_target_id: str | None = None,
    evidence: Sequence[Mapping[str, Any]] = (),
) -> TargetRecord:
    """Build one compressed record covering deferred ordinals start..end."""
    if status not in (
        TerminalStatus.DEFERRED_SAMPLED_OUT,
        TerminalStatus.DEFERRED_PARENT_FAILED,
    ):
        raise ValueError("range records must carry a deferred status")
    if end < start:
        raise ValueError(f"invalid deferred range ({start}, {end})")
    return TargetRecord(
        target_id=f"{axis}:range:{start}-{end}",
        axis=axis,
        ordinal=int(start),
        state_value=FrozenDict({"range_start": int(start), "range_end": int(end)}),
        complete_key=None,
        status=status,
        reason=reason,
        evidence=tuple(evidence),
        parent_target_id=parent_target_id,
    )


def scientific_category(record: TargetRecord) -> str:
    """Map an internal terminal status to the scientific certificate category.

    PUBLISHED_LEAF -> REALIZED; FAILED_DRIFT -> DRIFTED;
    SUPPRESSED_BY_VERIFIED_SYMMETRY -> REALIZATION_SUPPRESSED_BY_VERIFIED_
    SYMMETRY (verified records only, never bare booleans); geometry/
    numerical/atom-order/unsupported/policy failures and UNRESOLVED
    (ambiguous or unverifiable perception) -> UNRESOLVED (the reason
    retains the precise failure, e.g. clash as geometric failure, never
    a proof); deferred ranges -> DEFERRED. EXPANDED internal nodes are
    not certificate targets and map to ``"INTERNAL"``.
    """
    status = record.status
    if status is TerminalStatus.PUBLISHED_LEAF:
        return "REALIZED"
    if status is TerminalStatus.FAILED_DRIFT:
        return "DRIFTED"
    if status is TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY:
        return SUPPRESSED_BY_SYMMETRY
    if status in (
        TerminalStatus.DEFERRED_SAMPLED_OUT,
        TerminalStatus.DEFERRED_PARENT_FAILED,
    ):
        return "DEFERRED"
    if status is TerminalStatus.EXPANDED:
        return "INTERNAL"
    return "UNRESOLVED"


def leaf_weight(record: TargetRecord) -> tuple[int, bool]:
    """Return (leaf count, exact) covered by one target record.

    Singleton records weigh 1. Compressed deferred RANGE records weigh
    end-start+1 (counted arithmetically, never expanded). Conditional
    subtree estimates weigh their estimate but are INEXACT by construction.
    """
    state = dict(record.state_value)
    if "range_start" in state and "range_end" in state:
        return int(state["range_end"]) - int(state["range_start"]) + 1, True
    if "estimated_leaves" in state:
        return int(state["estimated_leaves"]), False
    return 1, True


def certificate_category_counts(records: Sequence[TargetRecord]) -> dict[str, Any]:
    """Count scientific certificate categories weighted by leaf coverage.

    Deferred ranges count per covered leaf (arithmetically, never expanded).
    Conditional subtree estimates are EXCLUDED from exact categories and
    reported separately under `estimated_deferred_leaves` with
    `equation_exact=False`. AMBIGUOUS_KEY anomalies count record events,
    never statuses. Internal EXPANDED nodes count separately (not targets).
    """
    categories: dict[str, int] = {name: 0 for name in SCIENTIFIC_TARGET_CATEGORIES}
    internal = 0
    anomalies = 0
    estimated = 0
    for record in records:
        weight, exact = leaf_weight(record)
        category = scientific_category(record)
        if category == "INTERNAL":
            internal += 1
            continue
        if not exact:
            estimated += weight
            continue
        categories[category] += weight
        for item in record.evidence:
            if (
                isinstance(item, Mapping)
                and item.get("kind") == "anomaly"
                and item.get("anomaly") == AMBIGUOUS_KEY
            ):
                anomalies += 1
                break
    return {
        "target_categories": categories,
        "internal_expanded": internal,
        "anomalies": {AMBIGUOUS_KEY: anomalies},
        "estimated_deferred_leaves": estimated,
        "equation_exact": estimated == 0,
    }


def record_unit(record: TargetRecord, has_children: bool) -> str:
    """Return the counting unit of one record (per-record scope rule).

    - ``"final-leaf"``: a leaf outcome (published, terminal failure,
      leaf rejection, verified suppression) with no accounted
      descendants -- the final-leaf certificate unit.
    - ``"internal-attempt"``: an issued stage attempt with accounted
      descendants (expanded nodes, failed/rejected ancestors whose
      subtrees deferred) -- the attempt-ledger unit, never a leaf.
    - ``"deferred-range"``: a compressed unissued-leaf range -- counted
      arithmetically in the leaf certificate, never an attempt.
    Any serialized record is classified by (status, parent linkage):
    no other channel is needed.
    """
    if record.status in (
        TerminalStatus.DEFERRED_SAMPLED_OUT,
        TerminalStatus.DEFERRED_PARENT_FAILED,
    ):
        return "deferred-range"
    if record.status is TerminalStatus.EXPANDED or has_children:
        return "internal-attempt"
    return "final-leaf"


def leaf_category_counts(records: Sequence[TargetRecord]) -> dict[str, Any]:
    """Count the FINAL-LEAF certificate partition (joint leaves only).

    Internal attempts (expanded nodes and failed/rejected ancestors
    with deferred descendants) are structural and excluded: a failed
    parent is reported in the attempt ledger while its descendants
    defer into the leaf equation, so no ancestor plus descendant ever
    shares one leaf equation. Deferred ranges count per covered leaf
    arithmetically. AMBIGUOUS_KEY anomalies count record events.
    """
    children: set[str] = set()
    for record in records:
        if record.parent_target_id is not None:
            children.add(record.parent_target_id)
    categories: dict[str, int] = {
        "REALIZED": 0,
        "DRIFTED": 0,
        "UNRESOLVED": 0,
        "DEFERRED": 0,
        "REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY": 0,
        "REJECTED": 0,
    }
    anomalies = 0
    estimated = 0
    for record in records:
        weight, exact = leaf_weight(record)
        if not exact:
            estimated += weight
            continue
        if record.status in (
            TerminalStatus.DEFERRED_SAMPLED_OUT,
            TerminalStatus.DEFERRED_PARENT_FAILED,
        ):
            categories["DEFERRED"] += weight
        elif record.status is TerminalStatus.EXPANDED or record.target_id in children:
            continue  # internal attempt, not a final leaf
        elif record.status is TerminalStatus.PUBLISHED_LEAF:
            categories["REALIZED"] += weight
        elif record.status is TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY:
            categories["REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"] += weight
        elif record.status is TerminalStatus.REJECTED_BY_POLICY:
            categories["REJECTED"] += weight
        else:
            mapped = scientific_category(record)
            if mapped == "DRIFTED":
                categories["DRIFTED"] += weight
            else:
                categories["UNRESOLVED"] += weight
        for item in record.evidence:
            if (
                isinstance(item, Mapping)
                and item.get("kind") == "anomaly"
                and item.get("anomaly") == AMBIGUOUS_KEY
            ):
                anomalies += 1
                break
    return {
        "leaf_categories": categories,
        "anomalies": {AMBIGUOUS_KEY: anomalies},
        "estimated_deferred_leaves": estimated,
        "equation_exact": estimated == 0,
    }


def attempt_ledger_counts(records: Sequence[TargetRecord]) -> dict[str, Any]:
    """Count the AXIS-ATTEMPT ledger (issued stage attempts only).

    Every issued attempt is one singleton record that reached stage
    realization: internal expansions and published leaves are
    geometry-successful; terminal failures without geometry are not.
    Policy-screened targets (rejected before geometry), verified
    suppressions (realization skipped by witness), and deferred
    unissued ranges are separate units, never attempts. Attempts are
    per-level issuances along every explored path, so attempt counts
    exceed leaf counts by construction (one attempt per tree level per
    path); deferred ranges were never issued at any level.
    """
    by_status: dict[str, int] = {}
    successful = 0
    internal_expanded = 0
    published = 0
    without_success = 0
    suppressed_skipped = 0
    policy_screened = 0
    deferred_unissued = 0
    for record in records:
        weight, exact = leaf_weight(record)
        if not exact:
            raise ValueError("inexact conditional estimates have no ledger unit")
        status = record.status
        if status in (
            TerminalStatus.DEFERRED_SAMPLED_OUT,
            TerminalStatus.DEFERRED_PARENT_FAILED,
        ):
            deferred_unissued += weight
            continue
        if status is TerminalStatus.REJECTED_BY_POLICY:
            policy_screened += weight
            continue
        if status is TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY:
            suppressed_skipped += weight
            continue
        by_status[status.value] = by_status.get(status.value, 0) + weight
        if status in (TerminalStatus.EXPANDED, TerminalStatus.PUBLISHED_LEAF):
            successful += weight
            if status is TerminalStatus.EXPANDED:
                internal_expanded += weight
            else:
                published += weight
        else:
            without_success += weight
    return {
        "issued_attempts": successful + without_success,
        "geometry_successful": successful,
        "internal_expanded": internal_expanded,
        "published_leaves": published,
        "attempted_without_success": without_success,
        "by_status": by_status,
        "suppressed_skipped": suppressed_skipped,
        "policy_screened": policy_screened,
        "deferred_unissued": deferred_unissued,
    }


def enumeration_digest(
    preflight_summary: Mapping[str, Any],
    sampling_summary: Mapping[str, Any],
    exclusions: Sequence[Mapping[str, Any]],
) -> str:
    """Digest the enumeration certificate (symbolic, geometry-free)."""
    canonical = json.dumps(
        {
            "preflight": dict(preflight_summary),
            "sampling": dict(sampling_summary),
            "exclusions": [dict(item) for item in exclusions],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_count_equations(
    records: Sequence[TargetRecord], *, raw: int, sampled: int
) -> tuple[bool, dict[str, Any]]:
    """Verify exact-tree count equations over accounted records.

    Leaf-level partition (unconditional exact trees only): every sampled
    leaf ends in exactly one leaf outcome -- published, terminal failure,
    leaf rejection -- or inside a deferred range (sampled-out complement or
    failed-parent subtree). Internal records (EXPANDED nodes and failed/
    rejected ancestors with deferred children) are structural, never leaf
    outcomes. Compressed ranges count arithmetically. Returns (ok, details).
    """
    children: set[str] = set()
    for record in records:
        if record.parent_target_id is not None:
            children.add(record.parent_target_id)
    published = failed = rejected = suppressed = 0
    deferred_sampled_out = deferred_parent = 0
    for record in records:
        weight, exact = leaf_weight(record)
        if not exact:
            return False, {"reason": "inexact conditional estimates present"}
        status = record.status
        if status is TerminalStatus.DEFERRED_SAMPLED_OUT:
            deferred_sampled_out += weight
            continue
        if status is TerminalStatus.DEFERRED_PARENT_FAILED:
            deferred_parent += weight
            continue
        if status is TerminalStatus.EXPANDED or record.target_id in children:
            continue  # internal structural record, not a leaf outcome
        if status is TerminalStatus.PUBLISHED_LEAF:
            published += weight
        elif status is TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY:
            suppressed += weight
        elif status in (
            TerminalStatus.FAILED_GEOMETRY,
            TerminalStatus.FAILED_NUMERICAL,
            TerminalStatus.FAILED_DRIFT,
            TerminalStatus.FAILED_ATOM_ORDER,
            TerminalStatus.UNSUPPORTED,
            TerminalStatus.UNRESOLVED,
        ):
            failed += weight
        elif status is TerminalStatus.REJECTED_BY_POLICY:
            rejected += weight
    attempted = published + failed + rejected + suppressed
    details = {
        "raw": raw,
        "sampled": sampled,
        "attempted": attempted,
        "published": published,
        "failed": failed,
        "rejected": rejected,
        "suppressed": suppressed,
        "deferred_sampled_out": deferred_sampled_out,
        "deferred_parent": deferred_parent,
    }
    ok = sampled + deferred_sampled_out == raw and attempted + deferred_parent == sampled
    details["ok"] = ok
    return ok, details


def verify_terminal_equations(records: Sequence[TargetRecord]) -> tuple[bool, str]:
    """Verify the target terminal-status equations over accounted records.

    Equations: every record carries exactly one terminal status (structural,
    enforced by the type); every DEFERRED_PARENT_FAILED record names a
    non-deferred failed ancestor; no EXPANDED node is published (publication
    is a disjoint leaf-id set checked by the engine); PUBLISHED_LEAF records
    carry complete keys.
    """
    by_id = {record.target_id: record for record in records}
    published_ids = {
        record.target_id for record in records if record.status is TerminalStatus.PUBLISHED_LEAF
    }
    failed_ids = {
        record.target_id
        for record in records
        if record.status
        in (
            TerminalStatus.FAILED_GEOMETRY,
            TerminalStatus.FAILED_NUMERICAL,
            TerminalStatus.FAILED_DRIFT,
            TerminalStatus.FAILED_ATOM_ORDER,
            TerminalStatus.UNSUPPORTED,
            TerminalStatus.UNRESOLVED,
            TerminalStatus.REJECTED_BY_POLICY,
        )
    }
    for record in records:
        if record.status is TerminalStatus.PUBLISHED_LEAF and record.complete_key is None:
            return False, f"published leaf {record.target_id} carries no complete key"
        if record.status is TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY:
            representative: Any = None
            for item in record.evidence:
                if isinstance(item, Mapping) and item.get("kind") == "suppression":
                    representative = item.get("representative_target")
            if representative not in published_ids:
                return False, (
                    f"suppressed record {record.target_id} names unrealized "
                    f"representative {representative!r}; suppression without a "
                    "realized representative is not a valid terminal outcome"
                )
        if record.status is TerminalStatus.DEFERRED_PARENT_FAILED:
            parent = record.parent_target_id
            if parent is None:
                return False, f"deferred record {record.target_id} names no failed parent"
            # Compressed range records name the failed parent target; the
            # parent itself may be summarized by id only when the ancestor
            # chain was pruned before expansion.
            if parent not in by_id and parent not in failed_ids:
                # Parent recorded inline in the reason chain is acceptable;
                # what is NOT acceptable is a deferred node with no failed
                # ancestor at all.
                return False, (f"deferred record {record.target_id} names unknown parent {parent}")
            if parent in by_id and parent not in failed_ids:
                return False, (
                    f"deferred record {record.target_id} names non-failed parent {parent}"
                )
    return True, "ok"


@dataclass(frozen=True, slots=True)
class RunCertificate:
    """Auditable certificate over explicit count layers and equations."""

    counts: Mapping[str, Any] = field(default_factory=FrozenDict)
    equations_ok: bool = False
    digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.counts, FrozenDict):
            object.__setattr__(self, "counts", FrozenDict(dict(self.counts)))
        if not isinstance(self.equations_ok, bool):
            raise ValueError("equations_ok must be a bool")
        if not isinstance(self.digest, str) or not self.digest:
            raise ValueError("digest must be a non-empty string")


def build_certificate(records: Sequence[TargetRecord], counts: Mapping[str, Any]) -> RunCertificate:
    """Build the run certificate: equation check plus digest over records."""
    equations_ok, _ = verify_terminal_equations(records)
    canonical = json.dumps(
        [
            {
                "target_id": record.target_id,
                "axis": record.axis,
                "ordinal": record.ordinal,
                "status": record.status.value,
                "reason": record.reason,
            }
            for record in records
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return RunCertificate(counts=FrozenDict(dict(counts)), equations_ok=equations_ok, digest=digest)


def stamp_production_results(
    leaves: Sequence[WorkingRealization],
    *,
    step_id: str,
    work_item_id: str,
    producer_digest: str,
    certificate_digest: str | None = None,
    upstream_state_id: str | None = None,
) -> ResultSet:
    """Stamp leaf state keys as production results via make_result_id.

    One ``confgen_state`` result per published leaf (value: the complete
    labeled StateKey canonical JSON); identity is producer-scoped through
    the producing work item's ``semantic_digest`` passed verbatim. The
    stable target ordinal travels in provenance metadata, distinct from
    orbit identity (orbits are separate records, never result fields).

    Optional ``certificate_digest`` / ``upstream_state_id`` attach as
    domain-semantic ``Provenance`` metadata (never in the StateKey value,
    never in structure metadata); the production ``result_id`` is
    untouched (provenance is outside ``make_result_id`` inputs). This
    lets the executor retire its local restamp: pass this run's
    certificate digest and the upstream chained ``confgen_state`` result
    id here directly.
    """
    from confflow.domain.result import Provenance, ResultSet, ScientificResult, make_result_id

    records: list[ScientificResult] = []
    for leaf in leaves:
        subject = leaf.structure.id
        result_id = make_result_id(
            step_id=step_id,
            work_item_id=work_item_id,
            kind="confgen_state",
            subject_structure_id=subject,
            discriminator=subject,
            program="confflow-confgen-v3",
            producer_digest=producer_digest,
        )
        provenance: Provenance | None = None
        if certificate_digest is not None or upstream_state_id is not None:
            meta: dict[str, Any] = {}
            if certificate_digest is not None:
                if not isinstance(certificate_digest, str) or not certificate_digest:
                    raise ValueError("certificate_digest must be a non-empty string")
                meta["certificate_digest"] = certificate_digest
            if upstream_state_id is not None:
                if not isinstance(upstream_state_id, str) or not upstream_state_id:
                    raise ValueError("upstream_state_id must be a non-empty string")
                meta["input_confgen_state"] = upstream_state_id
            provenance = Provenance(
                program="confflow-confgen-v3",
                step_id=step_id,
                work_item_id=work_item_id,
                metadata=FrozenDict(meta),
            )
        records.append(
            ScientificResult(
                kind="confgen_state",
                value=dict(leaf.state_key.to_dict()),
                subject_structure_id=subject,
                source_step_id=step_id,
                source_work_item_id=work_item_id,
                provenance=provenance,
                result_id=result_id,
                metadata=FrozenDict(
                    {
                        "target_ordinal": leaf.provenance.get("leaf_ordinal"),
                        "target_id": leaf.provenance.get("leaf_target_id"),
                    }
                ),
            )
        )
    return ResultSet(tuple(records))
