#!/usr/bin/env python3
"""Server-registered external scripts for ``script`` workflow steps.

Workflows reference scripts by id only; the argv vector lives in
server.toml ``[scripts.<id>]`` tables (``command = [...]``, optional
``description``). The registry resolves ids, checks the script file
exists, and hashes its bytes so changed scripts become new tasks.
"""

from __future__ import annotations

import hashlib
import re
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
    """One registered script: id, argv, file identity, interpreter."""

    id: str
    command: tuple[str, ...]
    script_path: str
    sha256: str
    interpreter: str
    description: str = ""


def _resolve_script_file(command: tuple[str, ...], config_dir: Path) -> Path:
    """Return the script file of *command*.

    The script is the first existing file among the arguments after
    ``argv[0]`` (``["python3", "/path/tspes.py"]``); a single-element
    command must itself be an existing executable file. The interpreter
    alone never counts as the script, so a missing script file fails
    closed even when the interpreter exists.
    """
    ordered = tuple(command[1:]) if len(command) > 1 else tuple(command[:1])
    for element in ordered:
        candidate = Path(element)
        if not candidate.is_absolute():
            candidate = config_dir / candidate
        if candidate.is_file():
            return candidate
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
        script_file = _resolve_script_file(tuple(command), path.parent)
        digest = hashlib.sha256(script_file.read_bytes()).hexdigest()
        entries[str(script_id)] = ScriptEntry(
            id=str(script_id),
            command=tuple(command),
            script_path=str(script_file),
            sha256=digest,
            interpreter=command[0],
            description=description,
        )
    return entries
