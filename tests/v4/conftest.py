#!/usr/bin/env python3

"""Shared fixtures for the V4 test suite.

Pure in-memory tests do not need fixtures beyond pytest's ``tmp_path``; the
conftest exists so future file-backed document corpora can live here without
touching the legacy test configuration.
"""

from __future__ import annotations
