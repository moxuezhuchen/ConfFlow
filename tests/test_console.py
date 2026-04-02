#!/usr/bin/env python3

"""Tests for confflow.core.console module.

Refactored to reduce repetition: many small print helpers are
covered via parameterized assertions to keep intent explicit
while avoiding boilerplate.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from confflow.core.console import (
    DOUBLE_LINE,
    LINE_WIDTH,
    SINGLE_LINE,
    DummyProgress,
    create_progress,
    error,
    format_conformer_table,
    format_step_table,
    heading,
    info,
    print_error,
    print_final_report_header,
    print_info,
    print_section_header,
    print_step_header,
    print_step_result,
    print_success,
    print_table,
    print_warning,
    print_workflow_end,
    print_workflow_header,
    success,
    warning,
)


class TestPrintFunctions:
    """Tests for basic print functions."""

    @pytest.mark.parametrize(
        "func, text, expected",
        [
            (print_info, "Test info message", "Test info message"),
            (print_success, "Operation completed", "Operation completed"),
            (print_warning, "This is a warning", "This is a warning"),
            (print_error, "An error occurred", "An error occurred"),
        ],
    )
    def test_print_helpers(self, capsys, func, text, expected):
        """Common printing helpers produce a message that includes the text."""
        func(text)
        captured = capsys.readouterr()
        assert expected in captured.out
        assert text in captured.out

    def test_print_step_header(self, capsys):
        print_step_header(1, 5, "TestStep", "opt", 10)
        captured = capsys.readouterr()
        assert "1" in captured.out
        assert "5" in captured.out
        assert "TestStep" in captured.out
        assert "OPT" in captured.out
        assert "10" in captured.out

    def test_print_step_header_with_width(self, capsys):
        print_step_header(2, 3, "Name", "sp", 5, width=80)
        captured = capsys.readouterr()
        assert "2" in captured.out
        assert "3" in captured.out


class TestCompatibilityHelpers:
    """Tests for English compatibility helper functions."""

    @pytest.mark.parametrize(
        "func, text, expected",
        [
            (info, "Test message", "Test message"),
            (success, "Done", "Done"),
            (warning, "Be careful", "Be careful"),
            (error, "Failed", "Failed"),
        ],
    )
    def test_english_helpers(self, capsys, func, text, expected):
        func(text)
        captured = capsys.readouterr()
        assert expected in captured.out

    def test_heading(self, capsys):
        heading("Section Title")
        captured = capsys.readouterr()
        assert "Section Title" in captured.out

    def test_print_table(self, capsys):
        """Test print_table function."""
        mock_table = Mock()
        print_table(mock_table)
        # Should not raise


class TestWorkflowFunctions:
    """Tests for workflow output functions."""

    def test_print_workflow_header(self, capsys):
        """Test workflow header printing."""
        print_workflow_header("input.xyz", 5)
        captured = capsys.readouterr()
        assert "ConfFlow" in captured.out
        assert "input.xyz" in captured.out
        assert "5 conformer" in captured.out

    def test_print_workflow_header_single(self, capsys):
        """Test workflow header with single conformer."""
        print_workflow_header("single.xyz", 1)
        captured = capsys.readouterr()
        assert "1 conformer" in captured.out
        assert "conformers" not in captured.out

    def test_print_step_result_completed(self, capsys):
        """Test step result for completed status."""
        print_step_result("completed", 10, 8, 0, "1.5s")
        captured = capsys.readouterr()
        assert "✔" in captured.out
        assert "Completed" in captured.out
        assert "10" in captured.out
        assert "8" in captured.out
        assert "1.5s" in captured.out

    def test_print_step_result_with_failures(self, capsys):
        """Test step result with failures."""
        print_step_result("completed", 10, 7, 3, "2.0s")
        captured = capsys.readouterr()
        assert "3 failed" in captured.out

    def test_print_step_result_failed(self, capsys):
        """Test step result for failed status."""
        print_step_result("failed", 10, 0, 10, "0.5s")
        captured = capsys.readouterr()
        assert "✘" in captured.out

    def test_print_step_result_skipped(self, capsys):
        """Test step result for skipped status."""
        print_step_result("skipped", 5, 5, 0, "0.0s")
        captured = capsys.readouterr()
        assert "✔" in captured.out
        assert "Skipped" in captured.out

    def test_print_final_report_header(self, capsys):
        """Test final report header."""
        print_final_report_header()
        captured = capsys.readouterr()
        assert "WORKFLOW SUMMARY" in captured.out

    def test_print_section_header(self, capsys):
        """Test section header printing."""
        print_section_header("Results Summary")
        captured = capsys.readouterr()
        assert "Results Summary" in captured.out

    def test_print_workflow_end(self, capsys):
        """Test workflow end printing."""
        print_workflow_end()
        captured = capsys.readouterr()
        assert "═" in captured.out  # Double line


class TestFormatFunctions:
    """Tests for table formatting functions."""

    def test_format_step_table_empty(self):
        """Test format_step_table with empty list."""
        result = format_step_table([])
        assert "Step" in result
        assert "Name" in result
        assert "Type" in result
        assert "Status" in result

    def test_format_step_table_with_steps(self):
        """Test format_step_table with step data."""
        steps = [
            {
                "index": 1,
                "name": "confgen",
                "type": "confgen",
                "status": "completed",
                "input_conformers": 1,
                "output_conformers": 50,
                "failed_conformers": 0,
                "duration_str": "1.5s",
            },
            {
                "index": 2,
                "name": "opt",
                "type": "opt",
                "status": "completed",
                "input_conformers": 50,
                "output_conformers": 45,
                "failed_conformers": 5,
                "duration_str": "30s",
            },
        ]
        result = format_step_table(steps)
        assert "confgen" in result
        assert "opt" in result
        assert "done" in result  # "✔ done" replaces "completed"
        assert "50" in result
        assert "45" in result
        assert "5" in result

    def test_format_step_table_missing_fields(self):
        """Test format_step_table with missing fields."""
        steps = [
            {
                "index": 1,
                # Missing name, type, etc.
            }
        ]
        result = format_step_table(steps)
        assert "1" in result  # Index should still appear

    def test_format_step_table_long_names(self):
        """Test format_step_table truncates long names."""
        steps = [
            {
                "index": 1,
                "name": "very_long_step_name",
                "type": "very_long_type",
                "status": "very_long_status",
                "input_conformers": 1,
                "output_conformers": 1,
                "failed_conformers": 0,
                "duration_str": "1s",
            }
        ]
        result = format_step_table(steps)
        # Names should be truncated
        assert "very_long" in result

    def test_format_conformer_table_empty(self):
        """Test format_conformer_table with empty list."""
        result = format_conformer_table([])
        assert "Rank" in result
        assert "Energy" in result
        assert "ΔG" in result
        assert "Pop" in result

    def test_format_conformer_table_with_data(self):
        """Test format_conformer_table with conformer data."""
        conformers = [
            {
                "rank": 1,
                "energy": -123.4567890,
                "dg": 0.0,
                "pop": 50.5,
                "imag": 0,
                "tsbond": 1.2345,
            },
            {
                "rank": 2,
                "energy": -123.4560000,
                "dg": 0.5,
                "pop": 30.0,
                "imag": "-",
                "tsbond": "-",
            },
        ]
        result = format_conformer_table(conformers)
        assert "1" in result
        assert "-123.4567890" in result
        assert "50.5" in result
        assert "1.2345" in result

    def test_format_conformer_table_none_energy(self):
        """Test format_conformer_table with None energy."""
        conformers = [
            {
                "rank": 1,
                "energy": None,
                "dg": 0.0,
                "pop": 100.0,
                "imag": "-",
                "tsbond": "-",
            }
        ]
        result = format_conformer_table(conformers)
        assert "N/A" in result

    def test_format_conformer_table_none_tsbond(self):
        """Test format_conformer_table with None tsbond."""
        conformers = [
            {
                "rank": 1,
                "energy": -100.0,
                "dg": 0.0,
                "pop": 100.0,
                "imag": "-",
                "tsbond": None,
            }
        ]
        result = format_conformer_table(conformers)
        # None tsbond should display as "-"
        assert "-" in result


class TestDummyProgress:
    """Tests for DummyProgress class."""

    def test_dummy_progress_context_manager(self):
        """Test DummyProgress as context manager."""
        progress = DummyProgress()
        with progress as p:
            assert p is progress

    def test_dummy_progress_add_task(self):
        """Test DummyProgress add_task returns 0."""
        progress = DummyProgress()
        result = progress.add_task("description", total=100)
        assert result == 0

    def test_dummy_progress_advance(self):
        """Test DummyProgress advance does not raise."""
        progress = DummyProgress()
        progress.advance(0)  # Should not raise

    def test_dummy_progress_update(self):
        """Test DummyProgress update does not raise."""
        progress = DummyProgress()
        progress.update(0)  # Should not raise

    def test_create_progress(self):
        """Test create_progress returns DummyProgress."""
        result = create_progress()
        assert isinstance(result, DummyProgress)


class TestConstants:
    """Tests for module constants."""

    def test_line_width_positive(self):
        """Test LINE_WIDTH is positive."""
        assert LINE_WIDTH > 0

    def test_double_line_content(self):
        """Test DOUBLE_LINE contains only double-line box characters."""
        assert all(c == "═" for c in DOUBLE_LINE)

    def test_single_line_content(self):
        """Test SINGLE_LINE contains only dashes."""
        assert all(c == "─" for c in SINGLE_LINE)

    def test_line_lengths_match(self):
        """Test line lengths match LINE_WIDTH."""
        assert len(DOUBLE_LINE) == LINE_WIDTH
        assert len(SINGLE_LINE) == LINE_WIDTH
