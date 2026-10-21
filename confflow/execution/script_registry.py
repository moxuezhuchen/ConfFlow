#!/usr/bin/env python3
"""Server-registered external scripts for ``script`` workflow steps.

Workflows reference scripts by id only; the argv vector lives in
server.toml ``[scripts.<id>]`` tables (``command = [...]``, optional
``description``). The registry resolves ids, checks the script file
exists, and hashes its bytes so changed scripts become new tasks.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.errors import DomainError
from .quota import QuotaError, read_server_config_toml, server_config_path

__all__ = [
    "SCRIPT_OUTPUT_CHANNELS",
    "SCRIPT_PLACEHOLDERS",
    "SCRIPT_PLACEHOLDER_HELP",
    "ScriptEntry",
    "expand_script_args",
    "script_identity",
    "format_mem_gb",
    "load_script_registry",
    "validate_script_args_template",
]

#: Placeholders allowed in a script step's ``args`` (N2.1, static).
SCRIPT_PLACEHOLDERS: tuple[str, ...] = ("input", "cores", "mem_gb", "item_id")

#: Fixed output channels of a script step (N2.1/W4/G19; never extended).
SCRIPT_OUTPUT_CHANNELS: tuple[str, ...] = ("artifacts", "structures", "summary")

#: Static placeholder semantics for the contract (N2.6; server-independent).
SCRIPT_PLACEHOLDER_HELP: dict[str, str] = {
    "input": "Absolute path of the per-item input.xyz written from the driving structure.",
    "cores": "The item's cores_per_item as an integer string.",
    "mem_gb": "The item's memory_per_item in gibibytes (bytes / 1024^3).",
    "item_id": "The work item id (wi:<logical_key>).",
}

_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]*)\}")


def format_mem_gb(memory_bytes: int) -> str:
    """Render *memory_bytes* as gibibytes for the ``{mem_gb}`` placeholder."""
    return f"{memory_bytes / 1024**3:g}"


def validate_script_args_template(args: Any) -> tuple[str, ...]:
    """Check an ``args`` template; only known placeholders may use braces."""
    if not isinstance(args, (list, tuple)):
        raise DomainError("script args must be a list of strings")
    checked: list[str] = []
    for index, entry in enumerate(args):
        if not isinstance(entry, str) or not entry:
            raise DomainError(f"script args[{index}] must be a non-empty string")
        for match in _PLACEHOLDER_PATTERN.finditer(entry):
            if match.group(1) not in SCRIPT_PLACEHOLDERS:
                raise DomainError(
                    f"script args[{index}] uses unknown placeholder "
                    f"'{match.group(0)}'; allowed: "
                    + ", ".join("{" + name + "}" for name in SCRIPT_PLACEHOLDERS)
                )
        remainder = _PLACEHOLDER_PATTERN.sub("", entry)
        if "{" in remainder or "}" in remainder:
            raise DomainError(
                f"script args[{index}] carries a stray brace; braces are only "
                "allowed as {input}, {cores}, {mem_gb} or {item_id}"
            )
        checked.append(entry)
    return tuple(checked)


def expand_script_args(
    template: tuple[str, ...] | list[str],
    *,
    input_path: str,
    cores: int,
    mem_gb: str,
    item_id: str,
) -> tuple[str, ...]:
    """Expand a validated *template* into concrete argv elements."""
    return tuple(
        entry.replace("{input}", input_path)
        .replace("{cores}", str(cores))
        .replace("{mem_gb}", mem_gb)
        .replace("{item_id}", item_id)
        for entry in template
    )


@dataclass(frozen=True, slots=True)
class ScriptEntry:
    """One registered script: id, argv, file identity, interpreter.

    ``interpreter_path`` and ``interpreter_sha256`` describe the program that
    actually runs the script (the script itself for a single-element command);
    both are ``None`` when the interpreter cannot be resolved, so the executor
    fails the item instead of silently reusing results.
    """

    id: str
    command: tuple[str, ...]
    script_path: str
    sha256: str
    interpreter: str
    description: str = ""
    script_arg_index: int = 1
    interpreter_path: str | None = None
    interpreter_sha256: str | None = None


def script_identity(entry: ScriptEntry) -> dict[str, Any]:
    """Return the reuse identity of *entry* for digests and item keys.

    The registered command is folded in with its script element replaced by the
    script content hash, so every fixed argument (``[..., "1"]`` vs ``[..., "2"]``)
    and the interpreter content are part of the identity. Only the
    server-side registration determines these values; the workflow cannot set them.
    """
    command = list(entry.command)
    command[entry.script_arg_index] = entry.sha256
    return {
        "script_sha256": entry.sha256,
        "command": command,
        # content, not the resolved path: a moved but identical interpreter still reuses
        "interpreter_sha256": entry.interpreter_sha256,
    }


def _resolve_interpreter_identity(
    command: tuple[str, ...], script_file: Path, script_sha256: str
) -> tuple[str | None, str | None]:
    """Return ``(path, sha256)`` of the interpreter, or ``(None, None)`` if unresolved."""
    if len(command) == 1:
        return str(script_file), script_sha256
    name = command[0]
    if os.path.isabs(name):
        path = name if os.path.isfile(name) else None
    else:
        path = shutil.which(name)
    if path is None:
        return None, None
    try:
        return path, hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None, None


def _resolve_script_file(command: tuple[str, ...], config_dir: Path) -> tuple[Path, int]:
    """Return the script file of *command* and its argv index.

    The script is the first existing file among the arguments after
    ``argv[0]`` (``["python3", "/path/tspes.py"]``); a single-element
    command must itself be an existing executable file. The interpreter
    alone never counts as the script, so a missing script file fails
    closed even when the interpreter exists. Relative candidates resolve
    against *config_dir* (the server.toml directory), never the process
    working directory.
    """
    ordered = tuple(command[1:]) if len(command) > 1 else tuple(command[:1])
    base = 1 if len(command) > 1 else 0
    for offset, element in enumerate(ordered):
        candidate = Path(element)
        if not candidate.is_absolute():
            candidate = config_dir / candidate
        if candidate.is_file():
            return candidate, base + offset
    raise QuotaError(f"registered script command {list(command)!r} names no existing file")


def load_script_registry(config: str | Path | None = None) -> dict[str, ScriptEntry]:
    """Load ``[scripts.<id>]`` entries, or ``{}`` when server.toml is absent."""
    path = server_config_path(config)
    if not path.is_file():
        return {}
    data = read_server_config_toml(path)
    raw_scripts = data.get("scripts", {})
    if raw_scripts is None:
        return {}
    if not isinstance(raw_scripts, dict):
        raise QuotaError(f"invalid server config {path}: '[scripts]' must be a table")
    entries: dict[str, ScriptEntry] = {}
    for script_id, raw in raw_scripts.items():
        if not isinstance(raw, dict):
            raise QuotaError(f"invalid server config {path}: scripts.{script_id} must be a table")
        command = raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise QuotaError(
                f"invalid server config {path}: scripts.{script_id}.command "
                "must be a non-empty list of strings"
            )
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise QuotaError(
                f"invalid server config {path}: scripts.{script_id}.description " "must be a string"
            )
        script_file, script_index = _resolve_script_file(tuple(command), path.parent)
        digest = hashlib.sha256(script_file.read_bytes()).hexdigest()
        interpreter_path, interpreter_sha = _resolve_interpreter_identity(
            tuple(command), script_file, digest
        )
        entries[str(script_id)] = ScriptEntry(
            id=str(script_id),
            command=tuple(command),
            script_path=str(script_file),
            sha256=digest,
            interpreter=command[0],
            description=description,
            script_arg_index=script_index,
            interpreter_path=interpreter_path,
            interpreter_sha256=interpreter_sha,
        )
    return entries
