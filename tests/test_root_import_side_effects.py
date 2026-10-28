#!/usr/bin/env python3

"""Import-time side effects of the root package and the CLI/worker entry points.

Each check runs in a fresh interpreter so that modules loaded or logging state
changed by one check cannot leak into another.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import confflow

REPO_ROOT = Path(__file__).resolve().parents[1]
HEAVY_TOP_LEVEL = ("rdkit", "numpy", "numba", "psutil")

# Mirrors the ``confflow`` console script: ``confflow.main:main`` is the entry.
ENTRY_CODE = "import sys; from confflow.main import main; sys.exit(main())"
WORKER_MODULE = "confflow.control_worker"


def _run_python(
    code: str, *args: str, extra_path: tuple[Path, ...] = ()
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([*map(str, extra_path), str(REPO_ROOT)])
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _run_module(module: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


_REPORT = """
import json, logging, sys
heavy = sorted({m.split('.')[0] for m in sys.modules} & set(%r))
print(json.dumps({"heavy": heavy, "disable": logging.root.manager.disable}))
"""


def _report(code_prefix: str, *args: str, extra_path: tuple[Path, ...] = ()) -> dict:
    result = _run_python(code_prefix + _REPORT % (HEAVY_TOP_LEVEL,), *args, extra_path=extra_path)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def test_models_import_loads_no_scientific_dependency_and_keeps_logging():
    report = _report("import confflow.application.execution.models\n")

    assert report["heavy"] == []
    assert report["disable"] == 0


def test_plain_package_import_loads_no_scientific_dependency():
    report = _report("import confflow\n")

    assert report["heavy"] == []
    assert report["disable"] == 0


def test_plain_package_import_does_not_touch_logging_even_with_probe_argv():
    # ``--version`` is a probe argv: the old root package disabled logging here.
    report = _report("import confflow\n", "--version")

    assert report["disable"] == 0


def test_package_import_does_not_load_the_probe_guard():
    result = _run_python("import sys, confflow\nprint('confflow.entry_probe' in sys.modules)\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


@pytest.mark.parametrize(
    "args",
    [
        ["--version"],
        ["--capabilities"],
        ["--capabilities", "--json"],
    ],
)
def test_cli_probe_argv_disables_logging_before_heavy_imports(args):
    result = _run_python(
        "import logging\nimport confflow.cli\nprint(logging.root.manager.disable)\n", *args
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(logging.WARNING)


def test_non_probe_cli_import_keeps_logging_enabled():
    result = _run_python(
        "import logging\nimport confflow.cli\nprint(logging.root.manager.disable)\n", "config"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


@pytest.mark.parametrize("module_name", ["rdkit", "psutil", "numba"])
def test_optional_flags_match_a_direct_import_attempt(module_name):
    code = (
        "import importlib, confflow\n"
        "try:\n"
        f"    importlib.import_module({module_name!r})\n"
        "    direct = True\n"
        "except Exception:\n"
        "    direct = False\n"
        "flag = {'rdkit': 'RDKIT_AVAILABLE', 'psutil': 'PSUTIL_AVAILABLE', 'numba': 'NUMBA_AVAILABLE'}\n"
        f"print(direct == getattr(confflow, flag[{module_name!r}]))\n"
    )
    result = _run_python(code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True"


def test_lazy_handles_resolve_to_the_imported_modules():
    code = (
        "import confflow\n"
        "if confflow.RDKIT_AVAILABLE:\n"
        "    import rdkit.Chem as chem, rdkit.Chem.AllChem as allchem\n"
        "    assert confflow.Chem is chem and confflow.AllChem is allchem\n"
        "else:\n"
        "    assert confflow.Chem is None and confflow.AllChem is None\n"
        "if confflow.PSUTIL_AVAILABLE:\n"
        "    import psutil\n"
        "    assert confflow.psutil is psutil\n"
        "print('ok')\n"
    )
    result = _run_python(code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_unknown_root_attribute_still_raises_attribute_error():
    result = _run_python(
        "import confflow\n"
        "try:\n"
        "    confflow.no_such_thing\n"
        "except AttributeError:\n"
        "    print('raised')\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "raised"


def test_broken_numba_package_degrades_to_fallback(tmp_path: Path):
    # A findable package whose import raises is unavailable, not a crash.
    fake = tmp_path / "numba"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        "raise RuntimeError('broken numba install')\n", encoding="utf-8"
    )
    code = (
        "import confflow\n"
        "print(confflow.NUMBA_AVAILABLE)\n"
        "@confflow.njit\n"
        "def plain(x):\n"
        "    return x + 1\n"
        "@confflow.njit(nopython=True, cache=True)\n"
        "def configured(x):\n"
        "    return x * 2\n"
        "print(plain(1), configured(3))\n"
    )
    result = _run_python(code, extra_path=(tmp_path,))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-2:] == ["False", "2 6"]


def test_numba_fallback_is_the_noop_decorator_when_unavailable(tmp_path: Path):
    fake = tmp_path / "numba"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    # A package with no ``njit`` attribute fails the ``from numba import njit``
    # import, which must degrade to the same fallback.
    code = "import confflow\n" "print(confflow.NUMBA_AVAILABLE, confflow.njit(lambda v: v)(5))\n"
    result = _run_python(code, extra_path=(tmp_path,))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False 5"


@pytest.mark.parametrize(
    "args",
    [
        ["--version"],
        ["--capabilities"],
        ["--capabilities", "--json"],
    ],
)
def test_version_and_capabilities_probes_emit_clean_stdout(args):
    result = _run_python(ENTRY_CODE, *args)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if args == ["--version"]:
        import confflow

        assert result.stdout == f"{confflow.__version__}\n"
    else:
        payload = json.loads(result.stdout)
        assert payload["schema_version"] >= 1


def test_control_and_config_json_probes_emit_clean_json_only():
    control = _run_python(ENTRY_CODE, "control", "capabilities", "--json")
    config = _run_python(ENTRY_CODE, "config", "contract", "--json")

    for result in (control, config):
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        json.loads(result.stdout)
    assert json.loads(control.stdout)["ok"] is True


def test_control_json_error_probe_keeps_structured_stdout():
    result = _run_python(ENTRY_CODE, "control", "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["protocol_schema"] == "confflow.control.v1"


def test_worker_json_probe_stdout_stays_machine_readable(tmp_path: Path):
    missing = tmp_path / "missing"
    result = _run_module(
        WORKER_MODULE,
        "--json",
        "--state-root",
        str(missing),
        "--run-id",
        "r1",
        "--handoff",
        str(missing / "handoff.json"),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("control worker failed:")
    assert "WARNING" not in result.stderr


class _BrokenModule(types.ModuleType):
    """Module whose attribute lookup raises, like a native library failing to bind."""

    def __getattr__(self, name):
        raise RuntimeError(f"broken {name}")


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["--capabilities", "--json"],
        ["control", "capabilities", "--json"],
        ["config", "contract", "--json"],
        ["--json", "--state-root", "s", "--run-id", "r", "--handoff", "h"],
    ],
)
def test_probe_detection_recognises_machine_readable_argv(argv):
    from confflow import entry_probe

    assert entry_probe.is_handshake_probe(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["control", "capabilities"],
        ["config", "contract"],
        ["v4", "run", "--json"],
        ["--json", "--state-root", "s", "--run-id", "r"],
    ],
)
def test_probe_detection_ignores_other_argv(argv):
    from confflow import entry_probe

    assert entry_probe.is_handshake_probe(argv) is False


def test_numba_detection_falls_back_when_the_import_is_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "numba", None)

    assert confflow._detect_numba() == (False, confflow._njit_fallback)


def test_numba_detection_treats_a_raising_package_as_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "numba", _BrokenModule("numba"))

    assert confflow._detect_numba() == (False, confflow._njit_fallback)


def test_numba_detection_returns_the_real_decorator_when_importable(monkeypatch):
    fake = types.ModuleType("numba")

    def njit(*args, **kwargs):
        return args[0] if args else (lambda func: func)

    fake.njit = njit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "numba", fake)

    assert confflow._detect_numba() == (True, njit)


def test_rdkit_and_psutil_detection_report_failures_without_raising(monkeypatch):
    monkeypatch.setitem(sys.modules, "rdkit", _BrokenModule("rdkit"))
    monkeypatch.setitem(sys.modules, "psutil", None)

    assert confflow._detect_rdkit() == (False, None, None)
    assert confflow._detect_psutil() == (False, None)


def test_rdkit_and_psutil_detection_return_the_modules_when_importable(monkeypatch):
    fake_rdkit = types.ModuleType("rdkit")
    fake_chem = types.ModuleType("rdkit.Chem")
    fake_allchem = types.ModuleType("rdkit.Chem.AllChem")
    fake_rdkit.Chem = fake_chem  # type: ignore[attr-defined]
    fake_chem.AllChem = fake_allchem  # type: ignore[attr-defined]
    fake_psutil = types.ModuleType("psutil")
    monkeypatch.setitem(sys.modules, "rdkit", fake_rdkit)
    monkeypatch.setitem(sys.modules, "rdkit.Chem", fake_chem)
    monkeypatch.setitem(sys.modules, "rdkit.Chem.AllChem", fake_allchem)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert confflow._detect_rdkit() == (True, fake_chem, fake_allchem)
    assert confflow._detect_psutil() == (True, fake_psutil)


def test_fallback_decorator_returns_the_function_unchanged():
    def target(value):
        return value

    assert confflow._njit_fallback(target) is target
    assert confflow._njit_fallback(nopython=True)(target) is target


def test_entry_probe_silences_logging_only_for_probe_argv(monkeypatch):
    from confflow import entry_probe

    with monkeypatch.context() as mp:
        mp.setattr(sys, "argv", ["confflow", "--capabilities"])
        importlib.reload(entry_probe)
        try:
            assert logging.root.manager.disable == logging.WARNING
            assert entry_probe.VERSION_OR_CAPABILITIES_PROBE is True
        finally:
            logging.disable(logging.NOTSET)
    importlib.reload(entry_probe)
    assert logging.root.manager.disable == logging.NOTSET
