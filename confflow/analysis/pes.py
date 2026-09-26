#!/usr/bin/env python3

"""Multi-group PES assembly for V4-6 reaction aggregation.

Per-group ``reaction_profile`` payloads (see
:mod:`confflow.analysis.reaction`) assemble here into one deterministic
potential-energy-surface view: groups sort by ``group_key`` so shuffled
inputs yield the identical payload and digest.  Digests use
:func:`confflow.domain.canonical.typed_digest` with dedicated domain
markers, keeping reaction and PES digests disjoint by construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from ..domain.canonical import typed_digest
from .units import AnalysisMathError

__all__ = [
    "PES_DIGEST_KIND",
    "PROFILE_DIGEST_KIND",
    "assemble_pes_profile",
    "pes_digest",
    "reaction_profile_digest",
]

#: Digest domain marker for one reaction-profile payload.
PROFILE_DIGEST_KIND: Final[str] = "confflow.analysis.reaction_profile.v1"

#: Digest domain marker for an assembled multi-group PES payload.
PES_DIGEST_KIND: Final[str] = "confflow.analysis.pes.v1"


def reaction_profile_digest(payload: Mapping[str, Any]) -> str:
    """Return the domain-separated digest of one profile payload.

    Parameters
    ----------
    payload : Mapping
        A ``reaction_profile`` value mapping (must carry ``group_key``).

    Returns
    -------
    str
        ``sha256:<hex>`` digest over the canonical JSON encoding.
    """
    return typed_digest(PROFILE_DIGEST_KIND, dict(payload))


def pes_digest(combined: Mapping[str, Any]) -> str:
    """Return the domain-separated digest of an assembled PES payload.

    Parameters
    ----------
    combined : Mapping
        Output of :func:`assemble_pes_profile`.

    Returns
    -------
    str
        ``sha256:<hex>`` digest over the canonical JSON encoding.
    """
    return typed_digest(PES_DIGEST_KIND, dict(combined))


def assemble_pes_profile(profiles: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Assemble per-group profiles into one PES payload sorted by group key.

    Parameters
    ----------
    profiles : iterable of Mapping
        Per-group ``reaction_profile`` value mappings in any order.
        Every entry must carry a non-empty string ``group_key``; keys
        must be unique.

    Returns
    -------
    dict
        ``{"groups": [...], "group_keys": [...], "count": int}`` with
        groups sorted by ``group_key``.  Shuffled inputs produce the
        identical payload and therefore the identical :func:`pes_digest`.

    Raises
    ------
    AnalysisMathError
        With code ``missing_group_key`` when an entry lacks a usable
        key, or ``duplicate_group_key`` when a key repeats.  The
        assembly fails closed rather than dropping or merging groups.
    """
    ranked: list[tuple[str, dict[str, Any]]] = []
    for index, profile in enumerate(profiles):
        payload = dict(profile)
        group_key = payload.get("group_key")
        if not isinstance(group_key, str) or not group_key.strip():
            raise AnalysisMathError(
                "missing_group_key",
                f"reaction profile at position {index} has no group key",
                details={"position": index},
            )
        ranked.append((group_key, payload))
    keys = [key for key, _ in ranked]
    if len(set(keys)) != len(keys):
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        raise AnalysisMathError(
            "duplicate_group_key",
            f"duplicate reaction group keys: {repeated}",
            details={"duplicate_keys": repeated},
        )
    ranked.sort(key=lambda item: item[0])
    ordered = [payload for _, payload in ranked]
    return {
        "groups": ordered,
        "group_keys": [key for key, _ in ranked],
        "count": len(ordered),
    }
