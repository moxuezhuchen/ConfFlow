#!/usr/bin/env python3
"""R6 frozen ring inputs (test-only, DRAFT rehearsal).

Byte-identical copies of ``ring/templates.py`` Cartesian coordinates at base
``ccf167f``.  No old realization implementation is moved here and no CP
geometry (``cp_to_coords``) is used: helpers return the exact same float
arrays the old ``template_coords(get_template(name))`` produced, so engine
input values/hashes are unchanged.

Source: ``confflow/science/confgen/ring/templates.py`` ``_planar_4`` ..
``_twist_boat_6`` via ``_centered`` at base HEAD.  Literals below are
``repr`` of the centered ``(n, 3)`` lists (shortest round-trip repr, hence
bit-identical on ``float()`` parse).  ``frozen_coords`` returns a fresh
``(n, 3)`` ``float`` array per call (same semantics as old
``template_coords``); ``frozen_torsions`` measures via
``geometry.ring_torsions`` (same as old ``template_torsions`` without the
registry import).
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "FROZEN_TEMPLATE_NAMES",
    "frozen_coords",
    "frozen_coordinates",
    "frozen_torsions",
]

planar_4 = [[0.77, 0.77, 0.0], [-0.77, 0.77, 0.0], [-0.77, -0.77, 0.0], [0.77, -0.77, 0.0]]
pucker_up_4 = [
    [-1.068082393825495, 0.0, -0.15],
    [0.0, 1.068082393825495, 0.15],
    [1.068082393825495, 0.0, -0.15],
    [0.0, -1.068082393825495, 0.15],
]
pucker_down_4 = [
    [-1.068082393825495, 0.0, 0.15],
    [0.0, 1.068082393825495, -0.15],
    [1.068082393825495, 0.0, 0.15],
    [0.0, -1.068082393825495, -0.15],
]
planar_5 = [
    [1.3100022448621416, 4.4408920985006264e-17, 0.0],
    [0.40481295633173303, 1.245886171337419, 0.0],
    [-1.0598140787628036, 0.7700000000000002, 0.0],
    [-1.0598140787628039, -0.7699999999999999, 0.0],
    [0.40481295633173275, -1.2458861713374192, 0.0],
]
envelope_5 = [
    [1.1895024476518716, 4.4408920985006264e-17, 0.4],
    [0.4349379056343005, 1.245886171337419, -0.1],
    [-1.0296891294602362, 0.7700000000000002, -0.1],
    [-1.0296891294602364, -0.7699999999999999, -0.1],
    [0.43493790563430024, -1.2458861713374192, -0.1],
]
twist_5 = [
    [1.1742827178374182, 0.0, 0.32],
    [0.4182403909799861, 1.2, -0.28],
    [-1.005381749898695, 0.77, 0.12],
    [-1.005381749898695, -0.77, 0.12],
    [0.4182403909799861, -1.2, -0.28],
]
chair_A_6 = [
    [1.4519259240363775, -7.401486830834377e-17, 0.25666666666666665],
    [0.7259629620181889, 1.257404734628698, -0.25666666666666665],
    [-0.7259629620181884, 1.257404734628698, 0.25666666666666665],
    [-1.4519259240363775, 1.0379477523867753e-16, -0.25666666666666665],
    [-0.7259629620181894, -1.2574047346286976, 0.25666666666666665],
    [0.7259629620181889, -1.257404734628698, -0.25666666666666665],
]
chair_B_6 = [
    [1.4519259240363775, -7.401486830834377e-17, -0.25666666666666665],
    [0.7259629620181889, 1.257404734628698, 0.25666666666666665],
    [-0.7259629620181884, 1.257404734628698, -0.25666666666666665],
    [-1.4519259240363775, 1.0379477523867753e-16, 0.25666666666666665],
    [-0.7259629620181894, -1.2574047346286976, -0.25666666666666665],
    [0.7259629620181889, -1.257404734628698, 0.25666666666666665],
]
boat_6 = [
    [1.7130535509715235, 0.0, 0.628702367314349],
    [0.77, 0.77, -0.3143511836571745],
    [-0.77, 0.77, -0.3143511836571745],
    [-1.7130535509715235, 0.0, 0.628702367314349],
    [-0.77, -0.77, -0.3143511836571745],
    [0.77, -0.77, -0.3143511836571745],
]
twist_boat_6 = [
    [0.0, 0.6, 0.48259714048054614],
    [0.0, -0.6, -0.48259714048054614],
    [1.3336791218280355, 0.0, 0.0],
    [0.0, 0.6, -0.48259714048054614],
    [0.0, -0.6, 0.48259714048054614],
    [-1.3336791218280355, 0.0, 0.0],
]

_FROZEN: dict[str, list[list[float]]] = {
    "planar_4": planar_4,
    "pucker_up_4": pucker_up_4,
    "pucker_down_4": pucker_down_4,
    "planar_5": planar_5,
    "envelope_5": envelope_5,
    "twist_5": twist_5,
    "chair_A_6": chair_A_6,
    "chair_B_6": chair_B_6,
    "boat_6": boat_6,
    "twist_boat_6": twist_boat_6,
}

FROZEN_TEMPLATE_NAMES: tuple[str, ...] = tuple(_FROZEN)


def frozen_coords(name: str) -> np.ndarray:
    """Return frozen ``(n, 3)`` coordinates for *name* (fresh array)."""
    try:
        rows = _FROZEN[str(name)]
    except KeyError as exc:
        raise KeyError(str(name)) from exc
    return np.asarray(rows, dtype=float)


def frozen_coordinates(name: str) -> tuple[tuple[float, float, float], ...]:
    """Return frozen coordinates as nested tuples (``.coordinates`` shape)."""
    try:
        rows = _FROZEN[str(name)]
    except KeyError as exc:
        raise KeyError(str(name)) from exc
    return tuple(tuple(float(v) for v in row) for row in rows)


def frozen_torsions(name: str) -> tuple[float, ...]:
    """Return endocyclic torsions of the frozen template (traversal order)."""
    from confflow.science.confgen.ring.geometry import ring_torsions

    return tuple(float(v) for v in ring_torsions(frozen_coords(name)))
