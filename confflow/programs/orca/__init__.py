#!/usr/bin/env python3

"""V4 ORCA program adapter: file-format authority for ORCA execution.

This package owns ORCA ``.inp`` rendering, ``.out`` parsing, artifact
discovery, and environment probing.  It never sees the workflow graph and
never imports the legacy calc runtime.
"""

from __future__ import annotations

from .adapter import OrcaProgramAdapter

__all__ = [
    "OrcaProgramAdapter",
]
