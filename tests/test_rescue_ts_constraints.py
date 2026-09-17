#!/usr/bin/env python3

"""P0-B: TS rescue must run a constrained bond optimization, not freeze atoms.

The scan point should fix only the a1-a2 bond length (Gaussian ModRedundant
``B a1 a2 F``) while leaving all other degrees of freedom free, and it must not
overwrite the user's ``freeze`` or ``gaussian_modredundant`` settings.
"""

from __future__ import annotations

import pytest

from confflow.calc.components.input_helpers import append_gaussian_modredundant
from confflow.calc.policies.gaussian import GaussianPolicy
from confflow.calc.scan_ops import _ConstrainedScanner
from confflow.core.keyword_rewrite import ensure_gaussian_modredundant_keyword

# --------------------------------------------------------------------------
# append_gaussian_modredundant
# --------------------------------------------------------------------------


def test_append_modredundant_none():
    assert append_gaussian_modredundant(None, "B 10 20 F") == ["B 10 20 F"]


def test_append_modredundant_string_preserves_content():
    result = append_gaussian_modredundant("D 1 2 3 4 F", "B 10 20 F")
    assert result == ["D 1 2 3 4 F", "B 10 20 F"]


def test_append_modredundant_multiline_string():
    result = append_gaussian_modredundant("D 1 2 3 4 F\nX 5 6", "B 10 20 F")
    assert result == ["D 1 2 3 4 F", "X 5 6", "B 10 20 F"]


def test_append_modredundant_list_preserves_content():
    result = append_gaussian_modredundant(["D 1 2 3 4 F"], "B 10 20 F")
    assert result == ["D 1 2 3 4 F", "B 10 20 F"]


def test_append_modredundant_does_not_duplicate():
    assert append_gaussian_modredundant(["B 10 20 F"], "B 10 20 F") == ["B 10 20 F"]


# --------------------------------------------------------------------------
# ensure_gaussian_modredundant_keyword
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("opt", "opt=modredundant"),
        ("opt=ts", "opt=(ts,modredundant)"),
        ("opt(ts)", "opt(ts,modredundant)"),
        ("opt=(ts,calcfc)", "opt=(ts,calcfc,modredundant)"),
        ("opt nomicro", "opt=modredundant nomicro"),
        ("opt=modredundant", "opt=modredundant"),
        ("opt(modredundant)", "opt(modredundant)"),
    ],
)
def test_ensure_gaussian_modredundant_keyword(keyword, expected):
    result = ensure_gaussian_modredundant_keyword(keyword)
    assert "modredundant" in result.lower()
    assert result == expected


# --------------------------------------------------------------------------
# Scanner integration: real constrained optimization config
# --------------------------------------------------------------------------


def _coords(n: int = 20) -> list[str]:
    lines = []
    for i in range(1, n + 1):
        lines.append(f"C {float(i) * 0.1:12.6f} 0.000000 0.000000")
    return lines


@pytest.mark.parametrize(
    "modredundant",
    [None, "D 1 2 3 4 F", ["D 1 2 3 4 F"], "D 1 2 3 4 F\nX 7 8"],
)
def test_scanner_constrains_only_target_bond(monkeypatch, tmp_path, modredundant):
    captured: dict = {}

    def fake_run(work_dir, job_name, prog_id, coords, config, is_sp_task=False):
        captured["coords"] = list(coords)
        captured["config"] = dict(config)
        return {"e_low": -1.0, "final_coords": coords}

    monkeypatch.setattr("confflow.calc.scan_ops.executor._run_calculation_step", fake_run)

    cfg = {
        "keyword": "opt=(ts,calcfc)",
        "freeze": "5,6",
        "gaussian_modredundant": modredundant,
    }
    scanner = _ConstrainedScanner(cfg, str(tmp_path), 10, 20)

    energy, final_coords, error = scanner.run(_coords(), target_r=1.5)

    assert error is None
    assert energy == -1.0
    assert final_coords is not None

    scan_cfg = captured["config"]
    # User's Cartesian freeze is preserved untouched.
    assert scan_cfg["freeze"] == "5,6"
    # Bond constraint is appended, user ModRedundant content preserved.
    assert scan_cfg["gaussian_modredundant"][-1] == "B 10 20 F"
    assert "modredundant" in str(scan_cfg["keyword"]).lower()
    if modredundant:
        assert "D 1 2 3 4 F" in scan_cfg["gaussian_modredundant"]

    # Render the actual Gaussian input and inspect the coordinate block.
    policy = GaussianPolicy()
    output = tmp_path / "scan.gjf"
    policy.generate_input(
        {"job_name": "scan", "coords": captured["coords"], "config": scan_cfg},
        str(output),
    )
    text = output.read_text(encoding="utf-8")

    lines = text.splitlines()
    cm_index = next(i for i, line in enumerate(lines) if line.strip() == "0 1")
    coord_lines = lines[cm_index + 1 : cm_index + 21]
    flags = {i + 1: line.split()[1] for i, line in enumerate(coord_lines)}

    assert flags[5] == "-1"
    assert flags[6] == "-1"
    assert flags[10] == "0"
    assert flags[20] == "0"
    if modredundant:
        assert "D 1 2 3 4 F" in text
    assert "B 10 20 F" in text
