#!/usr/bin/env python3

"""Deterministic human-facing YAML serialization for workflow documents.

The upgrade CLI emits V3 documents through here. It is deliberately *not* a
format-preserving rewriter: comments and original formatting are not retained.
What it guarantees is a stable, canonical presentation:

* a fixed root key order and a fixed step key order (RFC §14/§24);
* the **step array order is preserved** (author/migration order is presentation,
  not execution order — the graph is carried by ``inputs``);
* the same document always serializes to the same bytes.

Fingerprint canonicalization lives in
:mod:`confflow.config.canonical.serialization`; this module is only the human
writer and never sorts the semantic map by id.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

__all__ = ["dump_workflow_yaml", "order_workflow_document", "write_workflow_yaml_atomic"]

_ROOT_KEY_ORDER = ("schema", "global", "steps", "extensions", "annotations")
_STEP_KEY_ORDER = (
    "id",
    "label",
    "type",
    "enabled",
    "inputs",
    "params",
    "checkpoint",
    "extensions",
    "annotations",
)


def _ordered(mapping: Mapping[str, Any], order: Iterable[str]) -> dict[str, Any]:
    ordered: dict[str, Any] = {key: mapping[key] for key in order if key in mapping}
    for key, value in mapping.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def order_workflow_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``document`` with the canonical root/step key order applied."""
    ordered = _ordered(document, _ROOT_KEY_ORDER)
    steps = ordered.get("steps")
    if isinstance(steps, list):
        ordered["steps"] = [
            _ordered(step, _STEP_KEY_ORDER) if isinstance(step, Mapping) else step for step in steps
        ]
    return ordered


def dump_workflow_yaml(document: Mapping[str, Any]) -> str:
    """Serialize a workflow document deterministically (author order preserved)."""
    ordered = order_workflow_document(document)
    text = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return str(text)


def write_workflow_yaml_atomic(path: str | os.PathLike[str], document: Mapping[str, Any]) -> None:
    """Write the serialized document durably, replacing the destination only when complete."""
    text = dump_workflow_yaml(document)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
