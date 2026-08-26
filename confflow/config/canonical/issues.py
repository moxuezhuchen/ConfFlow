"""Structured configuration validation diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigIssue:
    """One user-facing configuration problem at a stable logical path."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ConfigValidationError(ValueError):
    """Raised when raw configuration cannot become a typed workflow model."""

    def __init__(self, issue: ConfigIssue) -> None:
        self.issue = issue
        super().__init__(str(issue))
