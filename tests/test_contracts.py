#!/usr/bin/env python3

"""Tests for core.contracts utilities."""

from __future__ import annotations

from confflow.core.contracts import cli_output_to_txt, output_txt_path_for_input


def test_output_txt_path_for_input(tmp_path):
    input_file = tmp_path / "sample.xyz"
    expected = tmp_path / "sample.txt"
    assert output_txt_path_for_input(str(input_file)) == str(expected)


def test_cli_output_to_txt_redirects_stdout(tmp_path):
    input_file = tmp_path / "sample.xyz"

    with cli_output_to_txt(str(input_file)) as out_path:
        print("contract-output-line")

    output_file = tmp_path / "sample.txt"
    assert out_path == str(output_file)
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "contract-output-line" in content
