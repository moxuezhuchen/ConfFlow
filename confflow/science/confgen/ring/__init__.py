#!/usr/bin/env python3

"""ConfGen v3 isolated-ring science lane (ring worker owned).

Pure geometric templates, perception, and realization for isolated covalent
4/5/6-membered rings, plus the ``RingStage`` adapter to the shared core stage
protocol. Dependencies: stdlib + NumPy (+ frozen core model/planner for the
stage adapter only).
"""

from __future__ import annotations

from .geometry import ring_torsions
from .perception import (
    RingPerception,
    commanded_state_dict,
    perceive_ring,
    ring_diagnostics,
    ring_state_dict,
    ring_states_match,
)
from .realization import (
    RingGeometryFailure,
    RingNumericalFailure,
    RingRealizeOutput,
    RingSpec,
    RingTolerances,
    RingUnsupported,
    parse_ring_specs,
    realize_rings,
    realize_single_system,
    validate_ring_system,
)
from .stage import BACKEND_NAME, RingStage
from .templates import (
    BOND_LENGTH,
    TEMPLATE_REGISTRY,
    TEMPLATES_BY_SIZE,
    RingTemplate,
    get_template,
    template_bond_spread,
    template_coords,
    template_torsions,
    templates_for_size,
)

__all__ = [
    "BACKEND_NAME",
    "BOND_LENGTH",
    "RingPerception",
    "RingRealizeOutput",
    "RingSpec",
    "RingStage",
    "RingTemplate",
    "RingTolerances",
    "RingGeometryFailure",
    "RingNumericalFailure",
    "RingUnsupported",
    "TEMPLATE_REGISTRY",
    "TEMPLATES_BY_SIZE",
    "commanded_state_dict",
    "get_template",
    "parse_ring_specs",
    "perceive_ring",
    "realize_rings",
    "realize_single_system",
    "ring_diagnostics",
    "ring_state_dict",
    "ring_states_match",
    "ring_torsions",
    "template_bond_spread",
    "template_coords",
    "template_torsions",
    "templates_for_size",
    "validate_ring_system",
]
