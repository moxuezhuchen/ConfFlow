"""Independent PR #198 acceptance probes; no production edits or internal spies."""

import json
import os
import tempfile
from pathlib import Path

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.domain import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import PersistenceError, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4 import test_arch_review_1029_resume_identity as m
from tests.v4 import test_deferred_d5_input_artifact_bytes as d5
from tests.v4._builders import run_inputs, structure_set

ROOT = Path(tempfile.mkdtemp(prefix="pr198-acceptance-cases."))
os.environ["CONFFLOW_SERVER_CONFIG"] = str(ROOT / "server.toml")
os.environ["CONFFLOW_SERVER_STATE_DIR"] = str(ROOT / "quota-state")
results = {}

for axis, overrides in {
    "provenance": {"producer_provenance": {"adapter_version": "old.v0"}},
    "input": {"work_item_digest": "sha256:" + "6" * 64},
    "definition": {"step_semantic_digest": "sha256:" + "5" * 64},
}.items():
    directory = ROOT / ("d4-" + axis)
    case = m._case(directory)
    m._seed(directory, case, "pending", **overrides)
    recorder = m._Recorder()
    try:
        m._run(case, case.req_a, recorder, "accept-d4")
        raise AssertionError("expected refusal")
    except PersistenceError as exc:
        results["d4-" + axis] = {
            "error": str(exc),
            "fresh_root_hint_visible": d5.HINT in str(exc),
            "launches": recorder.calls,
        }

for damage in ("tampered", "missing", "outside_symlink"):
    case = m._case(ROOT / ("d5-" + damage))
    path = d5._write(case, d5.UPSTREAM_REL, d5.UPSTREAM)
    item = d5._bind(case, case.item, (d5._ref(),))
    d5._register_pending(case, item)
    if damage == "tampered":
        path.write_bytes(d5.TAMPERED)
    else:
        path.unlink()
        if damage == "outside_symlink":
            outside = ROOT / "outside.dat"
            outside.write_bytes(d5.UPSTREAM)
            path.symlink_to(outside)
    before = d5._snapshot(case, item)
    recorder = m._Recorder()
    try:
        d5._run(case, [item], recorder, "accept-d5")
        raise AssertionError("expected refusal")
    except PersistenceError as exc:
        results["d5-" + damage] = {
            "error": str(exc),
            "artifact_path_visible": d5.UPSTREAM_REL in str(exc),
            "port_visible": "bound" in str(exc),
            "fresh_root_hint_visible": d5.HINT in str(exc),
            "registration_unchanged": d5._snapshot(case, item) == before,
            "launches": recorder.calls,
        }

# Actual V4 application: supplied checkpoint enters through RunInputs,
# compilation and assembly. No replacement of the batch or execution methods.
doc = m.h._document()
doc["inputs"]["checkpoints"] = {"kind": "artifact", "cardinality": "many", "role": "checkpoint"}
doc["steps"][0]["bindings"]["checkpoint"] = {
    "source": {"run": "checkpoints"},
    "pairing": "by_subject",
    "cardinality": "one",
}
run_root = ROOT / "application"
artifact_path = run_root / "inputs" / "external.chk"
artifact_path.parent.mkdir(parents=True)
artifact_path.write_bytes(d5.TAMPERED)
ref = ArtifactRef(
    id="external-checkpoint",
    role="checkpoint",
    locator=ArtifactLocator.run_relative("inputs/external.chk"),
    checksum=d5._sha(d5.UPSTREAM),
    subject_structure_id="c000",
)
request = V4RunRequest(
    workflow_document=doc,
    run_inputs=run_inputs(
        structures={"structures": structure_set("c000")},
        artifacts={"checkpoints": ArtifactSet.of(ref)},
    ),
    run_root=str(run_root),
    supervisor=NativeProcessSupervisor(),
)
try:
    V4RunApplication().run(request)
    raise AssertionError("expected refusal")
except PersistenceError as exc:
    manifest = json.loads((run_root / "run_result.json").read_text())
    with SqliteWorkItemStore.open(store_path(str(run_root), "s_opt")) as store:
        state = store.get_state("wi:s_opt:c000")
    results["application"] = {
        "error": str(exc),
        "manifest": manifest,
        "registered": state is not None,
        "file_path_in_manifest": "inputs/external.chk" in json.dumps(manifest),
        "hint_in_manifest": d5.HINT in json.dumps(manifest),
        "attempt_dirs": [str(p.relative_to(run_root)) for p in run_root.rglob("attempt*")],
    }

output = Path("/tmp/confflow-pr198-acceptance/probe-results.json")
output.write_text(json.dumps({"root": str(ROOT), "results": results}, indent=2))
print(output.read_text())
