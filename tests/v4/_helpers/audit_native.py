"""Shared audit fake-native and chain helpers (moved verbatim from test_audit_regressions_r2)."""

from __future__ import annotations

import copy
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

from confflow.application.v4_run import RunInputs, import_xyz
from confflow.domain import FrozenDict
from confflow.execution.environment import ExecutionEnvironment, measure_executable
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.producer import get_recipe_v4
from confflow.programs.registry import get_program_adapter

REPO_ROOT = (
    Path(__file__).resolve().parents[3]
)  # L0.7: repo root (was parents[2] in the test module)

WATER_XYZ = "3\nwater\nO 0 0 0\nH .76 .59 0\nH .76 -.59 0\n"


FAKE_ORCA = (
    Path(__file__).resolve().parents[1] / "fakes" / "fake_orca.py"
)  # L0.7: tests/v4/fakes (was parent / "fakes" in the test module)


def _science_native(root: Path, *, delay: float = 0.0) -> Path:
    """Install the audit fake native: records launch + received env.

    The script appends one line per launch to ``science-count`` and dumps
    the complete environment it actually received plus the value it used
    for the reported energy.  ``SCIENCE_ENV`` (or its absence) drives a
    real, observable scientific difference.
    """
    root.mkdir(parents=True, exist_ok=True)
    script = root / "science_orca"
    script.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, os, sys, time
            from pathlib import Path
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.v4.fakes import fake_orca as f
            count = Path({str(root / "science-count")!r})
            lines = count.read_text().splitlines() if count.exists() else []
            count.write_text("\\n".join(lines + ["launch"]) + "\\n")
            record = Path({str(root / "science-record")!r})
            record.write_text(json.dumps({{
                "value": os.environ.get("SCIENCE_ENV", "ABSENT"),
                "env": dict(os.environ),
            }}))
            time.sleep({delay!r})
            f.ENERGY_HARTREE = float(os.environ.get("SCIENCE_ENV", -30))
            os.environ["FAKE_MODE"] = "ts_candidate"
            sys.exit(f.main(sys.argv))
            """))
    script.chmod(0o755)
    return script


def _launches(root: Path) -> int:
    count = root / "science-count"
    if not count.exists():
        return 0
    return len(count.read_text().splitlines())


def _last_record(root: Path) -> dict[str, Any]:
    return json.loads((root / "science-record").read_text())


def _single_step_doc(executable: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Recipe-derived one-step document (the R1 attack shape)."""
    doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
    doc["steps"] = doc["steps"][:1]
    doc["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    execution: dict[str, Any] = {"executable": str(executable)}
    if env is not None:
        execution["env"] = dict(env)
    doc["steps"][0]["execution"] = execution
    return doc


def _stored_environment_digest(run_root: Path, step_id: str) -> str:
    """Return the single item's durable environment digest from the store."""
    with SqliteWorkItemStore.open(store_path(str(run_root), step_id)) as store:
        item_ids = store.list_items()
        assert len(item_ids) == 1, item_ids
        registered = store.get_registered(item_ids[0])
    digest = registered["environment_digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    return digest


def _science_chain_native(root: Path) -> Path:
    """Fake native for the full TSPES chain (freq failure switchable)."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "chain_orca"
    script.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, sys
            from pathlib import Path
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.v4.fakes import fake_orca as f
            counter = Path({str(root / "chain-count")!r})
            lines = counter.read_text().splitlines() if counter.exists() else []
            counter.write_text("\\n".join(lines + ["launch"]) + "\\n")
            text = open(sys.argv[1]).read()
            cwd = os.getcwd()
            if "IRC" in text:
                os.execv(sys.executable, [sys.executable, {str(REPO_ROOT / "tests/v4/fakes/fake_irc.py")!r}, *sys.argv[1:]])
            if "Freq" in text:
                os.environ["FAKE_MODE"] = "success_freq_noshift"
                ts = "/ts_freq/" in cwd
                f.ENERGY_HARTREE = float(os.environ["TS_FREQ_E"]) if ts else -65.0
                f.GIBBS_CORRECTION = 0.10 if ts else 0.20
            elif " SP" in text:
                os.environ["FAKE_MODE"] = "success_sp"
                f.ENERGY_HARTREE = float(os.environ["TS_SP_E"]) if "/ts_sp/" in cwd else -80.0
            elif "OptTS" in text:
                os.environ["FAKE_MODE"] = "ts_candidate"
            else:
                os.environ["FAKE_MODE"] = "success_opt"
            sys.exit(f.main(sys.argv))
            """))
    script.chmod(0o755)
    return script


def _tspes_doc(script: Path, *, sp: str, freq: str) -> dict[str, Any]:

    doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
    doc["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    for step in doc["steps"]:
        if step["executor"] == "calculation":
            step["execution"] = {
                "executable": str(script),
                "env": {"TS_SP_E": sp, "TS_FREQ_E": freq},
            }
    return doc


def _tspes_inputs() -> RunInputs:
    from dataclasses import replace as _replace

    from confflow.domain import StructureSet

    (record,) = tuple(import_xyz(WATER_XYZ))
    # Stable group identity for this pre-R5 helper: a constant group key
    # survives import-map reconciliation; lineage re-roots to the
    # persisted entity id on both the fresh and resumed paths.
    record = _replace(record, group_key="rxn")
    return RunInputs(structures=FrozenDict({"structures": StructureSet.of(record)}))


def _digest_over(executable: Path, env: dict[str, str]) -> str:
    """Recompute the exact environment digest over a launched env mapping."""
    identity = measure_executable(str(executable), adapter=get_program_adapter("orca"))
    return ExecutionEnvironment(
        program="orca",
        program_version=identity.program_version,
        executable_digest=identity.digest,
        relevant_env=FrozenDict(env),
    ).digest()
