#!/usr/bin/env python3

"""ConfGen v3 isolated-ring science lane (ring worker owned).

CP-constrained perception and realization for isolated covalent
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
        validate_ring_system as validate_ring_system,
    )
    from .stage import BACKEND_NAME as BACKEND_NAME
    from .stage import RingStage as RingStage

__all__ = [
    "BACKEND_NAME",
    "RingPerception",
    "RingSpec",
    "RingStage",
    "RingTolerances",
    "RingGeometryFailure",
    "RingNumericalFailure",
    "RingUnsupported",
    "commanded_state_dict",
    "parse_ring_specs",
    "perceive_ring",
    "ring_diagnostics",
    "ring_state_dict",
    "ring_states_match",
    "ring_torsions",
    "validate_ring_system",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BACKEND_NAME": (".stage", "BACKEND_NAME"),
    "RingPerception": (".perception", "RingPerception"),
    "RingSpec": (".realization", "RingSpec"),
    "RingStage": (".stage", "RingStage"),
    "RingTolerances": (".realization", "RingTolerances"),
    "RingGeometryFailure": (".realization", "RingGeometryFailure"),
    "RingNumericalFailure": (".realization", "RingNumericalFailure"),
    "RingUnsupported": (".realization", "RingUnsupported"),
    "commanded_state_dict": (".perception", "commanded_state_dict"),
    "parse_ring_specs": (".realization", "parse_ring_specs"),
    "perceive_ring": (".perception", "perceive_ring"),
    "ring_diagnostics": (".perception", "ring_diagnostics"),
    "ring_state_dict": (".perception", "ring_state_dict"),
    "ring_states_match": (".perception", "ring_states_match"),
    "ring_torsions": (".geometry", "ring_torsions"),
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
