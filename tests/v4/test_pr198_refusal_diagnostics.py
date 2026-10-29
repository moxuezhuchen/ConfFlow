"""PR #198 A1: publication refusals carry each gap item's diagnostic to callers.

The refusal ``PersistenceError`` keeps its ``reasons=<id>=<status>:<code>``
segment unchanged and appends each gap item's error message after it.  These
tests observe only what a caller can see: the raised exception text, the
run-generation failure record, and the absence of launches or attempts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.contract import RUN_GENERATION_FILE
from confflow.domain import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import PersistenceError
from tests.v4 import test_arch_review_1029_resume_identity as m
from tests.v4 import test_deferred_d5_input_artifact_bytes as d5
from tests.v4._builders import run_inputs, structure_set

HINT = "use a fresh run root or explicit migration"


def _reasons_prefix(step_id: str, item_id: str, status: str, code: str) -> str:
    """Return the unchanged leading part of the refusal message."""
    return (
        f"refusing to publish step {step_id!r}: 1 items lack durable results "
        f"({item_id}); gap code=publication_durability_gap; "
        f"reasons={item_id}={status}:{code}"
    )


@pytest.mark.parametrize(
    ("axis", "code", "overrides"),
    [
        (
            "provenance",
            "invalidate_provenance",
            {"producer_provenance": {"adapter_version": "old.v0"}},
        ),
        ("input", "invalidate_input", {"work_item_digest": "sha256:" + "6" * 64}),
        ("definition", "invalidate_definition", {"step_semantic_digest": "sha256:" + "5" * 64}),
    ],
)
def test_d4_refusal_caller_text_names_fresh_root_remedy(
    tmp_path: Path, axis: str, code: str, overrides: dict[str, Any]
) -> None:
    case = m._case(tmp_path)
    m._seed(tmp_path, case, "pending", **overrides)
    recorder = m._Recorder()

    with pytest.raises(PersistenceError) as excinfo:
        m._run(case, case.req_a, recorder, f"pr198-d4-{axis}")

    text = str(excinfo.value)
    assert text.startswith(_reasons_prefix(case.item.step_id, case.item.id, "failed", code)), text
    assert "nothing was re-executed" in text, text
    assert HINT in text, text
    assert recorder.calls == []


def _damage_upstream(path: Path, damage: str, outside: Path) -> None:
    if damage == "tampered":
        path.write_bytes(d5.TAMPERED)
        return
    path.unlink()
    if damage == "symlink_outside":
        outside.write_bytes(d5.UPSTREAM)
        path.symlink_to(outside)


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("tampered", "checksum mismatch"),
        ("missing", "is missing"),
        ("symlink_outside", "resolves outside"),
    ],
)
def test_d5_refusal_caller_text_names_port_artifact_locator_and_reason(
    tmp_path: Path, damage: str, reason: str
) -> None:
    case = m._case(tmp_path)
    upstream = d5._write(case, d5.UPSTREAM_REL, d5.UPSTREAM)
    item = d5._bind(case, case.item, (d5._ref(),))
    d5._register_pending(case, item)
    _damage_upstream(upstream, damage, tmp_path / "outside.dat")
    recorder = m._Recorder()

    with pytest.raises(PersistenceError) as excinfo:
        d5._run(case, [item], recorder, f"pr198-d5-{damage}")

    text = str(excinfo.value)
    assert "failed:invalidate_artifact" in text, text
    assert "bound input 'bound'" in text, text
    assert "artifact 'up'" in text, text
    assert f"run_relative:{d5.UPSTREAM_REL}" in text, text
    assert reason in text, text
    assert HINT in text, text
    assert recorder.calls == []


def test_v4_application_refusal_reaches_caller_and_run_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    doc = m.h._document()
    doc["inputs"]["checkpoints"] = {"kind": "artifact", "cardinality": "many", "role": "checkpoint"}
    doc["steps"][0]["bindings"]["checkpoint"] = {
        "source": {"run": "checkpoints"},
        "pairing": "by_subject",
        "cardinality": "one",
    }
    run_root = tmp_path / "application"
    artifact_path = run_root / "inputs" / "external.chk"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(d5.TAMPERED)
    ref = ArtifactRef(
        id="external-checkpoint",
        role="checkpoint",
        locator=ArtifactLocator.run_relative("inputs/external.chk"),
        checksum="sha256:" + hashlib.sha256(d5.UPSTREAM).hexdigest(),
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

    with pytest.raises(PersistenceError) as excinfo:
        V4RunApplication().run(request)

    text = str(excinfo.value)
    assert "reasons=" in text and "failed:invalidate_artifact" in text, text
    assert "external-checkpoint" in text, text
    assert "run_relative:inputs/external.chk" in text, text
    assert "checksum mismatch" in text, text
    assert HINT in text, text

    generation = json.loads((run_root / RUN_GENERATION_FILE).read_text(encoding="utf-8"))
    failure = generation["failure"]
    assert failure["type"] == "PersistenceError"
    assert failure["message"] == text[:2000]
    assert "external-checkpoint" in failure["message"], failure
    assert "inputs/external.chk" in failure["message"], failure
    assert HINT in failure["message"], failure


def test_refusal_message_keeps_reasons_segment_before_item_diagnostics(tmp_path: Path) -> None:
    """The appended diagnostics never precede or alter the reasons segment."""
    case = m._case(tmp_path)
    m._seed(tmp_path, case, "pending", producer_provenance={"adapter_version": "old.v0"})

    with pytest.raises(PersistenceError) as excinfo:
        m._run(case, case.req_a, m._Recorder(), "pr198-order")

    text = str(excinfo.value)
    reasons_at = text.index("; reasons=")
    diagnostics_at = text.index("; item diagnostics: ")
    assert reasons_at < diagnostics_at
    assert text.endswith(text[diagnostics_at:]) and case.item.id in text[diagnostics_at:]
    assert HINT in text


def test_refusal_exposes_structured_item_failures_without_changing_type(tmp_path: Path) -> None:
    """Callers can read each gap item's code and message; the error type stays plain."""
    case = m._case(tmp_path)
    m._seed(tmp_path, case, "pending", producer_provenance={"adapter_version": "old.v0"})

    with pytest.raises(PersistenceError) as excinfo:
        m._run(case, case.req_a, m._Recorder(), "pr198-structured")

    error = excinfo.value
    assert type(error) is PersistenceError
    failures = error.item_failures
    assert set(failures) == {case.item.id}
    failure = failures[case.item.id]
    assert failure.code == "invalidate_provenance"
    assert failure.retryable is False
    assert HINT in failure.message
    assert failure.message in str(error)


def _fifty_subject_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> V4RunRequest:
    """Build a V4 request whose checkpoint input is refused for 50 subjects."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    count = 50
    subjects = [f"c{i:03d}" for i in range(count)]
    doc = m.h._document()
    doc["inputs"]["checkpoints"] = {"kind": "artifact", "cardinality": "many", "role": "checkpoint"}
    doc["steps"][0]["bindings"]["checkpoint"] = {
        "source": {"run": "checkpoints"},
        "pairing": "by_subject",
        "cardinality": "one",
    }
    run_root = tmp_path / "fifty"
    refs = []
    for subject in subjects:
        rel = f"inputs/ext-{subject}.chk"
        path = run_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(d5.TAMPERED)
        refs.append(
            ArtifactRef(
                id=f"ext-{subject}",
                role="checkpoint",
                locator=ArtifactLocator.run_relative(rel),
                checksum="sha256:" + hashlib.sha256(d5.UPSTREAM).hexdigest(),
                subject_structure_id=subject,
            )
        )
    return V4RunRequest(
        workflow_document=doc,
        run_inputs=run_inputs(
            structures={"structures": structure_set(*subjects)},
            artifacts={"checkpoints": ArtifactSet.of(*refs)},
        ),
        run_root=str(run_root),
        supervisor=NativeProcessSupervisor(),
    )


def test_fifty_refused_items_keep_failure_record_bounded_and_useful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _fifty_subject_application(tmp_path, monkeypatch)

    with pytest.raises(PersistenceError) as excinfo:
        V4RunApplication().run(request)

    error = excinfo.value
    assert len(error.item_failures) == 50
    text = str(error)
    assert text.count(" | ") + 1 >= 3
    assert "(+47 more" in text, text

    generation = json.loads((Path(request.run_root) / RUN_GENERATION_FILE).read_text("utf-8"))
    message = generation["failure"]["message"]
    assert len(message) <= 2000, len(message)
    assert message.startswith("refusing to publish"), message[:200]
    assert HINT in message, message
    assert "ext-c" in message, message
    assert "chars omitted" in message, message
