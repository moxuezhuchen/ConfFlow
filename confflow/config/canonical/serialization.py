"""Deterministic JSON primitives for configuration contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Frozen identity of the canonical-JSON contract below (sorted keys,
#: ``,``/``:`` separators, UTF-8, no NaN). The schema/canonicalization binding
#: (RFC §16.B) records this version; any change to :func:`canonical_json`
#: that could move a digest MUST bump it, because binding comparison across
#: canonicalizers is rejected rather than guessed.
CANONICALIZATION_VERSION = "confflow.canonical-json.v1"


def canonical_json(value: Any) -> str:
    """Encode JSON deterministically without allowing non-standard numbers."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def canonical_sha256(value: Any) -> str:
    """Return a SHA-256 digest of :func:`canonical_json` output."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = ["CANONICALIZATION_VERSION", "canonical_json", "canonical_sha256"]
