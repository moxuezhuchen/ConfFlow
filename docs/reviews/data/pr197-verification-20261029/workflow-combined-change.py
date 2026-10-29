"""Application-level checks for simultaneous input and environment changes."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

from confflow.persistence.contracts import OwnerIdentity, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4 import test_arch_review_launch_identity as h

root = Path(tempfile.mkdtemp(prefix="confflow-pr197-workflow."))
with pytest.MonkeyPatch.context() as mp:
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(root / "server.toml"))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(root / "quota-state"))
    mp.setenv("REVIEW_LAUNCH_X", "1")
    h._register_script(root, root / "script-launches.log")
    h._install_mlip_runner(mp, root)
    model = root / "model.pt"
    model.write_text("-5\n")
    document = h._script_doc()
    mlip = h._mlip_doc(model, sys.executable)["steps"][0]
    mlip["bindings"]["structure"]["source"] = {"step": "s", "port": "structures"}
    document["steps"].append(mlip)
    run_root = root / "run"
    first = h._run_script(run_root, document)
    path = store_path(str(run_root), "uma")
    item_id = "wi:uma:all"
    with SqliteWorkItemStore.open(path) as store:
        before = store.get_registered(item_id)
        store.refresh_for_new_environment(
            item_id,
            work_item_digest=before["work_item_digest"],
            step_semantic_digest=before["step_semantic_digest"],
            environment_digest=before["environment_digest"],
            producer_provenance=before["producer_provenance"],
            reason="seed a crashed attempt",
        )
        assert store.claim(item_id, owner=OwnerIdentity(owner_token="ghost", pid=2**30))
    mp.setenv("REVIEW_LAUNCH_X", "2")
    error = None
    second = None
    try:
        second = h._run_script(run_root, document)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    with SqliteWorkItemStore.open(path) as store:
        after = store.get_registered(item_id)
    result = dict(
        root=str(root),
        first_status=first.status,
        second_status=second.status if second else None,
        error=error,
        before_x=first.step_results[0].structures[0].coordinates[0][2],
        after_x=second.step_results[0].structures[0].coordinates[0][2] if second else None,
        environment_changed=before["environment_digest"] != after["environment_digest"],
        input_identity_replaced=before["work_item_digest"] != after["work_item_digest"],
    )
    (root / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
