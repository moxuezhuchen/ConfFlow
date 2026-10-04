"""Negative fixture for the architecture policy (intentionally violating)."""

programs = object()


def first():
    return programs[0]
