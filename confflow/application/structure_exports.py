"""Publish downloadable XYZ projections of successful transform structures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..domain.artifact import ArtifactLocator, ArtifactRef
from ..domain.errors import DomainError
from ..persistence.fsatomic import publish_bytes


def publish_transform_structures(
    result: Any, *, run_root: str, generation_id: str
) -> tuple[ArtifactRef, ...]:
    """Export typed outputs without changing the persisted scientific model."""
    if result.status.value != "completed":
        return ()
    root = Path(run_root).resolve()
    namespace = hashlib.sha256(f"{generation_id}:{result.step_id}".encode()).hexdigest()[:24]
    exports: list[ArtifactRef] = []
    for index, structure in enumerate(result.structures):
        locator = f"exports/{namespace}/structure-{index + 1:06d}.xyz"
        target = root / locator
        if not target.resolve().is_relative_to(root):
            raise DomainError("transform export escapes run root")
        # The canonical StructureRecord coordinate unit is angstrom.
        comment = json.dumps(
            {
                "structure_id": structure.id,
                "step_id": result.step_id,
                "charge": structure.charge,
                "multiplicity": structure.multiplicity,
                "coordinate_unit": "angstrom",
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        lines = [str(len(structure.atoms)), comment]
        lines.extend(
            f"{atom} {xyz[0]:.17g} {xyz[1]:.17g} {xyz[2]:.17g}"
            for atom, xyz in zip(structure.atoms, structure.coordinates, strict=True)
        )
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        publish_bytes(str(target), payload)
        exports.append(
            ArtifactRef(
                id=f"export:{namespace}:{index + 1}",
                role="geometry",
                locator=ArtifactLocator.run_relative(locator),
                checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
                media_type="chemical/x-xyz",
                producer_step_id=result.step_id,
                subject_structure_id=structure.id,
            )
        )
    return tuple(exports)
