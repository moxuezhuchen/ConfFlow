#!/usr/bin/env python3

"""XYZ text import shared by run inputs and script outputs.

Pure structural parsing: XYZ text becomes a :class:`StructureSet` with
opaque identities. No workflow or application knowledge lives here.
"""

from __future__ import annotations

import uuid

from ..domain._immutable import FrozenDict
from ..domain.errors import DomainError
from ..domain.structure import StructureRecord, StructureSet

__all__ = ["import_xyz"]


def import_xyz(
    text: str,
    *,
    source_name: str = "<input>",
    entity_ids: tuple[str, ...] | list[str] | None = None,
) -> StructureSet:
    """Parse XYZ text into a :class:`StructureSet` with opaque identities."""
    if not isinstance(text, str) or not text.strip():
        raise DomainError(f"XYZ input from {source_name!r} must be non-empty text")
    blocks = _split_xyz_blocks(text)
    if not blocks:
        raise DomainError(f"XYZ input from {source_name!r} carries no molecule blocks")
    if entity_ids is not None:
        supplied = tuple(entity_ids)
        if len(supplied) != len(blocks):
            raise DomainError(
                f"XYZ input from {source_name!r} carries {len(blocks)} blocks "
                f"but {len(supplied)} explicit entity ids were supplied"
            )
        seen: set[str] = set()
        for position, candidate in enumerate(supplied):
            if not isinstance(candidate, str) or not candidate or candidate != candidate.strip():
                raise DomainError(
                    f"XYZ input from {source_name!r} entity_ids[{position}] "
                    "must be a non-empty string"
                )
            if candidate in seen:
                raise DomainError(
                    f"XYZ input from {source_name!r} carries a duplicate entity id {candidate!r}"
                )
            seen.add(candidate)
        resolved_ids = supplied
    else:
        resolved_ids = tuple(uuid.uuid4().hex for _ in blocks)
    records = []
    for index, ((count, rows), entity_id) in enumerate(zip(blocks, resolved_ids)):
        atoms: list[str] = []
        coords: list[tuple[float, float, float]] = []
        for row in rows:
            parts = row.split()
            if len(parts) < 4:
                raise DomainError(
                    f"XYZ input from {source_name!r} block {index} has a malformed row"
                )
            try:
                point = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as exc:
                raise DomainError(
                    f"XYZ input from {source_name!r} block {index} has non-numeric coordinates"
                ) from exc
            atoms.append(parts[0])
            coords.append(point)
        if len(atoms) != count:
            raise DomainError(
                f"XYZ input from {source_name!r} block {index} declares "
                f"{count} atoms but carries {len(atoms)}"
            )
        records.append(
            StructureRecord(
                id=entity_id,
                atoms=tuple(atoms),
                coordinates=tuple(coords),
                metadata=FrozenDict({"import_source": source_name}),
            )
        )
    return StructureSet.of(*records)


def _split_xyz_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Split XYZ text into ``(count, rows)`` molecule blocks."""
    lines = [line.strip() for line in text.splitlines()]
    blocks: list[tuple[int, list[str]]] = []
    cursor = 0
    while cursor < len(lines):
        while cursor < len(lines) and not lines[cursor]:
            cursor += 1
        if cursor >= len(lines):
            break
        try:
            count = int(lines[cursor].split()[0])
        except (ValueError, IndexError) as exc:
            raise DomainError(f"XYZ block at line {cursor + 1} needs an atom count") from exc
        if count <= 0:
            raise DomainError(f"XYZ block at line {cursor + 1} has no atoms")
        cursor += 2
        rows = [line for line in lines[cursor : cursor + count] if line]
        if len(rows) != count:
            raise DomainError(f"XYZ block declares {count} atoms but carries {len(rows)} rows")
        blocks.append((count, rows))
        cursor += count
    return blocks
