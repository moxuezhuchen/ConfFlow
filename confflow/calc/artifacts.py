#!/usr/bin/env python3

"""Manifest-based calc step artifacts for the non-legacy execution path."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..config.models import CalcStepParams
from ..core.path_policy import validate_cleanup_target

CalcStepStatus = Literal["planned", "running", "completed", "failed", "canceled", "stale"]

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compute_input_digest(input_source: str | os.PathLike[str]) -> str:
    """Digest the input file content, not its absolute path or mtime."""
    path = Path(input_source)
    return "sha256:" + _file_digest(path)


def compute_config_digest(config: CalcStepParams) -> str:
    """Digest canonical typed calc configuration."""
    return _json_digest(config.canonical_dict())


@dataclass(frozen=True)
class CalcManifest:
    schema_version: int
    step_name: str
    step_type: str
    status: CalcStepStatus
    config_digest: str
    input_digest: str
    output: str | None = None
    failed: str | None = None
    total_tasks: int | None = None
    succeeded: int | None = None
    failed_count: int | None = None
    created_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalcManifest":
        return cls(
            schema_version=int(data.get("schema_version", 0)),
            step_name=str(data.get("step_name", "")),
            step_type=str(data.get("step_type", "calc")),
            status=str(data.get("status", "planned")),  # type: ignore[arg-type]
            config_digest=str(data.get("config_digest", "")),
            input_digest=str(data.get("input_digest", "")),
            output=data.get("output"),
            failed=data.get("failed"),
            total_tasks=data.get("total_tasks"),
            succeeded=data.get("succeeded"),
            failed_count=data.get("failed_count"),
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "step_name": self.step_name,
            "step_type": self.step_type,
            "status": self.status,
            "config_digest": self.config_digest,
            "input_digest": self.input_digest,
            "output": self.output,
            "failed": self.failed,
            "total_tasks": self.total_tasks,
            "succeeded": self.succeeded,
            "failed_count": self.failed_count,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    @property
    def reusable_output(self) -> str | None:
        if self.status != "completed" or not self.output:
            return None
        return self.output


@dataclass(frozen=True)
class PreparedCalcArtifacts:
    manifest: CalcManifest | None
    reusable_output: Path | None
    cleaned_stale_artifacts: bool


class CalcArtifactManager:
    """Manage calc step manifest, reuse, and stale cleanup."""

    def __init__(
        self,
        step_dir: str | os.PathLike[str],
        *,
        step_name: str,
        config: CalcStepParams,
        input_path: str | os.PathLike[str],
    ) -> None:
        self.step_dir = Path(step_dir)
        self.step_name = step_name
        self.config = config
        self.input_path = Path(input_path)
        self.manifest_path = self.step_dir / MANIFEST_NAME
        self.config_digest = compute_config_digest(config)
        self.input_digest = compute_input_digest(self.input_path)

    def load(self) -> CalcManifest | None:
        try:
            with self.manifest_path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return CalcManifest.from_dict(data)
        except (TypeError, ValueError):
            return None

    def _write(self, manifest: CalcManifest) -> None:
        self.step_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(manifest.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, self.manifest_path)

    def _matches_current(self, manifest: CalcManifest | None) -> bool:
        return (
            manifest is not None
            and manifest.schema_version == MANIFEST_SCHEMA_VERSION
            and manifest.step_name == self.step_name
            and manifest.step_type == "calc"
            and manifest.config_digest == self.config_digest
            and manifest.input_digest == self.input_digest
        )

    def _clear_step_dir(self) -> None:
        sandbox_root = self.config.execution.sandbox_root
        safe_dir = Path(validate_cleanup_target(str(self.step_dir), sandbox_root=sandbox_root))
        if not safe_dir.exists():
            return
        for entry in safe_dir.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    def prepare(self, *, resume: bool) -> PreparedCalcArtifacts:
        manifest = self.load()
        if self._matches_current(manifest):
            reusable = manifest.reusable_output if manifest else None
            if reusable:
                output_path = self.step_dir / reusable
                if output_path.exists():
                    return PreparedCalcArtifacts(
                        manifest=manifest,
                        reusable_output=output_path,
                        cleaned_stale_artifacts=False,
                    )
            if resume and manifest and manifest.status == "running":
                return PreparedCalcArtifacts(
                    manifest=manifest,
                    reusable_output=None,
                    cleaned_stale_artifacts=False,
                )

        cleaned = False
        if self.step_dir.exists() and any(self.step_dir.iterdir()):
            self._clear_step_dir()
            cleaned = True
        self.step_dir.mkdir(parents=True, exist_ok=True)
        return PreparedCalcArtifacts(
            manifest=self.load(),
            reusable_output=None,
            cleaned_stale_artifacts=cleaned,
        )

    def mark_running(self) -> None:
        self._write(
            CalcManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                step_name=self.step_name,
                step_type="calc",
                status="running",
                config_digest=self.config_digest,
                input_digest=self.input_digest,
                created_at=_utc_now(),
            )
        )

    def mark_completed(
        self,
        *,
        output_path: str | os.PathLike[str],
        failed_path: str | os.PathLike[str] | None,
        total_tasks: int,
        succeeded: int,
        failed_count: int,
    ) -> None:
        self._write(
            CalcManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                step_name=self.step_name,
                step_type="calc",
                status="completed",
                config_digest=self.config_digest,
                input_digest=self.input_digest,
                output=os.path.relpath(output_path, self.step_dir),
                failed=(
                    None
                    if failed_path is None
                    else os.path.relpath(failed_path, self.step_dir)
                ),
                total_tasks=total_tasks,
                succeeded=succeeded,
                failed_count=failed_count,
                created_at=(self.load().created_at if self.load() else None) or _utc_now(),
                completed_at=_utc_now(),
            )
        )

    def mark_failed(self, error: str) -> None:
        self._write(
            CalcManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                step_name=self.step_name,
                step_type="calc",
                status="failed",
                config_digest=self.config_digest,
                input_digest=self.input_digest,
                error=error,
                created_at=(self.load().created_at if self.load() else None) or _utc_now(),
                completed_at=_utc_now(),
            )
        )
