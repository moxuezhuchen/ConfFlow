#!/usr/bin/env python3

"""Stochastic seed contract for V4.

Any stochastic operation (for example future confgen or GOAT sampling) must
carry an explicit seed, or a seed materialized once by the producer and
persisted in the canonical definition.  Seeds participate in semantic and
reuse identity, so a resume can never silently re-randomize a run.

V4-1 supports the ``explicit`` policy only: a stochastic step without a seed
is a compile error.  ``materialized`` is the reserved policy for the future
materialization flow, and its seeds must be persisted before fingerprinting.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .errors import DomainError

__all__ = [
    "SeedPolicy",
    "seed_payload",
    "seed_required",
    "validate_seed",
]


class SeedPolicy(str, Enum):
    """How a stochastic operation obtains its seed.

    Attributes
    ----------
    EXPLICIT
        The seed is declared in the workflow document.
    MATERIALIZED
        The producer generates the seed once and persists it; the workflow
        document alone is not enough to compile the step.
    """

    EXPLICIT = "explicit"
    MATERIALIZED = "materialized"


def validate_seed(seed: Any) -> None:
    """Validate a declared seed value.

    Raises
    ------
    DomainError
        Raised when *seed* is neither ``None`` nor an integer.
    """
    if seed is None:
        return
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DomainError("seed must be an integer or None")


def seed_required(*, stochastic: bool, policy: SeedPolicy) -> bool:
    """Return whether a seed must be present for this contract."""
    if not stochastic:
        return False
    return policy is SeedPolicy.EXPLICIT


def seed_payload(seed: int | None, policy: SeedPolicy) -> dict[str, Any]:
    """Return the seed contribution to a semantic digest payload."""
    return {"policy": policy.value, "seed": seed}
