"""Negative fixture: a forbidden legacy symbol used as code."""


def build():
    return TaskRunner  # noqa: F821
