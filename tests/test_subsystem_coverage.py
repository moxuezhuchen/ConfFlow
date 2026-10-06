"""Behavior tests for tools/subsystem_coverage.py (DIET-2 P0.1b)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "subsystem_coverage",
    Path(__file__).resolve().parents[1] / "tools" / "subsystem_coverage.py",
)
sc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sc)


def _write_xml(tmp_path: Path, files: dict[str, list[tuple[int, int]]]) -> str:
    parts = []
    for filename, rows in files.items():
        body = "".join(f'<line number="{n}" hits="{h}"/>' for n, h in rows)
        parts.append(
            f'<class name="{Path(filename).name}" filename="{filename}">'
            f"<methods/><lines>{body}</lines></class>"
        )
    p = tmp_path / "coverage.xml"
    p.write_text(
        "<coverage><sources><source>confflow</source></sources>"
        "<packages><package><classes>" + "".join(parts) + "</classes></package>"
        "</packages></coverage>"
    )
    return str(p)


def _write_baseline(tmp_path: Path, subs: dict[str, dict[str, float]], tol: float = 5.0) -> str:
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps({"tolerance_pp": tol, "subsystems": subs}))
    return str(p)


def test_aggregate_is_row_weighted(tmp_path: Path):
    xml = _write_xml(
        tmp_path,
        {
            "execution/a.py": [(1, 1), (2, 1), (3, 0), (4, 0)],
            "execution/b.py": [(1, 1), (2, 1)],
        },
    )
    got = sc.measure_xml(xml)
    assert (got["execution"]["covered"], got["execution"]["valid"]) == (4, 6)
    assert got["execution"]["percent"] == round(100.0 * 4 / 6, 2)
    assert (got["persistence"]["covered"], got["persistence"]["valid"]) == (0, 0)


def test_confgen_kernel_excludes_component_dirs(tmp_path: Path):
    xml = _write_xml(
        tmp_path,
        {
            "science/confgen/engine.py": [(1, 1), (2, 0)],
            "science/confgen/coordination/stage.py": [(1, 1), (2, 1)],
            "science/confgen/ring/stage.py": [(1, 1), (2, 1)],
            "science/confgen/torsion/stage.py": [(1, 1), (2, 1)],
        },
    )
    got = sc.measure_xml(xml)
    assert (got["confgen_kernel"]["covered"], got["confgen_kernel"]["valid"]) == (1, 2)
    assert sc.subsystem_of("science/confgen/coordination/stage.py") is None
    assert sc.subsystem_of("science/confgen/ring/forms.py") is None
    assert sc.subsystem_of("science/confgen/torsion/spec.py") is None
    assert sc.subsystem_of("science/confgen/engine.py") == "confgen_kernel"


def test_subsystem_prefix_mapping():
    assert sc.subsystem_of("execution/batch.py") == "execution"
    assert sc.subsystem_of("confflow/execution/batch.py") == "execution"
    assert sc.subsystem_of("persistence/run_state.py") == "persistence"
    assert sc.subsystem_of("workflow/v4/compiler.py") == "workflow"
    assert sc.subsystem_of("confflow/science/confgen/model.py") == "confgen_kernel"
    assert sc.subsystem_of("analysis/compute.py") is None


def test_check_fails_below_floor(tmp_path: Path):
    rows = [(i, 1 if i <= 74 else 0) for i in range(1, 101)]
    xml = _write_xml(tmp_path, {"execution/a.py": rows})
    base = _write_baseline(
        tmp_path,
        {
            "execution": {"percent": 80.0, "covered": 80, "valid": 100},
            "persistence": {"percent": 80.0, "covered": 8, "valid": 10},
            "workflow": {"percent": 80.0, "covered": 8, "valid": 10},
            "confgen_kernel": {"percent": 80.0, "covered": 8, "valid": 10},
        },
    )
    # execution at 74% is below floor 75% -> exit 1
    assert sc.cmd_check(xml, base) == 1
    measured = sc.measure_xml(xml)
    assert measured["execution"]["percent"] == 74.0
    assert (
        sc.evaluate(
            measured,
            {
                "execution": {"percent": 80.0},
                "persistence": {"percent": 80.0},
                "workflow": {"percent": 80.0},
                "confgen_kernel": {"percent": 80.0},
            },
            5.0,
        )
        != []
    )


def test_check_passes_at_exact_boundary(tmp_path: Path):
    rows = [(i, 1 if i <= 75 else 0) for i in range(1, 101)]
    xml = _write_xml(tmp_path, {"execution/a.py": rows})
    base = _write_baseline(
        tmp_path,
        {
            "execution": {"percent": 80.0, "covered": 80, "valid": 100},
            "persistence": {"percent": 0.0, "covered": 0, "valid": 0},
            "workflow": {"percent": 0.0, "covered": 0, "valid": 0},
            "confgen_kernel": {"percent": 0.0, "covered": 0, "valid": 0},
        },
    )
    assert sc.cmd_check(xml, base) == 0
    measured = sc.measure_xml(xml)
    assert (
        sc.evaluate(
            measured,
            {
                "execution": {"percent": 80.0},
                "persistence": {"percent": 0.0},
                "workflow": {"percent": 0.0},
                "confgen_kernel": {"percent": 0.0},
            },
            5.0,
        )
        == []
    )


def test_baseline_missing_subsystem_errors(tmp_path: Path):
    xml = _write_xml(tmp_path, {"execution/a.py": [(1, 1)]})
    base = _write_baseline(tmp_path, {"execution": {"percent": 80.0, "covered": 8, "valid": 10}})
    try:
        sc.evaluate(sc.measure_xml(xml), {"execution": {"percent": 80.0}}, 5.0)
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for missing subsystem")
    assert sc.cmd_check(xml, base) == 2
