#!/usr/bin/env python3

"""Path and executable safety helpers."""

from __future__ import annotations

import os
import re
import shlex
from typing import Any

from .exceptions import ExecutionPolicyError, PathSafetyError

__all__ = [
    "normalize_managed_path",
    "resolve_sandbox_root",
    "validate_managed_path",
    "validate_cleanup_target",
    "validate_executable_setting",
]

_REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HOME_DIR = os.path.realpath(os.path.expanduser("~"))
_SHELL_CONTROL_CHARS = frozenset(";&|<>`$\n\r")
_WINDOWS_PATH_PREFIX_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_WINDOWS_EXECUTABLE_EXT_RE = re.compile(r"\.(?:exe|cmd|bat|com)", re.IGNORECASE)


def _common_path_or_none(path_a: str, path_b: str) -> str | None:
    try:
        return os.path.commonpath([path_a, path_b])
    except ValueError:
        return None


def normalize_managed_path(path: str, *, base_dir: str | None = None) -> str:
    """Normalize a user/config supplied path without requiring it to exist."""
    if not isinstance(path, str) or not path.strip():
        raise PathSafetyError("path must be a non-empty string")
    candidate = path.strip()
    if base_dir and not os.path.isabs(candidate):
        candidate = os.path.join(base_dir, candidate)
    return os.path.realpath(os.path.abspath(candidate))


def resolve_sandbox_root(config: dict[str, Any] | None = None) -> str | None:
    """Return the normalized sandbox root configured for the current run."""
    if not config:
        return None
    raw = config.get("sandbox_root")
    if raw is None or not str(raw).strip():
        return None
    return normalize_managed_path(str(raw))


def validate_managed_path(
    path: str,
    *,
    label: str,
    sandbox_root: str | None = None,
    base_dir: str | None = None,
) -> str:
    """Normalize a managed path and ensure it stays within the sandbox when configured."""
    normalized = normalize_managed_path(path, base_dir=base_dir)
    if sandbox_root:
        sandbox = normalize_managed_path(sandbox_root)
        common = _common_path_or_none(normalized, sandbox)
        if common != sandbox:
            raise PathSafetyError(f"{label} escapes sandbox_root: {normalized}")
    return normalized


def validate_cleanup_target(path: str, *, sandbox_root: str | None = None) -> str:
    """Validate that a directory is safe to delete recursively."""
    normalized = validate_managed_path(path, label="cleanup target", sandbox_root=sandbox_root)
    blocked = {os.path.sep, _HOME_DIR, _REPO_ROOT}
    if normalized in blocked:
        raise PathSafetyError(f"refusing to delete unsafe path: {normalized}")
    return normalized


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _starts_with_windows_path_prefix(value: str) -> bool:
    return _WINDOWS_PATH_PREFIX_RE.match(value) is not None


def _looks_like_single_windows_executable_path(value: str) -> bool:
    if not _starts_with_windows_path_prefix(value):
        return False

    match = _WINDOWS_EXECUTABLE_EXT_RE.search(value)
    if match is None:
        return False

    return not value[match.end() :].strip()


def _parse_single_executable(value: str, *, label: str) -> str:
    if any(char in _SHELL_CONTROL_CHARS for char in value):
        raise ExecutionPolicyError(f"{label} must name exactly one executable, got: {value}")

    if _starts_with_windows_path_prefix(value):
        if _looks_like_single_windows_executable_path(value):
            return value
        raise ExecutionPolicyError(f"{label} must name exactly one executable, got: {value}")

    if not _has_whitespace(value):
        return value

    try:
        parts = shlex.split(value)
    except ValueError as exc:
        raise ExecutionPolicyError(f"{label} is not a valid executable spec: {value}") from exc

    if len(parts) == 1:
        executable = parts[0]
        if _starts_with_windows_path_prefix(executable):
            if _looks_like_single_windows_executable_path(executable):
                return executable
        elif not _has_whitespace(executable):
            return executable

    raise ExecutionPolicyError(f"{label} must name exactly one executable, got: {value}")


def validate_executable_setting(
    value: Any,
    *,
    label: str,
    allowed_executables: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    """Validate an executable setting and reject free-form command prefixes."""
    if value is None:
        raise ExecutionPolicyError(f"{label} must not be empty")

    raw = str(value).strip()
    if not raw:
        raise ExecutionPolicyError(f"{label} must not be empty")

    executable = _parse_single_executable(raw, label=label)
    if allowed_executables:
        allowed = {str(item).strip() for item in allowed_executables if str(item).strip()}
        allowed_absolute = {normalize_managed_path(item) for item in allowed if os.path.isabs(item)}
        allowed_named = {item for item in allowed if not os.path.isabs(item)}
        normalized_exec = (
            normalize_managed_path(executable) if os.path.isabs(executable) else executable
        )
        if os.path.isabs(executable):
            is_allowed = normalized_exec in allowed_absolute
        else:
            is_allowed = executable in allowed_named
        if not is_allowed:
            raise ExecutionPolicyError(
                f"{label} is not allowed by allowed_executables: {executable}"
            )

    return executable
