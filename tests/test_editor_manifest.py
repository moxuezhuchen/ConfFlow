#!/usr/bin/env python3

"""Producer-side consistency tests for the editor manifest.

Two kinds of assertion live here, and the difference matters:

* **Consistency** -- the manifest is internally coherent: ids unique, pointers
  well formed, conditions referencing fields that exist, inherit targets that are
  global fields, choice values unique.
* **Provenance** -- every published ``default`` and every ``choices`` list traces
  back to a source that already owns the fact (``shared.defaults``,
  ``shared.confgen_params``, the ``ProgramName`` / ``TaskName`` literals, the
  ``GlobalOptions`` dataclass).  A default written down twice is the failure mode
  this file exists to prevent.

The wire-key checks are deliberately backed by real introspection where one
exists -- ``dataclasses.fields(GlobalOptions)`` and
``confflow.shared.confgen_params.confgen_known_keys()`` -- rather than by a
hand-written list that could drift with the resolver.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, get_args

import pytest

from confflow.config.canonical.editor_manifest import (
    EDITOR_MANIFEST_SCHEMA,
    build_editor_manifest,
    editor_manifest_sha256,
)
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION
from confflow.config.canonical.serialization import canonical_json, canonical_sha256
from confflow.config.canonical.types import GlobalOptions, ProgramName, TaskName
from confflow.shared.confgen_params import (
    DEFAULT_CONFGEN_ANGLE_STEP,
    DEFAULT_CONFGEN_BOND_THRESHOLD,
    confgen_known_keys,
)
from confflow.shared.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_PROGRAM,
    DEFAULT_SCAN_COARSE_STEP,
    DEFAULT_TASK,
    DEFAULT_TOTAL_MEMORY,
    DEFAULT_TS_BOND_DRIFT_THRESHOLD,
    DEFAULT_TS_RESCUE_SCAN,
)

#: The editor kinds the consumer understands.  A producer may not invent a new
#: one: an unknown kind is a control the GUI cannot render.
ACCEPTED_EDITORS = {
    "atom_pair",
    "boolean",
    "integer",
    "memory",
    "multiline",
    "number",
    "select",
    "string_list",
    "text",
}

#: The value types the consumer understands.
ACCEPTED_VALUE_TYPES = {"array", "boolean", "integer", "number", "string"}

#: The presentation groups this manifest declares.  Pinned rather than derived so
#: that introducing a group is a reviewed change instead of a silent one.
DECLARED_GROUPS = {
    "calculation",
    "conformer generation",
    "input blocks",
    "programs",
    "resources",
    "screening",
    "system",
    "transition state",
}

#: Step ``params`` members a calc step is resolved from, read out of
#: ``confflow/config/canonical/types.py`` (``CalcStepParams.from_params`` /
#: ``CleanupOptions.from_params``).  There is no runtime registry for these, so
#: the list is maintained here and reviewed when the resolver changes.
CALC_PARAM_MEMBERS = {
    "allowed_executables",
    "blocks",
    "charge",
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
    "total_memory",
    "ts_bond_atoms",
    "ts_bond_drift_threshold",
    "ts_rescue_keep_scan_dirs",
    "ts_rescue_scan",
    "ts_rmsd_threshold",
}

_GLOBAL_MEMBERS = {item.name for item in dataclasses.fields(GlobalOptions)}


def _fields() -> list[dict[str, Any]]:
    return build_editor_manifest()["fields"]


def _by_id() -> dict[str, dict[str, Any]]:
    return {item["field_id"]: item for item in _fields()}


def _referenced_fields(condition: Any) -> set[str]:
    """Every field id a ``visible_when`` condition mentions, at any depth."""
    if not isinstance(condition, dict):
        return set()
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        found: set[str] = set()
        for operand in condition[key]:
            found |= _referenced_fields(operand)
        return found
    return {condition["field"]} if "field" in condition else set()


def _operators(condition: Any) -> list[str]:
    if not isinstance(condition, dict):
        return []
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        found: list[str] = []
        for operand in condition[key]:
            found.extend(_operators(operand))
        return found
    return [name for name in ("equals", "not_equals", "in") if name in condition]


class TestEnvelope:
    def test_schema_and_workflow_binding(self) -> None:
        manifest = build_editor_manifest()

        assert manifest["schema"] == EDITOR_MANIFEST_SCHEMA
        assert manifest["step_contexts"] == {"calc": "calc", "confgen": "confgen"}
        # The consumer disables editing when this disagrees with the contract's
        # workflow schema version, so it must be the same constant.
        assert manifest["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION

    def test_no_provenance_is_embedded(self) -> None:
        """Provenance belongs to the contract envelope, not to the artifact.

        Keeping it out is what makes the digest a function of content alone.
        """
        manifest = build_editor_manifest()

        assert "producer" not in manifest
        assert "contract_key" not in manifest

    def test_it_is_pure_data_with_no_toolkit_vocabulary(self) -> None:
        """A manifest that named a widget would not be portable."""
        text = json.dumps(build_editor_manifest(), ensure_ascii=False).lower()

        for token in ("qcombobox", "qlineedit", "qspinbox", "pyside", "pyqt", "widget"):
            assert token not in text


class TestDeterminism:
    def test_two_builds_are_identical(self) -> None:
        assert build_editor_manifest() == build_editor_manifest()
        assert canonical_json(build_editor_manifest()) == canonical_json(build_editor_manifest())

    def test_the_digest_is_the_digest_of_the_document(self) -> None:
        assert editor_manifest_sha256() == canonical_sha256(build_editor_manifest())
        assert editor_manifest_sha256() == editor_manifest_sha256()

    def test_a_returned_document_is_isolated_from_the_singleton(self) -> None:
        before = build_editor_manifest()
        before["fields"][0]["default"] = "tampered"
        before["fields"].append({"field_id": "global.injected"})
        before["step_contexts"]["calc"] = "global"

        after = build_editor_manifest()
        assert after["fields"][0]["default"] != "tampered"
        assert "global.injected" not in {item["field_id"] for item in after["fields"]}
        assert after["step_contexts"]["calc"] == "calc"
        assert editor_manifest_sha256() == canonical_sha256(after)


class TestFieldConsistency:
    def test_field_ids_are_unique(self) -> None:
        ids = [item["field_id"] for item in _fields()]
        assert len(set(ids)) == len(ids)

    def test_every_field_id_matches_its_context(self) -> None:
        for item in _fields():
            assert item["field_id"].startswith(f"{item['context']}.")

    def test_contexts_are_known(self) -> None:
        assert {item["context"] for item in _fields()} == {"global", "calc", "confgen"}

    def test_editors_and_value_types_are_ones_the_consumer_knows(self) -> None:
        for item in _fields():
            assert item["editor"] in ACCEPTED_EDITORS, item["field_id"]
            assert item["value_type"] in ACCEPTED_VALUE_TYPES, item["field_id"]

    def test_labels_and_descriptions_are_present_and_non_empty(self) -> None:
        for item in _fields():
            assert str(item["label"]).strip(), item["field_id"]
            assert str(item["description"]).strip(), item["field_id"]

    def test_groups_are_declared_and_non_empty(self) -> None:
        groups = {item["group"] for item in _fields()}

        assert "" not in groups
        assert groups == DECLARED_GROUPS

    def test_levels_are_basic_or_advanced(self) -> None:
        assert {item["level"] for item in _fields()} <= {"basic", "advanced"}

    def test_order_values_are_sane_and_unique_within_a_context(self) -> None:
        per_context: dict[str, list[int]] = {}
        for item in _fields():
            order = item["order"]
            assert isinstance(order, int) and not isinstance(order, bool)
            assert order >= 0
            per_context.setdefault(item["context"], []).append(order)

        for context, orders in per_context.items():
            assert len(set(orders)) == len(orders), context
            assert orders == sorted(orders), f"{context} fields are not declared in order"

    def test_read_only_fields_do_not_also_inherit(self) -> None:
        for item in _fields():
            assert not (item.get("read_only") and item.get("inherit_from")), item["field_id"]

    def test_item_type_only_appears_on_string_lists(self) -> None:
        for item in _fields():
            if "item_type" in item:
                assert item["editor"] == "string_list", item["field_id"]
                assert item["item_type"] in {"string", "integer"}


class TestChoiceConsistency:
    def test_only_selects_carry_choices(self) -> None:
        for item in _fields():
            if item["editor"] == "select":
                assert item.get("choices"), item["field_id"]
            else:
                assert "choices" not in item, item["field_id"]

    def test_choice_values_are_unique_and_labelled(self) -> None:
        for item in _fields():
            for choice in item.get("choices", []):
                assert set(choice) == {"value", "label"}
                assert str(choice["value"]).strip()
                assert str(choice["label"]).strip()
            values = [choice["value"] for choice in item.get("choices", [])]
            assert len(set(values)) == len(values), item["field_id"]

    def test_program_choices_come_from_the_program_literal(self) -> None:
        choices = [c["value"] for c in _by_id()["calc.program"]["choices"]]
        assert choices == list(get_args(ProgramName))

    def test_task_choices_come_from_the_task_literal(self) -> None:
        choices = [c["value"] for c in _by_id()["calc.task"]["choices"]]
        assert choices == list(get_args(TaskName))


class TestPointers:
    def test_every_pointer_is_absolute(self) -> None:
        for item in _fields():
            assert item["json_pointer"].startswith("/"), item["field_id"]

    def test_step_scoped_pointers_use_the_index_placeholder(self) -> None:
        for item in _fields():
            pointer = item["json_pointer"]
            if item["context"] == "global":
                assert "{index}" not in pointer, item["field_id"]
                assert pointer == f"/global/{item['field_id'].split('.', 1)[1]}"
            else:
                assert "{index}" in pointer, item["field_id"]
                assert pointer.startswith("/steps/{index}/params/")

    def test_no_two_fields_share_a_pointer(self) -> None:
        pointers = [item["json_pointer"] for item in _fields()]
        assert len(set(pointers)) == len(pointers)

    def test_global_pointers_name_real_global_members(self) -> None:
        """``GlobalOptions`` is a dataclass, so its members are the wire keys."""
        for item in _fields():
            if item["context"] != "global":
                continue
            member = item["json_pointer"].rsplit("/", 1)[-1]
            assert member in _GLOBAL_MEMBERS, item["field_id"]

    def test_confgen_pointers_name_real_confgen_members(self) -> None:
        """``confgen_known_keys`` is the resolver's own registry of accepted keys."""
        known = confgen_known_keys()
        for item in _fields():
            if item["context"] != "confgen":
                continue
            member = item["json_pointer"].rsplit("/", 1)[-1]
            assert member in known, item["field_id"]

    def test_calc_pointers_name_params_the_resolver_reads(self) -> None:
        for item in _fields():
            if item["context"] != "calc":
                continue
            member = item["json_pointer"].rsplit("/", 1)[-1]
            assert member in CALC_PARAM_MEMBERS, item["field_id"]


class TestConditions:
    def test_condition_operators_are_from_the_supported_set(self) -> None:
        for item in _fields():
            if "visible_when" not in item:
                continue
            operators = _operators(item["visible_when"])
            assert operators, item["field_id"]
            assert set(operators) <= {"equals", "not_equals", "in"}, item["field_id"]

    def test_a_leaf_condition_carries_exactly_one_operator(self) -> None:
        for item in _fields():
            condition = item.get("visible_when")
            if not isinstance(condition, dict) or "all" in condition or "any" in condition:
                continue
            assert len(_operators(condition)) == 1, item["field_id"]

    def test_the_in_operator_carries_a_list(self) -> None:
        for item in _fields():
            condition = item.get("visible_when")
            if isinstance(condition, dict) and "in" in condition:
                assert isinstance(condition["in"], list), item["field_id"]

    def test_conditions_reference_fields_that_exist(self) -> None:
        known = set(_by_id())
        for item in _fields():
            for reference in _referenced_fields(item.get("visible_when")):
                assert reference in known, f"{item['field_id']} references {reference}"

    def test_inherit_targets_exist_and_are_global(self) -> None:
        fields = _by_id()
        for item in _fields():
            target_id = item.get("inherit_from")
            if target_id is None:
                continue
            assert target_id in fields, item["field_id"]
            assert fields[target_id]["context"] == "global", item["field_id"]


class TestDefaultProvenance:
    """Every default must trace back to the source that owns the fact."""

    @pytest.mark.parametrize(
        ("field_id", "expected"),
        [
            ("global.charge", DEFAULT_CHARGE),
            ("global.multiplicity", DEFAULT_MULTIPLICITY),
            ("global.cores_per_task", DEFAULT_CORES_PER_TASK),
            ("global.total_memory", DEFAULT_TOTAL_MEMORY),
            ("global.max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS),
            ("global.scan_coarse_step", DEFAULT_SCAN_COARSE_STEP),
            ("global.ts_bond_drift_threshold", DEFAULT_TS_BOND_DRIFT_THRESHOLD),
            ("calc.program", DEFAULT_PROGRAM),
            ("calc.task", DEFAULT_TASK),
            ("calc.ts_rescue_scan", DEFAULT_TS_RESCUE_SCAN),
            ("confgen.angle_step", DEFAULT_CONFGEN_ANGLE_STEP),
            ("confgen.bond_multiplier", DEFAULT_CONFGEN_BOND_THRESHOLD),
        ],
    )
    def test_default_matches_its_owning_constant(self, field_id: str, expected: object) -> None:
        assert _by_id()[field_id]["default"] == expected

    def test_fields_without_a_producer_default_do_not_declare_one(self) -> None:
        """Absent means "ConfFlow has no default", which is different from null."""
        for field_id in (
            "global.freeze",
            "global.energy_window",
            "global.gaussian_path",
            "global.orca_path",
            "calc.keyword",
            "calc.orca_maxcore",
            "calc.ts_bond_atoms",
            "calc.blocks",
            "confgen.chains",
        ):
            assert "default" not in _by_id()[field_id], field_id

    def test_inheriting_fields_do_not_also_declare_a_default(self) -> None:
        """An inherited value comes from the global field, not from a second default."""
        for item in _fields():
            if item.get("inherit_from"):
                assert "default" not in item, item["field_id"]

    def test_the_program_and_task_defaults_are_legal_choices(self) -> None:
        assert DEFAULT_PROGRAM in get_args(ProgramName)
        assert DEFAULT_TASK in get_args(TaskName)

    def test_the_manifest_default_agrees_with_the_typed_model(self) -> None:
        """The manifest is not a second opinion about the producer's default."""
        options = GlobalOptions.from_mapping({})
        fields = _by_id()

        assert fields["calc.program"]["default"] == options.iprog
        assert fields["calc.task"]["default"] == options.itask
        assert fields["global.charge"]["default"] == options.charge
        assert fields["global.multiplicity"]["default"] == options.multiplicity
        assert fields["global.cores_per_task"]["default"] == options.cores_per_task
        assert fields["global.total_memory"]["default"] == options.total_memory
        assert fields["global.max_parallel_jobs"]["default"] == options.max_parallel_jobs

    def test_the_manifest_covers_every_global_option_it_claims_to(self) -> None:
        """Fields the editor shows for the global block are real global members."""
        shown = {
            item["json_pointer"].rsplit("/", 1)[-1]
            for item in _fields()
            if item["context"] == "global"
        }
        assert shown <= _GLOBAL_MEMBERS


class TestStepContexts:
    def test_confgen_and_calc_are_the_only_step_contexts(self) -> None:
        assert set(build_editor_manifest()["step_contexts"]) == {"calc", "confgen"}

    def test_every_step_context_has_fields(self) -> None:
        for context in build_editor_manifest()["step_contexts"].values():
            assert [item for item in _fields() if item["context"] == context]
