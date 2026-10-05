"""Preserve the absent and empty paths context-build behavior."""

import pytest

from confflow.domain.structure import StructureRecord
from confflow.science.confgen.model import build_context


@pytest.mark.parametrize("section", [{}, {"paths": []}])
def test_empty_paths_do_not_create_expansion_metadata(section):
    structure = StructureRecord(
        id="empty-paths",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )
    context = build_context(structure, {"index_base": 0, **section})
    assert "paths_resolved" not in context.resolved_spec
