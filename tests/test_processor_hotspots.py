#!/usr/bin/env python3
"""Hotspot tests for refine.processor."""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np

import confflow.blocks.refine.processor as processor


def test_processor_console_fallback_module_reload(capsys):
    with patch.dict(sys.modules, {"confflow.core.console": None}):
        fallback = importlib.reload(processor)

    try:
        with fallback.create_progress() as progress:
            progress.add_task("x")
            progress.update(0)
        fallback.info("hello")
        fallback.success("ok")
        fallback.warning("warn")
        fallback.error("err")
        fallback.heading("head")
        fallback.print_table("ignored")
        out = capsys.readouterr().out
        assert "INFO: hello" in out
        assert "SUCCESS: ok" in out
        assert "WARNING: warn" in out
        assert "ERROR: err" in out
        assert "=== head ===" in out
    finally:
        importlib.reload(processor)


def test_compute_dedup_counts_breaks_cycle():
    final_unique = [{"original_index": 1, "heavy_coords": np.zeros((1, 3))}]
    frames = [{"original_index": 2}, {"original_index": 1}]
    report = [
        {"Input_Frame_ID": 2, "Status": "Removed (Duplicate)", "Duplicate_Of_Input_ID": 2},
        {"Input_Frame_ID": 1, "Status": "Kept", "Duplicate_Of_Input_ID": "-"},
    ]

    processor._compute_dedup_counts(final_unique, frames, report)

    assert final_unique[0]["count"] == 1
    assert final_unique[0]["rmsd_to_min"] == 0.0


def test_write_refine_output_emits_g_and_skips_aux_fields(tmp_path: Path):
    out = tmp_path / "out.xyz"
    final_unique = [
        {
            "natoms": 1,
            "energy": -100.0,
            "energy_key": "G",
            "num_imag_freqs": 1,
            "extra_data": {
                "CID": "A000001",
                "G_corr": 1.0,
                "E_sp": -200.0,
                "E_includes_gcorr": True,
                "TSAtoms": "1,2",
            },
            "original_atoms": ["H"],
            "coords": np.array([[0.0, 0.0, 0.0]], dtype=np.float64),
        }
    ]

    processor._write_refine_output(str(out), final_unique, global_min=-100.0)
    text = out.read_text(encoding="utf-8")
    assert "G=-100.00000000" in text
    assert "CID=A000001" in text
    assert "G_corr" not in text
    assert "E_sp" not in text
    assert "TSAtoms" not in text


def test_process_xyz_missing_input_calls_error(monkeypatch):
    called = {}
    monkeypatch.setattr(processor, "error", lambda msg: called.setdefault("msg", msg))

    args = processor.RefineOptions(input_file="missing.xyz", output="out.xyz")
    processor.process_xyz(args)

    assert "Input file not found" in called["msg"]


def test_process_xyz_no_frames_returns_after_banner(tmp_path, monkeypatch):
    xyz = tmp_path / "in.xyz"
    xyz.write_text("1\nx\nH 0 0 0\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(processor, "read_xyz_file", lambda path: [])
    monkeypatch.setattr(processor.console, "print", lambda msg: seen.append(msg))

    args = processor.RefineOptions(input_file=str(xyz), output=str(tmp_path / "out.xyz"))
    processor.process_xyz(args)

    assert seen and "RMSD=" in seen[0]


def test_process_xyz_no_conformers_after_filtering(tmp_path, monkeypatch):
    xyz = tmp_path / "in.xyz"
    xyz.write_text("1\nx\nH 0 0 0\n", encoding="utf-8")
    frames = [
        {
            "atoms": ["H"],
            "coords": np.array([[0.0, 0.0, 0.0]], dtype=np.float64),
            "energy": 0.0,
            "num_imag_freqs": 1,
            "original_index": 0,
            "natoms": 1,
            "energy_key": "E",
            "extra_data": {},
            "original_atoms": ["H"],
        }
    ]

    class _Exec:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        def map(self, func, iterable, chunksize=None):
            del chunksize
            return map(func, iterable)

    class _Prog:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        def add_task(self, *args, **kwargs):
            del args, kwargs
            return 1

        def advance(self, *args, **kwargs):
            del args, kwargs
            return None

    seen = []
    monkeypatch.setattr(processor, "read_xyz_file", lambda path: frames.copy())
    monkeypatch.setattr(processor, "ProcessPoolExecutor", _Exec)
    monkeypatch.setattr(processor, "create_progress", lambda: _Prog())
    monkeypatch.setattr(processor, "get_topology_hash_worker", lambda pair: "topo")
    monkeypatch.setattr(processor, "process_topology_group", lambda *args: ([], []))
    monkeypatch.setattr(processor.console, "print", lambda msg: seen.append(msg))

    args = processor.RefineOptions(input_file=str(xyz), output=str(tmp_path / "out.xyz"), imag=0)
    processor.process_xyz(args)

    assert any("No conformers remain" in msg for msg in seen)


def test_processor_main_sets_default_output_and_calls_process(monkeypatch, tmp_path: Path):
    input_xyz = tmp_path / "in.xyz"
    input_xyz.write_text("1\nx\nH 0 0 0\n", encoding="utf-8")
    captured = {}

    @contextmanager
    def fake_cli_output(path):
        captured["cli_path"] = path
        yield str(tmp_path / "log.txt")

    def fake_process(args):
        captured["args"] = args

    monkeypatch.setattr(processor.multiprocessing, "set_start_method", lambda method: None)
    monkeypatch.setattr(processor, "cli_output_to_txt", fake_cli_output)
    monkeypatch.setattr(processor, "process_xyz", fake_process)
    monkeypatch.setattr(
        sys,
        "argv",
        ["confrefine", str(input_xyz), "-t", "0.2", "--energy-tolerance", "0.1"],
    )

    rc = processor.main()

    assert rc == processor.ExitCode.SUCCESS
    assert captured["cli_path"] == str(input_xyz)
    assert captured["args"].output == str(input_xyz.with_name("in_cleaned.xyz"))
    assert captured["args"].threshold == 0.2
    assert captured["args"].energy_tolerance == 0.1


def test_processor_main_start_method_runtimeerror_logs_debug(monkeypatch, tmp_path: Path):
    input_xyz = tmp_path / "in.xyz"
    input_xyz.write_text("1\nx\nH 0 0 0\n", encoding="utf-8")

    @contextmanager
    def fake_cli_output(path):
        del path
        yield str(tmp_path / "log.txt")

    monkeypatch.setattr(
        processor.multiprocessing,
        "set_start_method",
        lambda method: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(processor, "cli_output_to_txt", fake_cli_output)
    monkeypatch.setattr(processor, "process_xyz", lambda args: None)
    monkeypatch.setattr(processor.logger, "debug", lambda msg: None)
    monkeypatch.setattr(sys, "argv", ["confrefine", str(input_xyz)])

    rc = processor.main()
    assert rc == processor.ExitCode.SUCCESS
