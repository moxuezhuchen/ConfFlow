#!/usr/bin/env python3

"""ConfFlow - Automated computational chemistry conformer search workflow engine."""

from __future__ import annotations

__version__ = "1.0.10"
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


# ============================================================================
# Core module exports
# ============================================================================

try:
    from .blocks.confgen import run_generation
    from .blocks.refine import RefineOptions, process_xyz
    from .blocks.viz import parse_xyz_file
    from .calc import ChemTaskManager
    from .config.schema import ConfigSchema, merge_step_params
    from .core.io import parse_comment_metadata, read_xyz_file, write_xyz_file
    from .core.utils import ConfFlowLogger, get_logger
    from .main import main

    __all__ = [
        # Workflow entry
        "main",
        # Conformer generation
        "run_generation",
        # Quantum chemistry computation
        "ChemTaskManager",
        # Conformer refinement
        "RefineOptions",
        "process_xyz",
        # Visualization
        "parse_xyz_file",
        # Logging
        "ConfFlowLogger",
        "get_logger",
        # I/O
        "read_xyz_file",
        "write_xyz_file",
        "parse_comment_metadata",
        # Configuration
        "ConfigSchema",
        "merge_step_params",
        # Version
        "__version__",
        # Optional dependency availability flags
        "RDKIT_AVAILABLE",
        "PSUTIL_AVAILABLE",
        "NUMBA_AVAILABLE",
    ]
except ImportError as e:
    # Emit an import warning without interrupting package loading.
    import warnings

    warnings.warn(f"ConfFlow module import warning: {e}", stacklevel=2)
    __all__ = ["__version__", "RDKIT_AVAILABLE", "PSUTIL_AVAILABLE", "NUMBA_AVAILABLE"]
