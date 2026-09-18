#!/usr/bin/env python3
"""Analysis provenance inheritance: SP inherits, geometry-changing tasks don't."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from confflow.calc.components.task_runner import TaskRunner
from confflow.calc.result_writer import append_result, format_result_comment, write_failed_xyz
from confflow.core.models import TaskContext

TS_METADATA = {
    "CID": "A000001",
    "Imag": 1,
    "LowestFreq": -321.4,
    "TSAtoms": "1,2",
    "TSBond": 1.234,
    "G_corr": 0.05,
    "AddBond": "1-4",
    "DelBond": "2-3",
}


def _run_task(
    tmp_path: Path,
    metadata: dict,
    *,
    itask: int,
    result: dict,
    config: dict | None = None,
) -> dict:
    runner = TaskRunner()
    task_info = {
        "job_name": "job",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": itask, "iprog": 1, **(config or {})},
        "coords": ["C 0 0 0", "C 0 0 1.44"],
        "metadata": metadata,
    }
    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = result
        with patch("confflow.calc.components.executor.handle_backups"):
            return runner.run(task_info)


# ---------------------------------------------------------------------------
# SP inherits still-valid analysis provenance
# ---------------------------------------------------------------------------


def test_sp_inherits_imag_lowestfreq_ts_metadata(tmp_path):
    res = _run_task(
        tmp_path,
        TS_METADATA,
        itask=1,
        result={"final_coords": ["C 0 0 0", "C 0 0 1.44"], "e_high": -100.0},
    )

    assert res["status"] == "success"
    # inherited analysis fields
    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -321.4
    assert res["ts_bond_atoms"] == "1,2"
    assert res["ts_bond_length"] == 1.234
    # inherited Gibbs correction (existing behaviour)
    assert res["g_corr"] == 0.05
    assert res["final_gibbs_energy"] == -99.95

    comment = format_result_comment(res, TS_METADATA)
    assert "Imag=1" in comment
    assert "LowestFreq=-321.4" in comment
    assert "TSAtoms=1,2" in comment
    assert "TSBond=1.234000" in comment
    assert comment.count("G_corr=") == 1


def test_sp_inherits_imag_zero(tmp_path):
    """Imag=0 is a valid inherited value and must not be lost."""
    res = _run_task(
        tmp_path,
        {"CID": "A", "Imag": 0, "LowestFreq": 12.5},
        itask=1,
        result={"final_coords": ["C 0 0 0", "C 0 0 1.44"], "e_high": -100.0},
    )

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 12.5
    comment = format_result_comment(res, {"Imag": 0})
    assert "Imag=0" in comment


def test_sp_fresh_frequency_wins_over_inherited(tmp_path):
    res = _run_task(
        tmp_path,
        TS_METADATA,
        itask=1,
        result={
            "final_coords": ["C 0 0 0", "C 0 0 1.44"],
            "e_high": -100.0,
            "num_imag_freqs": 0,
            "lowest_freq": 50.0,
        },
    )

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 50.0
    comment = format_result_comment(res, TS_METADATA)
    assert "Imag=0" in comment
    assert "Imag=1" not in comment
    assert "LowestFreq=50.0" in comment


# ---------------------------------------------------------------------------
# Geometry-changing tasks never inherit previous-geometry conclusions
# ---------------------------------------------------------------------------


def test_opt_does_not_inherit_imag_lowestfreq_ts_metadata(tmp_path):
    res = _run_task(
        tmp_path,
        TS_METADATA,
        itask=0,  # opt
        result={"final_coords": ["C 0 0 0", "C 0 0 1.50"], "e_low": -60.0},
    )

    assert res["status"] == "success"
    assert res["num_imag_freqs"] is None
    assert res["lowest_freq"] is None
    assert "ts_bond_atoms" not in res
    assert "ts_bond_length" not in res
    assert res["g_corr"] is None

    comment = format_result_comment(res, TS_METADATA)
    assert "Imag=" not in comment
    assert "LowestFreq=" not in comment
    assert "TSAtoms=" not in comment
    assert "TSBond=" not in comment
    assert "G_corr=" not in comment


def test_writer_does_not_fall_back_to_input_imag():
    """The writer must not guess provenance: no Imag without a result value."""
    comment = format_result_comment({"energy": -1.0}, {"Imag": 1, "LowestFreq": -12.0})
    assert "Imag=" not in comment
    assert "LowestFreq=" not in comment


def test_ts_fresh_tsbond_wins_over_inherited(tmp_path):
    """A TS step's own config+geometry define the bond; old TSBond is stale."""
    res = _run_task(
        tmp_path,
        TS_METADATA,
        itask=4,  # ts (without freq keyword -> bond-drift validation path)
        result={
            "final_coords": ["C 0 0 0", "C 0 0 1.456"],
            "e_low": -60.0,
            "g_low": -59.5,
            "num_imag_freqs": 1,
            "lowest_freq": -120.0,
        },
        config={"ts_bond_atoms": "1,2", "ts_rmsd_threshold": 0.5},
    )

    assert res["status"] == "success"
    assert res["ts_bond_atoms"] == "1,2"
    assert res["ts_bond_length"] == 1.456
    comment = format_result_comment(res, TS_METADATA)
    assert "TSBond=1.456000" in comment
    assert "1.234" not in comment


# ---------------------------------------------------------------------------
# failed.xyz keeps the fields describing the original input structure
# ---------------------------------------------------------------------------


def test_failed_xyz_keeps_input_provenance(tmp_path):
    tasks = [
        TaskContext(
            job_name="A000001",
            work_dir=str(tmp_path / "A000001"),
            coords=["C 0 0 0", "C 0 0 1.44"],
            metadata=dict(TS_METADATA),
            config={},
        )
    ]

    write_failed_xyz(
        str(tmp_path),
        [{"job_name": "A000001", "status": "failed", "error": "SCF failed"}],
        tasks,
    )

    text = (tmp_path / "failed.xyz").read_text(encoding="utf-8")
    for fragment in (
        "CID=A000001",
        "AddBond=1-4",
        "DelBond=2-3",
        "TSAtoms=1,2",
        "TSBond=1.234",
        "Imag=1",
        "LowestFreq=-321.4",
    ):
        assert fragment in text, fragment


def test_failed_xyz_imag_zero_kept(tmp_path):
    tasks = [
        TaskContext(
            job_name="A000001",
            work_dir=str(tmp_path / "A000001"),
            coords=["C 0 0 0"],
            metadata={"CID": "A000001", "Imag": 0},
            config={},
        )
    ]

    write_failed_xyz(
        str(tmp_path),
        [{"job_name": "A000001", "status": "failed", "error": "boom"}],
        tasks,
    )

    text = (tmp_path / "failed.xyz").read_text(encoding="utf-8")
    assert "Imag=0" in text


# ---------------------------------------------------------------------------
# Comment metadata parsing keeps comma-containing values (TSAtoms round-trip)
# and still honors the historical comma pair separator
# ---------------------------------------------------------------------------


def test_parse_comment_metadata_keeps_comma_values():
    from confflow.core.xyz_metadata import parse_comment_metadata

    meta = parse_comment_metadata("G=-1.0 | Imag=1 | TSAtoms=1,2 | TSBond=1.5")
    assert meta["TSAtoms"] == "1,2"
    assert meta["Imag"] == 1.0
    assert meta["TSBond"] == 1.5


def test_parse_comment_metadata_value_boundary():
    """A comma delimits metadata only when it starts the next key= pair."""
    from confflow.core.xyz_metadata import parse_comment_metadata

    # comma inside a value is preserved (pipe / space / comma separators)
    assert parse_comment_metadata("TSAtoms=1,2 | TSBond=1.5") == {
        "TSAtoms": "1,2",
        "TSBond": 1.5,
    }
    assert parse_comment_metadata("TSAtoms=1,2 TSBond=1.5") == {
        "TSAtoms": "1,2",
        "TSBond": 1.5,
    }
    assert parse_comment_metadata("TSAtoms=1,2,3") == {"TSAtoms": "1,2,3"}

    # historical comma pair separator, with and without a space
    legacy_space = parse_comment_metadata("E=-1.23, CID=A000001")
    assert legacy_space["E"] == -1.23
    assert legacy_space["CID"] == "A000001"

    legacy_tight = parse_comment_metadata("E=-1.23,CID=A000001")
    assert legacy_tight["E"] == -1.23
    assert legacy_tight["CID"] == "A000001"

    mixed = parse_comment_metadata("E=-100.5, CID=A000001, Imag=1")
    assert mixed["E"] == -100.5
    assert mixed["CID"] == "A000001"
    assert mixed["Imag"] == 1.0

    # plain-keyword behaviour unchanged
    assert parse_comment_metadata("Rank=1 | E=-1.234 | G_corr=0.123") == {
        "Rank": 1.0,
        "E": -1.234,
        "G_corr": 0.123,
    }


def test_upsert_comment_kv_value_boundary():
    from confflow.core.xyz_metadata import upsert_comment_kv

    # replacing a comma-containing value leaves no dangling ",2"
    assert upsert_comment_kv("TSAtoms=1,2 | E=-1", "TSAtoms", "3,4") == "TSAtoms=3,4 | E=-1"
    assert upsert_comment_kv("TSAtoms=1,2", "TSAtoms", "3,4") == "TSAtoms=3,4"

    # historical comma separator: the next pair must survive the replace
    updated = upsert_comment_kv("E=-1.23,CID=A000001", "E", "-2.34")
    assert updated == "E=-2.34,CID=A000001"
    assert "CID=A000001" in updated

    updated = upsert_comment_kv("CID=A000001, Imag=1", "Imag", "0")
    assert "CID=A000001" in updated
    assert "Imag=0" in updated

    # insert into an existing comment / empty comment unchanged
    assert upsert_comment_kv("E=-1", "Imag", "0") == "E=-1 | Imag=0"
    assert upsert_comment_kv("", "Imag", "0") == "Imag=0"


# ---------------------------------------------------------------------------
# Real multi-step chain: TS/freq -> SP -> Refine -> SP
# ---------------------------------------------------------------------------


def _append_chain_frame(path: Path, job_meta_map: dict, res: dict) -> None:
    append_result(str(path), job_meta_map, res)


def test_ts_sp_refine_sp_chain(tmp_path):
    from confflow.blocks.refine.processor import RefineOptions, process_xyz, read_xyz_file
    from confflow.calc.run_services import TaskSourceBuilder
    from confflow.calc.runner import CalcStepRunner

    # --- leg 0: the TS opt/freq result (as a previous calc step wrote it) ----
    ts_result = tmp_path / "ts_result.xyz"
    job_meta_map = {"A000001": {"CID": "A000001", "AddBond": "1-4", "DelBond": "2-3"}}
    _append_chain_frame(
        ts_result,
        job_meta_map,
        {
            "status": "success",
            "job_name": "A000001",
            "final_gibbs_energy": -100.5,
            "g_corr": 0.03,
            "num_imag_freqs": 1,
            "lowest_freq": -321.4,
            "ts_bond_atoms": "1,2",
            "ts_bond_length": 1.456,
            "final_coords": ["C 0.0 0.0 0.0", "C 0.0 0.0 1.456"],
        },
    )

    def sp_leg(input_xyz: Path, out_name: str) -> Path:
        builder = TaskSourceBuilder(
            work_dir=str(tmp_path / ("calc_" + out_name)),
            config={"itask": "sp"},
            iter_geometries_fn=CalcStepRunner._iter_input_geometries,
            job_name_fn=CalcStepRunner._job_name_for_geom,
        )
        tasks, metas = builder.build_from_input(str(input_xyz))
        out = tmp_path / out_name
        for task in tasks:
            res = _run_task(
                tmp_path,
                dict(task.metadata),
                itask=1,
                result={"final_coords": list(task.coords), "e_high": -100.0},
            )
            res["job_name"] = task.job_name
            _append_chain_frame(out, metas, res)
        return out

    # --- leg 1: SP of the TS result ----------------------------------------
    sp1 = sp_leg(ts_result, "sp1.xyz")
    fr = read_xyz_file(str(sp1))[0]
    assert "G=" in fr["comment"]
    assert fr["num_imag_freqs"] == 1
    assert fr["extra_data"]["LowestFreq"] == -321.4
    assert fr["extra_data"]["TSAtoms"] == "1,2"
    assert fr["extra_data"]["TSBond"] == 1.456
    assert fr["extra_data"]["G_corr"] == 0.03
    assert fr["extra_data"]["AddBond"] == "1-4"
    assert fr["extra_data"]["DelBond"] == "2-3"

    # --- leg 2: Refine ------------------------------------------------------
    refined = tmp_path / "refined.xyz"
    outcome = process_xyz(RefineOptions(input_file=str(sp1), output=str(refined), threshold=0.5))
    assert outcome.produced_output, outcome.reason
    fr = read_xyz_file(str(refined))[0]
    assert fr["num_imag_freqs"] == 1
    assert fr["extra_data"]["LowestFreq"] == -321.4
    assert fr["extra_data"]["TSAtoms"] == "1,2"
    assert fr["extra_data"]["TSBond"] == 1.456
    assert fr["extra_data"]["G_corr"] == 0.03
    assert fr["extra_data"]["AddBond"] == "1-4"
    assert fr["extra_data"]["DelBond"] == "2-3"

    # --- leg 3: SP again ----------------------------------------------------
    sp2 = sp_leg(refined, "sp2.xyz")
    fr = read_xyz_file(str(sp2))[0]
    assert fr["num_imag_freqs"] == 1
    assert fr["extra_data"]["LowestFreq"] == -321.4
    assert fr["extra_data"]["TSAtoms"] == "1,2"
    assert fr["extra_data"]["TSBond"] == 1.456
    assert fr["extra_data"]["AddBond"] == "1-4"
    assert fr["extra_data"]["DelBond"] == "2-3"
    # G_corr inherited once and not stacked: G = -100.0 + 0.03
    assert fr["extra_data"]["G_corr"] == 0.03
    assert "G=-99.97" in fr["comment"]


def test_ts_freq_to_opt_drops_stale_analysis_metadata(tmp_path):
    from confflow.blocks.refine.processor import read_xyz_file
    from confflow.calc.run_services import TaskSourceBuilder
    from confflow.calc.runner import CalcStepRunner

    ts_result = tmp_path / "ts_result.xyz"
    job_meta_map = {"A000001": {"CID": "A000001"}}
    _append_chain_frame(
        ts_result,
        job_meta_map,
        {
            "status": "success",
            "job_name": "A000001",
            "final_gibbs_energy": -100.5,
            "g_corr": 0.03,
            "num_imag_freqs": 1,
            "lowest_freq": -321.4,
            "ts_bond_atoms": "1,2",
            "ts_bond_length": 1.456,
            "final_coords": ["C 0.0 0.0 0.0", "C 0.0 0.0 1.456"],
        },
    )

    builder = TaskSourceBuilder(
        work_dir=str(tmp_path / "calc_opt"),
        config={"itask": "opt"},
        iter_geometries_fn=CalcStepRunner._iter_input_geometries,
        job_name_fn=CalcStepRunner._job_name_for_geom,
    )
    tasks, metas = builder.build_from_input(str(ts_result))
    out = tmp_path / "opt.xyz"
    for task in tasks:
        res = _run_task(
            tmp_path,
            dict(task.metadata),
            itask=0,  # opt
            result={"final_coords": ["C 0.0 0.0 0.0", "C 0.0 0.0 1.500"], "e_low": -100.4},
        )
        _append_chain_frame(out, metas, res)

    fr = read_xyz_file(str(out))[0]
    assert "Imag=" not in fr["comment"]
    assert "LowestFreq=" not in fr["comment"]
    assert "TSAtoms=" not in fr["comment"]
    assert "TSBond=" not in fr["comment"]
    assert "G_corr=" not in fr["comment"]
