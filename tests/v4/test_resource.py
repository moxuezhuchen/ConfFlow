#!/usr/bin/env python3

"""Resource request and scheduler policy tests for the V4 domain.

Pins the binary memory-suffix parser, the scientific/operational split, and
the single-source ``to_dict`` shapes consumed by semantic digests.
"""

from __future__ import annotations

import dataclasses

import pytest

from confflow.domain import (
    InvalidResourceError,
    OnFailure,
    ResourceRequest,
    SchedulerPolicy,
    parse_memory_bytes,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("16GB", 16 * 1024**3, id="16gb"),
        pytest.param("16GiB", 16 * 1024**3, id="16gib"),
        pytest.param("16 GB", 16 * 1024**3, id="16gb-whitespace"),
        pytest.param("16  GiB", 16 * 1024**3, id="16gib-whitespace"),
        pytest.param("512MB", 512 * 1024**2, id="512mb"),
        pytest.param("512MiB", 512 * 1024**2, id="512mib"),
        pytest.param("1024KB", 1024 * 1024, id="1024kb"),
        pytest.param("1B", 1, id="one-byte"),
        pytest.param("2048", 2048, id="bare-number"),
        pytest.param("1.5GB", int(1.5 * 1024**3), id="decimal-gb"),
        pytest.param(2048, 2048, id="int-passthrough"),
        pytest.param(0, 0, id="zero-passthrough"),
        pytest.param(3.9, 3, id="float-truncation"),
    ],
)
def test_parse_memory_bytes_accepts_canonical_forms(value: object, expected: int) -> None:
    """Suffixes are binary; integer/float byte counts pass through."""
    assert parse_memory_bytes(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(-1, id="negative-int"),
        pytest.param(-1.5, id="negative-float"),
        pytest.param(True, id="bool"),
        pytest.param(False, id="false-bool"),
        pytest.param("16XB", id="unknown-suffix"),
        pytest.param("16G", id="single-letter-gigabyte"),
        pytest.param("16M", id="single-letter-megabyte"),
        pytest.param("16Ki", id="incomplete-iec"),
        pytest.param("GB", id="suffix-only"),
        pytest.param("", id="empty-string"),
        pytest.param("16 GB extra", id="trailing-junk"),
        pytest.param(None, id="none"),
        pytest.param([16], id="list"),
    ],
)
def test_parse_memory_bytes_rejects_malformed_values(value: object) -> None:
    """Malformed or negative amounts fail closed with a typed error."""
    with pytest.raises(InvalidResourceError):
        parse_memory_bytes(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"cores_per_item": 0}, id="zero-cores"),
        pytest.param({"cores_per_item": -2}, id="negative-cores"),
        pytest.param({"cores_per_item": True}, id="bool-cores"),
        pytest.param({"cores_per_item": 1.5}, id="float-cores"),
        pytest.param({"memory_per_item_bytes": 0}, id="zero-memory"),
        pytest.param({"memory_per_item_bytes": -1}, id="negative-memory"),
        pytest.param({"memory_per_item_bytes": True}, id="bool-memory"),
        pytest.param({"memory_per_item_bytes": 1.5}, id="float-memory"),
    ],
)
def test_resource_request_rejects_out_of_range_values(kwargs: dict[str, object]) -> None:
    """Cores are at least one; memory is a positive byte count."""
    with pytest.raises(InvalidResourceError):
        ResourceRequest(**kwargs)  # type: ignore[arg-type]


def test_resource_request_accepts_declared_values() -> None:
    """A fully resolved request exposes both dimensions."""
    request = ResourceRequest(cores_per_item=4, memory_per_item_bytes=1024**3)
    assert request.cores_per_item == 4
    assert request.memory_per_item_bytes == 1024**3
    assert request.memory_per_item == 1024**3
    assert request.is_resolved is True
    assert ResourceRequest().is_resolved is False


def test_resource_request_from_values_parses_textual_memory() -> None:
    """``from_values`` is the textual boundary into canonical bytes."""
    request = ResourceRequest.from_values(cores_per_item=8, memory_per_item="512MiB")
    assert request == ResourceRequest(cores_per_item=8, memory_per_item_bytes=512 * 1024**2)
    assert ResourceRequest.from_values(cores_per_item=None, memory_per_item=None) == (
        ResourceRequest()
    )
    with pytest.raises(InvalidResourceError):
        ResourceRequest.from_values(cores_per_item=1, memory_per_item="nope")


def test_resource_request_with_defaults_fills_only_unset_fields() -> None:
    """Defaults never overwrite an explicitly declared dimension."""
    partial = ResourceRequest(memory_per_item_bytes=2 * 1024**3)
    defaults = ResourceRequest(cores_per_item=8, memory_per_item_bytes=1024**3)
    merged = partial.with_defaults(defaults)
    assert merged == ResourceRequest(cores_per_item=8, memory_per_item_bytes=2 * 1024**3)
    assert partial.memory_per_item_bytes == 2 * 1024**3
    assert defaults == ResourceRequest(cores_per_item=8, memory_per_item_bytes=1024**3)
    resolved = ResourceRequest(cores_per_item=2, memory_per_item_bytes=1024**3)
    assert resolved.with_defaults(defaults) == resolved


def test_resource_request_to_dict_shape() -> None:
    """The digest payload carries exactly the two scientific dimensions."""
    request = ResourceRequest(cores_per_item=4, memory_per_item_bytes=1024**3)
    assert request.to_dict() == {
        "cores_per_item": 4,
        "memory_per_item_bytes": 1024**3,
    }
    assert "max_parallel_items" not in request.to_dict()


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"max_parallel_items": 0}, id="zero-width"),
        pytest.param({"max_parallel_items": -1}, id="negative-width"),
        pytest.param({"max_parallel_items": True}, id="bool-width"),
        pytest.param({"max_parallel_items": 2.5}, id="float-width"),
        pytest.param({"on_failure": "fail_fast"}, id="string-failure"),
    ],
)
def test_scheduler_policy_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    """Concurrency is at least one; failure behavior is an enum."""
    with pytest.raises(InvalidResourceError):
        SchedulerPolicy(**kwargs)  # type: ignore[arg-type]


def test_scheduler_policy_round_trip() -> None:
    """Scheduler policy serializes to operational fields only."""
    policy = SchedulerPolicy(max_parallel_items=4, on_failure=OnFailure.FAIL_FAST)
    assert policy.is_resolved is True
    assert policy.to_dict() == {"max_parallel_items": 4, "on_failure": "fail_fast"}
    assert SchedulerPolicy().to_dict() == {"max_parallel_items": None, "on_failure": None}
    assert [member.value for member in OnFailure] == ["continue", "fail_fast"]


def test_scheduler_policy_with_defaults_fills_only_unset_fields() -> None:
    """Scheduler defaults follow the same fill-unset rule as resources."""
    defaults = SchedulerPolicy(max_parallel_items=8, on_failure=OnFailure.CONTINUE)
    partial = SchedulerPolicy(on_failure=OnFailure.FAIL_FAST)
    merged = partial.with_defaults(defaults)
    assert merged == SchedulerPolicy(max_parallel_items=8, on_failure=OnFailure.FAIL_FAST)


def test_resources_and_scheduler_are_independent_types() -> None:
    """Scientific resources and operational scheduling never mix fields."""
    assert not hasattr(SchedulerPolicy(), "memory_per_item_bytes")
    assert not hasattr(SchedulerPolicy(), "memory_per_item")
    assert not hasattr(SchedulerPolicy(), "cores_per_item")
    assert not hasattr(ResourceRequest(), "max_parallel_items")
    assert not hasattr(ResourceRequest(), "on_failure")
    resource_fields = {field.name for field in dataclasses.fields(ResourceRequest)}
    scheduler_fields = {field.name for field in dataclasses.fields(SchedulerPolicy)}
    assert resource_fields == {"cores_per_item", "memory_per_item_bytes"}
    assert scheduler_fields == {"max_parallel_items", "on_failure"}
    assert resource_fields.isdisjoint(scheduler_fields)
