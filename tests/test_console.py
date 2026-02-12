# -*- coding: utf-8 -*-
"""Tests for confflow.core.console module"""

import pytest
from io import StringIO
from unittest.mock import patch, Mock

from confflow.core.console import (
    print_step_header,
    print_info,
    print_success,
    print_warning,
    print_error,
    info,
    success,
    warning,
    error,
    heading,
    print_table,
    print_workflow_header,
    print_step_result,
    print_final_report_header,
    print_section_header,
    print_workflow_end,
    format_step_table,
    format_conformer_table,
    DummyProgress,
    create_progress,
    console,
    LINE_WIDTH,
    DOUBLE_LINE,
    SINGLE_LINE,
)


class TestPrintFunctions:
    """Tests for basic print functions"""

    def test_print_step_header(self, capsys):
        """Test step header printing"""
        print_step_header(1, 5, "TestStep", "opt", 10)
        captured = capsys.readouterr()
        assert "[Step 1/5]" in captured.out
        assert "TestStep" in captured.out
        assert "opt" in captured.out
        assert "Input: 10" in captured.out

    def test_print_step_header_with_width(self, capsys):
        """Test step header with custom width"""
        print_step_header(2, 3, "Name", "sp", 5, width=80)
        captured = capsys.readouterr()
        assert "[Step 2/3]" in captured.out

    def test_print_info(self, capsys):
        """Test info message printing"""
        print_info("Test info message")
        captured = capsys.readouterr()
        assert "INFO:" in captured.out
        assert "Test info message" in captured.out

    def test_print_success(self, capsys):
        """Test success message printing"""
        print_success("Operation completed")
        captured = capsys.readouterr()
        assert "SUCCESS:" in captured.out
        assert "Operation completed" in captured.out

    def test_print_warning(self, capsys):
        """Test warning message printing"""
        print_warning("This is a warning")
        captured = capsys.readouterr()
        assert "WARNING:" in captured.out
        assert "This is a warning" in captured.out

    def test_print_error(self, capsys):
        """Test error message printing"""
        print_error("An error occurred")
        captured = capsys.readouterr()
        assert "ERROR:" in captured.out
        assert "An error occurred" in captured.out


class TestCompatibilityHelpers:
    """Tests for English compatibility helper functions"""

    def test_info_helper(self, capsys):
        """Test info helper"""
        info("Test message")
        captured = capsys.readouterr()
        assert "INFO: Test message" in captured.out

    def test_success_helper(self, capsys):
        """Test success helper"""
        success("Done")
        captured = capsys.readouterr()
        assert "SUCCESS: Done" in captured.out

    def test_warning_helper(self, capsys):
        """Test warning helper"""
        warning("Be careful")
        captured = capsys.readouterr()
        assert "WARNING: Be careful" in captured.out

    def test_error_helper(self, capsys):
        """Test error helper"""
        error("Failed")
        captured = capsys.readouterr()
        assert "ERROR: Failed" in captured.out

    def test_heading(self, capsys):
        """Test heading function"""
        heading("Section Title")
        captured = capsys.readouterr()
        assert "Section Title" in captured.out

    def test_print_table(self, capsys):
        """Test print_table function"""
        mock_table = Mock()
        print_table(mock_table)
        # Should not raise


class TestWorkflowFunctions:
    """Tests for workflow output functions"""

    def test_print_workflow_header(self, capsys):
        """Test workflow header printing"""
        print_workflow_header("input.xyz", 5)
        captured = capsys.readouterr()
        assert "ConfFlow" in captured.out
        assert "input.xyz" in captured.out
        assert "5 conformer" in captured.out

    def test_print_workflow_header_single(self, capsys):
        """Test workflow header with single conformer"""
        print_workflow_header("single.xyz", 1)
        captured = capsys.readouterr()
        assert "1 conformer" in captured.out
        assert "conformers" not in captured.out

    def test_print_step_result_completed(self, capsys):
        """Test step result for completed status"""
        print_step_result("completed", 10, 8, 0, "1.5s")
        captured = capsys.readouterr()
        assert "✓" in captured.out
        assert "Completed" in captured.out
        assert "10" in captured.out
        assert "8" in captured.out
        assert "1.5s" in captured.out

    def test_print_step_result_with_failures(self, capsys):
        """Test step result with failures"""
        print_step_result("completed", 10, 7, 3, "2.0s")
        captured = capsys.readouterr()
        assert "(3 failed)" in captured.out

    def test_print_step_result_failed(self, capsys):
        """Test step result for failed status"""
        print_step_result("failed", 10, 0, 10, "0.5s")
        captured = capsys.readouterr()
        assert "✗" in captured.out

    def test_print_step_result_skipped(self, capsys):
        """Test step result for skipped status"""
        print_step_result("skipped", 5, 5, 0, "0.0s")
        captured = capsys.readouterr()
        assert "✓" in captured.out
        assert "Skipped" in captured.out

    def test_print_final_report_header(self, capsys):
        """Test final report header"""
        print_final_report_header()
        captured = capsys.readouterr()
        assert "FINAL REPORT" in captured.out
        assert "Finished:" in captured.out

    def test_print_section_header(self, capsys):
        """Test section header printing"""
        print_section_header("Results Summary")
        captured = capsys.readouterr()
        assert "Results Summary" in captured.out

    def test_print_workflow_end(self, capsys):
        """Test workflow end printing"""
        print_workflow_end()
        captured = capsys.readouterr()
        assert "=" in captured.out  # Double line


class TestFormatFunctions:
    """Tests for table formatting functions"""

    def test_format_step_table_empty(self):
        """Test format_step_table with empty list"""
        result = format_step_table([])
        assert "Step" in result
        assert "Name" in result
        assert "Type" in result
        assert "Status" in result

    def test_format_step_table_with_steps(self):
        """Test format_step_table with step data"""
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
        assert "completed" in result
        assert "50" in result
        assert "45" in result
        assert "5" in result

    def test_format_step_table_missing_fields(self):
        """Test format_step_table with missing fields"""
        steps = [
            {
                "index": 1,
                # Missing name, type, etc.
            }
        ]
        result = format_step_table(steps)
        assert "1" in result  # Index should still appear

    def test_format_step_table_long_names(self):
        """Test format_step_table truncates long names"""
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
        """Test format_conformer_table with empty list"""
        result = format_conformer_table([])
        assert "Rank" in result
        assert "Energy" in result
        assert "ΔG" in result
        assert "Pop" in result

    def test_format_conformer_table_with_data(self):
        """Test format_conformer_table with conformer data"""
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
        """Test format_conformer_table with None energy"""
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
        """Test format_conformer_table with None tsbond"""
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
    """Tests for DummyProgress class"""

    def test_dummy_progress_context_manager(self):
        """Test DummyProgress as context manager"""
        progress = DummyProgress()
        with progress as p:
            assert p is progress

    def test_dummy_progress_add_task(self):
        """Test DummyProgress add_task returns 0"""
        progress = DummyProgress()
        result = progress.add_task("description", total=100)
        assert result == 0

    def test_dummy_progress_advance(self):
        """Test DummyProgress advance does not raise"""
        progress = DummyProgress()
        progress.advance(0)  # Should not raise

    def test_dummy_progress_update(self):
        """Test DummyProgress update does not raise"""
        progress = DummyProgress()
        progress.update(0)  # Should not raise

    def test_create_progress(self):
        """Test create_progress returns DummyProgress"""
        result = create_progress()
        assert isinstance(result, DummyProgress)


class TestConstants:
    """Tests for module constants"""

    def test_line_width_positive(self):
        """Test LINE_WIDTH is positive"""
        assert LINE_WIDTH > 0

    def test_double_line_content(self):
        """Test DOUBLE_LINE contains only equals signs"""
        assert all(c == "=" for c in DOUBLE_LINE)

    def test_single_line_content(self):
        """Test SINGLE_LINE contains only dashes"""
        assert all(c == "─" for c in SINGLE_LINE)

    def test_line_lengths_match(self):
        """Test line lengths match LINE_WIDTH"""
        assert len(DOUBLE_LINE) == LINE_WIDTH
        assert len(SINGLE_LINE) == LINE_WIDTH
