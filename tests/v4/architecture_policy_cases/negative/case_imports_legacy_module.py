"""Negative fixture: an import of a retired legacy runtime module."""

import confflow.workflow.engine  # noqa: F401


def run() -> int:
    return 0
