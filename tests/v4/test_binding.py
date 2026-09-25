#!/usr/bin/env python3

"""Binding vocabulary tests for the V4 dataflow model.

Pins source-kind rules, selector arity, self-reference rejection, and the
single-binding-per-target-port invariant.
"""

from __future__ import annotations

import pytest

from confflow.domain import (
    Binding,
    BindingSet,
    BindingSource,
    Cardinality,
    InvalidBindingError,
    Pairing,
    PartialConsumption,
    PortKind,
    PortSelector,
    SelectorKind,
    SourceKind,
)


def run_source(name: str = "structures", **kwargs: object) -> BindingSource:
    """Build a run-input source with optional overrides."""
    fields: dict[str, object] = {"kind": SourceKind.RUN_INPUT, "port": name}
    fields.update(kwargs)
    return BindingSource(**fields)  # type: ignore[arg-type]


def binding(
    target_step_id: str = "s_opt",
    target_port: str = "structure",
    *,
    source_step_id: str = "s_prep",
    source_port: str = "structures",
) -> Binding:
    """Build a step-output binding with overridable endpoints."""
    return Binding(
        target_step_id=target_step_id,
        target_port=target_port,
        source=BindingSource.step_output(source_step_id, source_port),
    )


def test_run_input_source_must_not_carry_step_id() -> None:
    """Run inputs are named, never produced by a step."""
    with pytest.raises(InvalidBindingError, match="must not carry a step id"):
        BindingSource(SourceKind.RUN_INPUT, "structures", step_id="s_prep")
    source = BindingSource.run_input("structures")
    assert source.kind is SourceKind.RUN_INPUT
    assert source.step_id is None
    assert source.selector.kind is SelectorKind.ALL


def test_step_output_source_requires_step_id() -> None:
    """Step outputs must name their producer."""
    with pytest.raises(InvalidBindingError, match="requires a step id"):
        BindingSource(SourceKind.STEP_OUTPUT, "structures")
    source = BindingSource.step_output("s_prep", "structures")
    assert source.step_id == "s_prep"
    assert source.port == "structures"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(SourceKind.RUN_INPUT, id="run-input"),
        pytest.param(SourceKind.STEP_OUTPUT, id="step-output"),
    ],
)
def test_empty_source_port_is_rejected(source: SourceKind) -> None:
    """Port names must be non-empty."""
    with pytest.raises(InvalidBindingError, match="source port"):
        BindingSource(source, "")


def test_all_selector_is_clean() -> None:
    """The unrestricted selector carries neither role nor ids."""
    selector = PortSelector.all()
    assert selector.kind is SelectorKind.ALL
    assert selector.role is None
    assert selector.ids == ()
    assert selector.to_dict() == {"kind": "all", "role": None, "ids": []}


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({"kind": SelectorKind.ROLE}, "requires a role", id="role-missing"),
        pytest.param({"kind": SelectorKind.ROLE, "role": ""}, "non-empty", id="role-empty"),
        pytest.param(
            {"kind": SelectorKind.ROLE, "role": "checkpoint", "ids": ("a1",)},
            "must not carry ids",
            id="role-with-ids",
        ),
        pytest.param({"kind": SelectorKind.IDS}, "requires at least one id", id="ids-missing"),
        pytest.param(
            {"kind": SelectorKind.IDS, "role": "checkpoint", "ids": ("a1",)},
            "must not carry a role",
            id="ids-with-role",
        ),
        pytest.param(
            {"kind": SelectorKind.IDS, "ids": ("",)},
            "non-empty",
            id="ids-empty-entry",
        ),
        pytest.param(
            {"kind": SelectorKind.ALL, "role": "checkpoint"},
            "must not carry role or ids",
            id="all-with-role",
        ),
        pytest.param(
            {"kind": SelectorKind.ALL, "ids": ("a1",)},
            "must not carry role or ids",
            id="all-with-ids",
        ),
        pytest.param({"kind": "all"}, "must be a SelectorKind", id="kind-not-enum"),
    ],
)
def test_selector_fields_must_match_kind(kwargs: dict[str, object], match: str) -> None:
    """Selector arity is strict per kind."""
    with pytest.raises(InvalidBindingError, match=match):
        PortSelector(**kwargs)  # type: ignore[arg-type]


def test_selector_constructors() -> None:
    """Convenience constructors populate the matching fields."""
    role = PortSelector.by_role("checkpoint")
    assert role.kind is SelectorKind.ROLE
    assert role.role == "checkpoint"
    assert role.ids == ()
    ids = PortSelector.by_ids("a1", "a2")
    assert ids.kind is SelectorKind.IDS
    assert ids.ids == ("a1", "a2")
    assert ids.role is None
    assert ids.to_dict() == {"kind": "ids", "role": None, "ids": ["a1", "a2"]}


def test_binding_rejects_self_reference() -> None:
    """A step must not bind its own output."""
    with pytest.raises(InvalidBindingError, match="must not bind its own output"):
        Binding(
            target_step_id="s_opt",
            target_port="structure",
            source=BindingSource.step_output("s_opt", "structures"),
        )


def test_binding_allows_undeclared_cardinality() -> None:
    """``None`` defers the contract decision to the port declaration."""
    edge = binding()
    assert edge.cardinality is None
    assert edge.pairing is None
    assert edge.partial_consumption is None
    assert edge.to_dict()["cardinality"] is None


def test_binding_accepts_declared_semantics() -> None:
    """Declared cardinality, pairing, and partial consumption round-trip."""
    edge = Binding(
        target_step_id="s_opt",
        target_port="structure",
        source=BindingSource.step_output("s_prep", "structures"),
        cardinality=Cardinality.ONE_OR_MORE,
        pairing=Pairing.BY_SUBJECT,
        partial_consumption=PartialConsumption.ACCEPT_SUBSET,
    )
    assert edge.to_dict() == {
        "target_step_id": "s_opt",
        "target_port": "structure",
        "source": {
            "kind": "step_output",
            "port": "structures",
            "step_id": "s_prep",
            "selector": {"kind": "all", "role": None, "ids": []},
        },
        "cardinality": "one_or_more",
        "pairing": "by_subject",
        "partial_consumption": "accept_subset",
    }


def test_binding_set_rejects_duplicate_target_port() -> None:
    """One binding per ``(step, port)`` is the V4-1 invariant."""
    first = binding("s_opt", "structure", source_step_id="s_a")
    second = binding("s_opt", "structure", source_step_id="s_b")
    with pytest.raises(
        InvalidBindingError, match=r"duplicate binding for target port s_opt.structure"
    ):
        BindingSet.of(first, second)
    allowed = BindingSet.of(first, binding("s_opt", "checkpoint", source_step_id="s_b"))
    assert len(allowed) == 2


def test_binding_set_lookup_helpers() -> None:
    """Target, incoming, outgoing, and per-port lookups are explicit."""
    incoming = binding("s_opt", "structure", source_step_id="s_prep")
    outgoing = binding("s_freq", "structure", source_step_id="s_opt")
    run_input = Binding(
        target_step_id="s_report",
        target_port="results",
        source=BindingSource.run_input("results"),
    )
    bindings = BindingSet.of(incoming, outgoing, run_input)
    assert bindings.by_target("s_opt").bindings == (incoming,)
    assert bindings.incoming("s_opt").bindings == (incoming,)
    assert bindings.outgoing("s_opt").bindings == (outgoing,)
    assert bindings.outgoing("s_report").is_empty is True
    assert bindings.for_port("s_opt", "structure") is incoming
    assert bindings.for_port("s_opt", "checkpoint") is None
    assert bindings.target_steps() == ("s_opt", "s_freq", "s_report")
    assert bindings[0] is incoming
    assert bindings[1:].bindings == (outgoing, run_input)


def test_binding_set_addition_detects_duplicates() -> None:
    """Set addition is a union that still enforces the port invariant."""
    first = binding("s_opt", "structure", source_step_id="s_prep")
    second = binding("s_freq", "structure", source_step_id="s_opt")
    third = binding("s_opt", "structure", source_step_id="s_other")
    assert (BindingSet.of(first) + BindingSet.of(second)).bindings == (first, second)
    with pytest.raises(InvalidBindingError, match="duplicate binding"):
        BindingSet.of(first) + BindingSet.of(third)


def test_frozen_enum_vocabulary_values() -> None:
    """The binding vocabulary strings are part of the frozen contract."""
    assert [member.value for member in Cardinality] == [
        "one",
        "optional",
        "one_or_more",
        "many",
    ]
    assert [member.value for member in Pairing] == [
        "single",
        "per_structure",
        "by_subject",
        "by_group_key",
    ]
    assert [member.value for member in PartialConsumption] == [
        "require_complete",
        "accept_subset",
    ]
    assert Cardinality.ONE == "one"
    assert Pairing.BY_GROUP_KEY == "by_group_key"
    assert PartialConsumption.REQUIRE_COMPLETE == "require_complete"
    assert [member.value for member in SourceKind] == ["run_input", "step_output"]
    assert [member.value for member in PortKind] == ["structure", "artifact", "result"]
    assert [member.value for member in SelectorKind] == ["all", "role", "ids"]
