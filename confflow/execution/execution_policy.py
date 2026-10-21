"""Enforce an execution binding's ``allowed_executables`` and ``sandbox`` before launch.

One rule for every executor that starts an external program (native calculations, the
ConfGen xTB backend, registered scripts): the program must be on the binding's allow list
when one is declared, and every directory the executor writes must lie inside the binding's
sandbox when one is declared. Both checks run before any file is written or any program is
probed or started; a violation is a ``DomainError`` so the work item fails closed.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from ..domain.errors import DomainError
from .contracts import ExecutionBinding

__all__ = ["enforce_execution_policy"]


def enforce_execution_policy(
    binding: ExecutionBinding | None,
    *,
    executable: str | None,
    resolved_executable: str | None = None,
    write_paths: Iterable[str] = (),
    label: str = "executable",
    check_program: bool = True,
) -> None:
    """Raise ``DomainError`` when ``binding`` forbids the program or a write path.

    ``executable`` is the program as configured (absolute path or bare name) and
    ``resolved_executable`` the absolute path it resolved to, if known; the program is
    allowed when either form matches an ``allowed_executables`` entry (absolute entries
    are compared after ``realpath``, bare entries by name). ``check_program=False`` checks
    only the sandbox (the caller checks the program itself once it is known).
    """
    if binding is None:
        return
    allowed = [item.strip() for item in binding.allowed_executables if item.strip()]
    if allowed and check_program:
        candidates = [c for c in (executable, resolved_executable) if c]
        if not candidates:
            raise DomainError(f"{label}: no executable to check against allowed_executables")
        absolute = {_real(item) for item in allowed if os.path.isabs(item)}
        named = {item for item in allowed if not os.path.isabs(item)}
        if not any(
            (_real(c) in absolute) if os.path.isabs(c) else (c in named) for c in candidates
        ):
            raise DomainError(f"{label} is not allowed by allowed_executables: {candidates[-1]}")
    if binding.sandbox:
        sandbox = _real(binding.sandbox)
        for path in write_paths:
            target = _real(os.fspath(path))
            try:
                inside = os.path.commonpath([target, sandbox]) == sandbox
            except ValueError:
                inside = False
            if not inside:
                raise DomainError(f"{label} work path escapes sandbox: {target}")


def _real(path: str) -> str:
    """Absolute, symlink-resolved form of ``path`` (it need not exist)."""
    return os.path.realpath(os.path.abspath(path.strip()))
