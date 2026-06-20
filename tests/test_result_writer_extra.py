"""Additional tests for calc result writer helpers."""

from __future__ import annotations

from confflow.calc.result_writer import append_result, format_result_comment, write_failed_xyz
from confflow.core.models import TaskContext


def test_write_failed_xyz_skips_empty_and_records_metadata(tmp_path) -> None:
    work_dir = tmp_path
    tasks = [
        TaskContext(
            job_name="JOB1",
            work_dir=str(work_dir / "JOB1"),
            coords=["H 0 0 0", "H 0 0 0.7"],
            metadata={"CID": "C1"},
            config={},
        ),
        TaskContext(
            job_name="JOB2", work_dir=str(work_dir / "JOB2"), coords=[], metadata={}, config={}
        ),
    ]
    write_failed_xyz(str(work_dir), [], tasks)
    assert not (work_dir / "failed.xyz").exists()

    write_failed_xyz(
        str(work_dir),
        [
            {
                "job_name": "JOB1",
                "error_kind": "exec_error",
                "error": "x" * 250,
            },
            {"job_name": "JOB2", "error": "ignored because coords missing"},
        ],
        tasks,
    )

    text = (work_dir / "failed.xyz").read_text(encoding="utf-8")
    assert "Failed=1 Job=JOB1 CID=C1 ErrorKind=exec_error Error=" in text
    assert "..." in text
    assert "JOB2" not in text
    assert "H    0.00000000   0.00000000   0.00000000" in text


def test_format_result_comment_combines_sp_and_gibbs_energy() -> None:
    comment = format_result_comment(
        {
            "final_gibbs_energy": -1.2,
            "final_sp_energy": -1.5,
            "g_corr": 0.3,
            "num_imag_freqs": 1,
            "lowest_freq": -123.456,
            "ts_bond_atoms": "1,2",
            "ts_bond_length": 1.2345678,
        },
        {"CID": "CID1", "G_corr": 9.9},
    )

    assert comment == ("G=-1.2 CID=CID1 Imag=1 LowestFreq=-123.5 TSAtoms=1,2 TSBond=1.234568")


def test_format_result_comment_uses_energy_metadata_fallbacks() -> None:
    comment = format_result_comment(
        {"energy": -2.0, "num_imag_freqs": 0},
        {"CID": "CID2", "G_corr": 0.12},
    )

    assert comment == "Energy=-2.0 CID=CID2 G_corr=0.12 Imag=0"


def test_append_result_filters_unsuccessful_or_incomplete_results(tmp_path) -> None:
    result = tmp_path / "result.xyz"
    append_result(str(result), {}, {"status": "failed", "final_coords": ["H 0 0 0"]})
    append_result(None, {}, {"status": "success", "final_coords": ["H 0 0 0"]})
    append_result(str(result), {}, {"status": "success", "final_coords": []})
    assert not result.exists()

    append_result(
        str(result),
        {"JOB1": {"CID": "C1"}},
        {
            "status": "success",
            "job_name": "JOB1",
            "energy": -1.0,
            "final_coords": ["H 0 0 0", "H 0 0 0.7"],
        },
    )

    text = result.read_text(encoding="utf-8")
    assert text.startswith("2\nEnergy=-1.0 CID=C1\n")
