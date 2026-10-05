"""Negative fixture for the architecture policy (intentionally violating)."""

from enum import Enum


class TaskName(str, Enum):
    IRC = "irc"
    QST2 = "qst2"


def run(task: TaskName) -> int:
    if task == TaskName.IRC:
        return 1
    return 0
