#!/usr/bin/env python3

"""R3.2 — Workflow V3 parser, schema profiles, param descriptors, extensions.

Covers:
* version detection (absent/v2/v3/unknown/malformed),
* the DOCUMENT and FRAGMENT JSON Schema profiles,
* the V3 parser's mapping into the canonical IR,
* the param descriptor registry and its exact V2/V3 membership,
* the extension registry and namespace grammar,
* schema digests and profile pairing,
* parser defensive copying.

Nothing here wires V3 into the planner, the engine or the CLI: R3.2 only makes a
V3 document recogniseable, checkable and parseable into the IR.
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from confflow.config.canonical.extensions import (
    DEFAULT_EXTENSION_REGISTRY,
    ExtensionRegistry,
    is_valid_namespace,
)
from confflow.config.canonical.issues import ConfigValidationError
from confflow.config.canonical.param_fields import (
    calc_param_fields,
    confgen_keys,
    confgen_param_fields,
    param_properties,
    v2_calc_keys,
    v3_calc_keys,
)
from confflow.config.canonical.parser import detect_schema_version, parse_workflow_mapping
from confflow.config.canonical.schema import (
    WORKFLOW_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION_V2,
    WORKFLOW_SCHEMA_VERSION_V3,
    SchemaProfile,
    workflow_fragment_schema_sha256_v3,
    workflow_json_schema,
    workflow_json_schema_v3,
    workflow_schema_sha256,
    workflow_schema_sha256_v3,
)
from confflow.config.canonical.serialization import canonical_sha256
from confflow.config.canonical.v3_parser import parse_v3_document

V3 = WORKFLOW_SCHEMA_VERSION_V3

#: The historical V2 calc parameter key set, pinned verbatim (50 keys).
#: R5 (RFC §18) adds the reserved ``theory`` sub-namespace to the single
#: descriptor registry; V2 keeps its open-bag tolerance so V2 validation
#: behaviour is unchanged — only the known-key set grows by one.
V2_CALC_KEYS_GOLDEN = frozenset(
    {
        "allowed_executables",
        "auto_clean",
        "blocks",
        "charge",
        "chk_from_step",
        "clean_opts",
        "clean_params",
        "cores_per_task",
        "dedup_only",
        "delete_work_dir",
        "enable_dynamic_resources",
        "energy_tolerance",
        "energy_window",
        "freeze",
        "gaussian_link0",
        "gaussian_modredundant",
        "gaussian_path",
        "gaussian_write_chk",
        "ibkout",
        "imag",
        "input_chk_dir",
        "iprog",
        "itask",
        "keep_all_topos",
        "keyword",
        "max_conformers",
        "max_parallel_jobs",
        "max_wall_time_seconds",
        "maxcore",
        "multiplicity",
        "noH",
        "orca_maxcore",
        "orca_path",
        "resume_from_backups",
        "rmsd_threshold",
        "sandbox_root",
        "scan_coarse_step",
        "scan_fine_half_window",
        "scan_fine_step",
        "scan_max_steps",
        "scan_uphill_limit",
        "stop_check_interval_seconds",
        "theory",
        "total_memory",
        "ts_bond_atoms",
        "ts_bond_drift_threshold",
        "ts_rescue_keep_scan_dirs",
        "ts_rescue_scan",
        "ts_rescue_scan_backup",
        "ts_rmsd_threshold",
    }
)


def _errors(document: Any, profile: SchemaProfile = SchemaProfile.DOCUMENT) -> list[str]:
    validator = Draft202012Validator(workflow_json_schema_v3(profile))
    return [error.message for error in validator.iter_errors(document)]


def _calc_step(**overrides: Any) -> dict[str, Any]:
    step: dict[str, Any] = {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}}
    step.update(overrides)
    return step


def _doc(*steps: dict[str, Any], **root: Any) -> dict[str, Any]:
    document: dict[str, Any] = {"schema": V3, "steps": list(steps)}
    document.update(root)
    return document


# ---------------------------------------------------------------------------
# 43 — version detection
# ---------------------------------------------------------------------------
def test_detect_version_absent_is_v2() -> None:
    assert detect_schema_version({}) == WORKFLOW_SCHEMA_VERSION_V2
    assert detect_schema_version({"global": {}, "steps": []}) == WORKFLOW_SCHEMA_VERSION_V2


def test_detect_version_explicit_v2_and_v3() -> None:
    assert detect_schema_version({"schema": WORKFLOW_SCHEMA_VERSION_V2}) == (
        WORKFLOW_SCHEMA_VERSION_V2
    )
    assert detect_schema_version({"schema": WORKFLOW_SCHEMA_VERSION_V3}) == (
        WORKFLOW_SCHEMA_VERSION_V3
    )


def test_detect_version_unknown_schema_fails_closed() -> None:
    with pytest.raises(ConfigValidationError):
        detect_schema_version({"schema": "confflow.workflow.v4"})


def test_detect_version_non_string_schema_fails() -> None:
    with pytest.raises(ConfigValidationError):
        detect_schema_version({"schema": 123})


# ---------------------------------------------------------------------------
# 44 — DOCUMENT schema
# ---------------------------------------------------------------------------
def test_document_accepts_minimal_calc_and_confgen() -> None:
    assert _errors(_doc(_calc_step())) == []
    assert _errors(_doc({"id": "s001", "type": "confgen", "inputs": [], "params": {}})) == []


def test_document_rejects_missing_id() -> None:
    assert _errors(_doc({"type": "calc", "inputs": [], "params": {"keyword": "HF"}}))


def test_document_rejects_missing_inputs() -> None:
    assert _errors(_doc({"id": "s001", "type": "calc", "params": {"keyword": "HF"}}))


def test_document_rejects_scalar_inputs() -> None:
    assert _errors(_doc(_calc_step(inputs="s000")))


def test_document_rejects_unknown_root_key() -> None:
    assert _errors(_doc(_calc_step(), extra=True))


def test_document_rejects_unknown_step_key() -> None:
    assert _errors(_doc(_calc_step(note="hi")))


def test_document_rejects_legacy_type_aliases() -> None:
    assert _errors(_doc({"id": "s001", "type": "task", "inputs": [], "params": {}}))
    assert _errors(_doc({"id": "s001", "type": "gen", "inputs": [], "params": {}}))


def test_document_rejects_invalid_id_grammar() -> None:
    assert _errors(_doc(_calc_step(id="S001")))
    assert _errors(_doc(_calc_step(id="1bad")))


def test_document_allows_duplicate_labels() -> None:
    assert (
        _errors(
            _doc(
                _calc_step(id="s001", label="Single Point"),
                _calc_step(id="s002", label="Single Point"),
            )
        )
        == []
    )


def test_document_accepts_structured_checkpoint() -> None:
    document = _doc(
        _calc_step(id="s001"),
        _calc_step(id="s002", inputs=["s001"], checkpoint={"from_step": "s001"}),
    )
    assert _errors(document) == []


def test_document_rejects_bad_checkpoint_shape() -> None:
    assert _errors(_doc(_calc_step(checkpoint={"from_step": 1})))
    assert _errors(_doc(_calc_step(checkpoint={"from_step": "s001", "extra": 1})))
    assert _errors(_doc(_calc_step(checkpoint={})))


# ---------------------------------------------------------------------------
# 45 — FRAGMENT schema
# ---------------------------------------------------------------------------
def test_fragment_accepts_missing_id() -> None:
    assert (
        _errors(_doc({"type": "confgen", "inputs": [], "params": {}}), SchemaProfile.FRAGMENT) == []
    )


def test_fragment_accepts_partial_confgen_params() -> None:
    assert (
        _errors(
            _doc({"label": "Conformer search", "type": "confgen", "inputs": [], "params": {}}),
            SchemaProfile.FRAGMENT,
        )
        == []
    )


def test_fragment_rejects_invalid_type() -> None:
    assert _errors(_doc({"type": "gen", "inputs": [], "params": {}}), SchemaProfile.FRAGMENT)


def test_fragment_rejects_scalar_inputs() -> None:
    assert _errors(_doc({"type": "calc", "inputs": "s001", "params": {}}), SchemaProfile.FRAGMENT)


def test_fragment_rejects_unknown_step_key() -> None:
    assert _errors(_doc({"type": "calc", "inputs": [], "note": 1}), SchemaProfile.FRAGMENT)


def test_fragment_rejects_malformed_checkpoint() -> None:
    assert _errors(
        _doc({"type": "calc", "inputs": [], "checkpoint": {"nope": 1}}), SchemaProfile.FRAGMENT
    )


def test_fragment_rejects_malformed_extension_namespace() -> None:
    assert _errors(
        _doc({"type": "calc", "inputs": [], "extensions": {"nosegments": {"x": 1}}}),
        SchemaProfile.FRAGMENT,
    )


# ---------------------------------------------------------------------------
# 46 — parser
# ---------------------------------------------------------------------------
def test_parser_maps_a_full_calc_step_into_the_ir() -> None:
    document = _doc(
        {
            "id": "s_abcd1234",
            "label": "Geometry Optimization",
            "type": "calc",
            "enabled": False,
            "inputs": [],
            "params": {"keyword": "B3LYP/6-31G(d)", "iprog": "g16"},
            "extensions": {"vendor.example": {"tier": 2}},
            "annotations": {"ui": {"x": 1}},
        }
    )

    definition = parse_v3_document(document)

    assert definition.source_version == WORKFLOW_SCHEMA_VERSION_V3
    assert definition.dependency_mode == "explicit"
    (step,) = definition.steps
    assert step.id == "s_abcd1234"
    assert step.name == "s_abcd1234"
    assert step.identity == "s_abcd1234"
    assert step.label == "Geometry Optimization"
    assert step.type == "calc"
    assert step.enabled is False
    assert step.inputs_declared is True
    assert step.params == {"keyword": "B3LYP/6-31G(d)", "iprog": "g16"}
    assert step.extensions == {"vendor.example": {"tier": 2}}
    assert step.annotations == {"ui": {"x": 1}}


def test_parser_maps_checkpoint_from_step() -> None:
    document = _doc(
        _calc_step(id="s001"),
        _calc_step(id="s002", inputs=["s001"], checkpoint={"from_step": "s001"}),
    )

    definition = parse_v3_document(document)

    by_id = {step.id: step for step in definition.steps}
    assert by_id["s002"].checkpoint_from == "s001"
    assert by_id["s001"].checkpoint_from is None
    assert "chk_from_step" not in by_id["s002"].params


def test_parser_rejects_chk_from_step_in_v3_params() -> None:
    with pytest.raises(ConfigValidationError):
        parse_v3_document(_doc(_calc_step(params={"keyword": "HF", "chk_from_step": "s000"})))


def test_parser_allows_duplicate_labels() -> None:
    definition = parse_v3_document(
        _doc(
            _calc_step(id="s001", label="Single Point"),
            _calc_step(id="s002", label="Single Point"),
        )
    )
    assert [step.label for step in definition.steps] == ["Single Point", "Single Point"]


def test_parser_preserves_root_extensions_and_annotations() -> None:
    document = _doc(
        _calc_step(),
        extensions={"vendor.example": {"enabled": True}},
        annotations={"ui": {"zoom": 1.5}},
    )

    definition = parse_v3_document(document)

    assert definition.extensions == {"vendor.example": {"enabled": True}}
    assert definition.annotations == {"ui": {"zoom": 1.5}}


def test_parser_sets_source_version_v3() -> None:
    assert parse_v3_document(_doc(_calc_step())).source_version == WORKFLOW_SCHEMA_VERSION_V3


def test_parser_does_not_generate_a_missing_fragment_id() -> None:
    definition = parse_v3_document(
        _doc({"type": "confgen", "inputs": [], "params": {}}), profile=SchemaProfile.FRAGMENT
    )
    (step,) = definition.steps
    assert step.id is None
    # The internal graph handle is not an id and is not a legal V3 id either.
    assert step.name != step.id
    assert step.identity == step.name


def test_parser_does_not_touch_the_v2_parser() -> None:
    # The V2 parser keeps returning a WorkflowConfig with its legacy shape.
    workflow = parse_workflow_mapping(
        {"global": {}, "steps": [{"name": "g", "type": "gen", "params": {"chains": ["1-2"]}}]}
    )
    assert workflow.as_legacy_shape()["steps"][0]["type"] == "confgen"


# ---------------------------------------------------------------------------
# 47 — param descriptor registry
# ---------------------------------------------------------------------------
def test_v2_calc_keys_are_the_pinned_golden_set() -> None:
    assert v2_calc_keys() == V2_CALC_KEYS_GOLDEN
    from confflow.config.canonical.fingerprint import _known_calc_keys

    assert _known_calc_keys() == V2_CALC_KEYS_GOLDEN


def test_v3_calc_keys_drop_the_v2_only_checkpoint_key() -> None:
    assert v3_calc_keys() == v2_calc_keys() - {"chk_from_step"}
    assert "chk_from_step" not in v3_calc_keys()


def test_confgen_keys_match_the_resolver_key_set() -> None:
    from confflow.shared.confgen_params import confgen_known_keys

    assert confgen_keys() == confgen_known_keys()


def test_generated_schema_properties_match_the_registry() -> None:
    document_schema = workflow_json_schema_v3(SchemaProfile.DOCUMENT)
    calc_props = document_schema["$defs"]["calcParams"]["properties"]
    confgen_props = document_schema["$defs"]["confgenParams"]["properties"]

    assert set(calc_props) == v3_calc_keys()
    assert set(confgen_props) == confgen_keys()
    assert set(param_properties("calc")) == v3_calc_keys()
    assert set(param_properties("confgen")) == confgen_keys()
    assert {f.key for f in calc_param_fields()} == v2_calc_keys()
    assert {f.key for f in calc_param_fields() if f.v3} == v3_calc_keys()
    assert {f.key for f in confgen_param_fields()} == confgen_keys()


def test_v3_params_are_strict_per_step_type() -> None:
    # calc-only key on a confgen step and vice versa are both rejected
    assert _errors(_doc({"id": "s1", "type": "calc", "inputs": [], "params": {"angle_step": 120}}))
    assert _errors(_doc({"id": "s1", "type": "confgen", "inputs": [], "params": {"keyword": "HF"}}))
    assert _errors(_doc({"id": "s1", "type": "calc", "inputs": [], "params": {"methd": "B3LYP"}}))


def test_v3_params_enum_fields_reject_invalid_values() -> None:
    assert _errors(_doc(_calc_step(params={"keyword": "HF", "iprog": "xtb"})))
    assert _errors(_doc(_calc_step(params={"keyword": "HF", "itask": "bogus"})))
    assert _errors(_doc(_calc_step(params={"keyword": "HF", "iprog": "orca", "itask": "sp"}))) == []


# ---------------------------------------------------------------------------
# 48 — resolver compatibility (registry did not change the resolvers)
# ---------------------------------------------------------------------------
def test_resolvers_remain_the_semantic_source() -> None:
    from confflow.config.canonical.resolve import resolve_calc_step, resolve_global_options
    from confflow.shared.confgen_params import resolve_confgen_params

    globals_ = resolve_global_options({"max_parallel_jobs": 2})
    resolved = resolve_calc_step({"keyword": "HF"}, globals_)
    assert resolved.keyword == "HF"
    confgen = resolve_confgen_params({"chains": ["1-2"]}, default_workers=2)
    assert confgen["chains"] == ["1-2"]


# ---------------------------------------------------------------------------
# 49 — extension registry
# ---------------------------------------------------------------------------
def test_valid_extension_namespace_syntax_is_schema_accepted() -> None:
    document = _doc(_calc_step(extensions={"vendor.example.basis": {"x": 1}}))
    assert _errors(document) == []


def test_parser_preserves_unknown_extension() -> None:
    document = _doc(_calc_step(extensions={"vendor.example.basis": {"x": 1}}))
    definition = parse_v3_document(document)
    assert definition.steps[0].extensions == {"vendor.example.basis": {"x": 1}}


def test_extension_namespace_grammar() -> None:
    assert is_valid_namespace("vendor.example")
    assert is_valid_namespace("org.example.feature_1")
    assert not is_valid_namespace("nosegments")
    assert not is_valid_namespace(".leading")
    assert not is_valid_namespace("Upper.example")
    assert not is_valid_namespace("a.")


def test_default_registry_is_empty_and_queryable() -> None:
    assert DEFAULT_EXTENSION_REGISTRY.namespaces() == frozenset()
    assert DEFAULT_EXTENSION_REGISTRY.is_known("vendor.example") is False


def test_registry_recognised_namespace_query() -> None:
    registry = ExtensionRegistry()
    registry.register("vendor.example", schema={"type": "object"})
    assert registry.is_known("vendor.example") is True
    assert registry.namespaces() == frozenset({"vendor.example"})
    assert registry.schema_for("vendor.example") == {"type": "object"}
    assert registry.schema_for("vendor.other") is None


def test_registry_rejects_duplicate_registration() -> None:
    registry = ExtensionRegistry()
    registry.register("vendor.example")
    with pytest.raises(ValueError):
        registry.register("vendor.example")


def test_registry_rejects_malformed_namespace() -> None:
    registry = ExtensionRegistry()
    with pytest.raises(ValueError):
        registry.register("nosegments")


# ---------------------------------------------------------------------------
# 50 — schema profiles and digests
# ---------------------------------------------------------------------------
def test_document_and_fragment_differ_only_by_the_id_requirement() -> None:
    fragment_step = {"type": "calc", "inputs": [], "params": {"keyword": "HF"}}
    fragment_document = {"schema": V3, "steps": [fragment_step]}

    assert _errors(fragment_document, SchemaProfile.DOCUMENT) != []
    assert _errors(fragment_document, SchemaProfile.FRAGMENT) == []


def test_document_and_fragment_digests_differ_and_are_deterministic() -> None:
    document_digest = workflow_schema_sha256_v3()
    fragment_digest = workflow_fragment_schema_sha256_v3()

    assert document_digest != fragment_digest
    assert document_digest == workflow_schema_sha256_v3()
    assert fragment_digest == workflow_fragment_schema_sha256_v3()
    assert document_digest == canonical_sha256(workflow_json_schema_v3(SchemaProfile.DOCUMENT))


def test_v2_schema_and_digest_are_unchanged() -> None:
    assert WORKFLOW_SCHEMA_VERSION == WORKFLOW_SCHEMA_VERSION_V2 == "confflow.workflow.v2"
    assert workflow_schema_sha256() == (
        "38c45669ff9f3be0d352ab8d6bb2a64be9cd67bb2327dd89cd72d804aee2e7ca"
    )
    # The default V2 schema API still describes V2.
    assert workflow_json_schema()["$id"].endswith("/workflow/v2.json")


# ---------------------------------------------------------------------------
# 51 — defensive copying at the parser boundary
# ---------------------------------------------------------------------------
def test_parser_defensively_copies_raw_mappings() -> None:
    document = _doc(
        _calc_step(params={"keyword": "HF", "freeze": [1, 2]}, extensions={"vendor.x": {"a": 1}}),
        annotations={"ui": {"zoom": 1.0}},
    )
    definition = parse_v3_document(document)

    # Mutate every nested container the parser read from.
    document["steps"][0]["params"]["keyword"] = "MUTATED"
    document["steps"][0]["params"]["freeze"].append(99)
    document["steps"][0]["extensions"]["vendor.x"]["a"] = 999
    document["annotations"]["ui"]["zoom"] = 42.0

    step = definition.steps[0]
    assert step.params["keyword"] == "HF"
    assert step.params["freeze"] == [1, 2]
    assert step.extensions == {"vendor.x": {"a": 1}}
    assert definition.annotations == {"ui": {"zoom": 1.0}}
