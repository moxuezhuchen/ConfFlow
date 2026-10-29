"""Rerun the seven original scenarios with explicit healthy-result assertions."""

import importlib.util
import json
import os

from confflow.domain.errors import DomainError

spec = importlib.util.spec_from_file_location(
    "original_review", "/opt/ConfFlow/docs/reviews/data/architecture-review-20261029/reproduce.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
os.environ["CONFFLOW_SERVER_CONFIG"] = str(m.ROOT / "server.toml")
os.environ["CONFFLOW_SERVER_STATE_DIR"] = str(m.ROOT / "quota-state")
cases = [
    ("abandoned_environment", m.abandoned_environment),
    ("pending_environment", lambda r: m.abandoned_environment(r, claimed=False)),
    ("abandoned_model_workflow", m.abandoned_model_workflow),
    ("mlip_topology", m.topology_and_energy),
    ("mlip_topology_workflow", m.mlip_topology_workflow),
    ("mlip_manifest", m.mlip_manifest),
    ("confgen_path", m.confgen_path),
]
result = {"root": str(m.ROOT), "cases": {}}
for name, case in cases:
    folder = m.ROOT / name
    folder.mkdir()
    try:
        actual = case(folder)
    except DomainError as exc:
        assert name == "confgen_path" and "allowed_executables" in str(exc)
        assert not (folder / "xtb-launches.jsonl").exists()
        actual = {"rejected": True, "error": str(exc), "launches": 0}
    if name in ("abandoned_environment", "pending_environment"):
        assert actual["registered_as_b"] and not actual["registered_as_a"]
        assert actual["actual_executions"] == ["B", "A"]
    elif name == "abandoned_model_workflow":
        assert actual["returned_energies"] == [-5, -10, -5]
    elif name == "mlip_topology":
        assert actual["input_graph"] == actual["output_graph"]
        assert actual["output_patch"] is not None
    elif name == "mlip_topology_workflow":
        assert actual["output_graph"] == [[1], [0], []]
    elif name == "mlip_manifest":
        energies = actual["manifest"]["results"][0]["energies"]
        assert energies["electronic_hartree"] == -5
        assert energies["source"]["program"] == "fairchem"
    elif name == "confgen_path":
        assert actual["rejected"]
    result["cases"][name] = actual
    print(name, "PASS", flush=True)
(m.ROOT / "verification.json").write_text(json.dumps(result, indent=2, default=str))
print("REPORT", m.ROOT / "verification.json", flush=True)
