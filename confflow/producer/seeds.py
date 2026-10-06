#!/usr/bin/env python3

"""Hidden deterministic seeds (Phase 2).

A stochastic step without an explicit seed gets a stable seed derived by
SHA-256 over the JCS canonical bytes of the *whole seed-free scientific
workflow identity* — never Python ``hash()``, never a circular digest.
Scientific changes move the seed; operational changes do not.

Seed identity INCLUDES (per assigned step id):

- the step's program, card-determined adapter/profile/checks/recovery,
  native mapping, and charge/multiplicity/freeze overrides;
- the step's normalized per-item scientific resources
  (``cores_per_item``/``memory_per_item`` with schema fallbacks applied;
  memory normalizes through the real ``domain.resources.parse_memory_bytes``
  authority, so ``1GiB`` and ``1024MiB`` are the same scientific identity,
  and omitted dimensions equal explicit schema defaults);
- the step's scientific acceptance state: ``enabled`` (absent means
  ``True``) and ``completion`` (absent means the strict V4 defaults
  ``require_all``/``deny``) — both are digest-covered in
  ``fingerprint.definition_payload`` and therefore move seeds;
- whole-workflow scientific meaning: every step's seed-free science
  (program/native/checks/overrides/normalized resources/enabled/
  completion) plus bindings/upstream edges, projected sorted by stable
  step id so wire list order alone never moves a seed (the binding graph
  still captures intent linear order scientifically);
- the declared structure-input identity (names/kinds/cardinality/pairing
  with schema defaults applied, topology edge sets, and global
  scientific defaults) plus the stable assigned step ids.
  Input ``description`` text is display-only and excluded, as is input
  topology ``provenance`` (nonsemantic by ``TopologyPatch`` authority:
  only ``add``/``delete`` edge sets move the seed, canonicalized so
  ``[2, 1]`` equals ``[1, 2]``).

Seed identity EXCLUDES: all seeds, scheduler policy, machine/execution
bindings, annotations, labels, output paths.

GOAT boundary: the derived (or explicit) seed is materialized as
``CalculationModel.seed`` — workflow identity only.  It is never injected
into ``native.goat`` (ORCA ``RANDOMSEED`` is a boolean switch; installed
ORCA 6.1.1 exposes no integer RNG seed), and no conformer-level native
determinism is claimed.  Callers record
``annotations.producer_resolution.seed_scope = "workflow_identity_only"``.

Full-enumeration typed ConfGen (v3 without ``sampling.cap``) is
deterministic and gets no seed.  Legacy ConfGen and capped typed ConfGen
require a seed: an explicit step seed is preserved verbatim, otherwise one
is derived.  The strict V4 compiler remains the authority — a wrong
decision here fails closed there.
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
    """Normalize per-item scientific resources with schema fallbacks.

    Memory folds through the real ``domain.resources.parse_memory_bytes``
    authority so equivalent spellings share identity.  Unparseable values
    pass through raw; the strict compiler reports them authoritatively.
    """
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
    """Project one input topology onto semantic identity.

    The ``domain.TopologyPatch`` authority owns conversion from the wire
    aliases (``add``/``add_edges``, ``delete``/``delete_edges``,
    ``provenance``) to the scientific payload: ``provenance`` never enters
    the payload, edge orientation/order canonicalize, and a valid empty
    patch projects to ``None`` so absent and empty share identity.
    Returns the ``_INVALID_TOPOLOGY`` sentinel when the raw value cannot
    build a patch; the caller retains the raw value deterministically and
    the strict compiler refuses authoritatively.
    """
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
    """Project run-input declarations onto scientific identity.

    Verbatim passthrough except display-only ``description`` and
    nonsemantic topology ``provenance``, which never move the seed.
    Topology projects through the ``TopologyPatch`` authority
    (``to_payload``/``is_empty``): valid empty patches collapse to absent
    so ``None`` and ``{add: [], delete: [], provenance}`` share identity,
    while invalid shapes retain a deterministic raw copy (minus provenance)
    and the strict compiler refuses authoritatively.  Missing
    ``cardinality`` canonicalizes to the schema default ``"many"`` and a
    missing ``pairing`` to explicit ``None`` so omitted defaults share
    identity with explicit ones.
    """
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


def _calculation_native(step: Mapping[str, Any]) -> Mapping[str, Any] | None:
    block = step.get("calculation")
    if not isinstance(block, Mapping):
        return None
    native = block.get("native")
    return native if isinstance(native, Mapping) else None


def needs_seed(step: Mapping[str, Any]) -> bool:
    """Return whether the wire *step* is stochastic and requires a seed.

    Mirrors the strict validation authority
    (``workflow.v4.validation``: GOAT native mode, legacy ConfGen, typed v3
    ``sampling.cap``).  The compiler stays authoritative; this only decides
    where derivation applies.
    """
    executor = step.get("executor")
    if executor == "calculation":
        native = _calculation_native(step)
        if isinstance(native, Mapping) and native.get("goat") is not None:
            return True
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
    """Return the provenance seed scope for one wire step (verbatim C2 rule).

    Moved verbatim from ``producer.intent.compiler`` (L1-A2a plumbing only):
    ``"none"`` source yields ``None``; confgen yields ``"native_sampling"``;
    calculation with a ``goat`` native mapping yields
    ``"workflow_identity_only"``; otherwise ``None``.  No derivation,
    tolerance, or fixture rule is changed here; GOAT science judgment stays
    in this seeds authority (mirroring :func:`needs_seed`), never copied
    into a new intent table.
    """
    if source == "none":
        return None
    executor = step.get("executor")
    if executor == "confgen":
        return "native_sampling"
    if executor == "calculation":
        calculation = step.get("calculation")
        native = calculation.get("native") if isinstance(calculation, Mapping) else None
        if isinstance(native, Mapping) and native.get("goat") is not None:
            return "workflow_identity_only"
    return None


def assign_seeds(
    wire_document: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Assign derived seeds where required; preserve explicit seeds.

    Returns ``(document, provenance)`` where provenance maps step id to
    ``{"seed": int|None, "source": "explicit"|"derived"|"none",
    "seed_version": SEED_VERSION}``.  An explicit seed is always recorded
    as ``"explicit"`` and kept verbatim in the wire — even on steps that
    are not stochastically required (e.g. an uncapped typed ConfGen with
    a user seed).  GOAT seeds land on ``calculation.seed`` only — native
    mappings are never touched.
    """
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
