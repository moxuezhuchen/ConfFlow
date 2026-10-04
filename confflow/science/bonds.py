#!/usr/bin/env python3

"""Bond-perception edge for V4 science (single explicit dependency).

The bond rule ``d < bond_scale * (r_i + r_j)`` with the 0.4 A minimum and
the GaussView covalent radii is centralised in ``confflow.science.bonding``
so the same atoms/coordinates/scale always produce the same topology.
This module is the V4 execution layer's sole entrypoint to that
authority: explicit typed inputs, :class:`ValueError` failures, no
legacy orchestration imports (no pools, CLI, file I/O, or progress UI
are touched — only the perception functions).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "covalent_radii",
    "perceive_adjacency",
]


def perceive_adjacency(
    atomic_numbers: Sequence[int],
    coords: Sequence[Sequence[float]],
    *,
    bond_scale: float,
) -> list[list[int]]:
    """Return a sorted neighbour list per atom.

    Parameters
    ----------
    atomic_numbers : Sequence[int]
        1-based atomic numbers in atom order.
    coords : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom, one ``(x, y, z)`` per atom.
    bond_scale : float
        Positive scale factor applied to summed covalent radii.

    Raises
    ------
    ValueError
        Raised for unknown elements, malformed coordinates, or a
        non-positive scale.
    """
    from .bonding import UnknownElementError, build_adjacency

    scale = float(bond_scale)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"bond_scale must be a positive number, got {bond_scale!r}")
    try:
        return build_adjacency([int(item) for item in atomic_numbers], coords, bond_scale=scale)
    except UnknownElementError as exc:
        raise ValueError(f"bond perception failed: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"bond perception failed: {exc}") from exc


def covalent_radii(atomic_numbers: Sequence[int]) -> list[float]:
    """Return the usable covalent radius per atom.

    Raises
    ------
    ValueError
        Raised when any atom has no usable covalent radius.
    """
    from .bonding import UnknownElementError, covalent_radius

    radii: list[float] = []
    for number in atomic_numbers:
        radius = covalent_radius(int(number))
        if radius is None:
            raise ValueError(
                f"atom Z={int(number)} has no usable covalent radius"
            ) from UnknownElementError(f"Z={int(number)}")
        radii.append(float(radius))
    return [float(item) for item in radii]
