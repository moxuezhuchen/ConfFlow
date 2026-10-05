"""Tool regression: capture plugin must preserve ConfgenEngine.run signature."""

from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path
from types import ModuleType

TOOLS_ACC = Path(__file__).resolve().parent
TOOLS = TOOLS_ACC.parent / "refactor"
CF_ROOT = Path(__file__).resolve().parents[2]


def _load_plugin_copy(monkeypatch, fake_engine: ModuleType):
    monkeypatch.setitem(sys.modules, "confflow.science.confgen.engine", fake_engine)
    monkeypatch.syspath_prepend(str(TOOLS))
    for mod in ("ts1_engine", "capture_engine_reports_sigcopy"):
        sys.modules.pop(mod, None)
    spec = importlib.util.spec_from_file_location(
        "capture_engine_reports_sigcopy",
        TOOLS / "capture_engine_reports.py",
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    sys.modules["capture_engine_reports_sigcopy"] = plugin
    spec.loader.exec_module(plugin)
    return plugin


def _fake_engine_module() -> ModuleType:
    mod = ModuleType("confflow.science.confgen.engine")

    class ConfgenEngine:
        def run(self, context, should_cancel=None):
            return ("fake-run", context, should_cancel)

    mod.ConfgenEngine = ConfgenEngine  # type: ignore[attr-defined]
    return mod


def test_capture_preserves_run_signature(monkeypatch) -> None:
    fake_engine = _fake_engine_module()
    plugin = _load_plugin_copy(monkeypatch, fake_engine)
    original = fake_engine.ConfgenEngine.run
    assert list(inspect.signature(original).parameters) == [
        "self",
        "context",
        "should_cancel",
    ]
    plugin.pytest_configure(None)
    try:
        wrapped = fake_engine.ConfgenEngine.run
        assert wrapped is not original
        assert list(inspect.signature(wrapped).parameters) == [
            "self",
            "context",
            "should_cancel",
        ]
        assert inspect.signature(wrapped) == inspect.signature(original)
        assert getattr(wrapped, "__wrapped__", None) is original
    finally:
        plugin.pytest_unconfigure(None)
    assert fake_engine.ConfgenEngine.run is original


def test_capture_preserves_real_engine_signature_isolated() -> None:
    probe = (
        "import inspect;"
        "from confflow.science.confgen.engine import ConfgenEngine;"
        "before=ConfgenEngine.run;"
        "sig_before=inspect.signature(before);"
        "import capture_engine_reports as cap;"
        "cap.pytest_configure(None);"
        "mid=ConfgenEngine.run;"
        "sig_mid=inspect.signature(mid);"
        "ok_sig=list(sig_mid.parameters)==['self','context','should_cancel'];"
        "ok_wrap=getattr(mid,'__wrapped__',None) is before;"
        "cap.pytest_unconfigure(None);"
        "after=ConfgenEngine.run;"
        "ok_restore=after is before;"
        "print(f'{ok_sig} {ok_wrap} {ok_restore} {sig_before} {sig_mid}');"
        "raise SystemExit(0 if (ok_sig and ok_wrap and ok_restore) else 1)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(CF_ROOT),
        env={
            **{
                k: v
                for k, v in __import__("os").environ.items()
                if k not in ("PYTHONPATH", "PYTHONHOME")
            },
            "PYTHONDONTWRITEBYTECODE": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": (
                f"{TOOLS_ACC / 'noeditable'}{__import__('os').pathsep}"
                f"{CF_ROOT}{__import__('os').pathsep}{TOOLS}"
            ),
        },
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
