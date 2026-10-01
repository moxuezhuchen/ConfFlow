#!/usr/bin/env python3
"""Ideal coordination templates and verified proper rotation groups.

Pure science: stdlib + NumPy only.

Each registered shape provides ideal vertex directions (unit vectors from the
metal center), a vertex ordering convention, the proper rotation group as
vertex permutations, and the trans-pair set used by forbidden-trans policy.

Group construction is computational, not hand-listed: every permutation of
the vertices is tested for realization by a proper rotation (``det == +1``)
via the Kabsch fit, keeping only permutations realized within tolerance.
Group axioms (identity, closure, inverses) are verified explicitly, and the
octahedral group is cross-checked against the TS1 fixture convention.

``auto`` in a :class:`CoordinationSpec` means all registered shapes at the
requested coordination number (see ``graph.CN_SHAPES``).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from functools import cache

import numpy as np

__all__ = [
    "ShapeTemplate",
    "SHAPE_CN",
    "registered_shapes",
    "get_shape",
    "proper_rotation_group",
    "kabsch_proper_rotation",
    "kabsch_rmsd",
    "verify_group_axioms",
    "shape_class_count",
]

_TOL = 1e-6


def kabsch_proper_rotation(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return the proper rotation minimizing ``|source @ R.T - target|``.

    Both point sets must be centered.  The determinant correction forces a
    proper rotation; callers check the residual to decide realizability.
    """
    hessian = np.asarray(source, dtype=float).T @ np.asarray(target, dtype=float)
    u_mat, _, vt_mat = np.linalg.svd(hessian)
    rotation = vt_mat.T @ u_mat.T
    if np.linalg.det(rotation) < 0.0:
        vt_mat = vt_mat.copy()
        vt_mat[-1, :] *= -1.0
        rotation = vt_mat.T @ u_mat.T
    return rotation


def kabsch_rmsd(source: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray]:
    """Return ``(rmsd, rotation)`` for the proper Kabsch fit."""
    src = np.asarray(source, dtype=float)
    tgt = np.asarray(target, dtype=float)
    src_c = src - src.mean(axis=0)
    tgt_c = tgt - tgt.mean(axis=0)
    rotation = kabsch_proper_rotation(src_c, tgt_c)
    fitted = src_c @ rotation.T
    rmsd = float(np.sqrt(np.mean(np.sum((fitted - tgt_c) ** 2, axis=1))))
    return rmsd, rotation


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if bool(np.any(norms < 1e-12)):
        raise ValueError("degenerate template vertex")
    return matrix / norms


def _octahedral_vertices() -> np.ndarray:
    # Fixture convention: [+X, -X, +Y, -Y, +Z, -Z].
    return np.array(
        [
            [1.0, 0.0, 0.0],
            [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, -1.0],
        ]
    )


def _tetrahedral_vertices() -> np.ndarray:
    verts = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ]
    )
    return _normalize_rows(verts)


def _square_planar_vertices() -> np.ndarray:
    # Cyclic order [+X, +Y, -X, -Y]; opposite pairs (0,2) and (1,3).
    return np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [-1.0, 0.0, 0.0],
            [0.0, -1.0, 0.0],
        ]
    )


def _trigonal_bipyramidal_vertices() -> np.ndarray:
    # Axial (0, 1), equatorial (2, 3, 4) at 120 degrees.
    verts = np.array(
        [
            [0.0, 0.0, 1.0],
            [0.0, 0.0, -1.0],
            [1.0, 0.0, 0.0],
            [-0.5, math.sqrt(3.0) / 2.0, 0.0],
            [-0.5, -math.sqrt(3.0) / 2.0, 0.0],
        ]
    )
    return _normalize_rows(verts)


def _square_pyramidal_vertices() -> np.ndarray:
    # Apex (0), base cyclic [+X, +Y, -X, -Y] slightly below the plane.
    verts = np.array(
        [
            [0.0, 0.0, 1.0],
            [1.0, 0.0, -0.25],
            [0.0, 1.0, -0.25],
            [-1.0, 0.0, -0.25],
            [0.0, -1.0, -0.25],
        ]
    )
    return _normalize_rows(verts)


def _trigonal_prismatic_vertices() -> np.ndarray:
    # Eclipsed triangles at z = +/-h; no trans pairs.
    angles = [
        math.pi / 2.0,
        math.pi / 2.0 + 2.0 * math.pi / 3.0,
        math.pi / 2.0 + 4.0 * math.pi / 3.0,
    ]
    verts = np.array(
        [[math.cos(a), math.sin(a), 0.35] for a in angles]
        + [[math.cos(a), math.sin(a), -0.35] for a in angles]
    )
    return _normalize_rows(verts)


@dataclass(frozen=True, slots=True)
class ShapeTemplate:
    """Ideal template: vertex directions, convention, and trans pairs."""

    name: str
    coordination_number: int
    vertex_names: tuple[str, ...]
    vertices: np.ndarray
    opposite: dict[int, int]
    trans_pairs: tuple[frozenset[int], ...]
    proper_group_order: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vertices", np.asarray(self.vertices, dtype=float))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible summary (no arrays)."""
        return {
            "name": self.name,
            "coordination_number": self.coordination_number,
            "vertex_names": list(self.vertex_names),
            "opposite": {str(k): v for k, v in self.opposite.items()},
            "trans_pairs": [sorted(pair) for pair in self.trans_pairs],
            "proper_group_order": self.proper_group_order,
        }


def _build_templates() -> dict[str, ShapeTemplate]:
    return {
        "tetrahedral": ShapeTemplate(
            name="tetrahedral",
            coordination_number=4,
            vertex_names=("T0", "T1", "T2", "T3"),
            vertices=_tetrahedral_vertices(),
            opposite={},
            trans_pairs=(),
            proper_group_order=12,
        ),
        "square_planar": ShapeTemplate(
            name="square_planar",
            coordination_number=4,
            vertex_names=("+X", "+Y", "-X", "-Y"),
            vertices=_square_planar_vertices(),
            opposite={0: 2, 2: 0, 1: 3, 3: 1},
            trans_pairs=(frozenset((0, 2)), frozenset((1, 3))),
            proper_group_order=8,
        ),
        "trigonal_bipyramidal": ShapeTemplate(
            name="trigonal_bipyramidal",
            coordination_number=5,
            vertex_names=("+Z", "-Z", "E0", "E1", "E2"),
            vertices=_trigonal_bipyramidal_vertices(),
            opposite={0: 1, 1: 0},
            trans_pairs=(frozenset((0, 1)),),
            proper_group_order=6,
        ),
        "square_pyramidal": ShapeTemplate(
            name="square_pyramidal",
            coordination_number=5,
            vertex_names=("apex", "+X", "+Y", "-X", "-Y"),
            vertices=_square_pyramidal_vertices(),
            opposite={1: 3, 3: 1, 2: 4, 4: 2},
            trans_pairs=(frozenset((1, 3)), frozenset((2, 4))),
            proper_group_order=4,
        ),
        "octahedral": ShapeTemplate(
            name="octahedral",
            coordination_number=6,
            vertex_names=("+X", "-X", "+Y", "-Y", "+Z", "-Z"),
            vertices=_octahedral_vertices(),
            opposite={0: 1, 1: 0, 2: 3, 3: 2, 4: 5, 5: 4},
            trans_pairs=(frozenset((0, 1)), frozenset((2, 3)), frozenset((4, 5))),
            proper_group_order=24,
        ),
        "trigonal_prismatic": ShapeTemplate(
            name="trigonal_prismatic",
            coordination_number=6,
            vertex_names=("A0", "A1", "A2", "B0", "B1", "B2"),
            vertices=_trigonal_prismatic_vertices(),
            opposite={},
            trans_pairs=(),
            proper_group_order=6,
        ),
    }


_TEMPLATES = _build_templates()

#: Coordination number per registered shape.
SHAPE_CN: dict[str, int] = {name: tpl.coordination_number for name, tpl in _TEMPLATES.items()}


def registered_shapes() -> tuple[str, ...]:
    """Return registered shape names in canonical order."""
    return tuple(_TEMPLATES)


def get_shape(name: str) -> ShapeTemplate:
    """Return the template for *name*, failing closed when unknown."""
    try:
        return _TEMPLATES[name]
    except KeyError as exc:
        raise ValueError(f"unknown coordination shape {name!r}") from exc


@cache
def proper_rotation_group(shape_name: str, tolerance: float = _TOL) -> tuple[tuple[int, ...], ...]:
    """Compute the proper rotation group as sorted vertex permutations.

    Every permutation is tested for realization by a proper rotation within
    *tolerance* RMSD.  Results are cached; computation is exact up to the
    stated tolerance, which callers audit.
    """
    template = get_shape(shape_name)
    verts = np.asarray(template.vertices, dtype=float)
    count = len(verts)
    group: list[tuple[int, ...]] = []
    for perm in itertools.permutations(range(count)):
        reordered = verts[list(perm)]
        rmsd, _ = kabsch_rmsd(verts, reordered)
        if rmsd <= tolerance:
            group.append(tuple(perm))
    return tuple(sorted(group))


def verify_group_axioms(group: tuple[tuple[int, ...], ...]) -> dict[str, bool | int]:
    """Verify identity, closure, and inverses for a permutation group."""
    members = set(group)
    order = len(group)
    if order == 0:
        return {"order": 0, "has_identity": False, "closed": False, "has_inverses": False}
    count = len(group[0])
    identity = tuple(range(count))
    has_identity = identity in members
    closed = True
    inverses = True
    for perm in group:
        inv = [0] * count
        for index, value in enumerate(perm):
            inv[value] = index
        if tuple(inv) not in members:
            inverses = False
        for other in group:
            composed = tuple(perm[other[index]] for index in range(count))
            if composed not in members:
                closed = False
                break
        if not closed:
            break
    return {
        "order": order,
        "has_identity": bool(has_identity),
        "closed": bool(closed),
        "has_inverses": bool(inverses),
    }


def shape_class_count(shape_name: str, distinct_sites: bool = True) -> int:
    """Return the labeled-assignment shape-class count (factorial / group order)."""
    template = get_shape(shape_name)
    count = math.factorial(template.coordination_number)
    if distinct_sites:
        count //= template.proper_group_order
    return count
