"""Secure publication of the external worker's legacy sidecar artifacts.

The process orchestrator keeps a compatibility wrapper in control_worker.
This module owns only the path/digest checks and publication ordering, with the
secure staging primitive injected by that wrapper.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .application.execution.state_root import StateRoot
from .core.contracts import output_txt_path_for_input
from .worker_handoff import _file_digest, _validate_attempt_root, _validate_path

StageFile = Callable[..., Path]
FileDigest = Callable[[str], str]


def _sidecar_sources(staged_input: str) -> tuple[Path, Path]:
    """Return the fixed report and minimum-XYZ artifacts for one input."""
    input_path = Path(staged_input)
    return (
        Path(output_txt_path_for_input(staged_input)),
        input_path.with_name(f"{input_path.stem}min.xyz"),
    )


def _publish_worker_sidecars(
    root: StateRoot,
    *,
    staged_input: str,
    work_dir: str,
    stage_file: StageFile,
    file_digest: FileDigest = _file_digest,
) -> None:
    """Publish CLI-compatible report/minimum files beside the workflow dir."""
    attempt_root = _validate_attempt_root(root)
    destination_root = Path(work_dir).parent
    if destination_root != attempt_root:
        _validate_path(
            destination_root,
            attempt_root,
            "workflow result root",
            kind="directory",
        )
    for source in _sidecar_sources(staged_input):
        if not source.is_file():
            raise FileNotFoundError(f"worker completed without required sidecar: {source.name}")
        destination = destination_root / source.name
        _validate_path(
            destination,
            attempt_root,
            "worker sidecar",
            kind="file",
            allow_missing=True,
        )
        if source == destination:
            continue
        stage_file(source, destination, expected_digest=file_digest(str(source)))


__all__ = ["_publish_worker_sidecars", "_sidecar_sources"]
