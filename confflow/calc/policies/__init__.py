#!/usr/bin/env python3
"""Calculation program policy registry."""

from __future__ import annotations

from typing import Any

from ..setup import parse_iprog
from .base import CalculationPolicy
from .gaussian import GAUSSIAN_POLICY
from .orca import ORCA_POLICY

# ---------------------------------------------------------------------------
# Policy registry -- all iprog -> Policy mappings are centralized here;
# adding a new program only requires a single registration.
# ---------------------------------------------------------------------------

_POLICY_REGISTRY: dict[int, CalculationPolicy] = {
    1: GAUSSIAN_POLICY,
    2: ORCA_POLICY,
}


def get_policy(iprog: int) -> CalculationPolicy:
    """Return the CalculationPolicy instance for the given program ID.

    Parameters
    ----------
    iprog : int
        Program ID (1: Gaussian, 2: ORCA).

    Returns
    -------
    CalculationPolicy
        The corresponding singleton policy.

    Raises
    ------
    ValueError
        If ``iprog`` is not supported.
    """
    policy = _POLICY_REGISTRY.get(iprog)
    if policy is None:
        raise ValueError(f"Unsupported iprog: {iprog}. Registered: {sorted(_POLICY_REGISTRY)}")
    return policy


def get_policy_for_config(config: dict[str, Any]) -> CalculationPolicy:
    """Resolve the calculation policy directly from a task config."""
    return get_policy(parse_iprog(config))


__all__ = [
    "CalculationPolicy",
    "get_policy",
    "get_policy_for_config",
    "GAUSSIAN_POLICY",
    "ORCA_POLICY",
]
