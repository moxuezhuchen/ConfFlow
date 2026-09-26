#!/usr/bin/env python3

"""JSON Schema views of the frozen V2 envelope contracts (V4-4).

The pydantic models in :mod:`confflow.remote.envelope` are the single source
of truth; this module only exposes thin wrappers that generate JSON Schema
from them, so field definitions are never duplicated here.
"""

from __future__ import annotations

from typing import Any, Final

from .envelope import HANDOFF_SCHEMA_V2, RESULT_SCHEMA_V2, ResultBundle, WorkerHandoffV2

__all__ = ["SCHEMA_IDS", "handoff_json_schema", "result_json_schema"]

#: Protocol identities of the V2 envelope schemas, in (handoff, result) order.
SCHEMA_IDS: Final[tuple[str, str]] = (HANDOFF_SCHEMA_V2, RESULT_SCHEMA_V2)


def handoff_json_schema() -> dict[str, Any]:
    """Return the JSON Schema generated from :class:`WorkerHandoffV2`.

    Returns
    -------
    dict[str, Any]
        JSON Schema mapping describing the worker-handoff V2 envelope.
    """
    schema: dict[str, Any] = WorkerHandoffV2.model_json_schema()
    return schema


def result_json_schema() -> dict[str, Any]:
    """Return the JSON Schema generated from :class:`ResultBundle`.

    Returns
    -------
    dict[str, Any]
        JSON Schema mapping describing the worker result bundle.
    """
    schema: dict[str, Any] = ResultBundle.model_json_schema()
    return schema
