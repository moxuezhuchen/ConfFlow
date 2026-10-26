#!/usr/bin/env python3

"""Unit tests for the distance_window and graph_unchanged scientific checks.

Built over the water fixture from ``tests.v4._builders`` with the same
CheckContext/profile plumbing as ``test_v42_profiles_checks``.  The
``ts_mode`` registration is covered here only as catalogue membership.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain import ArtifactSet, FrozenDict, ResourceRequest
from confflow.execution import (
    CHECK_DEFAULTS,
    CheckContext,
    CheckOutcome,
    GeometryOutput,
    NativeResult,
    ParsedGeometry,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.checks_standard import (
    CHECK_FAILED_CODE,
    CHECKS,
    DistanceWindowCheck,
    GraphUnchangedCheck,
    bond_length_angstrom,
)
from confflow.execution.profile_standard import PROFILES
from confflow.execution.profiles import ProfileContext
from confflow.execution.registry import _default_checks
from confflow.producer.validation import validate_workflow_bytes
from tests.v4._builders import calc_step, structure

STEP_ID = "s_ts"
WORK_ITEM_ID = "wi:s_ts:item0"
LOGICAL_KEY = "s_ts:item0"
KEYWORD = "B3LYP/6-31G* Opt"
Geometry = tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]


def _water() -> Any:
    return structure("s0")


def resolved_inputs() -> ResolvedCalculationInputs:
    return ResolvedCalculationInputs(
        structure=_water(),
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        native=FrozenDict({"keyword": KEYWORD}),
        checkpoints=(),
        step_id=STEP_ID,
        work_item_id=WORK_ITEM_ID,
        logical_key=LOGICAL_KEY,
    )


def native_result(geometry: Geometry | None) -> NativeResult:
    if geometry is None:
        output, final = GeometryOutput.NONE, None
    else:
        atoms, coords = geometry
        output = GeometryOutput.PRODUCED
        final = ParsedGeometry(atoms=atoms, coordinates=coords)
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=True,
        geometry_output=output,
        final_geometry=final,
        energies_hartree=FrozenDict({}),
        frequencies_cm=(),
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="job.log",
    )


def run_check(
    name: str, geometry: Geometry | None, params: dict[str, Any] | None = None
) -> CheckOutcome:
    profile_output = PROFILES["standard"].apply(
        ProfileContext(
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_name="standard",
            profile_version=PROFILES["standard"].contract_version,
            native_result=native_result(geometry),
            inputs=resolved_inputs(),
            discovered_artifacts=ArtifactSet(),
        )
    )
    return CHECKS[name].run(
        CheckContext(
            check_name=name,
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_output=profile_output,  # type: ignore[arg-type]
            inputs=resolved_inputs(),
            params=FrozenDict(dict(params or {})),
        )
    )


def _input_geometry() -> Geometry:
    record = _water()
    return record.atoms, record.coordinates


def _reason(outcome: CheckOutcome) -> str:
    assert outcome.passed is False
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == CHECK_FAILED_CODE
    return str(outcome.diagnostic.details["reason"])


def _input_distance(a: int, b: int) -> float:
    distance = bond_length_angstrom(_water(), a, b)
    assert distance is not None
    return distance


def _shift_atom(index: int, delta: tuple[float, float, float]) -> Geometry:
    record = _water()
    coords = tuple(
        tuple(c + d for c, d in zip(xyz, delta)) if i == index else xyz
        for i, xyz in enumerate(record.coordinates)
    )
    return record.atoms, coords


class TestRegistration:
    def test_catalogue_contains_new_checks(self) -> None:
        assert {"distance_window", "graph_unchanged", "ts_mode"} <= set(CHECKS)
        assert set(CHECK_DEFAULTS) == set(CHECKS)

    def test_defaults(self) -> None:
        assert CHECK_DEFAULTS["distance_window"] == {}
        assert CHECK_DEFAULTS["graph_unchanged"] == {"bond_scale": 1.15, "ignore_pairs": []}
        assert CHECK_DEFAULTS["ts_mode"] == {"min_overlap": 0.2}

    def test_contract_versions(self) -> None:
        assert CHECKS["distance_window"].contract_version == (
            "confflow.contract.check.distance_window.v1"
        )
        assert CHECKS["graph_unchanged"].contract_version == (
            "confflow.contract.check.graph_unchanged.v1"
        )

    def test_check_specs_declared(self) -> None:
        specs = {spec.name: spec for spec in _default_checks()}
        assert specs["distance_window"].contract_version.endswith("distance_window.v1")
        assert specs["graph_unchanged"].contract_version.endswith("graph_unchanged.v1")
        assert specs["ts_mode"].contract_version == "confflow.contract.check.ts_mode.v1"
        assert specs["ts_mode"].description.startswith("Check that the lowest imaginary mode")


class TestDistanceWindow:
    def test_inside_window_passes(self) -> None:
        d = _input_distance(1, 2)
        outcome = run_check(
            "distance_window",
            _input_geometry(),
            {"distances": [[1, 2, d - 0.1, d + 0.1]]},
        )
        assert outcome.passed is True

    def test_closed_bounds_pass(self) -> None:
        d = _input_distance(1, 2)
        outcome = run_check("distance_window", _input_geometry(), {"distances": [[2, 1, d, d]]})
        assert outcome.passed is True

    def test_all_violations_listed(self) -> None:
        d12 = _input_distance(1, 2)
        d13 = _input_distance(1, 3)
        outcome = run_check(
            "distance_window",
            _input_geometry(),
            {"distances": [[1, 2, d12 + 0.5, d12 + 1.0], [1, 3, 0.0, 0.1], [2, 3, 0.0, 10.0]]},
        )
        assert _reason(outcome) == "distance_out_of_window"
        details = outcome.diagnostic.details  # type: ignore[union-attr]
        assert len(details["distances"]) == 3
        assert [tuple(v["atoms"]) for v in details["violations"]] == [(1, 2), (1, 3)]
        assert details["violations"][1]["observed"] == pytest.approx(d13)
        assert details["violations"][0]["window"] == pytest.approx([d12 + 0.5, d12 + 1.0])
        message = outcome.diagnostic.message  # type: ignore[union-attr]
        assert "1-2" in message and "1-3" in message and "2-3" in message

    def test_evaluates_output_structure(self) -> None:
        moved = _shift_atom(1, (1.0, 0.0, 0.0))
        d_in = _input_distance(1, 2)
        outcome = run_check(
            "distance_window", moved, {"distances": [[1, 2, d_in - 0.1, d_in + 0.1]]}
        )
        assert _reason(outcome) == "distance_out_of_window"

    def test_falls_back_to_input_without_output(self) -> None:
        d = _input_distance(1, 2)
        passing = run_check("distance_window", None, {"distances": [[1, 2, d - 0.1, d + 0.1]]})
        assert passing.passed is True
        failing = run_check("distance_window", None, {"distances": [[1, 2, 5.0, 6.0]]})
        assert _reason(failing) == "distance_out_of_window"

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"distances": []},
            {"distances": "1-2"},
            {"distances": [[1, 2, 0.0]]},
            {"distances": [[1, 2, 0.0, 1.0, 9]]},
            {"distances": [[1, 1, 0.0, 1.0]]},
            {"distances": [[0, 2, 0.0, 1.0]]},
            {"distances": [[1.5, 2, 0.0, 1.0]]},
            {"distances": [[True, 2, 0.0, 1.0]]},
            {"distances": [[1, 2, -0.1, 1.0]]},
            {"distances": [[1, 2, 2.0, 1.0]]},
            {"distances": [[1, 2, "a", 1.0]]},
            {"distances": [[1, 2, True, 1.0]]},
            {"distances": ["12"]},
        ],
    )
    def test_invalid_parameters(self, params: dict[str, Any]) -> None:
        outcome = run_check("distance_window", _input_geometry(), params)
        assert _reason(outcome) == "invalid_check_parameter"

    def test_unresolvable_atoms(self) -> None:
        outcome = run_check(
            "distance_window", _input_geometry(), {"distances": [[1, 2, 0.0, 9.0], [1, 99, 0, 9]]}
        )
        assert _reason(outcome) == "bond_atoms_missing"
        assert outcome.diagnostic is not None
        assert [tuple(item) for item in outcome.diagnostic.details["atoms"]] == [(1, 99)]

    def test_direct_instance_name(self) -> None:
        assert DistanceWindowCheck().name == "distance_window"


class TestGraphUnchanged:
    def test_identical_graph_passes(self) -> None:
        assert run_check("graph_unchanged", _input_geometry()).passed is True

    def test_broken_bond_fails(self) -> None:
        outcome = run_check("graph_unchanged", _shift_atom(1, (3.0, 0.0, 0.0)))
        assert _reason(outcome) == "graph_changed"
        details = outcome.diagnostic.details  # type: ignore[union-attr]
        assert "1-2" in details["broken"]
        assert len(details["formed"]) == 0
        assert "broken [" in outcome.diagnostic.message  # type: ignore[union-attr]

    def test_formed_bond_fails(self) -> None:
        # Put H3 on top of H2 so that the 2-3 contact is covalent in the output.
        record = _water()
        h2 = record.coordinates[1]
        coords = list(record.coordinates)
        coords[2] = (h2[0] + 0.6, h2[1], h2[2])
        outcome = run_check("graph_unchanged", (record.atoms, tuple(coords)))
        assert _reason(outcome) == "graph_changed"
        details = outcome.diagnostic.details  # type: ignore[union-attr]
        assert "2-3" in details["formed"]

    def test_ignore_pairs_removes_expected_change(self) -> None:
        moved = _shift_atom(1, (3.0, 0.0, 0.0))
        assert run_check("graph_unchanged", moved, {"ignore_pairs": [[1, 2]]}).passed is True
        assert run_check("graph_unchanged", moved, {"ignore_pairs": [[2, 1]]}).passed is True

    def test_ignore_pairs_keeps_other_changes(self) -> None:
        moved = _shift_atom(1, (3.0, 0.0, 0.0))
        outcome = run_check("graph_unchanged", moved, {"ignore_pairs": [[1, 3]]})
        assert _reason(outcome) == "graph_changed"

    def test_bond_scale_changes_perception(self) -> None:
        moved = _shift_atom(1, (3.0, 0.0, 0.0))
        # A vanishingly small scale perceives no bonds on either side.
        assert run_check("graph_unchanged", moved, {"bond_scale": 0.05}).passed is True

    @pytest.mark.parametrize("scale", [0, -1.0, "wide", True])
    def test_invalid_bond_scale(self, scale: Any) -> None:
        outcome = run_check("graph_unchanged", _input_geometry(), {"bond_scale": scale})
        assert _reason(outcome) == "invalid_check_parameter"

    @pytest.mark.parametrize(
        "ignore",
        ["1-2", [[1]], [[1, 1]], [[0, 2]], [1, 2], [[1, 2, 3]], [[True, 2]]],
    )
    def test_invalid_ignore_pairs(self, ignore: Any) -> None:
        outcome = run_check("graph_unchanged", _input_geometry(), {"ignore_pairs": ignore})
        assert _reason(outcome) == "invalid_check_parameter"

    def test_missing_output_falls_back_to_input(self) -> None:
        # An absent or passthrough output is the input itself: graph unchanged.
        assert run_check("graph_unchanged", None).passed is True

    def test_atom_count_mismatch_fails_closed(self) -> None:
        record = _water()
        outcome = run_check("graph_unchanged", (record.atoms[:2], record.coordinates[:2]))
        assert _reason(outcome) == "graph_uncomputable"

    def test_perception_error_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from confflow.execution import checks_standard

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise ValueError("bond perception failed: injected")

        monkeypatch.setattr(checks_standard, "perceive_adjacency", _boom)
        outcome = run_check("graph_unchanged", _input_geometry())
        assert _reason(outcome) == "graph_uncomputable"
        assert "injected" in outcome.diagnostic.message  # type: ignore[union-attr]

    def test_direct_instance_name(self) -> None:
        assert GraphUnchangedCheck().name == "graph_unchanged"


class TestWorkflowValidation:
    def test_ts_step_with_new_checks_validates(self) -> None:
        import json

        step = calc_step(
            "ts_step",
            bindings={"structure": {"source": {"run": "structures"}}},
            program="g16",
            role="ts",
            checks=[
                "normal_termination",
                "imaginary_frequency_count",
                "distance_window",
                "ts_mode",
                "graph_unchanged",
            ],
        )
        step["calculation"]["check_params"] = {
            "imaginary_frequency_count": {"expected": 1},
            "distance_window": {"distances": [[39, 95, 1.6, 2.1], [95, 96, 1.24, 1.30]]},
            "ts_mode": {"pairs": [[39, 95]], "min_overlap": 0.2},
            "graph_unchanged": {"ignore_pairs": [[39, 95]], "bond_scale": 1.15},
        }
        document = {
            "schema": "confflow.workflow.v4",
            "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "steps": [step],
        }
        report = validate_workflow_bytes(json.dumps(document).encode("utf-8"))
        assert report.ok is True, report
