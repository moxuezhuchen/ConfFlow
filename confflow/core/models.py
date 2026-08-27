#!/usr/bin/env python3

"""Pydantic model compatibility facade.

The canonical runtime models live in confflow.config.canonical.pydantic.
This historical module intentionally contains no defaults or validators.
"""

from __future__ import annotations

from ..config.canonical.pydantic import CalcConfigModel, GlobalConfigModel, TaskContext

__all__ = ["TaskContext", "GlobalConfigModel", "CalcConfigModel"]
