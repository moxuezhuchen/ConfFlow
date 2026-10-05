#!/usr/bin/env python3

"""ConfGen v3 isolated-ring science lane (ring worker owned).

Pure geometric templates, perception, and realization for isolated covalent
4/5/6-membered rings, plus the ``RingStage`` adapter to the shared core stage
protocol. Dependencies: stdlib + NumPy (+ frozen core model/planner for the
stage adapter only).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .geometry import ring_torsions as ring_torsions
    from .perception import (
        RingPerception as RingPerception,
    )
    from .perception import (
        commanded_state_dict as commanded_state_dict,
    )
    from .perception import (
        perceive_ring as perceive_ring,
    )
    from .perception import (
        ring_diagnostics as ring_diagnostics,
    )
    from .perception import (
        ring_state_dict as ring_state_dict,
    )
    from .perception import (
        ring_states_match as ring_states_match,
    )
    from .realization import (
        RingGeometryFailure as RingGeometryFailure,
    )
    from .realization import (
        RingNumericalFailure as RingNumericalFailure,
    )
    from .realization import (
        RingRealizeOutput as RingRealizeOutput,
    )
    from .realization import (
        RingSpec as RingSpec,
    )
    from .realization import (
        RingTolerances as RingTolerances,
    )
    from .realization import (
        RingUnsupported as RingUnsupported,
    )
    from .realization import (
        parse_ring_specs as parse_ring_specs,
    )
    from .realization import (
        realize_rings as realize_rings,
    )
    from .realization import (
        realize_single_system as realize_single_system,
    )
    from .realization import (
        validate_ring_system as validate_ring_system,
    )
    from .stage import BACKEND_NAME as BACKEND_NAME
    from .stage import RingStage as RingStage
    from .templates import (
        BOND_LENGTH as BOND_LENGTH,
    )
    from .templates import (
        TEMPLATE_REGISTRY as TEMPLATE_REGISTRY,
    )
    from .templates import (
        TEMPLATES_BY_SIZE as TEMPLATES_BY_SIZE,
    )
    from .templates import (
        RingTemplate as RingTemplate,
    )
    from .templates import (
        get_template as get_template,
    )
    from .templates import (
        template_bond_spread as template_bond_spread,
    )
    from .templates import (
        template_coords as template_coords,
    )
    from .templates import (
        template_torsions as template_torsions,
    )
    from .templates import (
        templates_for_size as templates_for_size,
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

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BACKEND_NAME": (".stage", "BACKEND_NAME"),
    "BOND_LENGTH": (".templates", "BOND_LENGTH"),
    "RingPerception": (".perception", "RingPerception"),
    "RingRealizeOutput": (".realization", "RingRealizeOutput"),
    "RingSpec": (".realization", "RingSpec"),
    "RingStage": (".stage", "RingStage"),
    "RingTemplate": (".templates", "RingTemplate"),
    "RingTolerances": (".realization", "RingTolerances"),
    "RingGeometryFailure": (".realization", "RingGeometryFailure"),
    "RingNumericalFailure": (".realization", "RingNumericalFailure"),
    "RingUnsupported": (".realization", "RingUnsupported"),
    "TEMPLATE_REGISTRY": (".templates", "TEMPLATE_REGISTRY"),
    "TEMPLATES_BY_SIZE": (".templates", "TEMPLATES_BY_SIZE"),
    "commanded_state_dict": (".perception", "commanded_state_dict"),
    "get_template": (".templates", "get_template"),
    "parse_ring_specs": (".realization", "parse_ring_specs"),
    "perceive_ring": (".perception", "perceive_ring"),
    "realize_rings": (".realization", "realize_rings"),
    "realize_single_system": (".realization", "realize_single_system"),
    "ring_diagnostics": (".perception", "ring_diagnostics"),
    "ring_state_dict": (".perception", "ring_state_dict"),
    "ring_states_match": (".perception", "ring_states_match"),
    "ring_torsions": (".geometry", "ring_torsions"),
    "template_bond_spread": (".templates", "template_bond_spread"),
    "template_coords": (".templates", "template_coords"),
    "template_torsions": (".templates", "template_torsions"),
    "templates_for_size": (".templates", "templates_for_size"),
    "validate_ring_system": (".realization", "validate_ring_system"),
}


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = export
    module = importlib.import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
