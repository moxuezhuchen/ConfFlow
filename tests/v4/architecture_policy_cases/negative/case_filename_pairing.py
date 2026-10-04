"""Negative fixture for the architecture policy (intentionally violating)."""

import os


def pair(left: str, right: str) -> bool:
    return os.path.basename(left) == os.path.basename(right)
