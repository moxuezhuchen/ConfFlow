#!/usr/bin/env python3

"""Artifact, result, immutable-container, and work-item identity tests.

Separates the three V4 identity axes: entity identity (ids), content identity
(digests), and logical identity (work-item logical keys), and pins the
portable-locator and canonical-container rules that protect them.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
import re
from collections.abc import Callable
from typing import Any

import pytest

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    CanonicalizationError,
    DomainError,
    FrozenDict,
    InvalidArtifactError,
    InvalidResultError,
    InvalidWorkItemError,
    LocatorKind,
    OnFailure,
    QuantityKind,
    ResourceRequest,
    ResultError,
    ResultSet,
    SchedulerPolicy,
    ScientificResult,
    StepResult,
    StepStatus,
    Unit,
    WorkItem,
    WorkItemInputs,
    WorkItemResult,
    WorkItemStatus,
    freeze_value,
    make_work_item_id,
    thaw_value,
    typed_digest,
    work_item_semantic_digest,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
CHECKSUM_AB = "sha256:" + "ab" * 32


@dataclasses.dataclass
class _MutablePayload:
    """A deliberately non-frozen dataclass for freeze rejection tests."""

    value: int = 1


def artifact(
    artifact_id: str = "a1",
    *,
    role: str = "checkpoint",
    checksum: str | None = None,
    subject_structure_id: str | None = None,
    producer_step_id: str | None = None,
) -> ArtifactRef:
    """Build a run-relative artifact reference."""
    return ArtifactRef(
        id=artifact_id,
        role=role,
        locator=ArtifactLocator.run_relative(f"steps/{artifact_id}/{artifact_id}.chk"),
        checksum=checksum,
        subject_structure_id=subject_structure_id,
        producer_step_id=producer_step_id,
    )


# ---------------------------------------------------------------------------
# ArtifactLocator
# ---------------------------------------------------------------------------


def test_run_relative_locator_accepts_portable_path() -> None:
    """The canonical locator kind is run-root-relative POSIX."""
    locator = ArtifactLocator.run_relative("steps/s_opt/out.chk")
    assert locator.kind is LocatorKind.RUN_RELATIVE
    assert locator.path == "steps/s_opt/out.chk"
    assert locator.uri is None
    assert locator.to_dict() == {
        "kind": "run_relative",
        "path": "steps/s_opt/out.chk",
        "uri": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/absolute/path", id="absolute"),
        pytest.param("steps/../escape", id="dotdot-segment"),
        pytest.param("steps/./out", id="dot-segment"),
        pytest.param("./out", id="leading-dot"),
        pytest.param("steps\\out.chk", id="backslash"),
        pytest.param("C:/steps/out.chk", id="drive-prefix"),
        pytest.param("steps/au\x00d", id="nul-byte"),
        pytest.param("steps//out.chk", id="empty-segment"),
        pytest.param(" steps/out.chk", id="surrounding-whitespace"),
        pytest.param("", id="empty"),
    ],
)
def test_run_relative_locator_rejects_non_portable_paths(path: str) -> None:
    """Absolute, traversing, or platform-specific paths are rejected."""
    with pytest.raises(InvalidArtifactError):
        ArtifactLocator.run_relative(path)


def test_external_uri_locator_accepts_absolute_uri() -> None:
    """External locators must be absolute URIs."""
    locator = ArtifactLocator.external_uri("https://example.com/imported/out.chk")
    assert locator.kind is LocatorKind.EXTERNAL_URI
    assert locator.uri == "https://example.com/imported/out.chk"
    assert locator.path is None


@pytest.mark.parametrize(
    "uri",
    [
        pytest.param("notauri", id="no-scheme"),
        pytest.param("://missing-scheme", id="empty-scheme"),
        pytest.param("with space:x y", id="whitespace"),
        pytest.param("", id="empty"),
    ],
)
def test_external_uri_locator_requires_scheme(uri: str) -> None:
    """Scheme-less URIs are rejected."""
    with pytest.raises(InvalidArtifactError):
        ArtifactLocator.external_uri(uri)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            {"kind": LocatorKind.RUN_RELATIVE, "uri": "https://example.com/x"},
            id="run-relative-with-uri",
        ),
        pytest.param(
            {"kind": LocatorKind.RUN_RELATIVE, "path": "x", "uri": "https://example.com/x"},
            id="run-relative-with-both",
        ),
        pytest.param(
            {"kind": LocatorKind.EXTERNAL_URI, "path": "x", "uri": "https://example.com/x"},
            id="external-uri-with-path",
        ),
        pytest.param(
            {"kind": LocatorKind.EXTERNAL_URI, "uri": "https://example.com/x", "path": "x"},
            id="external-uri-with-both",
        ),
        pytest.param({"kind": "run_relative", "path": "x"}, id="kind-is-not-enum"),
        pytest.param({"kind": LocatorKind.EXTERNAL_URI, "uri": None}, id="external-uri-missing"),
        pytest.param({"kind": LocatorKind.RUN_RELATIVE, "path": None}, id="path-missing"),
    ],
)
def test_locator_fields_must_match_kind(kwargs: dict[str, Any]) -> None:
    """A locator carries exactly the target field its kind declares."""
    with pytest.raises(InvalidArtifactError):
        ArtifactLocator(**kwargs)


# ---------------------------------------------------------------------------
# ArtifactRef and ArtifactSet
# ---------------------------------------------------------------------------


def test_checksum_is_normalized_to_lowercase() -> None:
    """Checksums are normalized to lowercase ``<algo>:<hex>``."""
    ref = artifact(checksum="SHA256:" + "AB" * 32)
    assert ref.checksum == "sha256:" + "ab" * 32


@pytest.mark.parametrize(
    "checksum",
    [
        pytest.param("deadbeef", id="missing-algorithm"),
        pytest.param("sha256:" + "a" * 31, id="hex-too-short"),
        pytest.param("sha256:" + "g" * 64, id="non-hex"),
        pytest.param("sha256:", id="empty-hex"),
        pytest.param("", id="empty"),
        pytest.param("sha256 " + "a" * 64, id="bare-space"),
    ],
)
def test_invalid_checksum_is_rejected(checksum: str) -> None:
    """Malformed checksums fail closed."""
    with pytest.raises(InvalidArtifactError, match="checksum"):
        artifact(checksum=checksum)


def test_digest_payload_excludes_locator() -> None:
    """Moving identical bytes must not invalidate reuse identity."""
    first = artifact("a1", checksum=CHECKSUM_AB, subject_structure_id="s1")
    payload = first.digest_payload()
    assert "locator" not in payload
    moved = ArtifactRef(
        id="a1",
        role="checkpoint",
        locator=ArtifactLocator.external_uri("https://example.com/moved.chk"),
        checksum=first.checksum,
        subject_structure_id="s1",
    )
    assert moved.digest_payload() == payload


def test_has_same_content_requires_equal_checksums() -> None:
    """Content identity is the verified checksum, never the reference id."""
    first = artifact("a1", checksum=CHECKSUM_AB)
    same_bytes = artifact("a2", role="native_output", checksum=CHECKSUM_AB)
    other_bytes = artifact("a3", checksum="sha256:" + "cd" * 32)
    unverified = artifact("a4")
    assert first.has_same_content(same_bytes) is True
    assert first.has_same_content(other_bytes) is False
    assert first.has_same_content(unverified) is False
    assert unverified.has_same_content(unverified) is False


def test_artifact_set_lookup_helpers() -> None:
    """Role, subject, and producer sub-sets preserve order; by_id raises."""
    first = artifact("a1", role="checkpoint", subject_structure_id="s1", producer_step_id="p1")
    second = artifact("a2", role="native_output", subject_structure_id="s1", producer_step_id="p2")
    third = artifact("a3", role="checkpoint", subject_structure_id="s2", producer_step_id="p1")
    artifacts = ArtifactSet.of(first, second, third)
    assert artifacts.ids == ("a1", "a2", "a3")
    assert artifacts.by_role("checkpoint").ids == ("a1", "a3")
    assert artifacts.by_subject("s1").ids == ("a1", "a2")
    assert artifacts.by_producer("p1").ids == ("a1", "a3")
    assert artifacts.by_id("a2") is second
    assert artifacts.get("missing") is None
    with pytest.raises(KeyError):
        artifacts.by_id("missing")
    with pytest.raises(InvalidArtifactError, match="duplicate artifact id"):
        ArtifactSet.of(first, artifact("a1"))


# ---------------------------------------------------------------------------
# ScientificResult and ResultSet
# ---------------------------------------------------------------------------


def energy(
    value: Any = 1.0,
    *,
    subject_structure_id: str | None = None,
) -> ScientificResult:
    """Build a canonical Hartree energy result."""
    return ScientificResult(
        kind="energy",
        value=value,
        unit=Unit.HARTREE,
        subject_structure_id=subject_structure_id,
    )


def test_quantity_requires_explicit_unit() -> None:
    """Declaring a quantity without a unit is rejected."""
    with pytest.raises(InvalidResultError, match="requires an explicit unit"):
        ScientificResult(kind="energy", value=1.0, quantity=QuantityKind.ENERGY)


def test_unit_implies_quantity() -> None:
    """A declared unit is the single source of the quantity kind."""
    result = energy()
    assert result.quantity is QuantityKind.ENERGY
    assert result.to_dict()["unit"] == "hartree"
    assert result.to_dict()["quantity"] == "energy"


def test_mismatched_unit_and_quantity_is_rejected() -> None:
    """A unit must measure its declared quantity."""
    with pytest.raises(InvalidResultError, match="does not measure"):
        ScientificResult(
            kind="energy",
            value=1.0,
            unit=Unit.HARTREE,
            quantity=QuantityKind.FREQUENCY,
        )
    with pytest.raises(InvalidResultError, match="unit must be a Unit"):
        ScientificResult(kind="energy", value=1.0, unit="hartree")


def test_result_value_is_normalized_to_plain_canonical_data() -> None:
    """Tuples become lists; nested containers are normalized recursively."""
    result = ScientificResult(kind="frequencies", value=(1.0, (2.0, 3.0)), unit=Unit.CM_INVERSE)
    assert result.value == [1.0, [2.0, 3.0]]


def test_value_digest_is_stable_and_value_sensitive() -> None:
    """Value digests separate identical rebuilds from changed values or units."""
    base = energy(1.5)
    assert base.value_digest == energy(1.5).value_digest
    assert base.value_digest != energy(2.5).value_digest
    assert (
        base.value_digest
        != ScientificResult(
            kind="energy",
            value=1.5,
            unit=Unit.KILOCALORIE_PER_MOLE,
        ).value_digest
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", base.value_digest)


def test_result_set_lookup_helpers() -> None:
    """Kind and subject filters plus first() are positional and explicit."""
    first = energy(1.0, subject_structure_id="s1")
    second = energy(2.0, subject_structure_id="s2")
    frequency = ScientificResult(
        kind="frequency",
        value=[100.0],
        unit=Unit.CM_INVERSE,
        subject_structure_id="s1",
    )
    results = ResultSet.of(first, second, frequency)
    assert results.by_kind("energy").results == (first, second)
    assert results.by_subject("s1").results == (first, frequency)
    assert results.first("energy") is first
    assert results.first("energy", subject_structure_id="s2") is second
    assert results.first("frequency", subject_structure_id="s2") is None
    assert results.first("missing") is None
    assert results.is_empty is False
    assert results[0] is first
    assert results[1:].results == (second, frequency)


# ---------------------------------------------------------------------------
# FrozenDict and canonicalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: FrozenDict({"a": {1, 2}}), id="set-value"),
        pytest.param(lambda: FrozenDict({"a": object()}), id="opaque-object"),
        pytest.param(lambda: FrozenDict({"a": _MutablePayload()}), id="mutable-dataclass"),
        pytest.param(lambda: FrozenDict({1: "x"}), id="non-string-key"),
        pytest.param(lambda: FrozenDict({"a": float("nan")}), id="nan-float"),
        pytest.param(lambda: FrozenDict({"a": float("inf")}), id="infinite-float"),
    ],
)
def test_frozen_dict_rejects_non_canonical_values(factory: Callable[[], FrozenDict]) -> None:
    """Frozen storage is strict: no mutable or non-deterministic leaves."""
    with pytest.raises(DomainError):
        factory()


def test_frozen_dict_freezes_nested_containers() -> None:
    """Lists become tuples and mappings become FrozenDict recursively."""
    frozen = FrozenDict({"items": [1, {"inner": (2, 3)}]})
    assert isinstance(frozen["items"], tuple)
    assert frozen["items"][0] == 1
    assert isinstance(frozen["items"][1], FrozenDict)
    assert frozen["items"][1]["inner"] == (2, 3)


def test_frozen_dict_equality_and_hash_ignore_container_type() -> None:
    """Canonical bytes decide equality and hash stability."""
    frozen = FrozenDict({"a": [1, 2]})
    assert frozen == {"a": [1, 2]}
    assert frozen == FrozenDict({"a": (1, 2)})
    assert frozen != {"a": [2, 1]}
    assert hash(frozen) == hash(FrozenDict({"a": (1, 2)}))


def test_thaw_returns_independent_plain_data() -> None:
    """thaw() copies; mutating it never mutates the frozen source."""
    frozen = FrozenDict({"a": [1, 2], "b": {"c": [3]}})
    thawed = frozen.thaw()
    assert thawed == {"a": [1, 2], "b": {"c": [3]}}
    assert type(thawed["b"]) is dict
    thawed["a"].append(99)
    thawed["b"]["c"].append(99)
    assert frozen["a"] == (1, 2)
    assert frozen["b"]["c"] == (3,)


def test_freeze_value_maps_enum_to_its_value() -> None:
    """Enums freeze to their canonical scalar value."""
    assert freeze_value(Unit.HARTREE) == "hartree"
    assert freeze_value(Unit.HARTREE) == Unit.HARTREE.value
    assert freeze_value([OnFailure.FAIL_FAST]) == ("fail_fast",)
    assert thaw_value((1, 2)) == [1, 2]


def test_typed_digest_separates_domain_kinds() -> None:
    """The digest kind marker prevents cross-type digest confusion."""
    payload = {"x": 1.0}
    assert typed_digest("kind.a", payload) != typed_digest("kind.b", payload)
    assert typed_digest("kind.a", payload) == typed_digest("kind.a", {"x": 1.0})


def test_typed_digest_format_is_sha256_hex() -> None:
    """Digests are ``sha256:<64 lowercase hex>``."""
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", typed_digest("kind.a", {"x": 1}))


def test_typed_digest_rejects_non_finite_numbers() -> None:
    """Non-finite floats cannot be canonicalized."""
    with pytest.raises(CanonicalizationError):
        typed_digest("kind.a", {"x": math.nan})
    with pytest.raises(CanonicalizationError):
        typed_digest("kind.a", {"x": math.inf})


# ---------------------------------------------------------------------------
# WorkItem identity
# ---------------------------------------------------------------------------


def resolved_resources() -> ResourceRequest:
    """Return a fully resolved per-item resource request."""
    return ResourceRequest(cores_per_item=2, memory_per_item_bytes=1024**3)


def work_item(**overrides: Any) -> WorkItem:
    """Build a valid deterministic work item with overridable fields."""
    fields: dict[str, Any] = {
        "id": make_work_item_id("s1:a"),
        "logical_key": "s1:a",
        "step_id": "s1",
        "named_inputs": WorkItemInputs(),
        "resources": resolved_resources(),
        "semantic_digest": DIGEST_A,
    }
    fields.update(overrides)
    return WorkItem(**fields)


def test_make_work_item_id_is_deterministic_address() -> None:
    """The instance address is derived from the logical key."""
    assert make_work_item_id("s1:a") == "wi:s1:a"


def test_work_item_id_must_match_logical_key() -> None:
    """A hand-minted id that disagrees with the logical key is rejected."""
    with pytest.raises(InvalidWorkItemError, match="deterministic address"):
        work_item(id="wi:s1:b")


def test_work_item_resources_must_be_resolved() -> None:
    """Assembly cannot persist a partially resolved resource request."""
    with pytest.raises(InvalidWorkItemError, match="fully resolved"):
        work_item(resources=ResourceRequest(cores_per_item=2))


@pytest.mark.parametrize(
    "semantic_digest",
    [
        pytest.param("not-a-digest", id="garbage"),
        pytest.param("sha256:" + "A" * 64, id="uppercase-hex"),
        pytest.param("sha256:" + "a" * 63, id="short-hex"),
        pytest.param("md5:" + "a" * 64, id="wrong-algorithm"),
    ],
)
def test_work_item_semantic_digest_format_is_validated(semantic_digest: str) -> None:
    """Only ``sha256:<lowercase hex>`` digests are accepted."""
    with pytest.raises(InvalidWorkItemError, match="sha256"):
        work_item(semantic_digest=semantic_digest)


def test_work_item_accepts_deterministic_identity() -> None:
    """A well-formed item keeps its deterministic address."""
    item = work_item()
    assert item.id == "wi:s1:a"
    assert item.logical_key == "s1:a"
    assert item.resources.is_resolved is True
    assert item.ordinal == 0


# ---------------------------------------------------------------------------
# work_item_semantic_digest
# ---------------------------------------------------------------------------


def semantic_digest(**overrides: Any) -> str:
    """Compute a work-item semantic digest with overridable inputs."""
    payload: dict[str, Any] = {
        "step_semantic_digest": DIGEST_A,
        "input_payload": {"structure": {"charge": 0, "geometry_digest": DIGEST_B}},
        "resources": resolved_resources(),
    }
    payload.update(overrides)
    return work_item_semantic_digest(**payload)


def test_semantic_digest_is_deterministic() -> None:
    """Identical scientific inputs produce identical digests."""
    assert semantic_digest() == semantic_digest()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"step_semantic_digest": DIGEST_B}, id="step-digest"),
        pytest.param({"input_payload": {"structure": {"charge": 1}}}, id="input-payload"),
        pytest.param(
            {"resources": ResourceRequest(cores_per_item=4, memory_per_item_bytes=1024**3)},
            id="cores-per-item",
        ),
        pytest.param(
            {"resources": ResourceRequest(cores_per_item=2, memory_per_item_bytes=2 * 1024**3)},
            id="memory-per-item",
        ),
    ],
)
def test_semantic_digest_changes_with_scientific_inputs(overrides: dict[str, Any]) -> None:
    """Every scientific input participates in reuse identity."""
    assert semantic_digest(**overrides) != semantic_digest()


def test_semantic_digest_axes_are_science_only() -> None:
    """Logical and scheduler addresses never enter reuse identity by design."""
    parameters = inspect.signature(work_item_semantic_digest).parameters
    assert set(parameters) == {"step_semantic_digest", "input_payload", "resources"}
    assert "logical_key" not in parameters
    assert "scheduler" not in parameters
    before = semantic_digest()
    SchedulerPolicy(max_parallel_items=8, on_failure=OnFailure.FAIL_FAST)
    assert semantic_digest() == before


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------


def completed_item(item_id: str = "wi:s1:a") -> WorkItemResult:
    """Return a completed work-item result."""
    return WorkItemResult(work_item_id=item_id, status=WorkItemStatus.COMPLETED)


def failed_item(item_id: str = "wi:s1:b") -> WorkItemResult:
    """Return a failed work-item result carrying a structured error."""
    return WorkItemResult(
        work_item_id=item_id,
        status=WorkItemStatus.FAILED,
        error=ResultError(code="calculation_failed", message="native program failed"),
    )


def test_completed_step_result_rejects_failed_item() -> None:
    """A completed step cannot contain a failed item."""
    with pytest.raises(DomainError, match="every work item to be completed"):
        StepResult(
            step_id="s1",
            status=StepStatus.COMPLETED,
            item_results=(failed_item(),),
        )


def test_partial_step_result_requires_mixed_items() -> None:
    """Partial means at least one accepted and at least one non-completed item."""
    with pytest.raises(DomainError, match="at least one completed"):
        StepResult(
            step_id="s1",
            status=StepStatus.PARTIAL,
            item_results=(failed_item(),),
        )
    with pytest.raises(DomainError, match="must not have only completed"):
        StepResult(
            step_id="s1",
            status=StepStatus.PARTIAL,
            item_results=(completed_item(),),
        )
    partial = StepResult(
        step_id="s1",
        status=StepStatus.PARTIAL,
        item_results=(completed_item(), failed_item()),
    )
    assert partial.work_item_ids == ("wi:s1:a", "wi:s1:b")


def test_step_result_rejects_duplicate_item_results() -> None:
    """Accepted item results are unique by work-item id."""
    with pytest.raises(DomainError, match="duplicate work item result"):
        StepResult(
            step_id="s1",
            status=StepStatus.COMPLETED,
            item_results=(completed_item(), completed_item()),
        )


def test_step_result_has_no_output_path_projection() -> None:
    """Step results carry typed content, never export file paths."""
    result = StepResult(step_id="s1", status=StepStatus.COMPLETED)
    assert not hasattr(result, "output_path")
    assert not hasattr(result, "output_xyz")
    assert "output_path" not in result.to_dict()
    assert "output_xyz" not in result.to_dict()
