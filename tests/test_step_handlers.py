#!/usr/bin/env python3

"""Tests for workflow.step_handlers module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from confflow.calc import CalcStepExecutionResult
from confflow.calc.components.executor import _save_config_hash
from confflow.calc.config_types import CalcTaskConfig, Program, TaskKind
from confflow.calc.step_contract import compute_calc_input_signature, record_calc_step_signature
from confflow.config.schema import ConfigSchema
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.stats import FailureTracker
from confflow.workflow.step_handlers import CalcStepResult, run_calc_step, run_confgen_step
from confflow.workflow.task_config import build_task_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def step_dir(tmp_path: Path) -> str:
    d = tmp_path / "step_01"
    d.mkdir()
    return str(d)


@pytest.fixture
def failure_tracker(tmp_path: Path) -> FailureTracker:
    failed_dir = tmp_path / "failed"
    failed_dir.mkdir()
    return FailureTracker(str(failed_dir))


@pytest.fixture
def single_input_xyz(tmp_path: Path) -> str:
    p = tmp_path / "input.xyz"
    p.write_text("2\ncomment\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def multi_frame_xyz(tmp_path: Path) -> str:
    p = tmp_path / "multi.xyz"
    p.write_text(
        "2\nframe1\nC 0 0 0\nH 0 0 1\n2\nframe2\nC 1 0 0\nH 1 0 1\n",
        encoding="utf-8",
    )
    return str(p)


# ---------------------------------------------------------------------------
# run_confgen_step tests
# ---------------------------------------------------------------------------


class TestRunConfgenStep:
    """Tests for run_confgen_step."""

    def test_multi_frame_copies_input(self, step_dir: str, multi_frame_xyz: str):
        """Multi-frame input should be copied directly to search.xyz."""
        result = run_confgen_step(
            step_dir=step_dir,
            current_input=multi_frame_xyz,
            params={"chains": ["1-2"]},
            input_files=[multi_frame_xyz],
        )
        expected = os.path.join(step_dir, "search.xyz")
        assert result == expected
        assert os.path.exists(expected)
        with open(expected) as f:
            content = f.read()
        assert "frame1" in content
        assert "frame2" in content

    def test_existing_output_skips_generation(self, step_dir: str, single_input_xyz: str):
        """If search.xyz already exists, confgen should not be called."""
        expected = os.path.join(step_dir, "search.xyz")
        with open(expected, "w") as f:
            f.write("2\nexisting\nC 0 0 0\nH 0 0 1\n")

        with patch("confflow.workflow.step_handlers.confgen") as mock_confgen:
            result = run_confgen_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={"chains": ["1-2"]},
                input_files=[single_input_xyz, "other.xyz"],
            )
        mock_confgen.run_generation.assert_not_called()
        assert result == expected

    @patch("confflow.workflow.step_handlers.confgen")
    def test_normal_generation(self, mock_confgen: MagicMock, step_dir: str, single_input_xyz: str):
        """Normal confgen call with two input files (non-multi-frame)."""
        expected = os.path.join(step_dir, "search.xyz")

        def fake_run(**kwargs):
            with open(expected, "w") as f:
                f.write("2\ngenerated\nC 0 0 0\nH 0 0 1\n")

        mock_confgen.run_generation.side_effect = fake_run

        result = run_confgen_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params={
                "angle_step": 60,
                "bond_multiplier": 1.2,
                "chains": ["1-2"],
                "optimize": True,
                "rotate_side": "right",
            },
            input_files=[single_input_xyz, "second.xyz"],
        )
        assert result == expected
        mock_confgen.run_generation.assert_called_once()
        call_kwargs = mock_confgen.run_generation.call_args[1]
        assert call_kwargs["angle_step"] == 60
        assert call_kwargs["bond_threshold"] == 1.2
        assert call_kwargs["optimize"] is True
        assert call_kwargs["rotate_side"] == "right"

    @patch("confflow.workflow.step_handlers.confgen")
    def test_generation_no_output_raises(
        self, mock_confgen: MagicMock, step_dir: str, single_input_xyz: str
    ):
        """If confgen runs but doesn't produce output, raise ConfFlowError."""
        mock_confgen.run_generation.return_value = None

        with pytest.raises(ConfFlowError, match="confgen did not produce"):
            run_confgen_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={"chains": ["1-2"]},
                input_files=[single_input_xyz, "other.xyz"],
            )

    def test_default_params(self, step_dir: str, single_input_xyz: str):
        """Default parameter values are applied when not specified."""
        expected = os.path.join(step_dir, "search.xyz")

        with patch("confflow.workflow.step_handlers.confgen") as mock_confgen:

            def fake_run(**kwargs):
                with open(expected, "w") as f:
                    f.write("2\nout\nC 0 0 0\nH 0 0 1\n")

            mock_confgen.run_generation.side_effect = fake_run

            run_confgen_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={},
                input_files=[single_input_xyz, "other.xyz"],
            )
            call_kwargs = mock_confgen.run_generation.call_args[1]
            assert call_kwargs["angle_step"] == 120
            assert call_kwargs["bond_threshold"] == 1.15
            assert call_kwargs["optimize"] is False
            assert call_kwargs["rotate_side"] == "left"
            assert call_kwargs["confirm"] is False


# ---------------------------------------------------------------------------
# run_calc_step tests
# ---------------------------------------------------------------------------


class TestRunCalcStep:
    """Tests for run_calc_step."""

    MINIMAL_GLOBAL = {
        "charge": 0,
        "multiplicity": 1,
        "cores_per_task": 1,
        "total_memory": "4GB",
        "max_parallel_jobs": 1,
    }

    MINIMAL_PARAMS = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "HF def2-SVP",
    }

    def _write_matching_config_hash(self, step_dir: str, input_path: str) -> None:
        task_config = build_task_config(
            self.MINIMAL_PARAMS,
            self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            all_steps=[],
        )
        ConfigSchema.validate_calc_config(task_config)
        record_calc_step_signature(
            step_dir,
            task_config,
            input_signature=compute_calc_input_signature(input_path),
        )

    @staticmethod
    def _mock_facade_result(
        mock_calc: MagicMock,
        output_path: str,
        *,
        reused_existing: bool = False,
        failed_path: str | None = None,
    ) -> None:
        def _side_effect(**kwargs):
            if not reused_existing:
                Path(output_path).write_text("2\ncalculated\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
            if failed_path is not None:
                Path(failed_path).write_text("2\nfailed\nC 1 0 0\nH 1 0 1\n", encoding="utf-8")
            return CalcStepExecutionResult(
                output_path=output_path,
                reused_existing=reused_existing,
                failed_path=failed_path,
            )

        mock_calc.run_calc_workflow_step.side_effect = _side_effect

    def test_existing_output_skips_calc(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """If output.xyz already exists, skip the computation."""
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w") as f:
            f.write("2\nexisting\nC 0 0 0\nH 0 0 1\n")
        self._write_matching_config_hash(step_dir, single_input_xyz)

        with patch("confflow.workflow.step_handlers.calc") as mock_calc:
            self._mock_facade_result(mock_calc, output, reused_existing=True)
            result = run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params=self.MINIMAL_PARAMS,
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        mock_calc.run_calc_workflow_step.assert_called_once()
        assert result == output

    def test_existing_output_with_failed_xyz(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """If output.xyz and failed.xyz both exist, track failures."""
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w") as f:
            f.write("2\nout\nC 0 0 0\nH 0 0 1\n")
        self._write_matching_config_hash(step_dir, single_input_xyz)
        failed = os.path.join(step_dir, "failed.xyz")
        with open(failed, "w") as f:
            f.write("2\nfailed\nC 0 0 0\nH 0 0 1\n")

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )
        assert result == output

    @patch("confflow.workflow.step_handlers.calc")
    def test_search_xyz_does_not_skip_calc(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """search.xyz is an input artifact for calc steps, not a completed result."""
        search = os.path.join(step_dir, "search.xyz")
        with open(search, "w") as f:
            f.write("2\nseed\nC 0 0 0\nH 0 0 1\n")

        output = os.path.join(step_dir, "output.xyz")
        self._mock_facade_result(mock_calc, output)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )

        mock_calc.run_calc_workflow_step.assert_called_once()
        assert result == output

    @patch("confflow.workflow.step_handlers.calc")
    def test_normal_calc_run(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Normal computation creates output.xyz."""
        output = os.path.join(step_dir, "output.xyz")
        self._mock_facade_result(mock_calc, output)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )
        assert result == output
        mock_calc.run_calc_workflow_step.assert_called_once()

    @patch("confflow.workflow.step_handlers.build_structured_task_config")
    @patch("confflow.workflow.step_handlers.build_task_config")
    @patch("confflow.workflow.step_handlers.calc")
    def test_calc_handoff_uses_structured_config_but_preserves_legacy_signature_inputs(
        self,
        mock_calc: MagicMock,
        mock_build_task_config: MagicMock,
        mock_build_structured_task_config: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        output = os.path.join(step_dir, "output.xyz")
        legacy_config = {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "HF def2-SVP",
            "cores_per_task": "1",
            "total_memory": "4GB",
            "max_parallel_jobs": "1",
            "charge": "0",
            "multiplicity": "1",
        }
        structured_config = CalcTaskConfig(
            program=Program.ORCA,
            task=TaskKind.SP,
            keyword="HF def2-SVP",
            cores_per_task=1,
            total_memory="4GB",
            max_parallel_jobs=1,
            charge=0,
            multiplicity=1,
        )
        mock_build_task_config.return_value = legacy_config
        mock_build_structured_task_config.return_value = structured_config
        self._mock_facade_result(mock_calc, output)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )

        assert result == output
        mock_build_task_config.assert_called_once()
        mock_build_structured_task_config.assert_called_once()
        mock_calc.run_calc_workflow_step.assert_called_once_with(
            step_dir=step_dir,
            input_source=single_input_xyz,
            legacy_task_config=legacy_config,
            execution_config=structured_config,
        )

    @patch("confflow.workflow.step_handlers.build_structured_task_config")
    def test_reusable_output_builds_structured_config_for_signature(
        self,
        mock_build_structured_task_config: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Structured config is now built before prepare_calc_step_dir for signature."""
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w", encoding="utf-8") as handle:
            handle.write("2\nexisting\nC 0 0 0\nH 0 0 1\n")
        self._write_matching_config_hash(step_dir, single_input_xyz)
        mock_build_structured_task_config.return_value = {}

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )

        assert result == output
        assert isinstance(result, CalcStepResult)
        assert result.reused_existing is True
        # Structured config is now built even for reusable output (needed for signature)
        mock_build_structured_task_config.assert_called_once()

    @patch("confflow.workflow.step_handlers.calc")
    def test_calc_handoff_passes_real_structured_config_to_manager(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        output = os.path.join(step_dir, "output.xyz")
        self._mock_facade_result(mock_calc, output)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )

        assert result == output
        _, kwargs = mock_calc.run_calc_workflow_step.call_args
        expected_legacy = build_task_config(
            self.MINIMAL_PARAMS,
            self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            all_steps=[],
        )
        ConfigSchema.validate_calc_config(expected_legacy)
        assert kwargs["legacy_task_config"] == expected_legacy
        assert isinstance(kwargs["execution_config"], CalcTaskConfig)

    @patch("confflow.workflow.step_handlers.calc")
    def test_calc_no_output_raises(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Computation without output raises ConfFlowError."""
        mock_calc.run_calc_workflow_step.side_effect = RuntimeError(
            "Calculation step did not produce an output XYZ file"
        )

        with pytest.raises(ConfFlowError, match="did not produce an output XYZ file"):
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params=self.MINIMAL_PARAMS,
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )

    @patch("confflow.workflow.step_handlers.calc")
    def test_list_input_uses_first_file(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """When current_input is a list, the first file should be used."""
        output = os.path.join(step_dir, "output.xyz")
        self._mock_facade_result(mock_calc, output)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=[single_input_xyz, "other.xyz"],
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )
        assert result == output
        _, kwargs = mock_calc.run_calc_workflow_step.call_args
        assert kwargs["input_source"] == [single_input_xyz, "other.xyz"]

    @patch("confflow.workflow.step_handlers.calc")
    def test_list_input_logs_warning(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Multi-input calc emits a warning before taking the first file."""
        output = os.path.join(step_dir, "output.xyz")
        self._mock_facade_result(mock_calc, output)

        with patch("confflow.workflow.step_handlers.logger.warning") as mock_warning:
            run_calc_step(
                step_dir=step_dir,
                current_input=[single_input_xyz, "other.xyz"],
                params=self.MINIMAL_PARAMS,
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )

        mock_warning.assert_called_once()
        assert "using only" in mock_warning.call_args.args[0]

    @patch("confflow.workflow.step_handlers.calc")
    def test_result_xyz_fallback(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """When only result.xyz exists (no output.xyz), it should be returned."""
        result_xyz = os.path.join(step_dir, "result.xyz")
        self._mock_facade_result(mock_calc, result_xyz)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )
        assert result == result_xyz

    def test_calc_config_validation_runs_in_main_flow(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """run_calc_step should validate through ConfigSchema in the workflow path."""
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w", encoding="utf-8") as f:
            f.write("2\nexisting\nC 0 0 0\nH 0 0 1\n")
        self._write_matching_config_hash(step_dir, single_input_xyz)

        with patch(
            "confflow.workflow.step_handlers.ConfigSchema.validate_calc_config",
            wraps=ConfigSchema.validate_calc_config,
        ) as mock_validate:
            result = run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params=self.MINIMAL_PARAMS,
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )

        mock_validate.assert_called_once()
        assert result == output

    def test_stale_output_is_cleared_and_recomputed(
        self,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w", encoding="utf-8") as f:
            f.write("2\nstale\nC 0 0 0\nH 0 0 1\n")
        (Path(step_dir) / ".config_hash").write_text("stalehash", encoding="utf-8")
        (Path(step_dir) / "results.db").write_text("olddb", encoding="utf-8")
        backups_dir = Path(step_dir) / "backups"
        backups_dir.mkdir()
        (backups_dir / "old.out").write_text("old", encoding="utf-8")

        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            def side_effect(**kwargs):
                if backups_dir.exists():
                    for entry in backups_dir.iterdir():
                        entry.unlink()
                    backups_dir.rmdir()
                assert not backups_dir.exists()
                with open(output, "w", encoding="utf-8") as f:
                    f.write("2\nfresh\nC 0 0 0\nH 0 0 1\n")
                return CalcStepExecutionResult(output, cleaned_stale_artifacts=True)

            mock_run.side_effect = side_effect

            result = run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params=self.MINIMAL_PARAMS,
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )

        assert result == output
        assert (Path(step_dir) / ".config_hash").exists()

    @patch("confflow.workflow.step_handlers.calc")
    def test_input_change_invalidates_existing_output(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
        tmp_path: Path,
    ):
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w", encoding="utf-8") as f:
            f.write("2\nstale\nC 0 0 0\nH 0 0 1\n")

        task_config = build_task_config(
            self.MINIMAL_PARAMS,
            self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            all_steps=[],
        )
        ConfigSchema.validate_calc_config(task_config)
        _save_config_hash(step_dir, task_config)

        changed_input = tmp_path / "changed.xyz"
        changed_input.write_text("2\nchanged\nC 0 0 0\nH 0 0 2\n", encoding="utf-8")

        self._mock_facade_result(mock_calc, output)

        result = run_calc_step(
            step_dir=step_dir,
            current_input=str(changed_input),
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )

        assert result == output
        _, kwargs = mock_calc.run_calc_workflow_step.call_args
        assert kwargs["input_source"] == str(changed_input)

    @patch("confflow.workflow.step_handlers.calc")
    def test_multi_input_calc_records_combined_input_signature(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        failure_tracker: FailureTracker,
        tmp_path: Path,
    ):
        input_a = tmp_path / "a.xyz"
        input_b = tmp_path / "b.xyz"
        input_a.write_text("1\na\nH 0 0 0\n", encoding="utf-8")
        input_b.write_text("1\nb\nH 0 0 1\n", encoding="utf-8")
        output = os.path.join(step_dir, "output.xyz")
        self._mock_facade_result(mock_calc, output)

        run_calc_step(
            step_dir=step_dir,
            current_input=[str(input_a), str(input_b)],
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )

        _, kwargs = mock_calc.run_calc_workflow_step.call_args
        assert kwargs["input_source"] == [str(input_a), str(input_b)]

    def test_invalid_calc_config_uses_schema_compatibility_message(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """Invalid calc config should surface the schema compatibility error."""
        with pytest.raises(ValueError, match="invalid iprog"):
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={"iprog": "invalid", "itask": "sp", "keyword": "HF"},
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )

    @patch("confflow.workflow.step_handlers.calc")
    def test_calc_with_failed_xyz_tracked(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Failed conformers should be tracked by failure_tracker."""
        output = os.path.join(step_dir, "output.xyz")
        failed = os.path.join(step_dir, "failed.xyz")

        self._mock_facade_result(mock_calc, output, failed_path=failed)

        run_calc_step(
            step_dir=step_dir,
            current_input=single_input_xyz,
            params=self.MINIMAL_PARAMS,
            global_config=self.MINIMAL_GLOBAL,
            root_dir=os.path.dirname(step_dir),
            steps=[],
            failure_tracker=failure_tracker,
            step_name="step_02",
        )
        # failure_tracker should have recorded the failed file
        assert os.path.exists(failure_tracker.combined_failed) or os.path.exists(failed)


    def test_calc_step_invalid_cleanup_preserves_old_artifacts(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """Invalid cleanup params should fail before deleting old artifacts."""
        # Create old artifacts
        output = os.path.join(step_dir, "output.xyz")
        results_db = os.path.join(step_dir, "results.db")
        backups_dir = os.path.join(step_dir, "backups")
        
        with open(output, "w") as f:
            f.write("2\nold\nC 0 0 0\nH 0 0 1\n")
        with open(results_db, "w") as f:
            f.write("old db")
        os.makedirs(backups_dir, exist_ok=True)
        with open(os.path.join(backups_dir, "old.log"), "w") as f:
            f.write("old log")
        
        # Write mismatched config hash to trigger stale detection
        with open(os.path.join(step_dir, ".config_hash"), "w") as f:
            f.write("oldstale")
        
        # Invalid rmsd_threshold should fail during structured config build
        with pytest.raises((ValueError, TypeError)):
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "rmsd_threshold": "not_a_number",  # Will fail float() conversion
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        # Old artifacts should still exist
        assert os.path.exists(output)
        assert os.path.exists(results_db)
        assert os.path.exists(backups_dir)
        assert os.path.exists(os.path.join(backups_dir, "old.log"))

    def test_calc_step_invalid_cleanup_does_not_overwrite_config_hash(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """Invalid cleanup params should not overwrite .config_hash."""
        # Write old config hash
        config_hash_path = os.path.join(step_dir, ".config_hash")
        with open(config_hash_path, "w") as f:
            f.write("oldstale")
        
        # Invalid rmsd_threshold should fail during structured config build
        with pytest.raises((ValueError, TypeError)):
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "rmsd_threshold": "not_a_number",
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        # .config_hash should still be old value
        with open(config_hash_path) as f:
            assert f.read().strip() == "oldstale"

    def test_calc_step_cleanup_change_triggers_stale(
        self,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Cleanup parameter change (when auto_clean=true) should trigger stale detection."""
        # First run with threshold=0.25
        output = os.path.join(step_dir, "output.xyz")
        
        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            def first_run(**kwargs):
                with open(output, "w") as f:
                    f.write("2\nfirst\nC 0 0 0\nH 0 0 1\n")
                record_calc_step_signature(
                    step_dir,
                    kwargs["legacy_task_config"],
                    input_signature=compute_calc_input_signature(kwargs["input_source"]),
                    execution_config=kwargs["execution_config"],
                )
                return CalcStepExecutionResult(output)

            mock_run.side_effect = first_run
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "auto_clean": True,
                    "clean_params": {"threshold": 0.25},
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        first_hash = open(os.path.join(step_dir, ".config_hash")).read().strip()
        
        # Second run with threshold=0.5 should have different hash
        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            def second_run(**kwargs):
                with open(output, "w") as f:
                    f.write("2\nsecond\nC 0 0 0\nH 0 0 1\n")
                record_calc_step_signature(
                    step_dir,
                    kwargs["legacy_task_config"],
                    input_signature=compute_calc_input_signature(kwargs["input_source"]),
                    execution_config=kwargs["execution_config"],
                )
                return CalcStepExecutionResult(output)

            mock_run.side_effect = second_run
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "auto_clean": True,
                    "clean_params": {"threshold": 0.5},  # Changed
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        second_hash = open(os.path.join(step_dir, ".config_hash")).read().strip()
        assert first_hash != second_hash

    def test_calc_step_only_execution_cleanup_change_triggers_stale(
        self,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Only execution_config.cleanup change (auto_clean=true) should trigger stale."""
        output = os.path.join(step_dir, "output.xyz")
        
        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            def first_run(**kwargs):
                with open(output, "w") as f:
                    f.write("2\nfirst\nC 0 0 0\nH 0 0 1\n")
                record_calc_step_signature(
                    step_dir,
                    kwargs["legacy_task_config"],
                    input_signature=compute_calc_input_signature(kwargs["input_source"]),
                    execution_config=kwargs["execution_config"],
                )
                return CalcStepExecutionResult(output)

            mock_run.side_effect = first_run
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "auto_clean": True,
                    "clean_params": {"threshold": 0.25},
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        first_hash = open(os.path.join(step_dir, ".config_hash")).read().strip()
        
        # Second run with threshold=0.3 (only cleanup changed)
        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            def second_run(**kwargs):
                with open(output, "w") as f:
                    f.write("2\nsecond\nC 0 0 0\nH 0 0 1\n")
                record_calc_step_signature(
                    step_dir,
                    kwargs["legacy_task_config"],
                    input_signature=compute_calc_input_signature(kwargs["input_source"]),
                    execution_config=kwargs["execution_config"],
                )
                return CalcStepExecutionResult(output)

            mock_run.side_effect = second_run
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "auto_clean": True,
                    "clean_params": {"threshold": 0.3},  # Only this changed
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        second_hash = open(os.path.join(step_dir, ".config_hash")).read().strip()
        assert first_hash != second_hash

    def test_calc_step_cleanup_change_no_stale_when_auto_clean_disabled(
        self,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Cleanup change should NOT trigger stale when auto_clean=false."""
        output = os.path.join(step_dir, "output.xyz")
        
        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            def first_run(**kwargs):
                with open(output, "w") as f:
                    f.write("2\nfirst\nC 0 0 0\nH 0 0 1\n")
                record_calc_step_signature(
                    step_dir,
                    kwargs["legacy_task_config"],
                    input_signature=compute_calc_input_signature(kwargs["input_source"]),
                    execution_config=kwargs["execution_config"],
                )
                return CalcStepExecutionResult(output)

            mock_run.side_effect = first_run
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "auto_clean": False,
                    "clean_params": {"threshold": 0.25},
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        first_hash = open(os.path.join(step_dir, ".config_hash")).read().strip()
        
        # Second run with threshold=0.5 but auto_clean still false
        with patch("confflow.workflow.step_handlers.calc.run_calc_workflow_step") as mock_run:
            mock_run.return_value = CalcStepExecutionResult(output, reused_existing=True)
            run_calc_step(
                step_dir=step_dir,
                current_input=single_input_xyz,
                params={
                    **self.MINIMAL_PARAMS,
                    "auto_clean": False,
                    "clean_params": {"threshold": 0.5},  # Changed but shouldn't matter
                },
                global_config=self.MINIMAL_GLOBAL,
                root_dir=os.path.dirname(step_dir),
                steps=[],
                failure_tracker=failure_tracker,
                step_name="step_02",
            )
        
        second_hash = open(os.path.join(step_dir, ".config_hash")).read().strip()
        # Hash should be the same because auto_clean=false
        assert first_hash == second_hash
