"""Shared confgen-path structure fixtures (moved verbatim)."""

from __future__ import annotations

from confflow.domain.structure import StructureRecord


def _hexane(struct_id: str) -> StructureRecord:
    """Linear C6 chain: interior bonds have measurable typed frames."""
    return StructureRecord(
        id=struct_id,
        atoms=("C",) * 6,
        coordinates=tuple((float(i) * 1.5, 0.4 * (i % 2), 0.0) for i in range(6)),
        charge=0,
        multiplicity=1,
    )
