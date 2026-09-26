#!/usr/bin/env python3

"""V4-5 named-structure resolution and compatibility tests.

Covers the full pairing matrix of :mod:`confflow.execution.named_structures`:

- QST2 (reactant/product) and QST3 (plus guess) happy paths;
- missing/duplicate slot entries with typed error codes;
- group-key agreement, including ``None`` groups (never inferred);
- charge/multiplicity compatibility, including mixed known/unknown;
- ``qst_logical_key`` format and TS-output lineage delegation.

Every failure is asserted as a typed :class:`NamedStructureError` with its
machine-readable code; the module under test has no renderer, so typed
error assertions alone prove failures precede any rendering.
"""

from __future__ import annotations

import pytest

from confflow.domain import FrozenDict, ResourceRequest, StructureRecord, StructureSet
from confflow.domain.errors import DomainError
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.named_structures import (
    NamedReactionInputs,
    NamedStructureError,
    qst_logical_key,
    resolve_named_inputs,
    ts_output_lineage,
    validate_named_compatibility,
)

THREE_ATOMS = ("O", "H", "H")


def _record(
    record_id: str,
    *,
    atoms: tuple[str, ...] = THREE_ATOMS,
    charge: int | None = 0,
    multiplicity: int | None = 1,
    group_key: str | None = "g1",
    lineage_root_id: str | None = None,
) -> StructureRecord:
    """Build a deterministic structure record for one slot."""
    coords = tuple((float(index), 0.0, 0.0) for index in range(len(atoms)))
    return StructureRecord(
        id=record_id,
        atoms=atoms,
        coordinates=coords,
        charge=charge,
        multiplicity=multiplicity,
        group_key=group_key,
        lineage_root_id=lineage_root_id,
    )


def _item(
    ports: dict[str, StructureSet],
    *,
    logical_key: str = "qst:g1",
) -> WorkItem:
    """Build a work item carrying *ports* as named structure inputs."""
    step_id = logical_key.split(":")[0]
    return WorkItem(
        id=make_work_item_id(logical_key),
        logical_key=logical_key,
        step_id=step_id,
        named_inputs=WorkItemInputs(structures=FrozenDict(ports)),
        resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=1024**3),
        semantic_digest="sha256:" + "a" * 64,
    )


def _qst2_ports(**overrides: object) -> dict[str, StructureSet]:
    """Build reactant/product ports sharing group ``g1``."""
    ports: dict[str, StructureSet] = {
        "reactant": StructureSet.of(_record("r1")),
        "product": StructureSet.of(_record("p1")),
    }
    ports.update(overrides)  # type: ignore[arg-type]
    return ports


class TestResolveHappyPaths:
    """QST2 and QST3 resolution return typed inputs with the shared key."""

    def test_qst2_ok(self) -> None:
        resolved = resolve_named_inputs(_item(_qst2_ports()), require_guess=False)
        assert isinstance(resolved, NamedReactionInputs)
        assert resolved.reactant.id == "r1"
        assert resolved.product.id == "p1"
        assert resolved.guess is None
        assert resolved.group_key == "g1"

    def test_qst3_ok(self) -> None:
        ports = _qst2_ports(guess=StructureSet.of(_record("t1")))
        resolved = resolve_named_inputs(_item(ports), require_guess=True)
        assert resolved.guess is not None
        assert resolved.guess.id == "t1"
        assert resolved.group_key == "g1"

    def test_optional_guess_present_without_requirement(self) -> None:
        ports = _qst2_ports(guess=StructureSet.of(_record("t1")))
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        assert resolved.guess is not None
        assert resolved.guess.id == "t1"

    def test_errors_are_domain_errors(self) -> None:
        assert issubclass(NamedStructureError, DomainError)


class TestResolveCardinality:
    """Zero structures miss; more than one is ambiguous."""

    def test_missing_reactant(self) -> None:
        item = _item({"product": StructureSet.of(_record("p1"))})
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(item, require_guess=False)
        assert exc.value.code == "named_structure_missing"

    def test_missing_product(self) -> None:
        item = _item({"reactant": StructureSet.of(_record("r1"))})
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(item, require_guess=False)
        assert exc.value.code == "named_structure_missing"

    def test_missing_guess_when_required(self) -> None:
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(_qst2_ports()), require_guess=True)
        assert exc.value.code == "named_structure_missing"

    def test_missing_guess_when_optional_is_none(self) -> None:
        resolved = resolve_named_inputs(_item(_qst2_ports()), require_guess=False)
        assert resolved.guess is None

    def test_duplicate_reactant_is_ambiguous(self) -> None:
        ports = _qst2_ports(
            reactant=StructureSet.of(_record("r1"), _record("r2")),
        )
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=False)
        assert exc.value.code == "named_structure_ambiguous"

    def test_duplicate_product_is_ambiguous(self) -> None:
        ports = _qst2_ports(
            product=StructureSet.of(_record("p1"), _record("p2")),
        )
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=False)
        assert exc.value.code == "named_structure_ambiguous"

    def test_duplicate_guess_is_ambiguous_when_required(self) -> None:
        ports = _qst2_ports(guess=StructureSet.of(_record("t1"), _record("t2")))
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=True)
        assert exc.value.code == "named_structure_ambiguous"

    def test_duplicate_guess_is_ambiguous_when_optional(self) -> None:
        ports = _qst2_ports(guess=StructureSet.of(_record("t1"), _record("t2")))
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=False)
        assert exc.value.code == "named_structure_ambiguous"


class TestResolveGroupKey:
    """Present slots must share one non-empty group key; never inferred."""

    def test_group_mismatch_reactant_product(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", group_key="g1")),
            "product": StructureSet.of(_record("p1", group_key="g2")),
        }
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=False)
        assert exc.value.code == "named_group_mismatch"

    def test_group_mismatch_includes_guess(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", group_key="g1")),
            "product": StructureSet.of(_record("p1", group_key="g1")),
            "guess": StructureSet.of(_record("t1", group_key="g9")),
        }
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=True)
        assert exc.value.code == "named_group_mismatch"

    def test_none_group_mismatches(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", group_key="g1")),
            "product": StructureSet.of(_record("p1", group_key=None)),
        }
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=False)
        assert exc.value.code == "named_group_mismatch"

    def test_all_none_groups_mismatch(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", group_key=None)),
            "product": StructureSet.of(_record("p1", group_key=None)),
        }
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=False)
        assert exc.value.code == "named_group_mismatch"

    def test_none_guess_group_mismatches_qst3(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", group_key="g1")),
            "product": StructureSet.of(_record("p1", group_key="g1")),
            "guess": StructureSet.of(_record("t1", group_key=None)),
        }
        with pytest.raises(NamedStructureError) as exc:
            resolve_named_inputs(_item(ports), require_guess=True)
        assert exc.value.code == "named_group_mismatch"


class TestValidateCompatibility:
    """Charge/multiplicity must agree; partial knowledge fails closed."""

    def test_equal_charge_and_multiplicity(self) -> None:
        resolved = resolve_named_inputs(_item(_qst2_ports()), require_guess=False)
        assert validate_named_compatibility(resolved) == (0, 1)

    def test_all_none_returns_none_pair(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", charge=None, multiplicity=None)),
            "product": StructureSet.of(_record("p1", charge=None, multiplicity=None)),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        assert validate_named_compatibility(resolved) == (None, None)

    def test_mixed_none_charge_fails_closed(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", charge=0)),
            "product": StructureSet.of(_record("p1", charge=None)),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        with pytest.raises(NamedStructureError) as exc:
            validate_named_compatibility(resolved)
        assert exc.value.code == "named_compatibility_error"

    def test_mixed_none_multiplicity_fails_closed(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", multiplicity=1)),
            "product": StructureSet.of(_record("p1", multiplicity=None)),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        with pytest.raises(NamedStructureError) as exc:
            validate_named_compatibility(resolved)
        assert exc.value.code == "named_compatibility_error"

    def test_unequal_charge(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", charge=0)),
            "product": StructureSet.of(_record("p1", charge=1)),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        with pytest.raises(NamedStructureError) as exc:
            validate_named_compatibility(resolved)
        assert exc.value.code == "named_compatibility_error"

    def test_unequal_multiplicity(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", multiplicity=1)),
            "product": StructureSet.of(_record("p1", multiplicity=2)),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        with pytest.raises(NamedStructureError) as exc:
            validate_named_compatibility(resolved)
        assert exc.value.code == "named_compatibility_error"

    def test_guess_participates_in_compatibility(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", charge=0)),
            "product": StructureSet.of(_record("p1", charge=0)),
            "guess": StructureSet.of(_record("t1", charge=1)),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=True)
        with pytest.raises(NamedStructureError) as exc:
            validate_named_compatibility(resolved)
        assert exc.value.code == "named_compatibility_error"


class TestLogicalKeyAndLineage:
    """Logical-key format is pinned; lineage follows multi-parent rules."""

    def test_qst_logical_key_format(self) -> None:
        assert qst_logical_key("qst", "g1") == "qst:g1"

    @pytest.mark.parametrize("step_id, group_key", [("", "g1"), ("qst", ""), (None, "g1")])
    def test_qst_logical_key_rejects_empty(self, step_id: object, group_key: object) -> None:
        with pytest.raises(ValueError):
            qst_logical_key(step_id, group_key)  # type: ignore[arg-type]

    def test_ts_output_lineage_slot_order_qst2(self) -> None:
        resolved = resolve_named_inputs(_item(_qst2_ports()), require_guess=False)
        parent_ids, _, group_key = ts_output_lineage(resolved)
        assert parent_ids == ("r1", "p1")
        assert group_key == "g1"

    def test_ts_output_lineage_includes_guess(self) -> None:
        ports = _qst2_ports(guess=StructureSet.of(_record("t1")))
        resolved = resolve_named_inputs(_item(ports), require_guess=True)
        parent_ids, _, group_key = ts_output_lineage(resolved)
        assert parent_ids == ("r1", "p1", "t1")
        assert group_key == "g1"

    def test_ts_output_lineage_propagates_shared_root(self) -> None:
        ports = {
            "reactant": StructureSet.of(_record("r1", lineage_root_id="root")),
            "product": StructureSet.of(_record("p1", lineage_root_id="root")),
        }
        resolved = resolve_named_inputs(_item(ports), require_guess=False)
        _, lineage_root, _ = ts_output_lineage(resolved)
        assert lineage_root == "root"

    def test_ts_output_lineage_divergent_roots_yield_none(self) -> None:
        resolved = resolve_named_inputs(_item(_qst2_ports()), require_guess=False)
        _, lineage_root, _ = ts_output_lineage(resolved)
        assert lineage_root is None
