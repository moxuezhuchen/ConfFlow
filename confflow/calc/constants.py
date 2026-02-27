#!/usr/bin/env python3

"""Constants module.

Contains program paths, task type mappings, periodic table element symbols,
and other constants.
"""

from __future__ import annotations

from ..core.constants import HARTREE_TO_KCALMOL  # noqa: F401  (re-export for backward compat)
from ..core.data import PERIODIC_SYMBOLS, get_element_symbol  # noqa: F401

__all__ = [
    "ITASK_MAP",
    "GAUSSIAN_TEMPLATE",
    "ORCA_TEMPLATE",
    "BUILTIN_TEMPLATES",
    "HARTREE_TO_KCALMOL",
    "PERIODIC_SYMBOLS",
    "get_element_symbol",
]

# =============================================================================
# Task type mapping
# =============================================================================

ITASK_MAP: dict[str, int] = {
    "opt": 0,  # Geometry optimization
    "sp": 1,  # Single-point energy
    "freq": 2,  # Frequency calculation
    "opt_freq": 3,  # Optimization + frequency
    "ts": 4,  # Transition state search
}

# =============================================================================
# Input file templates
# =============================================================================

GAUSSIAN_TEMPLATE = """{link0}%nproc={cores}
%mem={memory}
{keyword_line}

{job_name}

{charge} {multiplicity}
{coordinates}

{extra_section}


"""

ORCA_TEMPLATE = """! {keyword}
%pal nprocs {cores} end
%maxcore {memory}
{generated_blocks}* xyz {charge} {multiplicity}
{coordinates}
*
"""

BUILTIN_TEMPLATES: dict[str, str] = {"gaussian": GAUSSIAN_TEMPLATE, "orca": ORCA_TEMPLATE}
