#!/usr/bin/env python3

"""Hidden deterministic seeds (Phase 2).

Stochastic step without explicit seed gets stable seed derived by SHA-256 over JCS canonical bytes of the whole seed-free scientific workflow identity (never Python ``hash()``, never circular digest); scientific changes move the seed, operational changes do not; explicit step seed preserved verbatim; strict V4 compiler remains authority, wrong decision fails closed there.
Identity INCLUDES per assigned step id: program, card-determined adapter/profile/checks/recovery, native mapping, charge/multiplicity/freeze overrides; normalized per-item resources (cores_per_item/memory_per_item with schema fallbacks via ``domain.resources.parse_memory_bytes``, so 1GiB equals 1024MiB, omitted equals defaults); scientific acceptance state ``enabled`` (absent True) and ``completion`` (absent require_all/deny, digest-covered); whole-workflow science plus bindings/upstream edges sorted by stable step id (wire order never moves seed); declared structure-input identity (names/kinds/cardinality/pairing with defaults, topology edge sets, global scientific defaults, stable ids); topology provenance excluded, only add/delete edge sets move seed canonicalized so [2,1] equals [1,2]; input description excluded.
Identity EXCLUDES: all seeds, scheduler policy, machine/execution bindings, annotations, labels, output paths. Full-enumeration typed ConfGen v3 without sampling.cap is deterministic and gets no seed; legacy ConfGen and capped typed ConfGen require a seed.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

from ..domain.canonical import canonical_json_bytes

__all__ = [
    "SEED_DIGEST_KIND",
    "SEED_VERSION",
    "assign_seeds",
    "derive_seed",
    "needs_seed",
    "seed_identity_for_step",
    "seed_scope_for_step",
]

#: Version of the seed derivation rule.
SEED_VERSION: str = "v1"

#: Domain separation for the derivation digest (never mixed with step digests).
SEED_DIGEST_KIND: str = "confflow.producer.seed.v1"

_DEFAULT_CORES = 1
_DEFAULT_MEMORY = "1GiB"

#: Sentinel for a topology value that cannot build a ``TopologyPatch``.
_INVALID_TOPOLOGY: Any = object()


def _normalize_resources(resources: Any) -> dict[str, Any]:
    """Normalize per-item scientific resources with schema fallbacks."""
    cores = _DEFAULT_CORES
    memory_bytes: Any = None
    raw_memory: Any = _DEFAULT_MEMORY
    if isinstance(resources, Mapping):
        if resources.get("cores_per_item") is not None:
            cores = resources["cores_per_item"]
        if resources.get("memory_per_item") is not None:
            raw_memory = resources["memory_per_item"]
    try:
        from ..domain.resources import parse_memory_bytes

        memory_bytes = parse_memory_bytes(
            raw_memory if not isinstance(raw_memory, bool) else _DEFAULT_MEMORY
        )
    except Exception:
        memory_bytes = raw_memory
    if not isinstance(cores, int) or isinstance(cores, bool):
        return {"cores_per_item": cores, "memory_per_item_bytes": memory_bytes}
    return {"cores_per_item": cores, "memory_per_item_bytes": memory_bytes}


def _canonical_topology(raw: Any) -> Any:
    """Project one input topology onto semantic identity."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return _INVALID_TOPOLOGY
    allowed = {"add", "delete", "add_edges", "delete_edges", "provenance"}
    if sorted(set(raw) - allowed):
        return _INVALID_TOPOLOGY
    if "add" in raw:
        add_raw = raw.get("add")
    elif "add_edges" in raw:
        add_raw = raw.get("add_edges")
    else:
        add_raw = ()
    if "delete" in raw:
        delete_raw = raw.get("delete")
    elif "delete_edges" in raw:
        delete_raw = raw.get("delete_edges")
    else:
        delete_raw = ()
    provenance_raw = raw.get("provenance", "")
    try:
        from ..domain.topology import TopologyPatch
    except ImportError:
        return _INVALID_TOPOLOGY
    try:
        patch = TopologyPatch(
            add_edges=() if add_raw is None else add_raw,
            delete_edges=() if delete_raw is None else delete_raw,
            provenance=provenance_raw,
        )
    except Exception:
        return _INVALID_TOPOLOGY
    if patch.is_empty:
        return None
    return patch.to_payload()


def _seed_inputs(inputs: Any) -> dict[str, Any]:
    """Project run-input declarations onto scientific identity."""
    if not isinstance(inputs, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for name, declaration in inputs.items():
        if isinstance(declaration, Mapping):
            cleaned = {key: copy.deepcopy(value) for key, value in declaration.items()}
            cleaned.pop("description", None)
            topology = cleaned.pop("topology", None)
            canonical = _canonical_topology(topology) if topology is not None else None
            if canonical is None:
                pass
            elif canonical is _INVALID_TOPOLOGY:
                # Invalid topology: retain deterministically (minus the
                # nonsemantic provenance) and let strict compilation fail.
                if isinstance(topology, Mapping):
                    kept = {key: copy.deepcopy(value) for key, value in topology.items()}
                    kept.pop("provenance", None)
                    if kept:
                        cleaned["topology"] = kept
                else:
                    cleaned["topology"] = copy.deepcopy(topology)
            else:
                cleaned["topology"] = canonical
            if "cardinality" not in cleaned or cleaned["cardinality"] is None:
                cleaned["cardinality"] = "many"
            if "pairing" not in cleaned:
                cleaned["pairing"] = None
            projected[str(name)] = cleaned
        else:
            projected[str(name)] = copy.deepcopy(declaration)
    return projected


def _canonical_completion(raw: Any) -> dict[str, Any]:
    """Normalize one wire ``completion`` mapping onto V4 defaults."""
    defaults: dict[str, Any] = {
        "mode": "require_all",
        "minimum_success": None,
        "partial_output": "deny",
    }
    if not isinstance(raw, Mapping):
        return defaults
    merged = dict(defaults)
    for key in ("mode", "minimum_success", "partial_output"):
        if key in raw:
            merged[key] = copy.deepcopy(raw[key])
    return merged


def _seed_free_step_science(step: Mapping[str, Any]) -> dict[str, Any]:
    """Project one wire step onto its seed-free scientific identity."""
    science: dict[str, Any] = {"id": step.get("id"), "executor": step.get("executor")}
    for block_name in ("calculation", "confgen", "transform", "analysis"):
        block = step.get(block_name)
        if not isinstance(block, Mapping):
            continue
        block_copy = copy.deepcopy(dict(block))
        block_copy.pop("seed", None)
        if block_name == "calculation":
            recovery = block_copy.get("recovery")
            if isinstance(recovery, Mapping):
                recovery_copy = dict(recovery)
                recovery_copy.pop("seed", None)
                block_copy["recovery"] = recovery_copy
        science[block_name] = block_copy
    science["resources"] = _normalize_resources(step.get("resources"))
    bindings = step.get("bindings")
    science["bindings"] = copy.deepcopy(dict(bindings)) if isinstance(bindings, Mapping) else {}
    enabled = step.get("enabled", True)
    science["enabled"] = bool(enabled) if isinstance(enabled, bool) else copy.deepcopy(enabled)
    science["completion"] = _canonical_completion(step.get("completion"))
    return science


def seed_identity_for_step(step_id: str, wire_document: Mapping[str, Any]) -> dict[str, Any]:
    """Build the whole-workflow seed-free identity provisioning *step_id*."""
    steps = wire_document.get("steps")
    step_list = list(steps) if isinstance(steps, (list, tuple)) else []
    global_block = wire_document.get("global")
    scientific_defaults: Any = {}
    if isinstance(global_block, Mapping):
        scientific_defaults = copy.deepcopy(global_block.get("scientific_defaults", {}))
    projected = [_seed_free_step_science(step) for step in step_list if isinstance(step, Mapping)]
    projected.sort(key=lambda item: str(item.get("id")))
    return {
        "seed_version": SEED_VERSION,
        "step_id": step_id,
        "inputs": _seed_inputs(wire_document.get("inputs")),
        "scientific_defaults": scientific_defaults,
        "steps": projected,
    }


def derive_seed(step_id: str, wire_document: Mapping[str, Any]) -> int:
    """Derive a stable positive seed for *step_id* from the wire identity."""
    identity = seed_identity_for_step(step_id, wire_document)
    digest = hashlib.sha256(canonical_json_bytes(identity)).digest()
    value = int.from_bytes(digest[:8], "big") % 2147483647
    return value if value != 0 else 1


def _confgen_block(step: Mapping[str, Any]) -> Mapping[str, Any] | None:
    block = step.get("confgen")
    return block if isinstance(block, Mapping) else None


def needs_seed(step: Mapping[str, Any]) -> bool:
    """Return whether the wire *step* is stochastic and requires a seed."""
    executor = step.get("executor")
    if executor == "calculation":
        return False
    if executor == "confgen":
        block = _confgen_block(step)
        if block is None:
            return False
        if block.get("schema_version") == 3:
            sampling = block.get("sampling")
            return isinstance(sampling, Mapping) and sampling.get("cap") is not None
        return True
    return False


def _current_seed(step: Mapping[str, Any]) -> Any:
    executor = step.get("executor")
    if executor == "calculation":
        block = step.get("calculation")
        if isinstance(block, Mapping):
            return block.get("seed")
    elif executor == "confgen":
        block = step.get("confgen")
        if isinstance(block, Mapping):
            return block.get("seed")
    return None


def _set_seed(step: dict[str, Any], seed: int) -> None:
    executor = step.get("executor")
    if executor == "calculation":
        block = step.get("calculation")
        if isinstance(block, dict):
            block["seed"] = seed
    elif executor == "confgen":
        block = step.get("confgen")
        if isinstance(block, dict):
            block["seed"] = seed


def seed_scope_for_step(step: Mapping[str, Any], source: str) -> str | None:
    """Return the provenance seed scope for one wire step (verbatim C2 rule)."""
    if source == "none":
        return None
    executor = step.get("executor")
    if executor == "confgen":
        return "native_sampling"
    return None


def assign_seeds(
    wire_document: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Assign derived seeds where required; preserve explicit seeds."""
    document = copy.deepcopy(dict(wire_document))
    raw_steps = document.get("steps")
    steps = list(raw_steps) if isinstance(raw_steps, (list, tuple)) else []
    provenance: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(steps):
        if not isinstance(raw, Mapping):
            continue
        step = copy.deepcopy(dict(raw))
        step_id = str(step.get("id"))
        existing = _current_seed(step)
        if not needs_seed(step):
            if existing is not None:
                provenance[step_id] = {
                    "seed": existing,
                    "source": "explicit",
                    "seed_version": SEED_VERSION,
                }
            else:
                provenance[step_id] = {
                    "seed": None,
                    "source": "none",
                    "seed_version": SEED_VERSION,
                }
            steps[index] = step
            continue
        if existing is not None:
            provenance[step_id] = {
                "seed": existing,
                "source": "explicit",
                "seed_version": SEED_VERSION,
            }
            steps[index] = step
            continue
        derived = derive_seed(step_id, document)
        _set_seed(step, derived)
        steps[index] = step
        # Re-read so later steps derive over the updated seed-free identity
        # (identity itself excludes seeds, so order is stable regardless).
        document["steps"] = steps
        provenance[step_id] = {
            "seed": derived,
            "source": "derived",
            "seed_version": SEED_VERSION,
        }
    document["steps"] = steps
    return document, provenance
