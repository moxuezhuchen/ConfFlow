"""Negative fixture: a domain module importing another repository root."""

from confflow.execution import anything  # noqa: F401


def run() -> int:
    return 0
