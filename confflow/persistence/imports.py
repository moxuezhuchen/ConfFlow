#!/usr/bin/env python3

"""Durable XYZ import identity maps for ConfFlow Workflow V4 (D-owned).

Binds one named run input to exact source bytes plus ordered opaque entity IDs; resume
of identical bytes reloads persisted IDs, reordered/edited bytes without explicit IDs
fail closed instead of guessing identity by geometry.
Layout ``<run_root>/imports/<input_name>.json``. Publication is atomic and arbitrated
via exclusive ``os.link`` so concurrent importers converge on one ID sequence; losers
adopt winner IDs (identical bytes parse identically; positional geometry digests still
verified). Maps are never overwritten: same digest reuses winner, different digest fails
closed. File bytes and directory both fsynced. Imports only ``confflow.domain`` + stdlib.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from typing import Any, Final

from ..domain.canonical import canonical_json_bytes
from .contracts import (
    CorruptStateError,
    PersistenceError,
    validate_run_root,
)
from .fsatomic import fsync_directory

__all__ = [
    "IMPORT_MAP_SCHEMA_VERSION",
    "load_import_map",
    "resolve_imported_structures",
    "save_import_map",
    "source_content_digest",
]

#: Version of the import-map payload shape. Any version skew fails closed.
IMPORT_MAP_SCHEMA_VERSION: Final[int] = 1

IMPORTS_DIRNAME: Final[str] = "imports"

_TMP_COUNTER = itertools.count()


def source_content_digest(text: str) -> str:
    """Return the content digest of raw XYZ source text.

    The digest covers the exact source bytes (UTF-8); formatting-only
    edits change the digest and therefore never silently reuse an identity
    map minted for different bytes.
    """
    if not isinstance(text, str):
        raise PersistenceError("source text must be a string")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _imports_dir(run_root: str) -> str:
    return os.path.join(validate_run_root(run_root), IMPORTS_DIRNAME)


def _map_path(run_root: str, input_name: str) -> str:
    if not isinstance(input_name, str) or not input_name or input_name != input_name.strip():
        raise PersistenceError("input_name must be a non-empty string")
    if input_name in (".", "..") or "/" in input_name or "\\" in input_name:
        raise PersistenceError(f"input_name must be a single path segment: {input_name!r}")
    return os.path.join(_imports_dir(run_root), f"{input_name}.json")


def _exclusive_publish(target_path: str, payload: bytes) -> bool:
    """Install *payload* at *target_path*, arbitrating concurrent writers."""
    directory = os.path.dirname(target_path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{target_path}.tmp.{os.getpid()}.{next(_TMP_COUNTER)}"
    with open(tmp_path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(tmp_path, target_path)
        except FileExistsError:
            won = False
        else:
            won = True
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    fsync_directory(directory)
    return won


def _checked_map(input_name: str, payload: Any) -> dict[str, Any]:
    """Validate a decoded import map payload, failing closed on any defect."""
    if not isinstance(payload, dict):
        raise CorruptStateError(f"import map for {input_name!r} must be a mapping")
    if payload.get("schema_version") != IMPORT_MAP_SCHEMA_VERSION:
        raise CorruptStateError(
            f"import map for {input_name!r} carries unsupported schema version "
            f"{payload.get('schema_version')!r}"
        )
    digest = payload.get("source_content_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise CorruptStateError(
            f"import map for {input_name!r} carries an invalid source_content_digest"
        )
    entity_ids = payload.get("entity_ids")
    geometry_digests = payload.get("geometry_digests")
    for key, value in (("entity_ids", entity_ids), ("geometry_digests", geometry_digests)):
        if not isinstance(value, list) or not value:
            raise CorruptStateError(f"import map for {input_name!r} carries invalid {key}")
        for position, entry in enumerate(value):
            if not isinstance(entry, str) or not entry or entry != entry.strip():
                raise CorruptStateError(
                    f"import map for {input_name!r} carries an invalid {key}[{position}]"
                )
    assert isinstance(entity_ids, list) and isinstance(geometry_digests, list)
    if len(entity_ids) != len(geometry_digests):
        raise CorruptStateError(
            f"import map for {input_name!r} has {len(entity_ids)} entity ids "
            f"but {len(geometry_digests)} geometry digests"
        )
    if len(set(entity_ids)) != len(entity_ids):
        raise CorruptStateError(f"import map for {input_name!r} carries duplicate entity ids")
    return payload


def save_import_map(
    *,
    run_root: str,
    input_name: str,
    source_name: str,
    source_text: str,
    structures: Any,
) -> dict[str, Any]:
    """Persist the identity map for one named structure input.

    The map is published with exclusive arbitration and never overwritten:
    when a map already exists with the same source digest the winner's
    entity IDs stand (newly minted UUIDs never replace them); when the
    digest differs the call fails closed because a changed import over the
    same run root is a new generation needing explicit migration.
    Returns the durable (winner's) map.
    """
    from ..domain.structure import StructureSet

    if not isinstance(structures, StructureSet):
        raise PersistenceError("structures must be a StructureSet")
    if not isinstance(source_name, str) or not source_name:
        raise PersistenceError("source_name must be a non-empty string")
    digest = source_content_digest(source_text)
    ordered = list(structures)
    if not ordered:
        raise PersistenceError("structures must not be empty")
    entity_ids = [record.id for record in ordered]
    if len(set(entity_ids)) != len(entity_ids):
        raise PersistenceError("imported structures carry duplicate entity ids")
    candidate = {
        "schema_version": IMPORT_MAP_SCHEMA_VERSION,
        "input_name": input_name,
        "source_name": source_name,
        "source_content_digest": digest,
        "entity_ids": list(entity_ids),
        "geometry_digests": [record.geometry_digest for record in ordered],
    }
    target = _map_path(run_root, input_name)
    _exclusive_publish(target, canonical_json_bytes(candidate))
    winner = load_import_map(run_root=run_root, input_name=input_name)
    assert winner is not None
    if winner["source_content_digest"] != digest:
        raise PersistenceError(
            f"import input {input_name!r} changed bytes (durable "
            f"{winner['source_content_digest']!r}, attempted {digest!r}); "
            "a changed import over the same run root is a new generation: "
            "use a fresh run root or explicit migration"
        )
    return winner


def load_import_map(*, run_root: str, input_name: str) -> dict[str, Any] | None:
    """Load the persisted identity map for *input_name*, if any.

    Corrupt maps raise :class:`CorruptStateError`; corruption is never
    reported as absence.
    """
    target = _map_path(run_root, input_name)
    try:
        with open(target, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CorruptStateError(f"cannot read import map for {input_name!r}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CorruptStateError(f"import map for {input_name!r} is not valid JSON: {exc}") from exc
    return _checked_map(input_name, payload)


def resolve_imported_structures(
    *,
    run_root: str,
    input_name: str,
    source_name: str,
    source_text: str,
    fresh: Any,
) -> Any:
    """Reconcile a fresh XYZ parse against the durable import map.

    The fresh parse is published through exclusive arbitration, then the
    winner's entity IDs are adopted positionally: identical source bytes
    parse identically, so no geometry guessing is involved, and the
    positional geometry digests are still verified.  Changed bytes fail
    closed.  Distinct duplicate geometries keep distinct IDs because IDs
    are positional, never content-derived.
    """
    from ..domain.structure import StructureRecord, StructureSet

    if not isinstance(fresh, StructureSet):
        raise PersistenceError("fresh must be a StructureSet")
    current = list(fresh)
    if not current:
        raise PersistenceError("fresh must not be empty")
    if len({record.id for record in current}) != len(current):
        raise PersistenceError("fresh structures carry duplicate entity ids")
    winner = save_import_map(
        run_root=run_root,
        input_name=input_name,
        source_name=source_name,
        source_text=source_text,
        structures=fresh,
    )
    persisted_ids: list[str] = list(winner["entity_ids"])
    persisted_geometries: list[str] = list(winner["geometry_digests"])
    if len(current) != len(persisted_ids):
        raise CorruptStateError(
            f"import input {input_name!r}: source bytes parsed to "
            f"{len(current)} blocks but the durable map holds {len(persisted_ids)}; "
            "refusing to guess"
        )
    for position, record in enumerate(current):
        if record.geometry_digest != persisted_geometries[position]:
            raise CorruptStateError(
                f"import input {input_name!r}: block {position} geometry does not match "
                "the durable map despite identical source bytes; refusing to guess"
            )
    rebuilt = []
    for position, record in enumerate(current):
        if record.id == persisted_ids[position]:
            rebuilt.append(record)
            continue
        rebuilt.append(
            StructureRecord(
                id=persisted_ids[position],
                atoms=record.atoms,
                coordinates=record.coordinates,
                charge=record.charge,
                multiplicity=record.multiplicity,
                parent_ids=record.parent_ids,
                lineage_root_id=persisted_ids[position],
                source_step_id=record.source_step_id,
                source_work_item_id=record.source_work_item_id,
                role=record.role,
                ordinal=record.ordinal,
                group_key=record.group_key,
                metadata=dict(record.metadata.thaw()),
                topology_patch=record.topology_patch,
                working_topology=record.working_topology,
            )
        )
    return StructureSet(tuple(rebuilt))
