#!/usr/bin/env python3
"""Backward-compatibility shim — canonical location is console.py."""

from __future__ import annotations

from .console import require_existing_path  # noqa: F401

__all__ = ["require_existing_path"]
