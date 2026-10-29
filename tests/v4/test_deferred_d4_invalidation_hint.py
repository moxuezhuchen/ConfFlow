"""Deferred D4: step-level invalidation names the fresh-run-root remedy.

A provenance, definition, input, or artifact invalidation of a work item is
refused without re-execution.  The run-level refusal already tells the operator
to use a fresh run root or an explicit migration; the step-level diagnostic must
carry the same remedy so that an old run root is never silently re-diagnosed
with a bare "invalidated" message.  Environment-only changes advance the
generation instead and never reach this diagnostic.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import _definition_mismatch_error
from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.completion import WorkItemStatus
from confflow.execution.batch import BatchStepExecutor
from confflow.persistence.contracts import OwnerIdentity, PersistenceError, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4 import test_arch_review_1029_resume_identity as m

HINT = "use a fresh run root or explicit migration"


def _capture_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the message of every synthetic (non-executed) item report."""
    original = BatchStepExecutor._durable_synthetic_failure
    messages: list[str] = []

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        messages.append(str(kwargs.get("message", "")))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BatchStepExecutor, "_durable_synthetic_failure", spy)
    return messages


def _invalidation_message(messages: list[str]) -> str:
    """Return the single invalidation message emitted for the probed item."""
    matches = [text for text in messages if "invalidated (" in text]
    assert len(matches) == 1, messages
    return matches[0]


@pytest.mark.parametrize(
    ("axis", "code", "overrides"),
    [
        ("definition", "invalidate_definition", {"step_semantic_digest": "sha256:" + "5" * 64}),
        ("input", "invalidate_input", {"work_item_digest": "sha256:" + "6" * 64}),
        (
            "provenance",
            "invalidate_provenance",
            {"producer_provenance": {"adapter_version": "old.v0"}},
        ),
    ],
)
def test_pending_invalidation_names_fresh_run_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    axis: str,
    code: str,
    overrides: dict[str, Any],
) -> None:
    case = m._case(tmp_path)
    m._seed(tmp_path, case, "pending", **overrides)
    messages = _capture_messages(monkeypatch)

    with pytest.raises(PersistenceError, match=f"failed:{code}"):
        m._run(case, case.req_a, m._Recorder(), "d4-resume")

    message = _invalidation_message(messages)
    assert HINT in message, message
    assert "nothing was re-executed" in message


def test_artifact_invalidation_names_fresh_run_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A COMPLETED item whose stored artifact bytes no longer match is refused with the hint."""
    case = m._case(tmp_path)
    data = b"original\n"
    artifact_file = Path(case.run_root) / "out" / "own.txt"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_bytes(data)
    ref = ArtifactRef(
        id="own",
        role="out",
        locator=ArtifactLocator.run_relative("out/own.txt"),
        checksum="sha256:" + hashlib.sha256(data).hexdigest(),
    )
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        m._register(store, case)
        assert store.claim(case.item.id, owner=OwnerIdentity(owner_token="seed", pid=os.getpid()))

        result = dataclasses.replace(
            m.h._result(case.item, status=WorkItemStatus.COMPLETED),
            artifacts=ArtifactSet.of(ref),
            metadata=FrozenDict({"actual_environment": m.ENV_A}),
        )
        store.record_finished(result, environment_digest=case.req_a.environment.digest())
    artifact_file.write_bytes(b"tampered\n")
    messages = _capture_messages(monkeypatch)

    with pytest.raises(PersistenceError, match="failed:invalidate_artifact"):
        m._run(case, case.req_a, m._Recorder(), "d4-artifact")

    message = _invalidation_message(messages)
    assert HINT in message, message


def test_hint_wording_matches_run_level_refusal() -> None:
    """The step-level remedy reuses the phrase the run-level refusal already uses."""
    assert HINT in str(_definition_mismatch_error("sha256:" + "1" * 64, "sha256:" + "2" * 64))


def test_environment_only_change_never_reaches_invalidation_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment-only changes advance the generation; no invalidation text is produced."""
    case = m._case(tmp_path)
    m._seed(tmp_path, case, "completed")
    messages = _capture_messages(monkeypatch)
    recorder = m._Recorder()
    m._run(case, case.req_b, recorder, "d4-env")
    assert recorder.calls == [m.ENV_B]
    assert not any("invalidated (" in text for text in messages)
