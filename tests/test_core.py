"""core 模块综合测试 - 合并 io/viz/config 相关测试"""

import os
import tempfile
import pytest


# =============================================================================
# IO 测试 (来自 test_io_config.py)
# =============================================================================

class TestIO:
    """core.io 模块测试"""

    def test_parse_comment_metadata(self):
        """测试注释行元数据解析"""
        from confflow.core.io import parse_comment_metadata

        meta = parse_comment_metadata("Rank=1 | E=-1.234 | G_corr=0.123")
        assert meta["Rank"] == 1.0
        assert meta["E"] == -1.234
        assert meta["G_corr"] == 0.123

        meta = parse_comment_metadata("E=-0.5 TSBond=1.89")
        assert meta["E"] == -0.5
        assert meta["TSBond"] == 1.89

        meta = parse_comment_metadata("")
        assert meta == {}

        meta = parse_comment_metadata("Status=success")
        assert meta["Status"] == "success"

    def test_read_xyz_file(self):
        """测试 XYZ 文件读取"""
        from confflow.core.io import read_xyz_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False) as f:
            f.write("3\n")
            f.write("E=-1.5 | Rank=1\n")
            f.write("H  0.0 0.0 0.0\n")
            f.write("C  1.0 0.0 0.0\n")
            f.write("O  2.0 0.0 0.0\n")
            f.write("3\n")
            f.write("E=-1.2 | Rank=2\n")
            f.write("H  0.0 0.0 0.1\n")
            f.write("C  1.0 0.0 0.1\n")
            f.write("O  2.0 0.0 0.1\n")
            tmp_path = f.name

        try:
            conformers = read_xyz_file(tmp_path)
            assert len(conformers) == 2
            assert conformers[0]["natoms"] == 3
            assert conformers[0]["atoms"] == ["H", "C", "O"]
            assert conformers[0]["metadata"]["E"] == -1.5
            assert conformers[0]["metadata"]["Rank"] == 1.0
            assert conformers[1]["metadata"]["E"] == -1.2
        finally:
            os.unlink(tmp_path)

    def test_write_xyz_file(self):
        """测试 XYZ 文件写入"""
        from confflow.core.io import read_xyz_file, write_xyz_file

        conformers = [
            {
                "natoms": 2,
                "comment": "Test molecule | E=-0.5",
                "atoms": ["H", "C"],
                "coords": [[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]],
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False) as f:
            tmp_path = f.name

        try:
            write_xyz_file(tmp_path, conformers)
            result = read_xyz_file(tmp_path)
            assert len(result) == 1
            assert result[0]["atoms"] == ["H", "C"]
            assert result[0]["metadata"]["E"] == -0.5
        finally:
            os.unlink(tmp_path)

    def test_calculate_bond_length(self):
        """测试键长计算"""
        from confflow.core.io import calculate_bond_length

        coords_lines = [
            "H 0.0 0.0 0.0",
            "C 1.5 0.0 0.0",
            "O 2.5 0.0 0.0",
        ]

        length = calculate_bond_length(coords_lines, 1, 2)
        assert abs(length - 1.5) < 0.001

        length = calculate_bond_length(coords_lines, 2, 3)
        assert abs(length - 1.0) < 0.001

        assert calculate_bond_length(coords_lines, 0, 1) is None
        assert calculate_bond_length(coords_lines, 1, 5) is None


class TestData:
    """core.data 模块测试"""

    def test_get_covalent_radius(self):
        """测试获取共价半径"""
        from confflow.core.data import get_covalent_radius
        assert get_covalent_radius(1) == 0.30  # H
        assert get_covalent_radius(6) == 0.77  # C
        assert get_covalent_radius(150) == 1.50  # Unknown

    def test_get_element_symbol(self):
        """测试获取元素符号"""
        from confflow.core.data import get_element_symbol
        assert get_element_symbol(1) == "H"
        assert get_element_symbol(6) == "C"
        assert get_element_symbol(0) == "X"
        assert get_element_symbol(400) == "X"

    def test_get_atomic_number(self):
        """测试获取原子序数"""
        from confflow.core.data import get_atomic_number
        assert get_atomic_number("H") == 1
        assert get_atomic_number("c") == 6
        assert get_atomic_number("Unknown") == 0


# =============================================================================
# Config 测试 (来自 test_io_config.py)
# =============================================================================

class TestConfig:
    """config 模块测试"""

    def test_config_schema_normalize_global(self):
        """测试全局配置规范化"""
        from confflow.config.schema import ConfigSchema

        raw = {
            "cores_per_task": 8,
            "freeze": [1, 2, 3],
            "gaussian_path": "/opt/g16/g16",
        }

        normalized = ConfigSchema.normalize_global_config(raw)
        assert normalized["cores_per_task"] == 8
        assert normalized["freeze"] == [1, 2, 3]
        assert normalized["gaussian_path"] == "/opt/g16/g16"
        assert normalized["charge"] == 0
        assert normalized["multiplicity"] == 1
        assert normalized["rmsd_threshold"] == 0.25

    def test_config_schema_validate_calc(self):
        """测试 calc 配置验证"""
        from confflow.config.schema import ConfigSchema

        valid = {"iprog": "orca", "itask": "opt", "keyword": "xTB2 Opt"}
        ConfigSchema.validate_calc_config(valid)

        with pytest.raises(ValueError, match="iprog"):
            ConfigSchema.validate_calc_config({"itask": "opt", "keyword": "test"})

        with pytest.raises(ValueError, match="itask"):
            ConfigSchema.validate_calc_config({"iprog": "orca", "itask": "invalid", "keyword": "test"})


# =============================================================================
# Viz Report 测试 (来自 test_viz_extended.py)
# =============================================================================

class TestVizReport:
    """viz.report 模块测试"""

    def test_calculate_boltzmann_weights_basic(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights
        
        energies = [-1.0, -1.001]
        weights = calculate_boltzmann_weights(energies)
        assert len(weights) == 2
        assert weights[1] > weights[0]
        assert abs(sum(weights) - 100.0) < 1e-5

    def test_calculate_boltzmann_weights_empty(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights
        assert calculate_boltzmann_weights([]) == []

    def test_calculate_boltzmann_weights_invalid(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights
        assert calculate_boltzmann_weights([None, float("inf")]) == [0, 0]

    def test_format_duration(self):
        from confflow.blocks.viz.report import format_duration
        assert format_duration(30) == "30.0s"
        assert format_duration(120) == "2.0min"
        assert format_duration(7200) == "2.0h"

    def test_generate_text_report_basic(self):
        from confflow.blocks.viz.report import generate_text_report

        conformers = [
            {"metadata": {"E": -1.0, "G_corr": 0.1}, "comment": "C1"},
            {"metadata": {"G": -1.1}, "comment": "C2"}
        ]
        stats = {
            "steps": [
                {"index": 1, "name": "Step1", "type": "calc", "status": "completed",
                 "input_conformers": 10, "output_conformers": 8, "duration_seconds": 60}
            ],
            "total_duration_seconds": 60,
            "initial_conformers": 10,
            "final_conformers": 8
        }
        text = generate_text_report(conformers, stats=stats)
        assert "WORKFLOW SUMMARY" in text
        assert "CONFORMER ANALYSIS" in text
        assert "Step1" in text


# =============================================================================
# Confflow 包测试 (来自 test_io_config.py)
# =============================================================================

class TestConfflowPackage:
    """confflow 包级测试"""

    def test_confflow_package_exports(self):
        """测试包级导出"""
        import confflow

        assert hasattr(confflow, "__version__")
        assert hasattr(confflow, "RDKIT_AVAILABLE")
        assert hasattr(confflow, "PSUTIL_AVAILABLE")
        assert hasattr(confflow, "NUMBA_AVAILABLE")
        assert hasattr(confflow, "read_xyz_file")
        assert hasattr(confflow, "ConfigSchema")


# =============================================================================
# CHK Artifact IO 测试 (来自 test_chk_artifact_io.py)
# =============================================================================

class TestChkArtifactIO:
    """checkpoint 文件 artifact IO 测试"""

    def test_gaussian_chk_artifact_stage_and_link0(self, tmp_path):
        from confflow.calc.components import executor
        from confflow.calc.policies.gaussian import GaussianPolicy

        prev = tmp_path / "prev_backups"
        prev.mkdir()

        job = "c0001"
        (prev / f"{job}.chk").write_text("dummy-checkpoint", encoding="utf-8")

        work = tmp_path / "work" / job
        cfg = {
            "iprog": "g16",
            "itask": "sp",
            "keyword": "sp",
            "input_chk_dir": str(prev),
            "gaussian_write_chk": "true",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "charge": 0,
            "multiplicity": 1,
            "freeze": "0",
        }

        executor.prepare_task_inputs(str(work), job, cfg)

        assert (work / f"{job}.old.chk").exists()
        assert cfg.get("gaussian_oldchk") == f"{job}.old.chk"

        inp = tmp_path / "job.gjf"
        GaussianPolicy().generate_input(
            {"job_name": job, "coords": ["H 0 0 0"], "config": cfg}, str(inp)
        )
        text = inp.read_text(encoding="utf-8")

        assert f"%OldChk={job}.old.chk" in text
        assert f"%Chk={job}.chk" in text

    def test_gaussian_chk_stage_missing_source_is_noop(self, tmp_path):
        from confflow.calc.components import executor

        work = tmp_path / "work" / "c0001"
        cfg = {"input_chk_dir": str(tmp_path / "nope")}

        executor.prepare_task_inputs(str(work), "c0001", cfg)

        assert not work.exists() or not any(work.iterdir())
        assert "gaussian_oldchk" not in cfg


# =============================================================================
# Input Generation Snapshot 测试 (来自 test_input_generation_snapshot.py)
# =============================================================================

class TestInputGenerationSnapshot:
    """输入文件生成快照测试"""

    def test_gaussian_generate_input_semantic_snapshot(self, tmp_path):
        import re
        from confflow.calc.policies.gaussian import GaussianPolicy

        cfg = {
            "iprog": 1,
            "cores_per_task": 2,
            "max_parallel_jobs": 1,
            "total_memory": "2048MB",
            "keyword": "opt freq b3lyp/6-31g(d)",
            "charge": 0,
            "multiplicity": 1,
            "freeze": "2",
            "solvent_block": "SCRF=(SMD,Solvent=Water)",
            "custom_block": "IOp(3/33=1)",
            "gaussian_modredundant": ["B 1 2 F", "A 1 2 3 F"],
        }

        task_info = {
            "job_name": "job1",
            "coords": [
                "O 0.0000 0.0000 0.0000",
                "H 0.0000 0.0000 1.0000",
                "H 1.0000 0.0000 0.0000",
            ],
            "config": cfg,
        }

        out = tmp_path / "job1.gjf"
        GaussianPolicy().generate_input(task_info, str(out))
        text = out.read_text(encoding="utf-8")

        assert "%nproc=2" in text
        assert "%mem=2GB" in text
        assert "#p opt freq b3lyp/6-31g(d)" in text
        assert "job1" in text
        assert "0 1" in text
        assert re.search(r"^\s*H\s+-1\b", text, flags=re.M) is not None
        assert "SCRF=(SMD,Solvent=Water)" in text
        assert "IOp(3/33=1)" in text
        assert "B 1 2 F" in text
        assert "A 1 2 3 F" in text

    def test_orca_generate_input_semantic_snapshot(self, tmp_path):
        from confflow.calc.policies.orca import OrcaPolicy

        cfg = {
            "iprog": 2,
            "cores_per_task": 4,
            "orca_maxcore": 512,
            "keyword": "opt",
            "charge": 0,
            "multiplicity": 1,
            "itask": "opt",
            "freeze": "1,3",
        }

        task_info = {
            "job_name": "job2",
            "coords": [
                "C 0.0 0.0 0.0",
                "H 0.0 0.0 1.0",
                "H 1.0 0.0 0.0",
            ],
            "config": cfg,
        }

        out = tmp_path / "job2.inp"
        OrcaPolicy().generate_input(task_info, str(out))
        text = out.read_text(encoding="utf-8")

        assert text.lstrip().startswith("! opt")
        assert "%pal nprocs 4 end" in text
        assert "%maxcore 512" in text
        assert "%geom" in text
        assert "Constraints" in text
        assert "{ C 0 C }" in text
        assert "{ C 2 C }" in text
        assert "* xyz 0 1" in text
        assert "C 0.0 0.0 0.0" in text


# =============================================================================
# confts Keyword 测试 (来自 test_confts_keyword.py)
# =============================================================================

class TestConftsKeyword:
    """confts 模块关键字测试"""

    @pytest.mark.parametrize(
        "kw,expected",
        [
            ("opt(nomicro,calcfc,tight,ts,noeigentest)", "opt(nomicro)"),
            ("opt=(nomicro,calcfc,tight,ts,noeigentest) freq", "opt=(nomicro)"),
            ("opt=(nomicro,calcfc,tight,ts,noeigentest) freq=noraman", "opt=(nomicro)"),
            ("opt(ts,calcfc) ts", "opt ts"),
            ("ts freq", "ts"),
        ],
    )
    def test_make_scan_keyword_from_ts_keyword(self, kw, expected):
        from confflow.confts import make_scan_keyword_from_ts_keyword
        assert make_scan_keyword_from_ts_keyword(kw) == expected

    def test_confts_cli_rewrite(self, capsys):
        from confflow.confts import _cli
        _cli(["--rewrite-scan-keyword", "opt(ts,calcfc) freq"])
        captured = capsys.readouterr()
        assert "opt" in captured.out
        assert "freq" not in captured.out

    def test_confts_cli_no_args(self, capsys):
        from confflow.confts import _cli
        res = _cli([])
        assert res == 1


# =============================================================================
# Low Energy Trace 测试 (来自 test_low_energy_trace.py)
# =============================================================================

class TestLowEnergyTrace:
    """低能量构象追踪测试"""

    def test_low_energy_trace_tracks_top6_across_steps(self, monkeypatch, tmp_path):
        import json
        import yaml
        import confflow.workflow.engine as engine

        def fake_run_generation(input_files, **kwargs):
            with open("search.xyz", "w", encoding="utf-8") as f:
                for i in range(6):
                    cid = f"cf_{i+1:06d}"
                    f.write("2\n")
                    f.write(f"Conformer {i+1} | CID={cid}\n")
                    f.write("H 0 0 0\n")
                    f.write("H 0 0 0.74\n")

        class FakeManager:
            def __init__(self, settings_file: str):
                self.config = {}
                self.work_dir = ""

            def run(self, input_xyz_file: str):
                from pathlib import Path
                confs = engine.io_xyz.read_xyz_file(input_xyz_file, parse_metadata=True)
                for i, c in enumerate(confs):
                    meta = c.get("metadata") or {}
                    cid = meta.get("CID")
                    c["comment"] = f"Energy={-(i+1)} CID={cid}"
                    c["metadata"] = engine.io_xyz.parse_comment_metadata(c["comment"])
                out_dir = Path(self.work_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / "output.xyz"
                engine.io_xyz.write_xyz_file(str(out), confs, atomic=False)

        monkeypatch.setattr(engine.confgen, "run_generation", fake_run_generation)
        monkeypatch.setattr(engine.calc, "ChemTaskManager", FakeManager)
        monkeypatch.setattr(engine.viz, "parse_xyz_file", lambda p: [])
        monkeypatch.setattr(engine.viz, "generate_text_report", lambda *a, **k: "")

        inp = tmp_path / "a.xyz"
        inp.write_text("2\nA\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

        cfg = {
            "global": {
                "gaussian_path": "g16",
                "orca_path": "orca",
                "cores_per_task": 1,
                "total_memory": "1GB",
                "max_parallel_jobs": 1,
            },
            "steps": [
                {"name": "step_01", "type": "confgen", "params": {"chains": ["1-2"]}},
                {
                    "name": "step_02",
                    "type": "calc",
                    "params": {"iprog": "orca", "itask": "sp", "keyword": "x"},
                },
            ],
        }
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

        work_dir = tmp_path / "work"
        stats = engine.run_workflow(
            input_xyz=[str(inp)],
            config_file=str(cfg_path),
            work_dir=str(work_dir),
            resume=False,
            verbose=False,
        )

        assert "low_energy_trace" in stats
        trace = stats["low_energy_trace"]
        assert trace["top_k"] == 6
        assert len(trace["conformers"]) == 6

        for item in trace["conformers"]:
            assert "cid" in item
            assert "trace" in item
            assert len(item["trace"]) == 2
            assert all(x["status"] == "found" for x in item["trace"])

        stats_path = work_dir / "workflow_stats.json"
        data = json.loads(stats_path.read_text(encoding="utf-8"))
        assert "low_energy_trace" in data


# =============================================================================
# Test Suite 快速检查测试 (来自 test_suite.py)
# =============================================================================

class TestSuiteQuickCheck:
    """快速体检测试"""

    def test_utils_radii_sanity(self):
        from confflow.core.data import GV_COVALENT_RADII
        assert len(GV_COVALENT_RADII) >= 100
        assert abs(GV_COVALENT_RADII[1] - 0.30) < 1e-12
        assert abs(GV_COVALENT_RADII[6] - 0.77) < 1e-12

    def test_logger_available(self):
        import confflow.core.utils as utils
        lg = utils.get_logger()
        assert lg is not None

    def test_refine_core_functions(self):
        import numpy as np
        import confflow.blocks.refine as refine
        assert refine.get_element_atomic_number("Cl") == 17
        coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        assert refine.fast_rmsd(coords, coords) < 1e-6

    def test_calc_resultsdb_roundtrip(self, tmp_path):
        import confflow.calc as calc
        db = calc.ResultsDB(str(tmp_path / "res.db"))
        job_id = db.insert_result({"job_name": "j", "index": 1, "status": "success"})
        assert job_id == 1
        got = db.get_result_by_job_name("j")
        assert got is not None and got["status"] == "success"
        db.close()

    def test_viz_report_generation(self, tmp_path):
        import confflow.blocks.viz as viz
        xyz = tmp_path / "result.xyz"
        xyz.write_text("2\nEnergy=-1.0\nH 0 0 0\nH 0 0 0.74\n", encoding="utf-8")
        confs = viz.parse_xyz_file(str(xyz))
        assert len(confs) == 1
        text = viz.generate_text_report(confs, stats={"steps": []})
        assert "CONFORMER ANALYSIS" in text

    def test_main_entrypoint_callable(self):
        import importlib
        main_mod = importlib.import_module("confflow.main")
        assert callable(main_mod.main)

    def test_confgen_key_symbols_present(self):
        import confflow.blocks.confgen as confgen
        assert hasattr(confgen, "run_generation")
        assert hasattr(confgen, "check_clash_core")
