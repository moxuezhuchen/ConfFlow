#!/usr/bin/env python3

"""ConfGen v3 ring-lane declared finite template library.

Isolated covalent 4/5/6-membered ring templates as idealized Cartesian
coordinates with uniform covalent bond lengths and exact ring closure by
symmetric closed-form construction (no iterative solver, no energy
optimization). Coverage is deliberately finite and declared: anything outside
this registry is out of scope and must be reported as ambiguous/unsupported,
never silently assigned.

Ring identity = template name + internal torsion descriptor (endocyclic
torsions in traversal order from the anchor, periodic (-180, 180] convention).

Constructions (nominal C-C bond ``BOND_LENGTH`` = 1.54 A):

- 4-ring: ``planar_4`` (square); ``pucker_up_4`` / ``pucker_down_4``
  (D2d butterfly, atoms 1/3 out of plane by +/- FLAP_4, exact uniform bonds).
- 5-ring: ``planar_5`` (regular pentagon); ``envelope_5`` (flap atom at
  traversal position 0 lifted by ENVELOPE_FLAP, radial position solved for
  exact uniform bonds); ``twist_5`` (C2 twist about the anchor axis,
  coordinates from closed-form symmetric solution, exact uniform bonds).
- 6-ring: ``chair_A_6`` / ``chair_B_6`` (D3d, staggered triangles, opposite
  z-parity; exact uniform bonds and tetrahedral angles); ``boat_6`` (C2v
  flagpole construction, exact uniform bonds); ``twist_boat_6`` (D2
  construction, exact uniform bonds).

Dependencies: stdlib + NumPy only.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import numpy as np

from .constants import TEMPLATES_BY_SIZE
from .geometry import ring_torsions

__all__ = [
    "BOND_LENGTH",
    "RingTemplate",
    "TEMPLATE_REGISTRY",
    "TEMPLATES_BY_SIZE",
    "get_template",
    "template_bond_spread",
    "template_coords",
    "template_torsions",
    "templates_for_size",
]

#: Nominal covalent bond length (Angstrom) used by every template.
BOND_LENGTH = 1.54

#: Cyclobutane pucker flap height (Angstrom) for the D2d butterfly.
FLAP_4 = 0.30

#: Cyclopentane envelope flap height (Angstrom) at traversal position 0.
ENVELOPE_FLAP = 0.50


@dataclass(frozen=True, slots=True)
class RingTemplate:
    """One declared ideal ring geometry."""

    name: str
    ring_size: int
    family: str
    coordinates: tuple[tuple[float, float, float], ...]
    construction: str


def _centered(points: list[list[float]]) -> tuple[tuple[float, float, float], ...]:
    """Return centroid-centered coordinates as nested tuples."""
    array = np.asarray(points, dtype=float)
    array = array - array.mean(axis=0)
    return tuple(tuple(float(value) for value in row) for row in array)


def _planar_4() -> RingTemplate:
    half = BOND_LENGTH / 2.0
    return RingTemplate(
        name="planar_4",
        ring_size=4,
        family="cyclobutane",
        coordinates=_centered(
            [[half, half, 0.0], [-half, half, 0.0], [-half, -half, 0.0], [half, -half, 0.0]]
        ),
        construction="square with side BOND_LENGTH",
    )


def _pucker_4(sign: float, name: str) -> RingTemplate:
    b = BOND_LENGTH
    h = FLAP_4 * sign
    d = float(np.sqrt((b**2 - h**2) / 2.0))
    # Butterfly fold about the 0-2 diagonal: atoms 1 and 3 leave the hinge
    # plane to the SAME side, giving alternating torsions (+t,-t,+t,-t).
    # (Displacing them to opposite sides yields a rotated square, which is
    # planar with all-zero torsions, not a pucker.)
    return RingTemplate(
        name=name,
        ring_size=4,
        family="cyclobutane",
        coordinates=_centered([[-d, 0.0, 0.0], [0.0, d, h], [d, 0.0, 0.0], [0.0, -d, h]]),
        construction="butterfly fold about the 0-2 diagonal: atoms 1/3 on "
        "the same side by +/-FLAP_4; radial distance solved for uniform bonds",
    )


def _planar_5() -> RingTemplate:
    radius = BOND_LENGTH / (2.0 * float(np.sin(np.pi / 5.0)))
    points = [
        [
            radius * float(np.cos(index * 2.0 * np.pi / 5.0)),
            radius * float(np.sin(index * 2.0 * np.pi / 5.0)),
            0.0,
        ]
        for index in range(5)
    ]
    return RingTemplate(
        name="planar_5",
        ring_size=5,
        family="cyclopentane",
        coordinates=_centered(points),
        construction="regular pentagon with side BOND_LENGTH",
    )


def _envelope_5() -> RingTemplate:
    b = BOND_LENGTH
    radius = b / (2.0 * float(np.sin(np.pi / 5.0)))
    flap = ENVELOPE_FLAP
    axial = radius * float(np.cos(2.0 * np.pi / 5.0))
    # Solve radial position r of the flap atom (angle 0, height flap) so that
    # bonds 0-1 and 0-4 are exactly BOND_LENGTH:
    # r^2 - 2*axial*r + (radius^2 + flap^2 - b^2) = 0.
    constant = radius**2 + flap**2 - b**2
    discriminant = axial**2 - constant
    if discriminant < 0.0:
        raise ValueError("envelope construction has no real solution")
    radial = axial + float(np.sqrt(discriminant))
    points = [[radial, 0.0, flap]]
    for index in range(1, 5):
        angle = index * 2.0 * np.pi / 5.0
        points.append([radius * float(np.cos(angle)), radius * float(np.sin(angle)), 0.0])
    return RingTemplate(
        name="envelope_5",
        ring_size=5,
        family="cyclopentane",
        coordinates=_centered(points),
        construction="envelope with flap at traversal position 0 lifted by "
        "ENVELOPE_FLAP; flap radius solved for exact uniform bonds",
    )


def _twist_5() -> RingTemplate:
    b = BOND_LENGTH
    # C2 twist about the x-axis (through atom 0 and the midpoint of bond 2-3).
    y2 = b / 2.0
    x0, f0 = 1.10, 0.45
    a, c, y1 = -0.15, 0.25, 1.20
    x1 = x0 - float(np.sqrt(b**2 - y1**2 - (f0 - a) ** 2))
    x2 = x1 - float(np.sqrt(b**2 - (y1 - y2) ** 2 - (a - c) ** 2))
    points = [
        [x0, 0.0, f0],
        [x1, y1, a],
        [x2, y2, c],
        [x2, -y2, c],
        [x1, -y1, a],
    ]
    return RingTemplate(
        name="twist_5",
        ring_size=5,
        family="cyclopentane",
        coordinates=_centered(points),
        construction="C2 twist: symmetric parameters with x1/x2 from "
        "closed-form uniform-bond solutions",
    )


def _chair_6(sign: float, name: str) -> RingTemplate:
    b = BOND_LENGTH
    # D3d chair: staggered triangles of radius R at z=+/-h. Tetrahedral angle
    # requires h = R/(4*sqrt(2)); bond^2 = R^2 + 4h^2 = 9R^2/8.
    radius = b * 2.0 * float(np.sqrt(2.0)) / 3.0
    height = radius / (4.0 * float(np.sqrt(2.0)))
    points = []
    for index in range(6):
        angle = index * np.pi / 3.0
        parity = 1.0 if index % 2 == 0 else -1.0
        points.append(
            [
                radius * float(np.cos(angle)),
                radius * float(np.sin(angle)),
                sign * parity * height,
            ]
        )
    return RingTemplate(
        name=name,
        ring_size=6,
        family="cyclohexane",
        coordinates=_centered(points),
        construction="D3d chair: staggered triangles, tetrahedral angles and "
        "exact uniform bonds by analytic construction",
    )


def _boat_6() -> RingTemplate:
    b = BOND_LENGTH
    # C2v boat: flagpole atoms 0/3 at (+/-L, 0, f), side atoms at
    # (+/-u, +/-v, 0) with u = v = b/2 and (L-u) = f = sqrt((b^2-v^2)/2).
    u = b / 2.0
    v = b / 2.0
    leg = float(np.sqrt((b**2 - v**2) / 2.0))
    length = u + leg
    points = [
        [length, 0.0, leg],
        [u, v, 0.0],
        [-u, v, 0.0],
        [-length, 0.0, leg],
        [-u, -v, 0.0],
        [u, -v, 0.0],
    ]
    return RingTemplate(
        name="boat_6",
        ring_size=6,
        family="cyclohexane",
        coordinates=_centered(points),
        construction="C2v flagpole boat with exact uniform bonds by closed-form symmetric solution",
    )


def _twist_boat_6() -> RingTemplate:
    b = BOND_LENGTH
    # D2 twist-boat: atoms 0..1 at (0,+/-q,+/-r), 3..4 mirrored, atoms 2/5 at
    # (+/-e,0,0) with q^2+r^2 = b^2/4 and e = b*sqrt(3)/2; all bonds exact.
    q = 0.60
    r = float(np.sqrt(b**2 / 4.0 - q**2))
    e = b * float(np.sqrt(3.0)) / 2.0
    points = [
        [0.0, q, r],
        [0.0, -q, -r],
        [e, 0.0, 0.0],
        [0.0, q, -r],
        [0.0, -q, r],
        [-e, 0.0, 0.0],
    ]
    return RingTemplate(
        name="twist_boat_6",
        ring_size=6,
        family="cyclohexane",
        coordinates=_centered(points),
        construction="D2 twist-boat with exact uniform bonds by closed-form symmetric solution",
    )


def _build_registry() -> dict[str, RingTemplate]:
    templates = [
        _planar_4(),
        _pucker_4(1.0, "pucker_up_4"),
        _pucker_4(-1.0, "pucker_down_4"),
        _planar_5(),
        _envelope_5(),
        _twist_5(),
        _chair_6(1.0, "chair_A_6"),
        _chair_6(-1.0, "chair_B_6"),
        _boat_6(),
        _twist_boat_6(),
    ]
    return {item.name: item for item in templates}


#: Declared finite template registry (insertion order is stable).
TEMPLATE_REGISTRY: dict[str, RingTemplate] = _build_registry()

#: Templates per ring size (single authority in :mod:`ring.constants`;
#: re-exported here as the same object for historic import paths).


def get_template(name: str) -> RingTemplate:
    """Return the declared template with *name*, else raise ``KeyError``."""
    return TEMPLATE_REGISTRY[name]


def templates_for_size(ring_size: int) -> tuple[RingTemplate, ...]:
    """Return declared templates for a ring size, or empty tuple if unsupported."""
    return tuple(TEMPLATE_REGISTRY[name] for name in TEMPLATES_BY_SIZE.get(ring_size, ()))


def template_coords(template: RingTemplate) -> np.ndarray:
    """Return template coordinates as an ``(n, 3)`` array."""
    return np.asarray(template.coordinates, dtype=float)


@cache
def _cached_torsions(name: str) -> tuple[float, ...]:
    template = TEMPLATE_REGISTRY[name]
    return tuple(float(value) for value in ring_torsions(template_coords(template)))


def template_torsions(template: RingTemplate) -> tuple[float, ...]:
    """Return the internal torsion descriptor of a template in traversal order."""
    return _cached_torsions(template.name)


def template_bond_spread(template: RingTemplate) -> float:
    """Return max |bond| deviation from nominal over the template ring bonds."""
    coords = template_coords(template)
    n_atoms = coords.shape[0]
    lengths = [
        float(np.linalg.norm(coords[(index + 1) % n_atoms] - coords[index]))
        for index in range(n_atoms)
    ]
    return max(abs(item - BOND_LENGTH) for item in lengths)
