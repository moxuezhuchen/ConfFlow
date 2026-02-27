#!/usr/bin/env python3

"""ConfFlow Pydantic data models.

Complementary to the TypedDict definitions in ``core.types``:

- TypedDict: lightweight static type annotations (no runtime overhead).
- Pydantic models: data containers requiring runtime validation and serialisation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TaskContext",
]


class TaskContext(BaseModel):
    """Context information for a computation task."""

    model_config = ConfigDict(extra="allow")

    job_name: str
    work_dir: str
    coords: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
