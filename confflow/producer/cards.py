#!/usr/bin/env python3

"""Versioned calculation cards (Phase 3).

A card fixes the *scientific* shape of one step — executor, execution
adapter, result profile, acceptance checks and recovery — from an explicit
``<type>@<version>`` reference.  The human ``role`` defaults from the card
and is preserved verbatim into ``CalculationModel.role``; downstream lanes
may consume it scientifically (checkpoint method compatibility keys on the
rendered method, never on guessed keywords), so overriding it is advanced.
Native keywords are never inspected to guess card, role, checks, or
recovery.

No registry, parser or compiler import lives here: this module is pure
constants so :func:`confflow.producer.intent.intent_catalog` stays free of
compiler/contract recursion.  :mod:`confflow.producer.intent` resolves the
descriptors against the real execution registry at compile time.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = [
    "CARD_TYPES",
    "CARD_VERSION",
    "get_card",
    "parse_card_ref",
]

#: Version of the card table.  New scientific meanings get a new version;
#: existing ``<type>@v1`` references never change meaning.
CARD_VERSION: str = "v1"

_CARD_TABLE: dict[str, dict[str, Any]] = {
    "opt": {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination", "geometry_required"],
        "check_params": {},
        "recovery": "none",
        "default_role": "opt",
        "requires_explicit_bindings": False,
        "description": "Structure relaxation to a minimum.",
    },
    "sp": {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination"],
        "check_params": {},
        "recovery": "none",
        "default_role": "sp",
        "requires_explicit_bindings": False,
        "description": "Single energy evaluation on the input geometry.",
    },
    "freq": {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination", "frequencies_required"],
        "check_params": {},
        "recovery": "none",
        "default_role": "freq",
        "requires_explicit_bindings": False,
        "description": "Vibrational frequencies on the input geometry.",
    },
    "opt_freq": {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination", "geometry_required", "frequencies_required"],
        "check_params": {},
        "recovery": "none",
        "default_role": "opt_freq",
        "requires_explicit_bindings": False,
        "description": "Relaxation plus stationary-point confirmation.",
    },
    "ts": {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination", "geometry_required"],
        "check_params": {},
        "recovery": "none",
        "default_role": "ts",
        "requires_explicit_bindings": False,
        "description": "Saddle-point search (plain OptTS leg, no frequency output).",
    },
    "ts_freq": {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": [
            "normal_termination",
            "frequencies_required",
            "imaginary_frequency_count",
        ],
        "check_params": {"imaginary_frequency_count": {"expected": 1}},
        "recovery": "none",
        "default_role": "freq",
        "requires_explicit_bindings": False,
        "description": "Transition-state frequency leg expecting one imaginary mode.",
    },
    "confgen": {
        "executor": "confgen",
        "adapter": None,
        "profile": None,
        "checks": [],
        "check_params": {},
        "recovery": "none",
        "default_role": None,
        "requires_explicit_bindings": False,
        "description": "Typed conformer generation (v3 or legacy native).",
    },
    "refine": {
        "executor": "structure_transform",
        "adapter": None,
        "profile": None,
        "checks": [],
        "check_params": {},
        "recovery": "none",
        "default_role": None,
        "transform_kind": "refine",
        "requires_explicit_bindings": False,
        "description": "Topology-grouped RMSD refinement.",
    },
    "deduplicate": {
        "executor": "structure_transform",
        "adapter": None,
        "profile": None,
        "checks": [],
        "check_params": {},
        "recovery": "none",
        "default_role": None,
        "transform_kind": "deduplicate",
        "requires_explicit_bindings": False,
        "description": "Content-identity deduplication.",
    },
}

#: Frozen card-type vocabulary in deterministic order.
CARD_TYPES: tuple[str, ...] = tuple(sorted(_CARD_TABLE))


def parse_card_ref(ref: Any) -> tuple[str, str]:
    """Split a card reference into ``(type, version)``.

    Accepts ``"<type>@<version>"`` or ``{"type": ..., "version": ...}``.
    Mapping references reject unknown keys.  Raises ``ValueError`` on any
    malformed or unknown reference.
    """
    if isinstance(ref, dict):
        unknown = sorted(set(ref) - {"type", "version"})
        if unknown:
            raise ValueError(f"card reference carries unknown keys: {', '.join(unknown)}")
        card_type = ref.get("type")
        version = ref.get("version")
    elif isinstance(ref, str) and "@" in ref:
        card_type, _, version = ref.partition("@")
    else:
        raise ValueError(f"card must be '<type>@<version>' or a type/version mapping, got {ref!r}")
    if not isinstance(card_type, str) or not card_type.strip():
        raise ValueError(f"card type must be a non-empty string, got {card_type!r}")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"card version must be a non-empty string, got {version!r}")
    card_type = card_type.strip()
    version = version.strip()
    if version != CARD_VERSION:
        raise ValueError(
            f"unsupported card version {version!r}; this producer serves {CARD_VERSION!r}"
        )
    if card_type not in _CARD_TABLE:
        raise ValueError(f"unknown card type {card_type!r}; expected one of {sorted(_CARD_TABLE)}")
    return card_type, version


def get_card(card_type: str, version: str = CARD_VERSION) -> dict[str, Any]:
    """Return an isolated copy of one card descriptor."""
    if version != CARD_VERSION:
        raise ValueError(
            f"unsupported card version {version!r}; this producer serves {CARD_VERSION!r}"
        )
    try:
        return copy.deepcopy(_CARD_TABLE[card_type])
    except KeyError as exc:
        raise ValueError(
            f"unknown card type {card_type!r}; expected one of {sorted(_CARD_TABLE)}"
        ) from exc
