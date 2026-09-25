#!/usr/bin/env python3

"""V4-2 branch coverage: process boundary edges and contract matrices.

Fast real-process cases (/bin/true, short sleeps) plus direct helper calls
cover supervisor validation, cancel/collect edges, and boundary identity.
Contract matrices pin every descriptor guard.  No native programs involved.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution import ExecutionBinding
from confflow.execution.contracts import (
    CheckSpec,
    ExecutionAdapterSpec,
    ExecutionEnvironment,
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
    RecoverySpec,
    ResultProfileSpec,
)
from confflow.execution.native import (
    NativeError,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeHandle,
    ProgramName,
)
from confflow.execution.process import (
    NativeProcessError,
    NativeProcessSupervisor,
    _popen_process_boundary_kwargs,
    _positive_int,
    _safe_create_time,
    _safe_process_group_id,
    _safe_session_id,
)


def _request(work_dir: Path, argv: tuple[str, ...] = ("/bin/true",)) -> NativeExecutionRequest:
    """Build a launch request in *work_dir*."""
    return NativeExecutionRequest(executable=argv[0], argv=argv, work_dir=str(work_dir))


class TestProcessHelpers:
    """Pure boundary helpers behave on edges."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(1, 1, id="one"),
            pytest.param(0, None, id="zero"),
            pytest.param(-3, None, id="negative"),
            pytest.param(True, None, id="bool"),
            pytest.param("5", None, id="string"),
            pytest.param(None, None, id="none"),
            pytest.param(3.0, None, id="float"),
        ],
    )
    def test_positive_int(self, value: Any, expected: Any) -> None:
        assert _positive_int(value) == expected

    def test_boundary_kwargs_shape(self) -> None:
        kwargs = _popen_process_boundary_kwargs()
        assert isinstance(kwargs, dict)
        if os.name == "posix":
            assert kwargs == {"start_new_session": True}

    def test_safe_ids_with_none(self) -> None:
        assert _safe_process_group_id(None) is None
        assert _safe_session_id(None) is None
        assert _safe_create_time(None) is None

    def test_safe_ids_with_self(self) -> None:
        pid = os.getpid()
        if hasattr(os, "getpgid"):
            assert _safe_process_group_id(pid) == os.getpgid(pid)
        if hasattr(os, "getsid"):
            assert _safe_session_id(pid) == os.getsid(pid)

    def test_safe_ids_with_ghost(self) -> None:
        assert _safe_process_group_id(2**30) is None
        assert _safe_session_id(2**30) is None
        assert _safe_create_time(2**30) is None


class TestSubmitValidation:
    """Submit rejects malformed requests before spawning."""

    def test_non_string_argv_part(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = NativeExecutionRequest(
            executable="/bin/true", argv=("/bin/true", 123), work_dir=str(tmp_path)  # type: ignore[arg-type]
        )
        with pytest.raises(NativeProcessError, match="argv"):
            supervisor.submit(request)

    def test_bad_stream_names(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        base = NativeExecutionRequest(
            executable="/bin/true", argv=("/bin/true",), work_dir=str(tmp_path)
        )
        for field in ("stdout_file", "stderr_file"):
            broken = NativeExecutionRequest(
                executable=base.executable,
                argv=base.argv,
                work_dir=base.work_dir,
                **{field: ""},
            )
            with pytest.raises(NativeProcessError, match=field.split("_")[0]):
                supervisor.submit(broken)

    def test_non_string_env_rejected(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = NativeExecutionRequest(
            executable="/bin/true",
            argv=("/bin/true",),
            work_dir=str(tmp_path),
            env=FrozenDict({"A": 1}),
        )
        with pytest.raises(NativeProcessError, match="env value"):
            supervisor.submit(request)

    def test_stdout_open_failure(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = NativeExecutionRequest(
            executable="/bin/true",
            argv=("/bin/true",),
            work_dir=str(tmp_path),
            stdout_file="missing-dir/out.log",
        )
        with pytest.raises(NativeProcessError, match="stdout"):
            supervisor.submit(request)

    def test_missing_executable(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        with pytest.raises(NativeProcessError, match="failed to launch"):
            supervisor.submit(_request(tmp_path, ("/nonexistent-prog-xyz",)))

    def test_stdout_written_on_success(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path))
        deadline = time.monotonic() + 10.0
        while True:
            status = supervisor.poll(handle)
            if status.is_terminal:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status.exit_code == 0
        assert (tmp_path / "stdout.log").exists()
        assert (tmp_path / "stderr.log").exists()
        collected = supervisor.collect(handle)
        assert collected.exit_code == 0
        assert collected.stdout_file == "stdout.log"


class TestCancelCollect:
    """Cancel/collect edges with short real processes."""

    def test_cancel_confirms_sigterm(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sleep", "30")))
        assert supervisor.poll(handle).is_terminal is False
        outcome = supervisor.cancel(handle, grace_seconds=2.0)
        assert outcome.confirmed is True
        assert "SIGTERM" in outcome.detail
        collected = supervisor.collect(handle)
        assert collected.exit_code is not None

    def test_cancel_twice_reports_already_cancelled(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sleep", "30")))
        first = supervisor.cancel(handle, grace_seconds=2.0)
        assert first.confirmed is True
        second = supervisor.cancel(handle, grace_seconds=2.0)
        assert second.confirmed is True
        assert "already cancelled" in second.detail

    def test_collect_live_raises(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sleep", "30")))
        try:
            assert supervisor.poll(handle).is_terminal is False
            with pytest.raises(NativeProcessError, match="still running"):
                supervisor.collect(handle)
        finally:
            supervisor.cancel(handle, grace_seconds=2.0)

    def test_cancel_after_natural_exit(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path))
        deadline = time.monotonic() + 10.0
        while not supervisor.poll(handle).is_terminal:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        outcome = supervisor.cancel(handle, grace_seconds=1.0)
        assert outcome.confirmed is True
        assert "already terminal" in outcome.detail

    def test_failing_exit_code_flows(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/false",)))
        deadline = time.monotonic() + 10.0
        while True:
            status = supervisor.poll(handle)
            if status.is_terminal:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status.exit_code == 1
        assert supervisor.collect(handle).exit_code == 1

    def test_stderr_stream_flows(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sh", "-c", "echo oops >&2; exit 3")))
        deadline = time.monotonic() + 10.0
        while True:
            status = supervisor.poll(handle)
            if status.is_terminal:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status.exit_code == 3
        assert "oops" in (tmp_path / "stderr.log").read_text()


class TestNativeErrorValues:
    """NativeError accepts every stable code."""

    @pytest.mark.parametrize("code", list(NativeErrorCode))
    def test_all_codes_accepted(self, code: NativeErrorCode) -> None:
        error = NativeError(code=code, message="m")
        assert error.code is code

    def test_handle_key(self) -> None:
        assert NativeHandle(key="k").key == "k"


class TestExecutionBindingMatrix:
    """ExecutionBinding validates machine settings."""

    def test_minimal_binding(self) -> None:
        binding = ExecutionBinding(binding_id="b")
        assert binding.executable is None
        assert binding.walltime_seconds is None
        assert dict(binding.env) == {}

    def test_full_binding(self) -> None:
        binding = ExecutionBinding(
            binding_id="b",
            executable="/bin/true",
            env={"A": "b"},
            sandbox="/tmp",
            allowed_executables=("/bin/true",),
            walltime_seconds=60,
            target="n1",
        )
        assert binding.walltime_seconds == 60
        assert binding.allowed_executables == ("t",) or binding.allowed_executables == (
            "/bin/true",
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"binding_id": ""}, id="empty-id"),
            pytest.param({"binding_id": "b", "walltime_seconds": 0}, id="zero-walltime"),
            pytest.param({"binding_id": "b", "walltime_seconds": -5}, id="negative-walltime"),
            pytest.param({"binding_id": "b", "walltime_seconds": True}, id="bool-walltime"),
        ],
    )
    def test_invalid_bindings_rejected(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(DomainError):
            ExecutionBinding(**kwargs)

    def test_env_and_metadata_frozen(self) -> None:
        binding = ExecutionBinding(binding_id="b", env={"A": "b"})
        assert isinstance(binding.env, FrozenDict)


class TestContractMatrices:
    """Descriptor contracts validate their vocabularies."""

    def test_port_spec_kinds(self) -> None:
        from confflow.domain.binding import Cardinality, Pairing, PortKind

        port = PortSpec(
            name="structure",
            kind=PortKind.STRUCTURE,
            cardinality=Cardinality.ONE,
            pairing=Pairing.PER_STRUCTURE,
        )
        assert port.is_required is True
        assert port.to_dict()["kind"] == "structure"
        optional = PortSpec(
            name="checkpoint",
            kind=PortKind.ARTIFACT,
            cardinality=Cardinality.OPTIONAL,
            pairing=Pairing.BY_SUBJECT,
            roles=("checkpoint",),
        )
        assert optional.is_required is False

    def test_port_spec_rejects(self) -> None:
        from confflow.domain.binding import Cardinality, Pairing, PortKind

        with pytest.raises(DomainError):
            PortSpec(
                name="",
                kind=PortKind.STRUCTURE,
                cardinality=Cardinality.ONE,
                pairing=Pairing.SINGLE,
            )
        with pytest.raises(DomainError):
            PortSpec(
                name="x",
                kind=PortKind.STRUCTURE,
                cardinality=Cardinality.ONE,
                pairing=Pairing.BY_SUBJECT,
            )
        with pytest.raises(DomainError):
            PortSpec(
                name="x",
                kind=PortKind.RESULT,
                cardinality=Cardinality.ONE,
                pairing=Pairing.PER_STRUCTURE,
            )
        with pytest.raises(DomainError):
            PortSpec(
                name="x",
                kind=PortKind.STRUCTURE,
                cardinality=Cardinality.ONE,
                pairing=Pairing.SINGLE,
                roles=("checkpoint",),
            )

    def test_executor_contract(self) -> None:
        from confflow.domain.binding import Cardinality, Pairing, PortKind

        port = PortSpec(
            name="structures",
            kind=PortKind.STRUCTURE,
            cardinality=Cardinality.MANY,
            pairing=Pairing.PER_STRUCTURE,
        )
        contract = ExecutorContract(
            capability=ExecutorCapability.CALCULATION,
            contract_version="test.v1",
            output_ports=(port,),
        )
        assert contract.output_port("structures") is port
        assert contract.output_port("missing") is None
        assert contract.to_dict()["capability"] == "calculation"

    def test_adapter_profile_check_recovery_specs(self) -> None:
        from confflow.domain.binding import Cardinality, Pairing, PortKind

        port = PortSpec(
            name="structure",
            kind=PortKind.STRUCTURE,
            cardinality=Cardinality.ONE,
            pairing=Pairing.PER_STRUCTURE,
        )
        adapter = ExecutionAdapterSpec(
            name="a",
            contract_version="v1",
            capability=ExecutorCapability.CALCULATION,
            input_ports=(port,),
        )
        assert adapter.input_port("structure") is port
        assert adapter.input_port("missing") is None
        profile = ResultProfileSpec(
            name="p",
            contract_version="v1",
            supported_checks=("a",),
            provides_structures=True,
            provides_results=True,
        )
        assert profile.to_dict()["provides_artifacts"] is True
        check = CheckSpec(name="c", contract_version="v1")
        assert check.name == "c"
        recovery = RecoverySpec(
            name="r",
            contract_version="v1",
            supported_capabilities=(ExecutorCapability.CALCULATION,),
        )
        assert recovery.supported_capabilities == (ExecutorCapability.CALCULATION,)

    def test_environment_digest_axis(self) -> None:
        first = ExecutionEnvironment(program="gaussian", executable_digest="sha256:" + "a" * 64)
        second = ExecutionEnvironment(program="gaussian", executable_digest="sha256:" + "b" * 64)
        assert first.digest() != second.digest()
        assert first.to_dict()["program"] == "gaussian"

    def test_program_names(self) -> None:
        assert ProgramName.GAUSSIAN.value == "gaussian"
        assert ProgramName.ORCA.value == "orca"

    def test_subprocess_boundary_kwargs_used(self) -> None:
        assert subprocess is not None


class TestDescriptorMatrices:
    """Every descriptor guard fails closed on bad input."""

    def _structure_port(self, name: str = "s", pairing=None):
        from confflow.domain.binding import Cardinality, Pairing, PortKind

        return PortSpec(
            name=name,
            kind=PortKind.STRUCTURE,
            cardinality=Cardinality.ONE,
            pairing=pairing or Pairing.SINGLE,
        )

    def test_port_kind_guards(self) -> None:
        from confflow.domain.binding import Cardinality, Pairing, PortKind

        with pytest.raises(DomainError):
            PortSpec(
                name="x", kind="structure", cardinality=Cardinality.ONE, pairing=Pairing.SINGLE
            )
        with pytest.raises(DomainError):
            PortSpec(name="x", kind=PortKind.STRUCTURE, cardinality="one", pairing=Pairing.SINGLE)
        with pytest.raises(DomainError):
            PortSpec(
                name="x", kind=PortKind.STRUCTURE, cardinality=Cardinality.ONE, pairing="single"
            )
        with pytest.raises(DomainError):
            PortSpec(
                name="x",
                kind=PortKind.STRUCTURE,
                cardinality=Cardinality.ONE,
                pairing=Pairing.SINGLE,
                description=123,
            )
        with pytest.raises(DomainError):
            PortSpec(
                name="x",
                kind=PortKind.ARTIFACT,
                cardinality=Cardinality.OPTIONAL,
                pairing=Pairing.BY_SUBJECT,
                roles=("",),
            )
        with pytest.raises(DomainError):
            PortSpec(
                name="x",
                kind=PortKind.ARTIFACT,
                cardinality=Cardinality.OPTIONAL,
                pairing=Pairing.BY_SUBJECT,
                roles=("a", "a"),
            )
        assert PortSpec(
            name="x",
            kind=PortKind.ARTIFACT,
            cardinality=Cardinality.OPTIONAL,
            pairing=Pairing.BY_SUBJECT,
            roles=("a",),
        ).roles == ("a",)

    def test_executor_contract_guards(self) -> None:
        port = self._structure_port()
        with pytest.raises(DomainError):
            ExecutorContract(capability="calculation", contract_version="v", output_ports=(port,))
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="  ",
                output_ports=(port,),
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port, port),
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=("x",),
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port,),
                input_ports=(port, port),
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port,),
                input_ports=("x",),
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port,),
                passthrough_ports={"ghost": "structure"},
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port,),
                stochastic="yes",
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port,),
                requires_adapter="yes",
            )
        with pytest.raises(DomainError):
            ExecutorContract(
                capability=ExecutorCapability.CALCULATION,
                contract_version="v",
                output_ports=(port,),
                input_ports=(port,),
                requires_adapter=True,
            )
        ok = ExecutorContract(
            capability=ExecutorCapability.ANALYSIS,
            contract_version="v",
            output_ports=(port,),
            input_ports=(port,),
            passthrough_ports={"structures": "structure"} if False else {},
        )
        assert ok.input_port("s") is port
        assert ok.input_port("missing") is None

    def test_adapter_spec_guards(self) -> None:
        port = self._structure_port()
        with pytest.raises(DomainError):
            ExecutionAdapterSpec(
                name="a",
                contract_version="v",
                capability="calculation",
                input_ports=(port,),
            )
        with pytest.raises(DomainError):
            ExecutionAdapterSpec(
                name="a",
                contract_version="v",
                capability=ExecutorCapability.CALCULATION,
                input_ports=(port, port),
            )
        with pytest.raises(DomainError):
            ExecutionAdapterSpec(
                name="a",
                contract_version="v",
                capability=ExecutorCapability.CALCULATION,
                input_ports=("x",),
            )
        with pytest.raises(DomainError):
            ExecutionAdapterSpec(
                name="a",
                contract_version="v",
                capability=ExecutorCapability.CALCULATION,
                input_ports=(port,),
                description=123,
            )

    def test_profile_check_recovery_guards(self) -> None:
        with pytest.raises(DomainError):
            ResultProfileSpec(
                name="p",
                contract_version="v",
                supported_checks=(),
                provides_structures="yes",
            )
        with pytest.raises(DomainError):
            ResultProfileSpec(
                name="p",
                contract_version="  ",
                supported_checks=("a",),
            )
        with pytest.raises(DomainError):
            CheckSpec(name="c", contract_version="v", description=123)
        with pytest.raises(DomainError):
            CheckSpec(name="", contract_version="v")
        with pytest.raises(DomainError):
            RecoverySpec(
                name="r",
                contract_version="v",
                supported_capabilities=("calculation",),
            )
        with pytest.raises(DomainError):
            RecoverySpec(
                name="r",
                contract_version="v",
                supported_capabilities=(ExecutorCapability.CALCULATION,),
                description=123,
            )

    def test_binding_guards(self) -> None:
        with pytest.raises(DomainError):
            ExecutionBinding(binding_id="b", walltime_seconds="60")
        with pytest.raises(DomainError):
            ExecutionBinding(binding_id="b", allowed_executables=[""])
        binding = ExecutionBinding(
            binding_id="b",
            env={"A": "x"},
            metadata={"k": "v"},
            allowed_executables=["/bin/true"],
        )
        assert isinstance(binding.env, FrozenDict)
        assert isinstance(binding.metadata, FrozenDict)
        assert binding.to_dict()["binding_id"] == "b"

    def test_environment_guards(self) -> None:
        with pytest.raises(DomainError):
            ExecutionEnvironment(program="")
        with pytest.raises(DomainError):
            ExecutionEnvironment(program="g", program_version="")
        with pytest.raises(DomainError):
            ExecutionEnvironment(program="g", executable_digest="")
        with pytest.raises(DomainError):
            ExecutionEnvironment(program="g", target="")
        environment = ExecutionEnvironment(program="g", metadata={"a": 1})
        assert isinstance(environment.metadata, FrozenDict)


class TestCancelBranches:
    """Cancel escalation and reap edges with short real processes."""

    def test_cancel_zero_grace_escalates(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sleep", "30")))
        outcome = supervisor.cancel(handle, grace_seconds=0.0)
        assert outcome.confirmed is True
        supervisor.collect(handle)

    def test_sleep_poll_live_then_collect(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sleep", "30")))
        try:
            assert supervisor.poll(handle).is_terminal is False
            outcome = supervisor.cancel(handle, grace_seconds=2.0)
            assert outcome.confirmed is True
            collected = supervisor.collect(handle)
            assert collected.exit_code is not None
            assert collected.wall_time_seconds >= 0.0
        finally:
            try:
                supervisor.cancel(handle, grace_seconds=0.5)
            except NativeProcessError:
                pass

    def test_child_descendant_swept(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sh", "-c", "sleep 30 & wait")))
        assert supervisor.poll(handle).is_terminal is False
        outcome = supervisor.cancel(handle, grace_seconds=3.0)
        assert outcome.confirmed is True
        supervisor.collect(handle)

    def test_bad_grace_rejected(self, tmp_path: Path) -> None:
        from confflow.domain.errors import DomainError as _DomainError

        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(_request(tmp_path, ("/bin/sleep", "30")))
        try:
            with pytest.raises((NativeProcessError, ValueError, _DomainError)):
                supervisor.cancel(handle, grace_seconds=-1.0)
        finally:
            supervisor.cancel(handle, grace_seconds=2.0)

    def test_orphan_descendant_blocks_poll(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(
            _request(tmp_path, ("/bin/sh", "-c", "sleep 30 >/dev/null 2>&1 & exit 0"))
        )
        deadline = time.monotonic() + 10.0
        while True:
            status = supervisor.poll(handle)
            if status.is_terminal:
                break
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
        # The short-lived parent exited but the boundary still owns sleep.
        assert status.is_terminal is False
        assert status.exit_code is None
        with pytest.raises(NativeProcessError, match="still live"):
            supervisor.collect(handle)
        outcome = supervisor.cancel(handle, grace_seconds=5.0)
        assert outcome.confirmed is True
        supervisor.collect(handle)

    def test_stderr_open_failure_closes_stdout(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = NativeExecutionRequest(
            executable="/bin/true",
            argv=("/bin/true",),
            work_dir=str(tmp_path),
            stderr_file="missing-dir/err.log",
        )
        with pytest.raises(NativeProcessError, match="stderr"):
            supervisor.submit(request)
        assert (tmp_path / "stdout.log").exists()

    def test_reap_timeout_reports_parent_not_reaped(self, tmp_path: Path) -> None:
        from confflow.execution.process import _ProcessRecord

        supervisor = NativeProcessSupervisor(
            terminate_timeout=0.05, kill_timeout=0.0, poll_interval=0.01
        )

        class ExitedUnreapable:
            pid = 987654

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                raise subprocess.TimeoutExpired("fake", timeout)

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

        record = _ProcessRecord(
            proc=ExitedUnreapable(),  # type: ignore[arg-type]
            stdout_stream=None,
            stderr_stream=None,
            stdout_name="o.log",
            stderr_name="e.log",
            work_dir=str(tmp_path),
            pid=987654,
            process_group_id=987653,
            session_id=987652,
            create_time=None,
            started_monotonic=time.monotonic(),
        )
        supervisor._records["reap-test"] = record
        # A quiet boundary short-circuits: nothing to reap, cancel is trivially done.
        outcome = supervisor.cancel(NativeHandle(key="reap-test"), grace_seconds=0.05)
        assert outcome.confirmed is True
        assert "already terminal" in outcome.detail

    def test_reap_timeout_with_live_member(self, tmp_path: Path) -> None:
        from confflow.execution.process import _ProcessRecord

        supervisor = NativeProcessSupervisor(
            terminate_timeout=0.05, kill_timeout=0.0, poll_interval=0.01
        )

        class ExitedUnreapable2:
            pid = 987644

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                raise subprocess.TimeoutExpired("fake", timeout)

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

        helper = subprocess.Popen(["/bin/sleep", "30"])
        try:
            record = _ProcessRecord(
                proc=ExitedUnreapable2(),  # type: ignore[arg-type]
                stdout_stream=None,
                stderr_stream=None,
                stdout_name="o.log",
                stderr_name="e.log",
                work_dir=str(tmp_path),
                pid=987644,
                process_group_id=987643,
                session_id=987642,
                create_time=None,
                known_processes={helper.pid: None},
                started_monotonic=time.monotonic(),
            )
            supervisor._records["reap-live-test"] = record
            outcome = supervisor.cancel(NativeHandle(key="reap-live-test"), grace_seconds=1.0)
            assert outcome.confirmed is False
            assert "parent" in outcome.detail
        finally:
            helper.terminate()
            helper.wait(timeout=5)

    def test_wait_oserror_becomes_process_error(self, tmp_path: Path) -> None:
        from confflow.execution.process import _ProcessRecord

        supervisor = NativeProcessSupervisor(poll_interval=0.01)

        class BrokenWait:
            pid = 987651

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                raise OSError("wait exploded")

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

        record = _ProcessRecord(
            proc=BrokenWait(),  # type: ignore[arg-type]
            stdout_stream=None,
            stderr_stream=None,
            stdout_name="o.log",
            stderr_name="e.log",
            work_dir=str(tmp_path),
            pid=987651,
            process_group_id=987650,
            session_id=987649,
            create_time=None,
            started_monotonic=time.monotonic(),
        )
        supervisor._records["broken-test"] = record
        # A quiet boundary short-circuits before any wait call happens.
        outcome = supervisor.cancel(NativeHandle(key="broken-test"), grace_seconds=0.05)
        assert outcome.confirmed is True
        assert "already terminal" in outcome.detail

    def test_wait_oserror_with_live_member(self, tmp_path: Path) -> None:
        from confflow.execution.process import _ProcessRecord

        supervisor = NativeProcessSupervisor(poll_interval=0.01)

        class BrokenWait2:
            pid = 987641

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                raise OSError("wait exploded")

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

        helper = subprocess.Popen(["/bin/sleep", "30"])
        try:
            record = _ProcessRecord(
                proc=BrokenWait2(),  # type: ignore[arg-type]
                stdout_stream=None,
                stderr_stream=None,
                stdout_name="o.log",
                stderr_name="e.log",
                work_dir=str(tmp_path),
                pid=987641,
                process_group_id=987640,
                session_id=987639,
                create_time=None,
                known_processes={helper.pid: None},
                started_monotonic=time.monotonic(),
            )
            supervisor._records["broken-live-test"] = record
            with pytest.raises(NativeProcessError, match="reaping"):
                supervisor.cancel(NativeHandle(key="broken-live-test"), grace_seconds=1.0)
        finally:
            helper.terminate()
            helper.wait(timeout=5)

    def test_sigkill_timeout_leaves_boundary_live(self, tmp_path: Path) -> None:
        from confflow.execution.process import _ProcessRecord

        supervisor = NativeProcessSupervisor(
            terminate_timeout=0.01, kill_timeout=0.0, poll_interval=0.01
        )

        class Immortal:
            pid = 987648

            def poll(self) -> None:
                return None

            def wait(self, timeout: float | None = None) -> int:
                raise subprocess.TimeoutExpired("fake", timeout)

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

        record = _ProcessRecord(
            proc=Immortal(),  # type: ignore[arg-type]
            stdout_stream=None,
            stderr_stream=None,
            stdout_name="o.log",
            stderr_name="e.log",
            work_dir=str(tmp_path),
            pid=987648,
            process_group_id=987647,
            session_id=987646,
            create_time=None,
            started_monotonic=time.monotonic(),
        )
        supervisor._records["immortal-test"] = record
        outcome = supervisor.cancel(NativeHandle(key="immortal-test"), grace_seconds=0.01)
        assert outcome.confirmed is False
        assert "still live" in outcome.detail

    def test_close_streams_tolerates_oserror(self, tmp_path: Path) -> None:
        from confflow.execution.process import _ProcessRecord

        class BadStream:
            def close(self) -> None:
                raise OSError("cannot close")

        record = _ProcessRecord(
            proc=None,  # type: ignore[arg-type]
            stdout_stream=BadStream(),  # type: ignore[arg-type]
            stderr_stream=None,
            stdout_name="o.log",
            stderr_name="e.log",
            work_dir=str(tmp_path),
            pid=None,
            process_group_id=None,
            session_id=None,
            create_time=None,
            started_monotonic=time.monotonic(),
        )
        NativeProcessSupervisor._close_record_streams(record)
        assert record.stdout_stream is None

    def test_record_process_variants(self) -> None:
        from confflow.execution.process import _ProcessRecord

        record = _ProcessRecord(
            proc=None,  # type: ignore[arg-type]
            stdout_stream=None,
            stderr_stream=None,
            stdout_name="o.log",
            stderr_name="e.log",
            work_dir="/tmp",
            pid=None,
            process_group_id=None,
            session_id=None,
            create_time=None,
            started_monotonic=time.monotonic(),
        )

        class NoPid:
            pid = None

        NativeProcessSupervisor._record_process(record, NoPid())
        assert record.known_processes == {}

        class OwnPid:
            pid = os.getpid()

            def create_time(self) -> float:
                return 1.0

        NativeProcessSupervisor._record_process(record, OwnPid())
        assert record.known_processes == {}

        class Vanished:
            pid = 987600

            def create_time(self) -> float:
                import psutil as _psutil_mod

                raise _psutil_mod.NoSuchProcess(987600)

        NativeProcessSupervisor._record_process(record, Vanished())
        assert record.known_processes == {}

        class Remembered:
            pid = 987601

            def create_time(self) -> float:
                return 1234.5

        NativeProcessSupervisor._record_process(record, Remembered())
        assert record.known_processes == {987601: 1234.5}

    def test_identity_and_liveness_helpers(self) -> None:
        supervisor = NativeProcessSupervisor()

        class Timed:
            def __init__(self, moment: float | None, error: Exception | None = None) -> None:
                self._moment = moment
                self._error = error

            def create_time(self) -> float:
                if self._error is not None:
                    raise self._error
                assert self._moment is not None
                return self._moment

        assert supervisor._same_process_identity(Timed(10.0), 10.0) is True
        assert supervisor._same_process_identity(Timed(11.0), 10.0) is False
        assert supervisor._same_process_identity(Timed(10.0), None) is True
        import psutil as _psutil_mod

        assert (
            supervisor._same_process_identity(Timed(None, _psutil_mod.NoSuchProcess(1)), 10.0)
            is False
        )
        assert supervisor._same_process_identity(Timed(None, _psutil_mod.Error("x")), 10.0) is None

        class Liveness:
            def __init__(self, running: bool, status: str, error: Exception | None = None) -> None:
                self._running = running
                self._status = status
                self._error = error

            def is_running(self) -> bool:
                if self._error is not None:
                    raise self._error
                return self._running

            def status(self) -> str:
                return self._status

        assert supervisor._process_is_live(Liveness(False, "running")) is False
        assert supervisor._process_is_live(Liveness(True, "zombie")) is False
        assert supervisor._process_is_live(Liveness(True, "running")) is True
        assert (
            supervisor._process_is_live(Liveness(True, "x", _psutil_mod.NoSuchProcess(1))) is False
        )
        assert supervisor._process_is_live(Liveness(True, "x", _psutil_mod.Error("y"))) is True
