#!/usr/bin/env python3

"""Versioned Refine/Dedup scientific presets (Phase 7).

Presets select the ``native`` vocabulary of an explicit structure-transform
step.  They never invent execution semantics: keys are the documented
transform-native vocabulary of :mod:`confflow.execution.transform_executor`
(``rmsd_threshold_angstrom``, ``bond_scale``, ``heavy_only``,
``max_structures`` for ``refine``; nothing for ``deduplicate``).  There is
no ``energy_window`` preset member — the V4 transform carries only structure
inputs, so that field is omitted rather than emitted as a backend-invalid
member or interpreted by a second runtime.

Pure constants only, so ``intent_catalog`` stays free of compiler/contract
recursion.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = [
    "PRESET_TYPES",
    "PRESET_VERSION",
    "get_preset",
    "parse_preset_ref",
]

#: Version of the preset table.
PRESET_VERSION: str = "v1"

_PRESET_TABLE: dict[str, dict[str, Any]] = {
    "refine_default": {
        "card": "refine",
        "native": {
            "rmsd_threshold_angstrom": 0.25,
            "bond_scale": 1.2,
            "heavy_only": False,
        },
        "description": "Pinned RMSD 0.25 A, bond scale 1.2, all atoms.",
    },
    "refine_strict": {
        "card": "refine",
        "native": {
            "rmsd_threshold_angstrom": 0.1,
            "bond_scale": 1.2,
            "heavy_only": False,
        },
        "description": "Pinned tighter RMSD witness (0.10 A); otherwise default.",
    },
    "dedup_default": {
        "card": "deduplicate",
        "native": {},
        "description": "Exact content-identity collapse within scientific groups.",
    },
}

#: Frozen preset vocabulary in deterministic order.
PRESET_TYPES: tuple[str, ...] = tuple(sorted(_PRESET_TABLE))


def parse_preset_ref(ref: Any) -> tuple[str, str]:
    """Split a preset reference into ``(name, version)``."""
    if isinstance(ref, dict):
        unknown = sorted(set(ref) - {"name", "type", "preset", "version"})
        if unknown:
            raise ValueError(f"preset reference carries unknown keys: {', '.join(unknown)}")
        name = ref.get("name", ref.get("type", ref.get("preset")))
        version = ref.get("version")
    elif isinstance(ref, str) and "@" in ref:
        name, _, version = ref.partition("@")
    else:
        raise ValueError(
            f"preset must be '<name>@<version>' or a name/version mapping, got {ref!r}"
        )
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"preset name must be a non-empty string, got {name!r}")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"preset version must be a non-empty string, got {version!r}")
    name = name.strip()
    version = version.strip()
    if version != PRESET_VERSION:
        raise ValueError(
            f"unsupported preset version {version!r}; this producer serves {PRESET_VERSION!r}"
        )
    if name not in _PRESET_TABLE:
        raise ValueError(f"unknown preset {name!r}; expected one of {sorted(_PRESET_TABLE)}")
    return name, version


def get_preset(name: str, version: str = PRESET_VERSION) -> dict[str, Any]:
    """Return an isolated copy of one preset descriptor."""
    if version != PRESET_VERSION:
        raise ValueError(
            f"unsupported preset version {version!r}; this producer serves {PRESET_VERSION!r}"
        )
    try:
        return copy.deepcopy(_PRESET_TABLE[name])
    except KeyError as exc:
        raise ValueError(
            f"unknown preset {name!r}; expected one of {sorted(_PRESET_TABLE)}"
        ) from exc
