#!/usr/bin/env python3
"""Tests for the tested-isolation ConfFlow wheel deployer.

The deployer covers M2-4 Gate A (candidate) and Gate B (release
attestation) failure paths. These tests intentionally drive the script
through ``subprocess`` so the real CLI surface stays in the gate, but
they build wheel / SHA256SUM fixtures locally without touching the
network or any persistent venv.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_release_wheel.py"


def _build_minimal_wheel(wheel_path, *, version, commit, requires=()):
    """Materialize a real pip-installable wheel for the deployer tests.

    Routes through ``python -m build`` so the resulting wheel has a proper
    ``entry_points.txt`` (which generates ``bin/confflow``) and a valid
    ``.dist-info/RECORD``. We use build-system = setuptools against a tiny
    throwaway project; nothing in this directory is used.
    """
    import tempfile
    from pathlib import Path as _P

    wheel_path.parent.mkdir(parents=True, exist_ok=True)
    if wheel_path.exists():
        wheel_path.unlink()
    with tempfile.TemporaryDirectory() as tmp:
        project = _P(tmp) / "fixture"
        pkg = project / "src" / "confflow"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        (pkg / "__build__.py").write_text(
            f'COMMIT: str | None = "{commit}"\n' "DIRTY: bool | None = False\n",
            encoding="utf-8",
        )
        cli_text = (
            "from . import __version__\n"
            "import json\n"
            "import sys\n"
            "def main(*argv):\n"
            "    args = list(argv) if argv else sys.argv[1:]\n"
            "    if len(args) >= 2 and args[-2:] == ['--capabilities', '--json']:\n"
            "        sys.stdout.write(json.dumps({\n"
            "            'schema_version': 4,\n"
            f"            'version': __version__,\n"
            "            'capabilities': {'workflow_state': True, 'resume': True, 'dag': True},\n"
            "            'artifacts': {\n"
            "                'run_summary': 'run_summary.json',\n"
            "                'workflow_stats': 'workflow_stats.json',\n"
            "                'workflow_state': '.workflow_state.json',\n"
            "                'run_report': 'run_report.txt',\n"
            "                'min_xyz': 'min.xyz',\n"
            "                'output_manifest': 'output_manifest.json',\n"
            "            },\n"
            "            'commands': {n: True for n in ('bash','nohup','setsid','xargs','sha256sum','mktemp','base64')},\n"
            f"            'build': {{'commit': '{commit}', 'dirty': False}},\n"
            "            'producer': {\n"
            "                'package': 'confflow',\n"
            f"                'version': __version__,\n"
            f"                'build': {{'commit': '{commit}', 'dirty': False}},\n"
            "                'wheel': {'filename': None, 'sha256': None},\n"
            "                'install_provenance': {'status': 'missing', 'reason_code': 'missing_file'},\n"
            "            },\n"
            "            'executable': {'path': None, 'sha256': None, 'python': '/usr/bin/python3'},\n"
            "        }) + '\\n')\n"
            "        return 0\n"
            "    return 1\n"
        )
        (pkg / "cli.py").write_text(cli_text, encoding="utf-8")
        (pkg / "__main__.py").write_text(
            "from .cli import main\nimport sys\nsys.exit(main(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        dependency_lines = (
            "dependencies = [" + ", ".join(repr(item) for item in requires) + "]\n"
            if requires
            else ""
        )
        (project / "pyproject.toml").write_text(
            "[build-system]\n"
            "requires = ['setuptools>=82', 'wheel']\n"
            "build-backend = 'setuptools.build_meta'\n"
            "[project]\n"
            f"name = 'confflow'\nversion = '{version}'\n"
            "requires-python = '>=3.10'\n"
            f"{dependency_lines}"
            "[project.scripts]\n"
            "confflow = 'confflow.cli:main'\n"
            "[tool.setuptools.packages.find]\n"
            "where = ['src']\n",
            encoding="utf-8",
        )
        out = project / "out"
        out.mkdir()
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
            cwd=str(project),
            check=True,
        )
        built = next(out.glob("*.whl"))
        wheel_path.write_bytes(built.read_bytes())


def _write_sha256sums(sums_path, *, wheel):
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    sums_path.parent.mkdir(parents=True, exist_ok=True)
    sums_path.write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    return digest


def _alloc_paths(tmp_path, name="confflow-stage1"):
    paths = {
        "wheel_dir": tmp_path / "wheel",
        "sums_path": tmp_path / "SHA256SUMS",
        "dependency_lock": tmp_path / "runtime.lock",
        "wheelhouse": tmp_path / "wheelhouse",
        "target_venv": tmp_path / name,
        "parent": tmp_path,
    }
    paths["dependency_lock"].write_text("--only-binary=:all:\n", encoding="utf-8")
    paths["wheelhouse"].mkdir()
    (paths["wheelhouse"] / "SHA256SUMS").write_text("", encoding="utf-8")
    return paths


def _run_deployer(*, args, include_dependency_inputs=True):
    # Use the worktree's source tree; the deployer lives in the candidate
    # worktree and imports confflow.* from it, not from any globally installed
    # confflow in /opt/ConfFlow/.venv.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    full_args = list(args)
    if include_dependency_inputs:
        target_index = full_args.index("--target-venv")
        root = Path(full_args[target_index + 1]).parent
        full_args.extend(
            [
                "--dependency-lock",
                str(root / "runtime.lock"),
                "--wheelhouse",
                str(root / "wheelhouse"),
            ]
        )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *full_args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_dry_run_with_no_target_venv(tmp_path):
    paths = _alloc_paths(tmp_path)
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wheel)

    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
            "--dry-run",
        ]
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["mode"] == "candidate"
    assert payload["wheel_digest"] == hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_checksum_mismatch_aborts(tmp_path):
    paths = _alloc_paths(tmp_path)
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    paths["sums_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["sums_path"].write_text("0" * 64 + "  " + wheel.name + "\n", encoding="utf-8")
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
            "--dry-run",
        ]
    )
    assert result.returncode != 0
    assert "digest mismatch" in result.stderr.lower()


def test_basename_must_match_expected_version(tmp_path):
    paths = _alloc_paths(tmp_path)
    wrong = paths["wheel_dir"] / "confflow-9.9.9-py3-none-any.whl"
    _build_minimal_wheel(wrong, version="9.9.9", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wrong)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wrong),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
            "--dry-run",
        ]
    )
    assert result.returncode != 0
    assert "basename" in result.stderr.lower()


def test_glob_in_sha256sums_is_rejected(tmp_path):
    """A ``*`` glob inside ``SHA256SUMS`` must not count as a wheel row."""
    paths = _alloc_paths(tmp_path)
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    sums = paths["sums_path"]
    sums.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    # A ``*`` glob is silently dropped by ``read_sha256sums``; with no row
    # remaining for ``confflow-1.4.5-py3-none-any.whl`` the deployer must
    # reject the install.
    sums.write_text(f"{digest}  *.whl\n", encoding="utf-8")
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(sums),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
            "--dry-run",
        ]
    )
    assert result.returncode != 0
    stderr_lower = result.stderr.lower()
    assert (
        "glob" in stderr_lower
        or "no wheel" in stderr_lower
        or "exactly one row" in stderr_lower
        or "found 0" in stderr_lower
    ), result.stderr


def test_existing_target_venv_refused(tmp_path):
    paths = _alloc_paths(tmp_path)
    paths["target_venv"].mkdir()
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wheel)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
            "--dry-run",
        ]
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr.lower()


def test_build_commit_mismatch_aborts(tmp_path):
    paths = _alloc_paths(tmp_path)
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wheel)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "b" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
            "--dry-run",
        ]
    )
    assert result.returncode != 0
    assert "commit" in result.stderr.lower()


def test_candidate_install_rewrites_console_script_shebang_to_final_target(tmp_path):
    paths = _alloc_paths(tmp_path, name="confflow-shebang-venv")
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wheel)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
        ]
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    script = paths["target_venv"] / "bin" / "confflow"
    launcher_header = script.read_text(encoding="utf-8").splitlines()[:3]
    assert launcher_header[0].startswith("#!")
    assert any(str(paths["target_venv"]) in line for line in launcher_header)
    assert all(str(paths["target_venv"]) + ".staging" not in line for line in launcher_header)


def test_candidate_mode_record_is_unverified(tmp_path):
    """End-to-end: candidate install writes an unverified record + runnable venv."""
    paths = _alloc_paths(tmp_path, name="confflow-candidate-venv")
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wheel)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
        ]
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["installed"] is True
    assert payload["mode"] == "candidate"
    record_path = paths["target_venv"] / "share" / "confflow" / "install-provenance.json"
    assert record_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["attestation_verified"] is False
    assert record["schema"] == "confflow.install-provenance.v2"
    assert (
        record["dependency_lock_sha256"]
        == hashlib.sha256(paths["dependency_lock"].read_bytes()).hexdigest()
    )
    assert (
        record["wheelhouse_manifest_sha256"]
        == hashlib.sha256((paths["wheelhouse"] / "SHA256SUMS").read_bytes()).hexdigest()
    )
    assert record["python_version"].startswith("3.12.")
    assert record["platform"] == "linux-x86_64"
    assert "include-system-site-packages = false" in (
        (paths["target_venv"] / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    )


def test_candidate_record_status_is_unverified(tmp_path):
    """``read_install_provenance`` reports ``attestation_unverified`` for candidate installs."""
    from confflow.install_provenance import (
        REASON_ATTESTATION_UNVERIFIED,
        read_install_provenance,
    )

    paths = _alloc_paths(tmp_path, name="confflow-elev-venv")
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(wheel, version="1.4.5", commit="a" * 40)
    _write_sha256sums(paths["sums_path"], wheel=wheel)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
        ]
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    digest_obj, _errors = read_install_provenance(sys_prefix=str(paths["target_venv"]))
    assert digest_obj.status == "invalid"
    assert digest_obj.reason_code == REASON_ATTESTATION_UNVERIFIED


def test_lock_and_wheelhouse_are_required(tmp_path):
    result = _run_deployer(
        args=["--mode", "candidate"],
        include_dependency_inputs=False,
    )
    assert result.returncode != 0
    assert "--dependency-lock" in result.stderr


def test_pip_check_failure_rolls_back_staging(tmp_path):
    paths = _alloc_paths(tmp_path, name="confflow-pip-check-venv")
    wheel = paths["wheel_dir"] / "confflow-1.4.5-py3-none-any.whl"
    _build_minimal_wheel(
        wheel,
        version="1.4.5",
        commit="a" * 40,
        requires=("missing-dependency==1.0",),
    )
    _write_sha256sums(paths["sums_path"], wheel=wheel)
    result = _run_deployer(
        args=[
            "--mode",
            "candidate",
            "--wheel",
            str(wheel),
            "--sha256sums",
            str(paths["sums_path"]),
            "--target-venv",
            str(paths["target_venv"]),
            "--expected-version",
            "1.4.5",
            "--expected-commit",
            "a" * 40,
            "--expected-tag",
            "v1.4.5-candidate",
        ]
    )
    assert result.returncode != 0
    assert "pip check failed" in result.stderr.lower()
    assert not paths["target_venv"].exists()
    assert not list(tmp_path.glob("confflow-pip-check-venv.staging*"))
