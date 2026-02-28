#!/usr/bin/env python3
"""Calculation program policy registry."""

from __future__ import annotations

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


__all__ = ["CalculationPolicy", "get_policy", "GAUSSIAN_POLICY", "ORCA_POLICY"]
