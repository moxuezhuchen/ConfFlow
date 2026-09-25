#!/usr/bin/env python3

"""Program-name to adapter resolution.

Program names are scientific vocabulary (which file format), resolved here to
the owning :class:`ProgramAdapter`.  Aliases such as ``g16`` exist for human
convenience; the canonical program name is the adapter's
:attr:`ProgramAdapter.program_name` value.
"""

from __future__ import annotations

from ..domain.errors import DomainError
from ..execution.native import ProgramAdapter, ProgramName

__all__ = [
    "PROGRAM_ALIASES",
    "get_program_adapter",
    "register_program_adapter",
]

_PROGRAM_ADAPTERS: dict[str, ProgramAdapter] = {}

#: Human-convenience aliases to canonical program names.
PROGRAM_ALIASES: dict[str, str] = {
    "g16": ProgramName.GAUSSIAN.value,
    "gaussian": ProgramName.GAUSSIAN.value,
    "g03": ProgramName.GAUSSIAN.value,
    "g09": ProgramName.GAUSSIAN.value,
    "orca": ProgramName.ORCA.value,
}


def register_program_adapter(adapter: ProgramAdapter) -> None:
    """Register a program adapter under its canonical program name."""
    if not isinstance(adapter, ProgramAdapter):
        raise DomainError("adapter must be a ProgramAdapter")
    _PROGRAM_ADAPTERS[adapter.program_name.value] = adapter


def get_program_adapter(program: str) -> ProgramAdapter:
    """Resolve a program name or alias to its adapter.

    Raises
    ------
    DomainError
        Raised when the program is unknown or its adapter is not registered.
    """
    canonical = PROGRAM_ALIASES.get(str(program).strip().lower(), "")
    adapter = _PROGRAM_ADAPTERS.get(canonical)
    if adapter is None:
        if not canonical:
            raise DomainError(
                f"unknown program {program!r}; expected one of {sorted(PROGRAM_ALIASES)}"
            )
        # Lazy import keeps ``confflow.programs`` light until an adapter is used.
        if canonical == ProgramName.GAUSSIAN.value:
            from .gaussian import GaussianProgramAdapter

            adapter = GaussianProgramAdapter()
        elif canonical == ProgramName.ORCA.value:
            from .orca import OrcaProgramAdapter

            adapter = OrcaProgramAdapter()
        else:  # pragma: no cover - unreachable while aliases match ProgramName
            raise DomainError(f"no adapter registered for program {program!r}")
        register_program_adapter(adapter)
    return adapter
