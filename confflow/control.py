"""Thin one-shot adapter for the frozen ConfFlow control protocol v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, cast

from .application.execution import (
    ArtifactManifest,
    ErrorCode,
    ExecutableIdentity,
    ExecutionService,
    ExecutionServiceError,
    PrepareRequest,
    RunSnapshot,
)
from .application.execution.workflow_adapter import open_control_service
from .core.contracts import ExitCode

logger = logging.getLogger(__name__)

PROTOCOL = "confflow.control.v1"
_OPERATIONS = frozenset(
    {"capabilities", "prepare", "execute", "status", "events", "cancel", "resume", "artifacts"}
)
_SCHEMA_FILES = frozenset(
    {
        "common.schema.json",
        "worker-handoff.schema.json",
        "requests.schema.json",
        "responses.schema.json",
        "input-manifest.schema.json",
    }
)
_ERROR_CODES = {code.value for code in ErrorCode}


class ControlRequestError(ExecutionServiceError):
    """Typed protocol/CLI error raised before a service call."""


class _Parser(argparse.ArgumentParser):
    """Argument parser that never writes usage text to protocol stdout."""

    def __init__(self) -> None:
        super().__init__(add_help=False, allow_abbrev=False)

    def error(self, message: str) -> NoReturn:
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, message)


def main(args_list: list[str]) -> int:
    """Run one control operation and emit exactly one protocol response."""
    exit_code, response = run_request(args_list)
    _write_response(response)
    return exit_code


def run_request(
    args_list: list[str],
    *,
    post_execute: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
    identity_executable: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run one control operation without writing its protocol response.

    The optional hook is reserved for an alternate executable that must hand
    off a formally queued execute intent before emitting the one final control
    response.  Parsing, schema validation, service dispatch, response
    validation and exit-code mapping stay in this adapter.
    """
    operation = _operation_hint(args_list)
    try:
        args = _parse_args(args_list)
        operation = args.operation
        request = _request_from_args(args)
        _validate_request(request, operation)
        if operation == "capabilities":
            response = _capabilities_response()
        else:
            state_root = _resolve_state_root(args.state_root)
            if operation == "prepare":
                response = _prepare_response(
                    state_root, request, identity_executable=identity_executable
                )
            else:
                # The application service is the only stateful operation owner.
                with redirect_stdout(sys.stderr):
                    service = _open_control_service(state_root, identity_executable)
                    response = _dispatch(service, request)
                    if operation == "execute" and post_execute is not None and response["ok"]:
                        response = post_execute(state_root, args.run_id, response)
        _validate_response(response)
        return ExitCode.SUCCESS, response
    except ExecutionServiceError as error:
        response = _error_response(operation, error)
        return _exit_code(error), response
    except Exception:  # pragma: no cover - final protocol safety net
        logger.exception("ConfFlow control adapter failed")
        typed = ExecutionServiceError(ErrorCode.INTERNAL, "Control adapter failed", retryable=True)
        response = _error_response(operation, typed)
        return _exit_code(typed), response


def _parse_args(args_list: list[str]) -> argparse.Namespace:
    parser = _Parser()
    parser.add_argument("operation", choices=sorted(_OPERATIONS))
    parser.add_argument("--state-root")
    parser.add_argument("--request")
    parser.add_argument("--run-id")
    parser.add_argument("--after")
    parser.add_argument("--checkpoint")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(args_list)
    if not args.json:
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, "control operations require --json")
    _validate_cli_shape(args)
    return args


def _validate_cli_shape(args: argparse.Namespace) -> None:
    allowed: dict[str, set[str]] = {
        "capabilities": {"json"},
        "prepare": {"json", "state_root", "request"},
        "execute": {"json", "state_root", "run_id"},
        "status": {"json", "state_root", "run_id"},
        "events": {"json", "state_root", "run_id", "after"},
        "cancel": {"json", "state_root", "run_id"},
        "resume": {"json", "state_root", "run_id", "checkpoint"},
        "artifacts": {"json", "state_root", "run_id"},
    }
    values = {
        name
        for name in ("state_root", "request", "run_id", "after", "checkpoint")
        if getattr(args, name) is not None
    }
    unexpected = values - allowed[args.operation]
    if unexpected:
        raise ControlRequestError(
            ErrorCode.INVALID_REQUEST,
            f"Unsupported argument(s) for {args.operation}: {', '.join(sorted(unexpected))}",
        )
    if args.operation == "prepare" and not args.request:
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, "--request is required for prepare")
    if args.operation != "prepare" and args.request:
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, "--request is only valid for prepare")
    if (
        args.operation in {"execute", "status", "events", "cancel", "resume", "artifacts"}
        and not args.run_id
    ):
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, "--run-id is required")
    if args.operation == "events" and args.after == "":
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, "--after must not be empty")
    if args.operation != "resume" and args.checkpoint is not None:
        raise ControlRequestError(
            ErrorCode.INVALID_REQUEST, "--checkpoint is only valid for resume"
        )


def _request_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.operation == "prepare":
        try:
            text = Path(args.request).read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ControlRequestError(
                ErrorCode.INVALID_REQUEST, f"Invalid request JSON: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise ControlRequestError(ErrorCode.INVALID_REQUEST, "Request JSON must be an object")
        return cast(dict[str, Any], payload)
    generated: dict[str, Any] = {"protocol_schema": PROTOCOL, "operation": args.operation}
    if args.operation != "capabilities":
        generated["run_id"] = args.run_id
    if args.operation == "events" and args.after is not None:
        generated["after"] = args.after
    if args.operation == "resume" and args.checkpoint is not None:
        generated["checkpoint"] = args.checkpoint
    return generated


def _validate_request(payload: dict[str, Any], operation: str) -> None:
    protocol = payload.get("protocol_schema")
    if protocol != PROTOCOL:
        if isinstance(protocol, str) and protocol.startswith("confflow.control."):
            raise ControlRequestError(
                ErrorCode.UNSUPPORTED_PROTOCOL, f"Unsupported protocol: {protocol}"
            )
        raise ControlRequestError(ErrorCode.INVALID_REQUEST, "protocol_schema is required")
    if payload.get("operation") != operation:
        raise ControlRequestError(
            ErrorCode.INVALID_REQUEST, "Request operation does not match the CLI operation"
        )
    validator = _validator("requests.schema.json")
    try:
        validator.validate(payload)
    except Exception as error:
        raise ControlRequestError(
            ErrorCode.INVALID_REQUEST, f"Request schema validation failed: {error}"
        ) from error
    if operation == "prepare":
        expected = _request_digest(payload)
        if payload["request_digest"] != expected:
            raise ControlRequestError(
                ErrorCode.INVALID_REQUEST, "request_digest does not match RFC 8785 JCS"
            )


def _prepare_response(
    state_root: str,
    request: dict[str, Any],
    *,
    identity_executable: str | None = None,
) -> dict[str, Any]:
    service: ExecutionService
    # Content verification is read-only protocol input validation. Durable state
    # and idempotency remain exclusively owned by ExecutionService.prepare().
    with redirect_stdout(sys.stderr):
        service = _open_control_service(state_root, identity_executable)
        model = _prepare_model(request)
        if identity_executable is not None:
            service.verify_executable_identity(model.expected_executable_identity)
        snapshot = service.prepare(model)
    return _snapshot_response("prepare", snapshot)


def _open_control_service(state_root: str, identity_executable: str | None) -> ExecutionService:
    """Keep the ordinary adapter call unchanged while allowing fixture binding."""
    if identity_executable is None:
        return open_control_service(state_root)
    return open_control_service(state_root, identity_executable=identity_executable)


def _prepare_model(request: dict[str, Any]) -> PrepareRequest:
    identity = request["expected_executable_identity"]
    return PrepareRequest(
        run_id=request["run_id"],
        idempotency_key=request["idempotency_key"],
        request_digest=request["request_digest"],
        workflow_config_digest=request["workflow_config"]["sha256"],
        input_manifest_digest=request["input_manifest"]["sha256"],
        expected_executable_identity=ExecutableIdentity(
            sha256=identity["sha256"],
            realpath=identity.get("realpath"),
            device_inode=identity.get("device_inode"),
        ),
    )


def _dispatch(service: ExecutionService, request: dict[str, Any]) -> dict[str, Any]:
    operation = request["operation"]
    run_id = request.get("run_id")
    if operation == "execute":
        return _snapshot_response(operation, service.execute(run_id))
    if operation == "status":
        return _snapshot_response(operation, service.status(run_id))
    if operation == "events":
        page = service.events(run_id, after=request.get("after"))
        response = _snapshot_response(operation, page.snapshot)
        response.update(
            {
                "next_cursor": page.next_cursor,
                "events": [
                    {"cursor": event.cursor, "revision": event.revision, "type": event.type}
                    for event in page.events
                ],
            }
        )
        return response
    if operation == "cancel":
        return _snapshot_response(operation, service.cancel(run_id))
    if operation == "resume":
        return _snapshot_response(
            operation, service.resume(run_id, checkpoint_id=request.get("checkpoint"))
        )
    if operation == "artifacts":
        return _artifacts_response(service.artifacts(run_id))
    raise ControlRequestError(ErrorCode.INVALID_REQUEST, f"Unsupported operation: {operation}")


def _capabilities_response() -> dict[str, Any]:
    return {
        "protocol_schema": PROTOCOL,
        "operation": "capabilities",
        "ok": True,
        "supported_protocols": [PROTOCOL],
    }


def _snapshot_response(operation: str, snapshot: RunSnapshot) -> dict[str, Any]:
    return {
        "protocol_schema": PROTOCOL,
        "operation": operation,
        "ok": True,
        "run_id": snapshot.run_id,
        "revision": snapshot.revision,
        "state": snapshot.state.value,
    }


def _artifacts_response(manifest: ArtifactManifest) -> dict[str, Any]:
    artifacts = sorted(manifest.artifacts, key=lambda item: (item.terminal, item.path))
    response = _snapshot_response("artifacts", manifest.snapshot)
    response["artifacts"] = [
        {
            "terminal": item.terminal,
            "path": item.path,
            "sha256": item.sha256,
            "size": item.size,
            "content_schema": item.content_schema,
        }
        for item in artifacts
    ]
    return response


def _error_response(operation: str, error: ExecutionServiceError) -> dict[str, Any]:
    code = error.code.value if isinstance(error.code, ErrorCode) else str(error.code)
    if code not in _ERROR_CODES:
        code = ErrorCode.INTERNAL.value
    return {
        "protocol_schema": PROTOCOL,
        "operation": operation if operation in _OPERATIONS else "capabilities",
        "ok": False,
        "error": {"code": code, "message": str(error), "retryable": bool(error.retryable)},
    }


def _resolve_state_root(explicit: str | None) -> str:
    if explicit is not None:
        value = explicit
    elif "CONFFLOW_CONTROL_STATE_ROOT" in os.environ:
        value = os.environ["CONFFLOW_CONTROL_STATE_ROOT"]
    elif "XDG_STATE_HOME" in os.environ:
        value = str(Path(os.environ["XDG_STATE_HOME"]) / "confflow" / "control")
    elif os.environ.get("HOME"):
        value = str(Path(os.environ["HOME"]) / ".local" / "state" / "confflow" / "control")
    else:
        raise ControlRequestError(
            ErrorCode.INVALID_REQUEST, "No state-root or absolute HOME is available"
        )
    if not value.startswith("/"):
        raise ControlRequestError(
            ErrorCode.INVALID_REQUEST, "State root must be an absolute POSIX path"
        )
    return value


def _request_digest(payload: dict[str, Any]) -> str:
    try:
        import rfc8785
    except ImportError as error:  # pragma: no cover - dependency gate catches this
        raise ControlRequestError(
            ErrorCode.INTERNAL, "rfc8785 is required for control requests"
        ) from error
    semantic = dict(payload)
    semantic.pop("request_digest", None)
    return hashlib.sha256(rfc8785.dumps(semantic)).hexdigest()


@lru_cache(maxsize=4)
def _schema_store() -> tuple[dict[str, Any], ...]:
    schema_dir = Path(__file__).resolve().parents[1] / "docs" / "control_protocol" / "v1"
    if not schema_dir.is_dir():
        schema_dir = Path(sys.prefix) / "share" / "confflow" / "control_protocol" / "v1"
    try:
        return tuple(
            json.loads((schema_dir / name).read_text(encoding="utf-8"))
            for name in sorted(_SCHEMA_FILES)
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionServiceError(
            ErrorCode.INTERNAL, "Control protocol schemas are unavailable"
        ) from error


@lru_cache(maxsize=8)
def _validator(schema_name: str):
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError as error:  # pragma: no cover - dependency gate catches this
        raise ExecutionServiceError(
            ErrorCode.INTERNAL, "jsonschema is required for control requests"
        ) from error
    schemas = _schema_store()
    selected = next(
        (schema for schema in schemas if str(schema.get("$id", "")).endswith(schema_name)), None
    )
    if selected is None:
        raise ExecutionServiceError(ErrorCode.INTERNAL, f"Missing control schema: {schema_name}")
    registry = Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema)) for schema in schemas
    )
    return jsonschema.Draft202012Validator(selected, registry=registry)


def _validate_response(response: dict[str, Any]) -> None:
    try:
        _validator("responses.schema.json").validate(response)
    except Exception as error:
        raise ExecutionServiceError(
            ErrorCode.INTERNAL, "Service returned an invalid control response"
        ) from error


def _write_response(response: dict[str, Any]) -> None:
    # One compact canonical line keeps stdout machine-only; diagnostics remain stderr.
    print(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _exit_code(error: ExecutionServiceError) -> int:
    if error.code in {ErrorCode.INVALID_REQUEST, ErrorCode.UNSUPPORTED_PROTOCOL}:
        return ExitCode.USAGE_ERROR
    return ExitCode.RUNTIME_ERROR


def _operation_hint(args_list: list[str]) -> str:
    for value in args_list:
        if value in _OPERATIONS:
            return value
    return "capabilities"


__all__ = ["main", "run_request"]
