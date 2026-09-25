#!/usr/bin/env python3

"""Gaussian program adapter package for V4 native execution.

This package is the file-format authority for Gaussian: it renders ``.gjf``
input, parses ``.log`` output into facts, discovers artifacts, and answers
environment probes. It imports only the standard library plus the V4 domain
and execution contracts.
"""

from __future__ import annotations

from .adapter import ADAPTER_VERSION, PARSER_VERSION, GaussianProgramAdapter
from .rendering import scan_keyword_from_ts

__all__ = [
    "ADAPTER_VERSION",
    "PARSER_VERSION",
    "GaussianProgramAdapter",
    "scan_keyword_from_ts",
]
