"""Provenance tests for the GFN2-xTB calculator: version string and the ``done.json`` cache key.

Uses small shell-script fakes (no real xtb).  A fake that converges appends one ``x`` to a
counter file per launch, so tests can tell reuse from relaunch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from confflow.execution.confgen_xtb import XtbCalculator, write_xyz_frames, xtb_version
from confflow.science.confgen.calculator import DistanceRestraint, FixedAtom, RelaxRequest

_ELS = ["H", "H"]
_COORDS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]])


def _write_sh(path: Path, body: str) -> Path:
    """Write ``body`` as an executable ``/bin/sh`` script at ``path``."""
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_xtb(path: Path, counter: Path) -> Path:
    """Return a fake xtb that logs each launch to ``counter`` and converges at once."""
    body = (
        f'echo x >> "{counter}"\n'
        "cp in.xyz xtbopt.xyz\n"
        'echo " TOTAL ENERGY -5.0 Eh"\n'
        'echo " GEOMETRY OPTIMIZATION CONVERGED"\n'
    )
    return _write_sh(path, body)


def _launches(counter: Path) -> int:
    return counter.read_text(encoding="utf-8").count("x") if counter.exists() else 0


def _calc(exe: Path, tmp_path: Path) -> XtbCalculator:
    return XtbCalculator(str(exe), tmp_path / "work", _ELS, 0, 0, 5, cores=1)


def test_xtb_version_returns_version_line_after_banner(tmp_path: Path) -> None:
    exe = _write_sh(
        tmp_path / "xtb",
        "echo '          -------------------------------------'\n"
        "echo\n"
        "echo\n"
        "echo \"   * xtb version 6.7.1 (abc1234) compiled by 'u@h' on 2026-10-08\"\n",
    )
    assert xtb_version(str(exe)) == "xtb version 6.7.1 (abc1234) compiled by 'u@h' on 2026-10-08"


def test_xtb_version_falls_back_to_first_word_line(tmp_path: Path) -> None:
    exe = _write_sh(tmp_path / "tool", "echo '-----------'\necho 'some tool 1.0'\n")
    assert xtb_version(str(exe)) == "some tool 1.0"


def test_xtb_version_missing_executable_is_empty(tmp_path: Path) -> None:
    assert xtb_version(str(tmp_path / "no-such-xtb")) == ""


def test_same_request_twice_reuses_done_json(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    exe = _fake_xtb(tmp_path / "xtb", counter)
    calc = _calc(exe, tmp_path)
    req = RelaxRequest("a", _COORDS)
    first = calc.relax_many([req])
    second = calc.relax_many([req])
    assert _launches(counter) == 1
    assert second[0].energy_eh == first[0].energy_eh
    assert second[0].converged is True


def test_changed_restraints_miss_cache(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    calc = _calc(_fake_xtb(tmp_path / "xtb", counter), tmp_path)
    calc.relax_many([RelaxRequest("a", _COORDS, (DistanceRestraint(0, 1, 1.5),))])
    calc.relax_many([RelaxRequest("a", _COORDS, (DistanceRestraint(0, 1, 1.6),))])
    assert _launches(counter) == 2


def test_fixed_reference_is_part_of_cache_identity(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    calc = _calc(_fake_xtb(tmp_path / "xtb", counter), tmp_path)
    xyz = tmp_path / "cache-input.xyz"
    write_xyz_frames(xyz, _ELS, [(_COORDS, "same coordinates")])
    first = calc._run_key(xyz, None, (FixedAtom(0, (0.0, 0.0, 0.0)),))
    second = calc._run_key(xyz, None, (FixedAtom(0, (0.0, 0.0, 0.1)),))
    assert first != second


def test_cached_backend_output_is_rechecked_for_fixed_core_drift(tmp_path: Path) -> None:
    exe = _write_sh(
        tmp_path / "drift_xtb",
        "awk 'NR==3 { $2 += 0.001 } { print }' in.xyz > xtbopt.xyz\n"
        "echo 'TOTAL ENERGY -5.0 Eh'\n"
        "echo 'GEOMETRY OPTIMIZATION CONVERGED'\n",
    )
    counter = tmp_path / "count.txt"
    exe.write_text(exe.read_text(encoding="utf-8") + f'echo x >> "{counter}"\n')
    request = RelaxRequest("fixed", _COORDS, fixed_atoms=(FixedAtom(0, (0.0, 0.0, 0.0)),))
    calc = _calc(exe, tmp_path)
    for _ in range(2):
        (outcome,) = calc.relax_many([request])
        assert not outcome.converged
        assert outcome.coords is None
        assert outcome.failure_kind == "core_displacement"
        assert "0.001" in outcome.detail
    assert _launches(counter) == 1


def test_changed_executable_content_misses_cache(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    exe = _fake_xtb(tmp_path / "xtb", counter)
    req = RelaxRequest("a", _COORDS)
    _calc(exe, tmp_path).relax_many([req])
    assert _launches(counter) == 1
    exe.write_text(exe.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    _calc(exe, tmp_path).relax_many([req])
    assert _launches(counter) == 2


def test_old_format_done_json_is_not_reused(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    exe = _fake_xtb(tmp_path / "xtb", counter)
    here = tmp_path / "work" / "a"
    here.mkdir(parents=True)
    write_xyz_frames(here / "in.xyz", _ELS, [(_COORDS, "frame")])
    in_sha = hashlib.sha256((here / "in.xyz").read_bytes()).hexdigest()
    old = {"in_sha256": in_sha, "energy_eh": -5.0, "converged": True, "wall_s": 0.1}
    (here / "done.json").write_text(json.dumps(old), encoding="utf-8")
    (outcome,) = _calc(exe, tmp_path).relax_many([RelaxRequest("a", _COORDS)])
    assert _launches(counter) == 1
    assert outcome.converged is True
    rewritten = json.loads((here / "done.json").read_text(encoding="utf-8"))
    assert "key_sha256" in rewritten
    assert rewritten["key_sha256"] != in_sha


def test_stale_constrain_file_removed_when_restraints_dropped(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    calc = _calc(_fake_xtb(tmp_path / "xtb", counter), tmp_path)
    run_dir = tmp_path / "work" / "a"
    calc.relax_many([RelaxRequest("a", _COORDS, (DistanceRestraint(0, 1, 1.5),))])
    assert (run_dir / "c.inp").is_file()
    calc.relax_many([RelaxRequest("a", _COORDS)])
    assert not (run_dir / "c.inp").exists()
    assert _launches(counter) == 2
