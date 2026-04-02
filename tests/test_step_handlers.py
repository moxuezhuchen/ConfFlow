#!/usr/bin/env python3

"""Tests for workflow.step_handlers module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from confflow.core.exceptions import ConfFlowError
from confflow.workflow.stats import FailureTracker
from confflow.workflow.step_handlers import run_calc_step, run_confgen_step

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

    def test_existing_output_skips_calc(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """If output.xyz already exists, skip the computation."""
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w") as f:
            f.write("2\nexisting\nC 0 0 0\nH 0 0 1\n")

        with patch("confflow.workflow.step_handlers.calc") as mock_calc:
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
        mock_calc.ChemTaskManager.assert_not_called()
        assert result == output

    def test_existing_output_with_failed_xyz(
        self, step_dir: str, single_input_xyz: str, failure_tracker: FailureTracker
    ):
        """If output.xyz and failed.xyz both exist, track failures."""
        output = os.path.join(step_dir, "output.xyz")
        with open(output, "w") as f:
            f.write("2\nout\nC 0 0 0\nH 0 0 1\n")
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

        def fake_run(input_xyz_file):
            with open(output, "w") as f:
                f.write("2\ncalculated\nC 0 0 0\nH 0 0 1\n")

        mock_manager = MagicMock()
        mock_manager.run.side_effect = fake_run
        mock_calc.ChemTaskManager.return_value = mock_manager

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

        mock_calc.ChemTaskManager.assert_called_once()
        mock_manager.run.assert_called_once_with(input_xyz_file=single_input_xyz)
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

        def fake_run(input_xyz_file):
            with open(output, "w") as f:
                f.write("2\ncalculated\nC 0 0 0\nH 0 0 1\n")

        mock_manager = MagicMock()
        mock_manager.run.side_effect = fake_run
        mock_calc.ChemTaskManager.return_value = mock_manager

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
        mock_manager.run.assert_called_once_with(input_xyz_file=single_input_xyz)

    @patch("confflow.workflow.step_handlers.calc")
    def test_calc_no_output_raises(
        self,
        mock_calc: MagicMock,
        step_dir: str,
        single_input_xyz: str,
        failure_tracker: FailureTracker,
    ):
        """Computation without output raises ConfFlowError."""
        mock_manager = MagicMock()
        mock_manager.run.return_value = None
        mock_calc.ChemTaskManager.return_value = mock_manager

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

        def fake_run(input_xyz_file):
            with open(output, "w") as f:
                f.write("2\nout\nC 0 0 0\nH 0 0 1\n")

        mock_manager = MagicMock()
        mock_manager.run.side_effect = fake_run
        mock_calc.ChemTaskManager.return_value = mock_manager

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
        mock_manager.run.assert_called_once_with(input_xyz_file=single_input_xyz)

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

        def fake_run(input_xyz_file):
            with open(output, "w", encoding="utf-8") as f:
                f.write("2\nout\nC 0 0 0\nH 0 0 1\n")

        mock_manager = MagicMock()
        mock_manager.run.side_effect = fake_run
        mock_calc.ChemTaskManager.return_value = mock_manager

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

        def fake_run(input_xyz_file):
            with open(result_xyz, "w") as f:
                f.write("2\nraw\nC 0 0 0\nH 0 0 1\n")

        mock_manager = MagicMock()
        mock_manager.run.side_effect = fake_run
        mock_calc.ChemTaskManager.return_value = mock_manager

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

        with patch(
            "confflow.workflow.step_handlers.ConfigSchema.validate_calc_config"
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

        def fake_run(input_xyz_file):
            with open(output, "w") as f:
                f.write("2\nok\nC 0 0 0\nH 0 0 1\n")
            with open(failed, "w") as f:
                f.write("2\nfail\nC 1 0 0\nH 1 0 1\n")

        mock_manager = MagicMock()
        mock_manager.run.side_effect = fake_run
        mock_calc.ChemTaskManager.return_value = mock_manager

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
