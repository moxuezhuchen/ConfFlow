#!/usr/bin/env python3

"""Output parsing (compatibility layer).

Historically a standalone generic parser was implemented here; the runtime now
defers to ``policy.parse_output``.  To reduce code duplication, this module
selects the policy by ``prog_id`` and delegates parsing.
"""

from __future__ import annotations

import os
from typing import Any

from ..policies import get_policy

__all__ = [
    "parse_output",
]


def parse_output(
    log_file: str, config: dict[str, Any], prog_id: int, is_sp_task: bool = False
) -> dict[str, Any]:
    if not os.path.exists(log_file):
        return {}

    try:
        policy = get_policy(int(prog_id))
    except ValueError:
        return {}
    return policy.parse_output(log_file, config, is_sp_task=is_sp_task) or {}
