#!/usr/bin/env python3

"""Ring lightweight constants (FIX-1A A5, stdlib only).

Single authority for the template-name table needed by schema/contract.
``ring/templates.py`` re-exports the same object (``is`` identical) so
historic paths keep working against one source. Only name tables live
here; geometry/registry objects stay in ``templates.py``.
"""

from __future__ import annotations

__all__ = [
    "TEMPLATES_BY_SIZE",
]

#: Templates per ring size, in stable declaration order.
TEMPLATES_BY_SIZE: dict[int, tuple[str, ...]] = {
    4: ("planar_4", "pucker_up_4", "pucker_down_4"),
    5: ("planar_5", "envelope_5", "twist_5"),
    6: ("chair_A_6", "chair_B_6", "boat_6", "twist_boat_6"),
}
