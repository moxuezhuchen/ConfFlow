#!/usr/bin/env python3

"""V4 program adapters: file-format authorities for quantum-chemistry programs.

Each program package owns rendering, parsing, artifact discovery, and
environment probing for one program's file formats.  Program packages never
import the workflow compiler, the legacy calc runtime, or task-name dispatch.
"""

from __future__ import annotations

from .registry import PROGRAM_ALIASES, get_program_adapter, register_program_adapter

__all__ = [
    "PROGRAM_ALIASES",
    "get_program_adapter",
    "register_program_adapter",
]
