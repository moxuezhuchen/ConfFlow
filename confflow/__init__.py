#!/usr/bin/env python3

"""ConfFlow - Automated computational chemistry conformer search workflow engine."""

from __future__ import annotations

import importlib
import logging
import sys
from importlib.metadata import PackageNotFoundError, version

# Keep machine-readable CLI probes free of import-time warning noise while
# preserving the normal console logging behavior for workflow execution.
_HANDSHAKE_PROBE = any(flag in sys.argv[1:] for flag in ("--version", "--capabilities"))
if _HANDSHAKE_PROBE:
    logging.disable(logging.WARNING)

try:
    __version__ = version("confflow")
except PackageNotFoundError:
    __version__ = "1.4.3"
__author__ = "ConfFlow Team"

# ============================================================================
# Centralized optional dependency management
# ============================================================================

# RDKit - required for conformer generation
try:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False
    Chem = None  # type: ignore[assignment]
    AllChem = None  # type: ignore[assignment]

# psutil - resource monitoring (optional)
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None  # type: ignore[assignment]

# numba - JIT acceleration (optional)
try:
    from numba import njit

    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False

    # Return the original function when Numba is unavailable.
    def njit(*args, **kwargs):
        def decorator(func):
            return func

        return decorator if not args else args[0]


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "main": (".main", "main"),
    "run_workflow": (".workflow", "run_workflow"),
    "CalcStepRunner": (".calc", "CalcStepRunner"),
    "CalcStepRequest": (".calc", "CalcStepRequest"),
    "CalcStepResult": (".calc", "CalcStepResult"),
    "read_xyz_file": (".core.io", "read_xyz_file"),
    "write_xyz_file": (".core.io", "write_xyz_file"),
    "parse_comment_metadata": (".core.io", "parse_comment_metadata"),
    "ConfFlowLogger": (".core.logging", "ConfFlowLogger"),
    "get_logger": (".core.logging", "get_logger"),
}

__all__ = [
    "__version__",
    "RDKIT_AVAILABLE",
    "PSUTIL_AVAILABLE",
    "NUMBA_AVAILABLE",
    *sorted(_LAZY_EXPORTS),
]


def __getattr__(name: str):
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = export
    module = importlib.import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
