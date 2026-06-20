"""Tests for path and executable policy helpers."""

from __future__ import annotations

import os

import pytest

from confflow.core.exceptions import ExecutionPolicyError, PathSafetyError
from confflow.core.path_policy import (
    normalize_managed_path,
    resolve_sandbox_root,
    validate_cleanup_target,
    validate_executable_setting,
    validate_managed_path,
)


def test_normalize_managed_path_resolves_relative_base(tmp_path) -> None:
    assert normalize_managed_path("nested/../file", base_dir=str(tmp_path)) == os.path.realpath(
        tmp_path / "file"
    )


def test_normalize_managed_path_rejects_empty_values() -> None:
    with pytest.raises(PathSafetyError, match="path must be a non-empty string"):
        normalize_managed_path("")
    with pytest.raises(PathSafetyError, match="path must be a non-empty string"):
        normalize_managed_path(None)  # type: ignore[arg-type]


def test_resolve_sandbox_root_normalizes_configured_root(tmp_path) -> None:
    assert resolve_sandbox_root({}) is None
    assert resolve_sandbox_root({"sandbox_root": " "}) is None
    assert resolve_sandbox_root({"sandbox_root": str(tmp_path)}) == os.path.realpath(tmp_path)


def test_validate_managed_path_rejects_sandbox_escape(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    inside = sandbox / "work"
    outside = tmp_path / "outside"

    assert validate_managed_path(str(inside), label="work_dir", sandbox_root=str(sandbox)) == str(
        inside.resolve()
    )
    with pytest.raises(PathSafetyError, match="work_dir escapes sandbox_root"):
        validate_managed_path(str(outside), label="work_dir", sandbox_root=str(sandbox))


def test_validate_cleanup_target_rejects_unsafe_paths(tmp_path) -> None:
    assert validate_cleanup_target(str(tmp_path / "work")) == str((tmp_path / "work").resolve())
    with pytest.raises(PathSafetyError, match="refusing to delete unsafe path"):
        validate_cleanup_target(os.path.sep)


def test_validate_executable_setting_accepts_single_names_and_paths(tmp_path) -> None:
    exe = tmp_path / "orca"
    exe.write_text("", encoding="utf-8")

    assert validate_executable_setting("orca", label="orca_path") == "orca"
    assert validate_executable_setting(
        str(exe), label="orca_path", allowed_executables=[str(exe), "g16"]
    ) == str(exe)
    assert (
        validate_executable_setting("g16", label="gaussian_path", allowed_executables=["g16"])
        == "g16"
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "orca -v",
        "orca; rm -rf work",
        "orca | tee out",
        "orca\nnext",
        "C:\\Program Files\\orca\\orca.exe -v",
        "C:\\Program Files\\orca\\orca",
        "orca 'unterminated",
    ],
)
def test_validate_executable_setting_rejects_command_fragments(value) -> None:
    with pytest.raises(ExecutionPolicyError):
        validate_executable_setting(value, label="orca_path")


def test_validate_executable_setting_rejects_disallowed_executable(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    allowed.write_text("", encoding="utf-8")
    other.write_text("", encoding="utf-8")

    with pytest.raises(ExecutionPolicyError, match="not allowed by allowed_executables"):
        validate_executable_setting(
            str(other), label="orca_path", allowed_executables=[str(allowed)]
        )

    with pytest.raises(ExecutionPolicyError, match="not allowed by allowed_executables"):
        validate_executable_setting("orca", label="orca_path", allowed_executables=["g16"])


def test_validate_executable_setting_accepts_windows_path() -> None:
    assert (
        validate_executable_setting(r"C:\Program Files\orca\orca.exe", label="orca_path")
        == r"C:\Program Files\orca\orca.exe"
    )
