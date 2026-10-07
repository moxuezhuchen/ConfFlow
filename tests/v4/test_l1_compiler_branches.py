#!/usr/bin/env python3

"""L1 compiler error branches and compat-wrapper behavior.

Pins observable fail-closed behavior of
``confflow.producer.intent.compiler`` helper paths: allocation,
card resolution, fragment validation, misplaced-field rejection, and
the recipe-compat wrappers.  Full ``compile_intent`` runs use real
registries and real cards; injected faults target only the documented
defensive seams (unreadable metadata, failing authorities).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

import confflow.producer.intent.compiler as compiler
from confflow.producer.cards import CARD_VERSION
from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import (
    build_default_intent_registry,
    build_intent_registry,
)


def _handler(user, card, step_id):
    return {"calculation": {}}


BASE_CARD = {
    "executor": "calculation",
    "adapter": "standard",
    "profile": "standard",
    "checks": ["normal_termination"],
    "check_params": {},
    "recovery": "none",
    "default_role": None,
    "requires_explicit_bindings": False,
    "description": "probe",
}


def _probe_registry(key="evfrag", handler=_handler, **overrides):
    default = build_default_intent_registry()
    kw = {
        "key": key,
        "executor": "calculation",
        "intent_handler": handler,
        "card": copy.deepcopy(BASE_CARD),
        "fragment_keys": ("calculation",),
        "seed_block_keys": ("calculation",),
    }
    kw.update(overrides)
    probe = CapabilityDescriptor(**kw)
    return build_intent_registry([*default.entries.values(), probe])


def _intent(steps, **top):
    doc = {
        "schema": INTENT_SCHEMA,
        "globals": {"charge": 0, "multiplicity": 1},
        "steps": steps,
    }
    doc.update(top)
    return doc


def test_extract_card_type_with_failing_resolve_returns_none() -> None:
    class _Bad:
        def resolve(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert compiler._extract_card_type_for_alloc("sp@v1", _Bad()) is None


def test_allocate_ids_rejects_unidentifiable_custom_key() -> None:
    reg = _probe_registry(key="bad-key")
    with pytest.raises(IntentCompilationError, match="cannot allocate a step id"):
        compiler._allocate_ids([{"card": "bad-key@v1"}], reg)


def test_select_family_native_and_expected_purpose_delegate() -> None:
    assert compiler._select_family_native({"native": {"a": 1}}, None, context="t") == {"a": 1}
    assert compiler._expected_purpose(
        "ts", "whatever", recipe_id=None
    ) == compiler._expected_purpose("ts", "whatever", recipe_id=None)


def test_apply_role_cards_wrapper_delegates_empty() -> None:
    assert compiler._apply_role_cards([], {}, {}, skip_ids=set(), recipe_id=None) == set()


def test_parse_with_failing_resolve_uses_legacy() -> None:
    class _Bad:
        def resolve(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert compiler._parse_card_ref_with_registry("sp@v1", _Bad(), "s") == (
        "sp",
        CARD_VERSION,
    )


def test_parse_custom_hit_with_bad_version_rejected() -> None:
    reg = _probe_registry(key="mycustom")
    with pytest.raises(ValueError, match="unsupported card version"):
        compiler._parse_card_ref_with_registry("mycustom@v9", reg, "s")


def test_resolve_card_and_entry_with_failing_resolve_uses_legacy() -> None:
    class _Bad:
        def resolve(self, *args, **kwargs):
            raise RuntimeError("boom")

    _, _, card, entry = compiler._resolve_card_and_entry("sp@v1", _Bad(), "s")
    assert entry is None
    assert card["executor"] == "calculation"


def test_resolve_card_and_entry_without_card_dict_deep_copies() -> None:
    owned = {"executor": "calculation", "adapter": "standard"}

    class _Reg:
        def resolve(self, *args, **kwargs):
            return SimpleNamespace(card=owned)

    _, _, card, _ = compiler._resolve_card_and_entry("sp@v1", _Reg(), "s")
    assert card == owned
    assert card is not owned


def test_resolve_card_and_entry_with_none_entry_uses_legacy() -> None:
    class _Reg:
        def resolve(self, *args, **kwargs):
            return None

    card_type, version, _, entry = compiler._resolve_card_and_entry("sp@v1", _Reg(), "s")
    assert (card_type, version, entry) == ("sp", CARD_VERSION, None)


def test_effective_wire_block_key_survives_unreadable_entry() -> None:
    class _Evil:
        @property
        def wire_block_key(self):
            raise RuntimeError("boom")

        @property
        def fragment_keys(self):
            raise RuntimeError("boom")

    assert compiler._effective_wire_block_key(_Evil()) is None


def test_adapter_from_wire_survives_unreadable_and_foreign_blocks() -> None:
    class _EvilGet(dict):
        def get(self, key, default=None):
            raise RuntimeError("boom")

    assert compiler._adapter_from_wire(_EvilGet({"calculation": {}}), "calculation") is None

    class _EvilBlock(dict):
        def get(self, key, default=None):
            if key == "execution_adapter":
                raise RuntimeError("boom")
            return super().get(key, default)

    assert (
        compiler._adapter_from_wire(
            {"calculation": _EvilBlock({"execution_adapter": "x"})}, "calculation"
        )
        is None
    )
    assert compiler._adapter_from_wire({"id": "s"}, "calculation") is None


def test_reject_misplaced_fields_survives_unreadable_entry() -> None:
    class _Evil:
        @property
        def rejected_step_keys(self):
            raise RuntimeError("boom")

    assert compiler._reject_misplaced_fields({"a": 1}, "sp", "calculation", "s1", _Evil()) is None


def test_reject_misplaced_fields_legacy_failure_modes(monkeypatch) -> None:
    import confflow.producer.intent.capabilities.registry as regmod

    def _boom(executor):
        raise IntentCompilationError("bad metadata")

    monkeypatch.setattr(regmod, "rejected_step_keys_for_legacy_fallback", _boom)
    with pytest.raises(IntentCompilationError, match="bad metadata"):
        compiler._reject_misplaced_fields({"a": 1}, "sp", "calculation", "s1", None)

    def _boom2(executor):
        raise RuntimeError("gone")

    monkeypatch.setattr(regmod, "rejected_step_keys_for_legacy_fallback", _boom2)
    with pytest.raises(IntentCompilationError, match="rejected-metadata unavailable"):
        compiler._reject_misplaced_fields({"a": 1}, "sp", "calculation", "s1", None)


def test_reject_misplaced_fields_survives_unhashable_step() -> None:
    reg = build_default_intent_registry()
    entry = reg.resolve("sp")
    assert compiler._reject_misplaced_fields(123, "sp", "calculation", "s1", entry) is None  # type: ignore[arg-type]


def test_seed_scope_delegates_to_seeds_authority() -> None:
    from confflow.producer.seeds import seed_scope_for_step

    step = {"id": "s1", "executor": "calculation", "calculation": {"seed": 7}}
    assert compiler._seed_scope(step, "explicit") == seed_scope_for_step(step, "explicit")


def test_role_cards_without_recipe_rejected() -> None:
    with pytest.raises(IntentCompilationError, match="need a 'recipe' base chain"):
        compile_intent(_intent([], role_cards={"sp": "mycard"}))


def test_intent_catalog_survives_missing_recipe_table(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "confflow.producer.recipes", None)
    catalog = compiler.intent_catalog()
    assert catalog["supported_recipes"] == []


def test_compile_recipe_needs_producer_recipes(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "confflow.producer.recipes", None)
    with pytest.raises(IntentCompilationError, match="needs producer.recipes"):
        compile_intent(_intent([], recipe="single_point"))


def test_compile_survives_missing_intent_registry(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "confflow.producer.intent.capabilities.registry", None)
    with pytest.raises(IntentCompilationError, match="cannot load the intent registry"):
        compile_intent(_intent([{"card": "sp@v1", "program": "orca", "native": {"k": "v"}}]))


def test_compile_survives_missing_execution_registry(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "confflow.execution.registry", None)
    with pytest.raises(IntentCompilationError, match="cannot load the execution registry"):
        compile_intent(
            _intent([{"card": "sp@v1", "program": "orca", "native": {"k": "v"}}]),
            intent_registry=build_default_intent_registry(),
        )


def test_compile_with_unreadable_bound_registry_falls_back_to_default() -> None:
    class _BadBound:
        @property
        def execution_registry(self):
            raise RuntimeError("boom")

    with pytest.raises(IntentCompilationError, match="not servable"):
        compile_intent(
            _intent([{"card": "sp@v1", "program": "orca", "native": {"k": "v"}}]),
            intent_registry=_BadBound(),
        )


def test_compile_with_failing_resolve_reports_unservable() -> None:
    class _BadResolve:
        execution_registry = None

        def resolve(self, *args, **kwargs):
            raise RuntimeError("boom")

    with pytest.raises(IntentCompilationError, match="not servable"):
        compile_intent(
            _intent([{"card": "sp@v1", "program": "orca", "native": {"k": "v"}}]),
            intent_registry=_BadResolve(),
        )


def test_label_string_carried_to_wire() -> None:
    out = compile_intent(
        _intent(
            [
                {
                    "id": "s1",
                    "card": "sp@v1",
                    "program": "orca",
                    "native": {"keyword": "B3LYP D3BJ SP"},
                    "label": "hello",
                }
            ]
        )
    )
    assert out["steps"][0]["label"] == "hello"


def test_capability_fragment_must_return_mapping() -> None:
    def _bad(user, card, step_id):
        return []

    reg = _probe_registry(key="badfrag", handler=_bad)
    with pytest.raises(IntentCompilationError, match="must return a mapping"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "s1",
                        "card": "badfrag@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ SP"},
                    }
                ]
            ),
            intent_registry=reg,
        )


def test_capability_fragment_keys_unreadable_fails_closed() -> None:
    class _BadIter:
        def __iter__(self):
            raise RuntimeError("uniterable")

    reg = _probe_registry(key="evfrag")
    probe = reg.resolve("evfrag")
    assert probe is not None
    object.__setattr__(probe, "fragment_keys", _BadIter())
    with pytest.raises(IntentCompilationError, match="undeclared"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "s1",
                        "card": "evfrag@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ SP"},
                    }
                ]
            ),
            intent_registry=reg,
        )


def test_family_template_without_role_uses_card_default_role() -> None:
    doc = _intent(
        [{"id": "s1", "card": "fam", "program": "orca"}],
        cards={
            "fam": {
                "card": "sp@v1",
                "program": "orca",
                "native_by_role": {
                    "sp": {"keyword": "B3LYP D3BJ SP"},
                    "other": {"keyword": "B3LYP D3BJ OPT"},
                },
            }
        },
    )
    out = compile_intent(doc)
    assert out["steps"][0]["calculation"]["native"] == {"keyword": "B3LYP D3BJ SP"}


def test_legacy_passthrough_wraps_authority_failure(monkeypatch) -> None:
    import confflow.workflow.v4.compiler as workflow_compiler

    def _boom(*args, **kwargs):
        raise RuntimeError("authority down")

    monkeypatch.setattr(workflow_compiler, "compile_workflow", _boom)
    with pytest.raises(IntentCompilationError, match="failed to compile"):
        compiler._passthrough_legacy({"schema": "confflow.workflow.v4"}, registry=None)


def test_require_recipe_assignment_wrapper_skips() -> None:
    assert compiler._require_recipe_assignment({}, None, "s1") is None  # type: ignore[arg-type]
    assert compiler._require_recipe_assignment({}, {"executor": "unknown_exec"}, "s1") is None


def test_patch_recipe_step_wrapper_delegates() -> None:
    patched: dict = {"id": "b1", "executor": "calculation", "calculation": {"a": 1}}
    compiler._patch_recipe_step(patched, {"program": "orca"}, "b1")
    assert patched["_expanded"] is True
    blank: dict = {}
    compiler._patch_recipe_step(blank, {}, "s9")
    assert blank["_expanded"] is True


def test_extract_card_type_without_registry_returns_none() -> None:
    assert compiler._extract_card_type_for_alloc("sp@v1", None) is None


def test_resolve_card_and_entry_legacy_parse_failure_reraises() -> None:
    default = build_default_intent_registry()
    probe = default.resolve("sp")

    class _FlipNone:
        execution_registry = None

        def __init__(self):
            self.calls = 0

        def resolve(self, *args, **kwargs):
            self.calls += 1
            return probe if self.calls == 1 else None

    with pytest.raises(ValueError, match="unknown card type 'flip'"):
        compiler._resolve_card_and_entry("flip@v1", _FlipNone(), "s")


def test_inconsistent_registry_reads_prefer_latest_entry() -> None:
    from confflow.producer.cards import get_card
    from confflow.producer.intent.capabilities.calculation import calculation_fragment

    def _two_keys(suffix):
        return CapabilityDescriptor(
            key="flip",
            executor="calculation",
            intent_handler=calculation_fragment,
            card=get_card("sp"),
            fragment_keys=("calculation",),
        )

    first, second = _two_keys("a"), _two_keys("b")
    assert first is not second

    class _FlipReg:
        execution_registry = None

        def __init__(self):
            self.calls = 0

        def resolve(self, *args, **kwargs):
            self.calls += 1
            return first if self.calls % 2 == 1 else second

    out = compile_intent(
        _intent(
            [
                {
                    "id": "s1",
                    "card": "flip@v1",
                    "program": "orca",
                    "native": {"keyword": "B3LYP D3BJ SP"},
                }
            ]
        ),
        intent_registry=_FlipReg(),
    )
    assert out["steps"][0]["calculation"]["program"] == "orca"


def test_role_cards_wrapper_invokes_executor_closures() -> None:
    covered = compiler._apply_role_cards(
        [
            {
                "id": "b",
                "executor": "calculation",
                "calculation": {"role": "r", "program": "demo", "native": {"old": 1}},
            }
        ],
        {"b": "c"},
        {"c": {"card": "sp@v1", "program": "orca", "native": {"keyword": "X"}}},
        skip_ids=set(),
        recipe_id=None,
    )
    assert covered == {"b"}


def test_seed_identity_fallback_survives_canonical_failure(monkeypatch) -> None:
    import confflow.producer.seeds as seeds

    def _boom(*args, **kwargs):
        raise RuntimeError("no identity")

    monkeypatch.setattr(seeds, "seed_identity_for_step", _boom)
    out = compile_intent(
        _intent(
            [
                {
                    "id": "s1",
                    "card": "sp@v1",
                    "program": "orca",
                    "native": {"keyword": "B3LYP D3BJ SP"},
                }
            ]
        )
    )
    assert out["steps"][0]["annotations"]["producer_resolution"]["seed_identity_digest"] is None


def test_strict_parsing_failure_wrapped(monkeypatch) -> None:
    import confflow.workflow.v4.parser as parser

    def _boom(*args, **kwargs):
        raise RuntimeError("parser down")

    monkeypatch.setattr(parser, "parse_workflow_document", _boom)
    with pytest.raises(IntentCompilationError, match="failed strict parsing"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "s1",
                        "card": "sp@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ SP"},
                    }
                ]
            )
        )


def test_strict_compilation_failure_wrapped(monkeypatch) -> None:
    import confflow.workflow.v4.compiler as workflow_compiler

    def _boom(*args, **kwargs):
        raise RuntimeError("compiler down")

    monkeypatch.setattr(workflow_compiler, "compile_workflow", _boom)
    with pytest.raises(IntentCompilationError, match="failed strict compilation"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "s1",
                        "card": "sp@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ SP"},
                    }
                ]
            )
        )


def test_require_recipe_assignment_wrapper_survives_unreadable_base() -> None:
    class _EvilBase(dict):
        def get(self, key, default=None):
            if key == "executor":
                raise RuntimeError("boom")
            return super().get(key, default)

    assert compiler._require_recipe_assignment({}, _EvilBase({"id": "b1"}), "b1") is None


def test_patch_recipe_step_wrapper_survives_unreadable_step() -> None:
    class _EvilStep(dict):
        def get(self, key, default=None):
            if key == "executor":
                raise RuntimeError("boom")
            return super().get(key, default)

    patched = _EvilStep({"id": "b1"})
    compiler._patch_recipe_step(patched, {}, "b1")
    assert patched["_expanded"] is True
