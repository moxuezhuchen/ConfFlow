"""Deferred D5: consumers verify the bytes of bound input artifacts.

A work item's bound ``ArtifactRef`` inputs carry a checksum.  Before the reuse
decision and before any launch, the consumer re-hashes those bytes.  A
tampered upstream output (or a run-supplied input) is refused per item: no
launch, no reuse hit, no registration rewrite.  Checksum-less references and
``EXTERNAL_URI`` locators keep the existing ``verify_artifact`` semantics.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.work_item import WorkItemInputs, work_item_semantic_digest
from confflow.execution import batch as batch_module
from confflow.execution.batch import BatchStepExecutor
from confflow.persistence.contracts import PersistenceError, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4 import test_arch_review_1029_resume_identity as m
from tests.v4._helpers import v43_coverage as h

HINT = "use a fresh run root or explicit migration"
UPSTREAM = b"upstream-output\n"
TAMPERED = b"TAMPERED-upstream\n"
UPSTREAM_REL = "up/out.dat"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ref(
    *,
    checksum: str | None = _sha(UPSTREAM),
    locator: ArtifactLocator | None = None,
    artifact_id: str = "up",
) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        role="bound",
        locator=locator or ArtifactLocator.run_relative(UPSTREAM_REL),
        checksum=checksum,
    )


def _write(case: m._Case, rel: str, data: bytes) -> Path:
    path = Path(case.run_root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _bind(case: m._Case, item: Any, refs: tuple[ArtifactRef, ...]) -> Any:
    """Return *item* bound to *refs* under the digest assembly would compute."""
    inputs = WorkItemInputs(
        structures=FrozenDict(),
        artifacts=FrozenDict({"bound": ArtifactSet.of(*refs)}),
        results=FrozenDict(),
    )
    digest = work_item_semantic_digest(
        step_semantic_digest=case.plan.steps[0].step_semantic_digest,
        input_payload={"bound": {"source": None, "artifacts": [r.digest_payload() for r in refs]}},
        resources=item.resources,
    )
    return dataclasses.replace(item, named_inputs=inputs, semantic_digest=digest)


def _run(case: m._Case, items: list[Any], recorder: m._Recorder, token: str) -> Any:
    request = dataclasses.replace(case.req_a, items=tuple(items))
    return m._run(case, request, recorder, token)


def _snapshot(case: m._Case, item: Any) -> tuple[Any, ...]:
    """Return the durable registration facts of *item* (no rewrite may change them)."""
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        state = store.get_state(item.id)
        if state is None:
            return (None,)
        registered = store.get_registered(item.id)
        return (
            state,
            registered["work_item_digest"],
            registered["current_attempt"],
            store.get_result(item.id) is not None,
        )


def _register_pending(case: m._Case, item: Any) -> None:
    """Register *item* as PENDING under the A identity, without claiming it."""
    batch = m._batch(m._Recorder())
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        store.register_item(
            work_item_id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            work_item_digest=item.semantic_digest,
            step_semantic_digest=case.plan.steps[0].step_semantic_digest,
            environment_digest=case.req_a.environment.digest(),
            producer_provenance=dict(batch._current_provenance(case.req_a).thaw()),
        )


@pytest.fixture
def synthetic(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the keyword arguments of every synthetic (non-executed) item report."""
    original = BatchStepExecutor._durable_synthetic_failure
    seen: list[dict[str, Any]] = []

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BatchStepExecutor, "_durable_synthetic_failure", spy)
    return seen


def _assert_bound_diagnostic(seen: list[dict[str, Any]], *, count: int = 1) -> None:
    """Each refused item names the artifact id, locator, reason, and remedy."""
    matches = [kw for kw in seen if "bound input" in str(kw.get("message", ""))]
    assert len(matches) == count, seen
    for kw in matches:
        assert kw["code"] == "invalidate_artifact"
        assert kw["retryable"] is False
        message = str(kw["message"])
        assert "'up'" in message and UPSTREAM_REL in message, message
        assert "checksum mismatch" in message, message
        assert HINT in message and "nothing was re-executed" in message, message
        assert kw["details"]["artifact_id"] == "up"
        assert kw["details"]["locator"] == f"run_relative:{UPSTREAM_REL}"
        assert "checksum mismatch" in kw["details"]["reason"]


# (i) tampered upstream output, downstream COMPLETED: refused, no reuse, no launch.
def test_tampered_upstream_refuses_completed_reuse(
    tmp_path: Path, synthetic: list[dict[str, Any]]
) -> None:
    case = m._case(tmp_path)
    upstream = _write(case, UPSTREAM_REL, UPSTREAM)
    item = _bind(case, case.item, (_ref(),))
    seeded = m._Recorder()
    _run(case, [item], seeded, "d5-seed")
    assert seeded.calls == [m.ENV_A]

    upstream.write_bytes(TAMPERED)
    before = _snapshot(case, item)
    recorder = m._Recorder()
    with pytest.raises(PersistenceError, match="failed:invalidate_artifact"):
        _run(case, [item], recorder, "d5-reuse")

    assert recorder.calls == []
    assert _snapshot(case, item) == before
    _assert_bound_diagnostic(synthetic)


# (ii) tampered upstream, downstream PENDING or never registered: refused before launch.
@pytest.mark.parametrize("state", ["pending", "new"])
def test_tampered_upstream_refuses_new_execution(
    tmp_path: Path, synthetic: list[dict[str, Any]], state: str
) -> None:
    case = m._case(tmp_path)
    upstream = _write(case, UPSTREAM_REL, UPSTREAM)
    item = _bind(case, case.item, (_ref(),))
    if state == "pending":
        _register_pending(case, item)
    upstream.write_bytes(TAMPERED)
    before = _snapshot(case, item)

    recorder = m._Recorder()
    with pytest.raises(PersistenceError, match="failed:invalidate_artifact"):
        _run(case, [item], recorder, "d5-new")

    assert recorder.calls == []
    assert _snapshot(case, item) == before
    _assert_bound_diagnostic(synthetic)


# (iii) a run-supplied input artifact (run_inputs.artifacts) tampered in the run root.
def test_tampered_run_supplied_input_is_refused(
    tmp_path: Path, synthetic: list[dict[str, Any]]
) -> None:
    case = m._case(tmp_path)
    rel = "inputs/external.dat"
    supplied = _write(case, rel, b"supplied-input\n")
    ref = _ref(
        checksum=_sha(b"supplied-input\n"),
        locator=ArtifactLocator.run_relative(rel),
        artifact_id="up",
    )
    item = _bind(case, case.item, (ref,))
    supplied.write_bytes(b"edited-input\n")

    recorder = m._Recorder()
    with pytest.raises(PersistenceError, match="failed:invalidate_artifact"):
        _run(case, [item], recorder, "d5-supplied")

    assert recorder.calls == []
    assert synthetic and "bound input" in str(synthetic[-1]["message"])
    assert "inputs/external.dat" in str(synthetic[-1]["message"])


# (iv) controls: untampered bound inputs reuse or execute exactly as before.
def test_untampered_upstream_still_reuses_completed_result(tmp_path: Path) -> None:
    case = m._case(tmp_path)
    _write(case, UPSTREAM_REL, UPSTREAM)
    item = _bind(case, case.item, (_ref(),))
    seeded = m._Recorder()
    _run(case, [item], seeded, "d5-ctl-seed")
    assert seeded.calls == [m.ENV_A]

    recorder = m._Recorder()
    _run(case, [item], recorder, "d5-ctl-reuse")
    assert recorder.calls == []


def test_untampered_upstream_pending_item_executes(tmp_path: Path) -> None:
    case = m._case(tmp_path)
    _write(case, UPSTREAM_REL, UPSTREAM)
    item = _bind(case, case.item, (_ref(),))
    recorder = m._Recorder()
    _run(case, [item], recorder, "d5-ctl-new")
    assert recorder.calls == [m.ENV_A]


# (v) one artifact bound by several items is hashed once per step.
def _three_items(case: m._Case) -> list[Any]:
    items = h._items(case.plan, 3)
    return [_bind(case, item, (_ref(),)) for item in items]


def test_shared_artifact_is_hashed_once_per_step_when_untampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = m._case(tmp_path)
    _write(case, UPSTREAM_REL, UPSTREAM)
    calls: list[str] = []
    original = batch_module.verify_artifact

    def counting(**kwargs: Any) -> None:
        calls.append(kwargs["ref"].id)
        return original(**kwargs)

    monkeypatch.setattr(batch_module, "verify_artifact", counting)
    recorder = m._Recorder()
    _run(case, _three_items(case), recorder, "d5-dedupe-ok")

    assert calls.count("up") == 1
    assert len(recorder.calls) == 3


def test_shared_tampered_artifact_is_hashed_once_and_refuses_every_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic: list[dict[str, Any]]
) -> None:
    case = m._case(tmp_path)
    upstream = _write(case, UPSTREAM_REL, UPSTREAM)
    items = _three_items(case)
    upstream.write_bytes(TAMPERED)
    calls: list[str] = []
    original = batch_module.verify_artifact

    def counting(**kwargs: Any) -> None:
        calls.append(kwargs["ref"].id)
        return original(**kwargs)

    monkeypatch.setattr(batch_module, "verify_artifact", counting)
    recorder = m._Recorder()
    with pytest.raises(PersistenceError, match="failed:invalidate_artifact"):
        _run(case, items, recorder, "d5-dedupe-bad")

    assert calls.count("up") == 1
    assert recorder.calls == []
    _assert_bound_diagnostic(synthetic, count=3)


# Semantics kept outside the D5 rule.
def test_checksumless_bound_input_is_not_hashed_and_still_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = m._case(tmp_path)
    upstream = _write(case, UPSTREAM_REL, UPSTREAM)
    item = _bind(case, case.item, (_ref(checksum=None),))
    upstream.write_bytes(TAMPERED)
    calls: list[str] = []
    original = batch_module.verify_artifact

    def counting(**kwargs: Any) -> None:
        calls.append(kwargs["ref"].id)
        return original(**kwargs)

    monkeypatch.setattr(batch_module, "verify_artifact", counting)
    recorder = m._Recorder()
    _run(case, [item], recorder, "d5-nosum")

    assert calls == []
    assert recorder.calls == [m.ENV_A]


def test_external_uri_bound_input_keeps_shape_only_semantics(tmp_path: Path) -> None:
    case = m._case(tmp_path)
    locator = ArtifactLocator.external_uri("https://example.invalid/out.dat")
    item = _bind(case, case.item, (_ref(locator=locator, checksum=_sha(b"anything")),))
    recorder = m._Recorder()
    _run(case, [item], recorder, "d5-external")
    assert recorder.calls == [m.ENV_A]
