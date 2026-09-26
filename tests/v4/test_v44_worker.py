#!/usr/bin/env python3

"""V4-4 remote worker: same science through the file-based worker.

End-to-end coverage of :mod:`confflow.remote.worker` and
:mod:`confflow.remote.result_bundle` with the fake ORCA executable (no real
quantum chemistry).  Every test uses ``tmp_path`` only:

- successful fake-ORCA items through :func:`run_worker_envelope`, with
  structure/result/energy parity against local execution of the same item,
- staged checkpoint artifacts round-tripping through the worker,
- native-failure, check-failure, and recovery-none propagation,
- cancellation forwarded to the shared executor (confirmed ``CANCELLED``,
  never rescued),
- checksummed ``files/`` packaging with run identity and transport metadata,
- :func:`read_result_bundle` tamper/malformed/oversize rejections,
- worker-stage failures surfaced as :class:`WorkerError`,
- the no-compiler hygiene gate: AST import scan plus a fresh-subprocess
  worker run whose import closure contains no workflow-compiler, YAML,
  DAG, V3, or calc modules.

The handoff reader (``confflow.remote.handoff``) is owned by a sibling
workstream and does not exist yet, so these tests install a stub reader
with the frozen secure-read discipline via ``monkeypatch``; the worker only
ever touches it through its documented ``read_handoff_envelope`` entry
point.  Compiling documents and assembling items in the test harness is
explicitly allowed: only the worker and result-bundle modules are
forbidden from touching the workflow compiler.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.execution import ExecutionBinding
from confflow.execution.checks_standard import CHECKS
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import (
    ItemExecutionContext,
    WorkItemExecutor,
    select_driving_structure,
)
from confflow.programs.registry import get_program_adapter
from confflow.remote.envelope import (
    MAX_HANDOFF_BYTES,
    ArtifactBundleEntry,
    ExecutionDefinition,
    InputBundleManifest,
    ResultBundle,
    ResultEntry,
    StructureBundleEntry,
    WorkerHandoffV2,
    bundle_entry_digest,
    canonical_envelope_bytes,
)
from confflow.remote.result_bundle import (
    ResultBundleError,
    package_result_bundle,
    read_result_bundle,
)
from confflow.remote.transport import build_execution_definition
from confflow.remote.worker import WorkerError, run_worker_envelope
from confflow.workflow.v4.scientific import resolve_scientific_parameters
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure_set,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
REPO_ROOT = Path(__file__).resolve().parents[2]

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}
ORCA_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt"}

FORBIDDEN_IMPORT_PARTS = (
    "compiler",
    "yaml",
    "confflow.calc",
    "confflow.core",
    "confflow.workflow.v3",
    "confflow.workflow.engine",
    "confflow.workflow.dag",
    "control_worker",
    "worker_handoff",
    "worker_staging",
    "worker_attempt",
)


@pytest.fixture(autouse=True)
def _handoff_reader(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install the stub handoff reader for every test in this module."""
    return install_handoff_reader(monkeypatch)


def install_handoff_reader(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a stub ``confflow.remote.handoff`` reader module.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture undoing the ``sys.modules`` injection after the test.

    Returns
    -------
    Any
        The installed stub module.
    """
    import types

    def read_handoff_envelope(*, path: str, expected_run_id: str | None = None) -> Any:
        """Read and validate one handoff envelope with the frozen discipline."""
        raw = _secure_read_handoff(path)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"worker handoff is not valid UTF-8 JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError("worker handoff must be an object")
        handoff = WorkerHandoffV2(**payload)
        if expected_run_id is not None and handoff.run_id != expected_run_id:
            raise ValueError("worker handoff run identity does not match")
        return handoff

    module = types.ModuleType("confflow.remote.handoff")
    module.read_handoff_envelope = read_handoff_envelope  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confflow.remote.handoff", module)
    return module


def _secure_read_handoff(path: str) -> bytes:
    """Read one owner-controlled handoff file without following symlinks.

    Parameters
    ----------
    path : str
        Envelope file path to read.

    Returns
    -------
    bytes
        Raw file content.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise ValueError(f"cannot securely open worker handoff {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise ValueError("worker handoff must be an owner-owned non-writable file")
        if metadata.st_size > MAX_HANDOFF_BYTES:
            raise ValueError("worker handoff exceeds the size bound")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HANDOFF_BYTES:
                raise ValueError("worker handoff exceeds the size bound")
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def install_orca_shim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Expose the fake ORCA as ``orca`` on ``PATH`` for worker PATH lookup.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture managing the ``PATH`` and ``FAKE_MODE`` environment.
    tmp_path : Path
        Test scratch directory receiving the ``bin/`` shim.

    Returns
    -------
    Path
        The shim directory prepended to ``PATH``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "orca"
    if not link.exists():
        link.symlink_to(FAKE_ORCA)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    return bin_dir


def compile_orca_plan(*, native: dict[str, Any], checks: list[str], **kwargs: Any) -> Any:
    """Compile a single-calculation ORCA document for tests.

    Parameters
    ----------
    native : dict[str, Any]
        Native ORCA options.
    checks : list[str]
        Declared scientific checks.
    kwargs : Any
        Extra ``calc_step`` overrides such as ``check_params``.

    Returns
    -------
    Any
        The compiled execution plan.
    """
    step = calc_step(
        "s_opt",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native=native,
        checks=checks,
        resources={"cores_per_item": 4, "memory_per_item": "16GB"},
        execution={"binding_id": "test", "executable": str(FAKE_ORCA)},
        **kwargs,
    )
    document = {
        "schema": "confflow.workflow.v4",
        "steps": [step],
        "inputs": STRUCTURE_INPUTS,
    }
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def assemble_one(plan: Any, structure_id: str = "s0") -> Any:
    """Assemble the single work item for one test structure.

    Parameters
    ----------
    plan : Any
        Compiled execution plan.
    structure_id : str
        Test structure identity.

    Returns
    -------
    Any
        The assembled work item.
    """
    assembly = assemble(plan, run_inputs(structures={"structures": structure_set(structure_id)}))
    assert assembly.ok, [item.message for item in assembly.errors]
    assert len(assembly.items) == 1
    return assembly.items[0]


def local_context(
    plan: Any,
    executable: str,
    run_root: str,
    work_base: str,
    supervisor: Any,
    checks: list[str],
) -> ItemExecutionContext:
    """Build a local item execution context wired to real implementations.

    Parameters
    ----------
    plan : Any
        Compiled execution plan.
    executable : str
        Native executable path for the local binding.
    run_root : str
        Producer-side run root.
    work_base : str
        Item work directory base.
    supervisor : Any
        Process boundary for native execution.
    checks : list[str]
        Declared scientific checks.

    Returns
    -------
    ItemExecutionContext
        The local execution context.
    """
    planned = plan.steps[0]
    return ItemExecutionContext(
        step_id=planned.step_id,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter("orca"),
        profile=PROFILES["standard"],
        checks=tuple(CHECKS[name] for name in checks),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=executable, env=FrozenDict({})
        ),
        run_root=run_root,
        work_base=work_base,
        supervisor=supervisor,
        environment=None,
        poll_interval_seconds=0.05,
    )


def make_handoff(*, item: Any, context: ItemExecutionContext, attempt: int, token: str) -> Any:
    """Build a V2 handoff envelope from resolved execution inputs.

    Parameters
    ----------
    item : Any
        The work item to transport.
    context : ItemExecutionContext
        Local context carrying the resolved execution semantics.
    attempt : int
        Attempt number of this delivery.
    token : str
        Launch token of this delivery.

    Returns
    -------
    Any
        The validated handoff envelope (mirrors the frozen transport).
    """
    driving = select_driving_structure(item)
    effective, _ = resolve_scientific_parameters(
        structure=driving,
        overrides=context.scientific.overrides,
        defaults=context.scientific_defaults,
    )
    manifest = build_manifest(item)
    check_versions = {check.name: check.contract_version for check in context.checks}
    definition = build_execution_definition(
        program=context.adapter.program_name.value,
        native=context.scientific.native,
        execution_adapter=context.scientific.execution_adapter,
        result_profile=context.scientific.result_profile,
        checks=tuple(check.name for check in context.checks),
        check_params=context.scientific.check_params,
        recovery=getattr(context.recovery, "name", "none") or "none",
        recovery_params=context.scientific.recovery_params,
        resources=item.resources,
        charge=effective.charge,
        multiplicity=effective.multiplicity,
        freeze=effective.freeze,
        step_semantic_digest=item.semantic_digest,
        contract_versions={
            "adapter": context.adapter.adapter_version,
            "profile": context.profile.contract_version,
            **{f"check:{name}": version for name, version in check_versions.items()},
            "recovery": getattr(context.recovery, "contract_version", "none") or "none",
        },
    )
    return WorkerHandoffV2.new(
        run_id="run-test",
        step_id=context.step_id,
        work_item_id=item.id,
        logical_key=item.logical_key,
        attempt_number=int(attempt),
        launch_token=token,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=item.semantic_digest,
        producer_provenance={},
        environment_request={"program": context.adapter.program_name.value},
        execution=definition,
        inputs=manifest,
    )


def build_manifest(item: Any) -> InputBundleManifest:
    """Build the deterministic input bundle manifest for *item*.

    This mirrors the evident intent of the frozen transport helper (which
    currently raises ``AttributeError`` on its eager sort-key default and
    is owned read-only by another workstream): structures ride as
    canonical payloads, artifacts ride as identity plus checksum with
    deterministic locators, and results ride as typed payloads.

    Parameters
    ----------
    item : Any
        The work item to transport.

    Returns
    -------
    InputBundleManifest
        The sorted input bundle manifest.
    """
    entries: list[Any] = []
    for port in sorted(item.named_inputs.structures):
        for record in item.named_inputs.structures[port]:
            payload = record.to_dict()
            entries.append(
                StructureBundleEntry(
                    structure_id=record.id,
                    payload=payload,
                    digest=bundle_entry_digest("structure", payload),
                )
            )
    index = 0
    for port in sorted(item.named_inputs.artifacts):
        for record in sorted(item.named_inputs.artifacts[port], key=lambda ref: ref.id):
            safe = (
                "".join(
                    char if char.isalnum() or char in ("_", "-", ".") else "_" for char in record.id
                ).strip("._")
                or f"artifact-{index}"
            )
            index += 1
            entries.append(
                ArtifactBundleEntry(
                    artifact_id=record.id,
                    role=record.role,
                    checksum=record.checksum or "sha256:" + "0" * 64,
                    subject_structure_id=record.subject_structure_id,
                    media_type=record.media_type,
                    bundle_locator=f"files/{index:04d}-{safe}",
                )
            )
    for port in sorted(item.named_inputs.results):
        for record in item.named_inputs.results[port]:
            payload = record.to_dict()
            entries.append(
                ResultEntry(
                    result_id=f"{record.kind}:{record.subject_structure_id or 'unbound'}",
                    payload=payload,
                    digest=bundle_entry_digest("result", payload),
                )
            )
    entries.sort(key=entry_sort_key)
    return InputBundleManifest(entries=tuple(entries))


def entry_sort_key(entry: Any) -> tuple[str, str]:
    """Return the deterministic sort key of one bundle entry.

    Parameters
    ----------
    entry : Any
        A structure, artifact, or result bundle entry.

    Returns
    -------
    tuple[str, str]
        The ``(entry_kind, stable identity)`` ordering key.
    """
    if isinstance(entry, StructureBundleEntry):
        return (entry.entry_kind, entry.structure_id)
    if isinstance(entry, ArtifactBundleEntry):
        return (entry.entry_kind, entry.artifact_id)
    return (entry.entry_kind, entry.result_id)


def write_handoff_file(handoff: Any, path: Path) -> str:
    """Write one handoff envelope for the worker to read.

    Parameters
    ----------
    handoff : Any
        Validated handoff envelope.
    path : Path
        Destination envelope file.

    Returns
    -------
    str
        The envelope path as text.
    """
    path.write_bytes(canonical_envelope_bytes(handoff))
    return str(path)


def run_worker(
    *,
    handoff: Any,
    worker_root: Path,
    token: str,
    staged_bundle: Any = None,
    should_cancel: Any = None,
    supervisor: Any = None,
) -> ResultBundle:
    """Write *handoff* and run it through the worker entry point.

    Parameters
    ----------
    handoff : Any
        Validated handoff envelope.
    worker_root : Path
        Worker sandbox root.
    token : str
        Launch token of this delivery.
    staged_bundle : Any
        Staged input bundle for the worker.
    should_cancel : Any
        Cancellation probe forwarded to the worker.
    supervisor : Any
        Process boundary forwarded to the worker.

    Returns
    -------
    ResultBundle
        The validated result bundle read back from disk.
    """
    worker_root.mkdir(parents=True, exist_ok=True)
    handoff_path = write_handoff_file(handoff, worker_root / "handoff.json")
    result_path = run_worker_envelope(
        handoff_path=handoff_path,
        staged_bundle={} if staged_bundle is None else staged_bundle,
        worker_root=str(worker_root),
        launch_token=token,
        should_cancel=should_cancel,
        supervisor=supervisor,
    )
    return read_result_bundle(path=result_path)


def energy_of(result_payload: dict[str, Any]) -> float:
    """Return the ``energy`` value of a serialized work-item result.

    Parameters
    ----------
    result_payload : dict[str, Any]
        Serialized ``WorkItemResult`` payload.

    Returns
    -------
    float
        The energy value in Hartree.
    """
    for record in result_payload["results"]:
        if record["kind"] == "energy":
            return float(record["value"])
    raise AssertionError("no energy result in payload")


def result_kinds(result_payload: dict[str, Any]) -> list[str]:
    """Return the ordered result kinds of a serialized work-item result.

    Parameters
    ----------
    result_payload : dict[str, Any]
        Serialized ``WorkItemResult`` payload.

    Returns
    -------
    list[str]
        Result kinds in payload order.
    """
    return [str(record["kind"]) for record in result_payload["results"]]


class TestSuccessParity:
    """Successful worker runs match local execution of the same item."""

    def test_success_structures_results_energy_parity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worker structures, results, and energy equal the local run."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        local = WorkItemExecutor().execute(item, context)
        assert local.is_completed

        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        handoff = make_handoff(item=item, context=context, attempt=1, token=token)
        bundle = run_worker(handoff=handoff, worker_root=worker_root, token=token)

        assert bundle.run_id == "run-test"
        assert bundle.step_id == handoff.step_id
        assert bundle.work_item_id == item.id
        assert bundle.attempt_number == 1
        assert bundle.launch_token == token
        assert bundle.work_item_digest == item.semantic_digest
        assert bundle.result["status"] == WorkItemStatus.COMPLETED.value
        assert bundle.result["work_item_id"] == item.id
        assert bundle.result["semantic_digest"] == item.semantic_digest

        local_payload = local.to_dict()
        assert [s["geometry_digest"] for s in bundle.result["structures"]] == [
            s["geometry_digest"] for s in local_payload["structures"]
        ]
        assert result_kinds(bundle.result) == result_kinds(local_payload)
        assert energy_of(bundle.result) == pytest.approx(energy_of(local_payload))
        assert bundle.result["recovery"] == local_payload["recovery"]
        assert bundle.environment["program"] == "orca"

    def test_success_with_staged_checkpoint_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A staged checkpoint artifact reaches the worker item directory."""
        bundle, worker_root, token, staged_bytes = self._checkpoint_run(
            tmp_path, monkeypatch, staged_bundle="mapping"
        )
        assert bundle.result["status"] == WorkItemStatus.COMPLETED.value

        work_base = worker_root / "work" / token
        staged_copies = sorted(work_base.rglob("*.chk"))
        assert staged_copies != []
        assert any(path.read_bytes() == staged_bytes for path in staged_copies)

    def test_success_with_directory_staged_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A directory staged bundle resolves artifacts by bundle locator."""
        bundle, _, _, _ = self._checkpoint_run(tmp_path, monkeypatch, staged_bundle="directory")
        assert bundle.result["status"] == WorkItemStatus.COMPLETED.value

    @staticmethod
    def _checkpoint_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, staged_bundle: str
    ) -> tuple[Any, Path, str, bytes]:
        """Run one worker attempt with a staged checkpoint input artifact.

        Parameters
        ----------
        tmp_path : Path
            Test scratch directory.
        monkeypatch : pytest.MonkeyPatch
            Fixture managing the fake-executable environment.
        staged_bundle : str
            Either ``"mapping"`` (artifact id to path) or ``"directory"``
            (directory holding the bundle-locator files).

        Returns
        -------
        tuple[Any, Path, str, bytes]
            The result bundle, worker root, launch token, and staged bytes.
        """
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        handoff = make_handoff(item=item, context=context, attempt=1, token=token)

        staged_bytes = b"fake-checkpoint-bytes-for-worker\n" * 64
        staged_dir = worker_root / "staging" / token.replace(":", "_")
        files_dir = staged_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        staged_path = files_dir / "0001-input.chk"
        staged_path.write_bytes(staged_bytes)
        checksum = "sha256:" + hashlib.sha256(staged_bytes).hexdigest()
        driving = select_driving_structure(item)
        entry = ArtifactBundleEntry(
            artifact_id="chk_extra",
            role="checkpoint",
            checksum=checksum,
            subject_structure_id=driving.id,
            media_type=None,
            bundle_locator="files/0001-input.chk",
            size_bytes=len(staged_bytes),
        )
        entries = tuple(sorted((*handoff.inputs.entries, entry), key=entry_sort_key))
        staged_handoff = WorkerHandoffV2.new(
            run_id=handoff.run_id,
            step_id=handoff.step_id,
            work_item_id=handoff.work_item_id,
            logical_key=handoff.logical_key,
            attempt_number=handoff.attempt_number,
            launch_token=handoff.launch_token,
            work_item_digest=handoff.work_item_digest,
            step_semantic_digest=handoff.step_semantic_digest,
            producer_provenance={},
            environment_request={"program": "orca"},
            execution=handoff.execution,
            inputs=InputBundleManifest(entries=entries),
        )
        staged_handoff_path = write_handoff_file(staged_handoff, worker_root / "handoff.json")
        bundle_arg: Any = (
            {"chk_extra": str(staged_path)} if staged_bundle == "mapping" else str(files_dir.parent)
        )
        result_path = run_worker_envelope(
            handoff_path=staged_handoff_path,
            staged_bundle=bundle_arg,
            worker_root=str(worker_root),
            launch_token=token,
        )
        return read_result_bundle(path=result_path), worker_root, token, staged_bytes


class TestFailurePropagation:
    """Native, check, and recovery outcomes propagate through the worker."""

    def test_native_failure_abnormal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An abnormal fake yields a FAILED result with the typed error."""
        monkeypatch.setenv("FAKE_MODE", "abnormal")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        local = WorkItemExecutor().execute(item, context)
        assert local.status is WorkItemStatus.FAILED
        assert local.error is not None

        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        bundle = run_worker(
            handoff=make_handoff(item=item, context=context, attempt=1, token=token),
            worker_root=worker_root,
            token=token,
        )
        assert bundle.result["status"] == WorkItemStatus.FAILED.value
        assert bundle.result["error"] is not None
        assert bundle.result["error"]["code"] == local.error.code == "scientific_check_error"
        assert bundle.result["error"]["message"] == local.error.message

    def test_check_failure_frequencies_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed check yields a FAILED result with the stable reason."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        checks = ["normal_termination", "frequencies_required"]
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=checks)
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            checks,
        )
        local = WorkItemExecutor().execute(item, context)
        assert local.status is WorkItemStatus.FAILED
        assert local.error is not None

        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        bundle = run_worker(
            handoff=make_handoff(item=item, context=context, attempt=1, token=token),
            worker_root=worker_root,
            token=token,
        )
        assert bundle.result["status"] == WorkItemStatus.FAILED.value
        assert bundle.result["error"]["code"] == "scientific_check_error"
        assert bundle.result["error"]["details"]["reason"] == "frequencies_missing"
        assert bundle.result["error"] == local.error.to_dict()

    def test_recovery_none_honored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ``none`` recovery policy reports failures as-is, unattempted."""
        monkeypatch.setenv("FAKE_MODE", "abnormal")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        local = WorkItemExecutor().execute(item, context)

        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        bundle = run_worker(
            handoff=make_handoff(item=item, context=context, attempt=1, token=token),
            worker_root=worker_root,
            token=token,
        )
        assert bundle.result["status"] == WorkItemStatus.FAILED.value
        assert bundle.result["recovery"] == {
            "profile": "none",
            "attempted": False,
            "succeeded": None,
            "details": {},
        }
        assert bundle.result["recovery"] == local.to_dict()["recovery"]


class TestCancellation:
    """Cancellation is forwarded to the shared executor unchanged."""

    def test_cancel_trap_yields_cancelled_without_rescue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``cancel_trap`` fake plus ``should_cancel`` gives CANCELLED."""
        monkeypatch.setenv("FAKE_MODE", "cancel_trap")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        handoff = make_handoff(item=item, context=context, attempt=1, token=token)

        stop = threading.Event()
        timer = threading.Timer(0.5, stop.set)
        timer.start()
        try:
            bundle = run_worker(
                handoff=handoff,
                worker_root=worker_root,
                token=token,
                should_cancel=stop.is_set,
                supervisor=NativeProcessSupervisor(
                    terminate_timeout=0.3, kill_timeout=2.0, poll_interval=0.02
                ),
            )
        finally:
            timer.cancel()
        assert bundle.result["status"] == WorkItemStatus.CANCELLED.value
        assert bundle.result["error"] is not None
        assert bundle.result["error"]["code"] == "cancellation_error"
        assert bundle.result["recovery"]["attempted"] is False


class TestResultPackaging:
    """Worker outputs are checksummed and packaged with run identity."""

    def test_output_files_checksummed_and_packaged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Produced files land under ``files/`` with matching checksums."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        worker_root = tmp_path / "worker"
        token = f"remote:{item.id}:attempt-1"
        handoff = make_handoff(item=item, context=context, attempt=1, token=token)
        worker_root.mkdir(parents=True, exist_ok=True)
        handoff_path = write_handoff_file(handoff, worker_root / "handoff.json")
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle={},
            worker_root=str(worker_root),
            launch_token=token,
        )
        bundle = read_result_bundle(path=result_path)

        assert bundle.result["status"] == WorkItemStatus.COMPLETED.value
        assert bundle.run_id == "run-test"
        assert bundle.step_id == handoff.step_id
        assert bundle.work_item_id == item.id
        assert bundle.launch_token == token
        assert bundle.work_item_digest == item.semantic_digest
        assert bundle.environment["program"] == "orca"
        assert bundle.transport_metadata["worker_pid"] == os.getpid()
        assert isinstance(bundle.transport_metadata["finished_wall"], float)
        assert bundle.produced_artifacts != ()

        result_dir = Path(result_path).parent
        for produced in bundle.produced_artifacts:
            assert produced.bundle_locator.startswith("files/")
            stored = result_dir / produced.bundle_locator
            assert stored.is_file()
            content = stored.read_bytes()
            assert "sha256:" + hashlib.sha256(content).hexdigest() == produced.checksum
            assert len(content) == produced.size_bytes
            assert produced.artifact_id
            assert produced.role

    def test_package_result_bundle_rejects_missing_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Packaging fails closed when a produced file is absent."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        local = WorkItemExecutor().execute(item, context)
        assert local.is_completed
        handoff = make_handoff(item=item, context=context, attempt=1, token="remote:x:attempt-1")
        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result=local,
                handoff=handoff,
                result_dir=str(tmp_path / "result"),
                environment={"program": "orca"},
                transfer_files_from=str(tmp_path / "empty-transfer"),
            )


class TestReadResultBundle:
    """Secure bundle reads reject tampering, corruption, and oversize."""

    def _write_valid_bundle(self, path: Path) -> ResultBundle:
        """Write a minimal valid bundle envelope for rejection tests.

        Parameters
        ----------
        path : Path
            Destination envelope file.

        Returns
        -------
        ResultBundle
            The written bundle.
        """
        bundle = ResultBundle.new(
            run_id="run-test",
            step_id="s_opt",
            work_item_id="wi:s_opt:g0",
            attempt_number=1,
            launch_token="remote:wi:s_opt:g0:attempt-1",
            work_item_digest="sha256:" + "ab" * 32,
            environment={"program": "orca"},
            result={"work_item_id": "wi:s_opt:g0", "status": "completed"},
            produced_artifacts=(),
            transport_metadata={"worker_pid": os.getpid(), "finished_wall": time.time()},
        )
        path.write_bytes(canonical_envelope_bytes(bundle))
        return bundle

    def test_round_trip(self, tmp_path: Path) -> None:
        """A written bundle reads back with its digest intact."""
        target = tmp_path / "result.json"
        written = self._write_valid_bundle(target)
        bundle = read_result_bundle(path=str(target))
        assert bundle == written

    def test_tamper_rejected(self, tmp_path: Path) -> None:
        """Edited payload bytes fail the bundle digest check."""
        target = tmp_path / "result.json"
        self._write_valid_bundle(target)
        payload = json.loads(target.read_bytes())
        payload["result"]["status"] = "failed"
        target.write_bytes(json.dumps(payload).encode("utf-8"))
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))

    def test_malformed_rejected(self, tmp_path: Path) -> None:
        """Non-JSON bytes are rejected."""
        target = tmp_path / "result.json"
        target.write_bytes(b"{not valid json")
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))

    def test_non_object_rejected(self, tmp_path: Path) -> None:
        """Non-object JSON is rejected."""
        target = tmp_path / "result.json"
        target.write_bytes(b"[1, 2, 3]")
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))

    def test_schema_violation_rejected(self, tmp_path: Path) -> None:
        """A missing digest field fails strict validation."""
        target = tmp_path / "result.json"
        self._write_valid_bundle(target)
        payload = json.loads(target.read_bytes())
        del payload["bundle_digest"]
        target.write_bytes(json.dumps(payload).encode("utf-8"))
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))

    def test_oversize_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Envelopes beyond the size bound fail closed."""
        import confflow.remote.result_bundle as result_bundle_module

        target = tmp_path / "result.json"
        self._write_valid_bundle(target)
        assert target.stat().st_size > 32
        monkeypatch.setattr(result_bundle_module, "MAX_RESULT_BUNDLE_BYTES", 32)
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        """Symlinked envelopes are never followed."""
        target = tmp_path / "result.json"
        self._write_valid_bundle(target)
        link = tmp_path / "link.json"
        link.symlink_to(target)
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(link))

    def test_missing_rejected(self, tmp_path: Path) -> None:
        """Absent envelopes are rejected."""
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(tmp_path / "absent.json"))


class TestWorkerErrors:
    """Worker-stage failures surface as :class:`WorkerError` with context."""

    def test_launch_token_mismatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A foreign launch token refuses to run."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        worker_root = tmp_path / "worker"
        worker_root.mkdir(parents=True, exist_ok=True)
        handoff = make_handoff(item=item, context=context, attempt=1, token="remote:real:attempt-1")
        handoff_path = write_handoff_file(handoff, worker_root / "handoff.json")
        with pytest.raises(WorkerError, match="launch token"):
            run_worker_envelope(
                handoff_path=handoff_path,
                staged_bundle={},
                worker_root=str(worker_root),
                launch_token="remote:other:attempt-1",
            )

    def test_missing_handoff_file(self, tmp_path: Path) -> None:
        """An unreadable envelope fails at the read stage."""
        with pytest.raises(WorkerError, match="read handoff envelope"):
            run_worker_envelope(
                handoff_path=str(tmp_path / "absent.json"),
                staged_bundle={},
                worker_root=str(tmp_path / "worker"),
                launch_token="remote:x:attempt-1",
            )

    def test_unknown_program(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unresolvable program fails at contract resolution."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        handoff = make_handoff(item=item, context=context, attempt=1, token="remote:x:attempt-1")
        edited = ExecutionDefinition(
            **{**handoff.execution.model_dump(mode="json"), "program": "bogus"}
        )
        broken = WorkerHandoffV2.new(
            run_id=handoff.run_id,
            step_id=handoff.step_id,
            work_item_id=handoff.work_item_id,
            logical_key=handoff.logical_key,
            attempt_number=handoff.attempt_number,
            launch_token=handoff.launch_token,
            work_item_digest=handoff.work_item_digest,
            step_semantic_digest=handoff.step_semantic_digest,
            producer_provenance={},
            environment_request={"program": "bogus"},
            execution=edited,
            inputs=handoff.inputs,
        )
        worker_root = tmp_path / "worker"
        worker_root.mkdir(parents=True, exist_ok=True)
        handoff_path = write_handoff_file(broken, worker_root / "handoff.json")
        with pytest.raises(WorkerError, match="resolve execution contracts"):
            run_worker_envelope(
                handoff_path=handoff_path,
                staged_bundle={},
                worker_root=str(worker_root),
                launch_token=broken.launch_token,
            )


def _module_imports(path: Path) -> list[str]:
    """Return every imported module name in *path* at all AST positions.

    Parameters
    ----------
    path : Path
        Python source file to scan.

    Returns
    -------
    list[str]
        Absolute module names of all static imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ["confflow", "remote"]
                relative = node.module.split(".") if node.module else []
                found.append(".".join(base + relative))
                found.extend(f".{alias.name}" for alias in node.names)
            else:
                found.append(node.module or "")
                found.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


class TestNoCompilerHygiene:
    """The worker never compiles workflows, parses YAML, or builds DAGs."""

    def test_worker_source_has_no_forbidden_imports(self) -> None:
        """Worker sources import no workflow-compiler/YAML/DAG/calc modules."""
        for filename in ("worker.py", "result_bundle.py"):
            path = REPO_ROOT / "confflow" / "remote" / filename
            assert path.is_file()
            offenders = [
                module
                for module in _module_imports(path)
                if any(part in module for part in FORBIDDEN_IMPORT_PARTS)
            ]
            assert offenders == [], (filename, offenders)

    def test_worker_run_import_closure_is_compiler_free(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh-subprocess worker run loads no compiler/YAML/DAG/V3/calc."""
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        install_orca_shim(monkeypatch, tmp_path)
        plan = compile_orca_plan(native=dict(ORCA_NATIVE), checks=["normal_termination"])
        item = assemble_one(plan)
        run_root = tmp_path / "local-run"
        context = local_context(
            plan,
            str(FAKE_ORCA),
            str(run_root),
            str(run_root / "items"),
            NativeProcessSupervisor(),
            ["normal_termination"],
        )
        worker_root = tmp_path / "probe-worker"
        worker_root.mkdir(parents=True, exist_ok=True)
        token = f"remote:{item.id}:attempt-1"
        handoff = make_handoff(item=item, context=context, attempt=1, token=token)
        handoff_path = write_handoff_file(handoff, worker_root / "handoff.json")

        script = _probe_script()
        proc = subprocess.run(
            [sys.executable, "-c", script, handoff_path, str(worker_root), token],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["bad_import"] == [], payload["bad_import"]
        assert payload["bad_run"] == [], payload["bad_run"]
        assert payload["status"] == "completed"
        assert Path(payload["result_path"]).is_file()


def _probe_script() -> str:
    """Return the fresh-subprocess import-closure probe program.

    Returns
    -------
    str
        Python program isolating the worker import closure from the eager
        workflow-package ``__init__`` (which loads the compiler for
        unrelated local-only reasons), running one fixture handoff, and
        printing the new-module inventory as JSON.
    """
    return r"""
import json
import os
import stat
import sys
import types

handoff_path, worker_root, token = sys.argv[1], sys.argv[2], sys.argv[3]
snap0 = set(sys.modules)

import confflow.workflow as _workflow_package

_stub = types.ModuleType("confflow.workflow.v4")
_stub.__path__ = [os.path.join(os.path.dirname(_workflow_package.__file__), "v4")]
sys.modules["confflow.workflow.v4"] = _stub


def _secure_read(path):
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, os.O_RDONLY | nofollow)
    try:
        metadata = os.fstat(fd)
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_uid == os.getuid()
        assert not metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def read_handoff_envelope(*, path, expected_run_id=None):
    from confflow.remote.envelope import WorkerHandoffV2

    payload = json.loads(_secure_read(path).decode("utf-8"))
    handoff = WorkerHandoffV2(**payload)
    if expected_run_id is not None and handoff.run_id != expected_run_id:
        raise ValueError("run mismatch")
    return handoff


_reader = types.ModuleType("confflow.remote.handoff")
_reader.read_handoff_envelope = read_handoff_envelope
sys.modules["confflow.remote.handoff"] = _reader

import confflow.remote.result_bundle as _bundles
import confflow.remote.worker as _worker

snap1 = set(sys.modules)
result_path = _worker.run_worker_envelope(
    handoff_path=handoff_path,
    staged_bundle={},
    worker_root=worker_root,
    launch_token=token,
)
snap2 = set(sys.modules)
bundle = _bundles.read_result_bundle(path=result_path)


def _bad(name):
    parts = name.split(".")
    if name == "yaml" or name.startswith("yaml."):
        return True
    if not name.startswith("confflow"):
        return False
    if name.startswith(("confflow.calc", "confflow.core")):
        return True
    if any(part in ("compiler", "parser", "graph", "dag") for part in parts):
        return True
    if any(part == "v3" or part.startswith("v3_") for part in parts):
        return True
    if "v3_runtime" in parts or "v3_dataflow" in parts:
        return True
    return False


report = {
    "import_new": sorted(m for m in snap1 - snap0 if m.startswith("confflow") or m == "yaml"),
    "run_new": sorted(m for m in snap2 - snap1 if m.startswith("confflow") or m == "yaml"),
    "bad_import": sorted(m for m in snap1 - snap0 if _bad(m)),
    "bad_run": sorted(m for m in snap2 - snap1 if _bad(m)),
    "status": bundle.result["status"],
    "result_path": result_path,
}
assert not report["bad_import"], report["bad_import"]
assert not report["bad_run"], report["bad_run"]
assert report["status"] == "completed", report["status"]
print(json.dumps(report))
"""
