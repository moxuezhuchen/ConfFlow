"""Deterministic JSON primitives for configuration contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Encode JSON deterministically without allowing non-standard numbers."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def canonical_sha256(value: Any) -> str:
    """Return a SHA-256 digest of :func:`canonical_json` output."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = ["canonical_json", "canonical_sha256"]
