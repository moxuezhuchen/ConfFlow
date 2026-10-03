#!/usr/bin/env python3

"""Phase 0 review-fix regressions (parent review round 2).

Covers the eight accepted review defects: the element-aware short-bond
heuristic on the exact user examples (1.34 A C-C warn, 1.33 A C-N warn,
1.52 A C-C quiet) with stable WARNING_SHORT_BOND diagnostics; canonical
(post-dedup, post-exclusion) task counts read from output reports;
mixed-mode shared exclusions; explicit single-state grids; valid V4 YAML
examples compiled through normal entrypoints; per-source resolved route
audits for legacy and typed (including upstream geometry).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureRecord
from confflow.domain.completion import WorkItemStatus
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.science.bonds import covalent_radii
from confflow.science.confgen.torsion.paths import (
    WARNING_SHORT_BOND,
    parse_path_declarations,
    resolve_paths,
)
from tests.v4.test_repair_executors import _butane, _ctx, _item, _sci


def _run_legacy(records, native, tmp_path):
    """Execute the legacy executor (FAILED results returned, never raised)."""
    item = _item("c1:g1", "c1", records)
    return ConfgenExecutor().execute(
        item, _ctx(_sci(seed=11, native=FrozenDict(dict(native))), str(tmp_path))
    )


def _legacy_report(tmp_path) -> dict[str, Any]:
    """Load the legacy confgen report artifact from the attempt directory."""
    for root, _, files in os.walk(str(tmp_path)):
        if "confgen_report.json" in files:
            with open(os.path.join(root, "confgen_report.json"), encoding="utf-8") as fh:
                return json.load(fh)
    raise AssertionError("confgen_report.json not written")


def _ensemble_report(tmp_path) -> dict[str, Any]:
    """Load the typed ensemble report artifact from the attempt directory."""
    for root, _, files in os.walk(str(tmp_path)):
        if "ensemble_report.json" in files:
            with open(os.path.join(root, "ensemble_report.json"), encoding="utf-8") as fh:
                return json.load(fh)
    raise AssertionError("ensemble_report.json not written")


def _linear_coords(*gaps: float) -> tuple[tuple[float, float, float], ...]:
    """Lay a linear chain with the given consecutive gaps on the x axis."""
    points = [0.0]
    for gap in gaps:
        points.append(points[-1] + gap)
    return tuple((x, 0.0, 0.0) for x in points)


# -- defect 1: short-bond heuristic on the exact user examples ------------------


def test_short_bond_cc_134_warns():
    """The user's 1.34 A C-C example warns (exact regression)."""
    adjacency = [[1], [0, 2], [1, 3], [2]]
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(
        parsed,
        adjacency,
        coords=_linear_coords(1.5, 1.34, 1.5),
        radii=covalent_radii([6, 6, 6, 6]),
    )
    assert len(resolution.warnings) == 1
    (note,) = resolution.warnings
    assert note.startswith(WARNING_SHORT_BOND)
    assert "2-3" in note and "1.340" in note


def test_short_bond_cc_152_quiet():
    """A 1.52 A C-C bond stays quiet (exact regression)."""
    adjacency = [[1], [0, 2], [1, 3], [2]]
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(
        parsed,
        adjacency,
        coords=_linear_coords(1.5, 1.52, 1.5),
        radii=covalent_radii([6, 6, 6, 6]),
    )
    assert resolution.warnings == ()


def test_short_bond_cn_133_warns():
    """The user's 1.33 A C-N example warns (exact regression)."""
    adjacency = [[1], [0, 2], [1]]
    parsed = parse_path_declarations(
        [{"start": 1, "end": 3, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(
        parsed,
        adjacency,
        coords=_linear_coords(1.5, 1.33),
        radii=covalent_radii([6, 6, 7]),
    )
    assert len(resolution.warnings) == 1
    assert resolution.warnings[0].startswith(WARNING_SHORT_BOND)


def test_legacy_emits_stable_short_bond_diagnostics(tmp_path):
    """Legacy execution exposes WARNING_SHORT_BOND diagnostics + report notes."""
    record = StructureRecord(
        id="cc-short",
        atoms=("C", "C", "C", "C"),
        coordinates=_linear_coords(1.5, 1.34, 1.5),
        charge=0,
        multiplicity=1,
    )
    result = _run_legacy(
        [record],
        {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 180.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    short = [d for d in result.diagnostics if d.code == "warning_short_bond"]
    assert len(short) == 1
    assert short[0].message.startswith(WARNING_SHORT_BOND)
    assert short[0].severity.value == "warning"
    payload = _legacy_report(tmp_path)
    assert len(payload["path_resolution"]["warnings"]) == 1


def test_typed_short_bond_reported(tmp_path):
    """Typed execution reports short-bond findings (diagnostic + report)."""
    record = StructureRecord(
        id="hex-short",
        atoms=("C",) * 6,
        coordinates=(  # interior bond 3-4 compressed to 1.34 A
            (0.0, 0.0, 0.0),
            (1.5, 0.4, 0.0),
            (3.0, 0.0, 0.0),
            (4.34, 0.4, 0.0),
            (5.84, 0.0, 0.0),
            (7.34, 0.4, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )
    item = _item("c1:g1", "c1", [record])
    native = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0]}],
    }
    result = ConfgenExecutor().execute(
        item, _ctx(_sci(seed=None, native=FrozenDict(native)), str(tmp_path))
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 1
    short = [d for d in result.diagnostics if d.code == "warning_short_bond"]
    assert len(short) == 1
    assert short[0].message.startswith(WARNING_SHORT_BOND)
    payload = _ensemble_report(tmp_path)
    warnings = payload["path_resolution"]["warnings"]
    assert len(warnings) == 1
    assert warnings[0].startswith(WARNING_SHORT_BOND)


# -- defect 2: canonical counts from output reports ------------------------------


def test_duplicated_paths_report_canonical_counts(tmp_path):
    """Two identical declarations: declared 81, final 2 rotors / 9 states."""
    result = _run_legacy(
        [_butane("seed-a")],
        {
            "paths": [
                {"start": 1, "end": 3, "move": "end"},
                {"start": 1, "end": 3, "move": "end"},
            ]
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _legacy_report(tmp_path)
    resolved = payload["path_resolution"]
    assert resolved["declared_cartesian_size"] == 81
    assert resolved["raw_cartesian_size"] == 9
    assert len(resolved["rotors"]) == 2
    assert payload["grid_points"] == 9
    assert len(payload["members"]) == 9


def test_mixed_extra_chain_rotor_counts(tmp_path):
    """A chain-only rotor joins the canonical space (report-read)."""
    result = _run_legacy(
        [_butane("seed-a")],
        {
            "paths": [{"start": 1, "end": 3, "move": "end", "angles": [0.0, 120.0]}],
            "chains": ["3-4"],
            "chain_angles": "0,120",
            "rotate_side": "right",
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _legacy_report(tmp_path)
    resolved = payload["path_resolution"]
    assert len(resolved["rotors"]) == 3
    assert resolved["raw_cartesian_size"] == 8
    assert payload["grid_points"] == 8
    assert [m["ordinal"] for m in payload["members"]] == list(range(8))


def test_exclusion_reduces_canonical_counts(tmp_path):
    """no_rotate collapses to factor 1 in the reported canonical count."""
    result = _run_legacy(
        [_butane("seed-a")],
        {
            "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            "no_rotate": ["1-2"],
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _legacy_report(tmp_path)
    resolved = payload["path_resolution"]
    assert resolved["declared_cartesian_size"] == 8
    assert resolved["raw_cartesian_size"] == 4
    first = resolved["rotors"][0]
    assert first["bond"] == [1, 2]
    assert first["angles"] == [0.0]
    assert "excluded:no_rotate" in first["sources"]


def test_typed_duplicated_paths_counts(tmp_path):
    """Typed duplicates dedup: path-only 3 vs declared 9, total space split."""
    item = _item("c1:g1", "c1", [_butane("seed-a")])
    native = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [
            {"start": 2, "end": 3, "move": "end", "angles": [0.0, 120.0, 240.0]},
            {"start": 2, "end": 3, "move": "end", "angles": [0.0, 120.0, 240.0]},
        ],
    }
    result = ConfgenExecutor().execute(
        item, _ctx(_sci(seed=None, native=FrozenDict(native)), str(tmp_path))
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 3
    payload = _ensemble_report(tmp_path)
    resolved = payload["path_resolution"]
    assert resolved["declared_cartesian_size"] == 9
    assert resolved["raw_cartesian_size"] == 3
    assert len(resolved["rotors"]) == 1
    assert payload["counts"]["raw"] == 3  # total C/R/T space


# -- defect 3: shared exclusions ---------------------------------------------------


def test_mixed_identical_with_shared_exclusion_succeeds(tmp_path):
    """The parent repro: identical declarations + shared exclusion succeed."""
    result = _run_legacy(
        [_butane("seed-a")],
        {
            "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            "chains": ["1-2-3-4"],
            "chain_angles": "0,120;0,120;0,120",
            "rotate_side": "right",
            "no_rotate": ["1-2"],
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 4
    payload = _legacy_report(tmp_path)
    assert payload["path_resolution"]["raw_cartesian_size"] == 4


def test_mixed_excluded_contradictory_grid_still_conflicts(tmp_path):
    """Exclusions do not mask contradictory declared grids (conflict first)."""
    result = _run_legacy(
        [_butane("seed-a")],
        {
            "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            "chains": ["1-2-3-4"],
            "chain_angles": "0,90;0,90;0,90",
            "rotate_side": "right",
            "no_rotate": ["1-2"],
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.FAILED
    assert "ROTOR_SAMPLING_CONFLICT" in " ".join(d.message for d in result.diagnostics)


def test_mixed_ring_refusal_gives_no_exclusion_advice(tmp_path):
    """Ring bonds stay forbidden; the error must not suggest no_rotate."""
    record = StructureRecord(
        id="ring-tail",
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (1.5, 1.5, 0.0),
            (0.0, 1.5, 0.0),
            (0.0, 3.0, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )
    result = _run_legacy(
        [record],
        {
            "paths": [{"start": 4, "end": 5, "move": "end", "angles": [0.0]}],
            "chains": ["1-2-3"],
            "chain_angles": "0;0",
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.FAILED
    message = " ".join(d.message for d in result.diagnostics)
    assert "ring bond" in message
    assert "no_rotate" not in message


# -- defect 4: explicit single-state grids ------------------------------------------


def test_explicit_zero_grid_executes(tmp_path):
    """Angles [0.0] is one valid state, not an exclusion."""
    result = _run_legacy(
        [_butane("seed-a")],
        {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 1
    assert result.structures[0].ordinal == 0
    payload = _legacy_report(tmp_path)
    assert payload["path_resolution"]["raw_cartesian_size"] == 1


def test_explicit_single_nonzero_grid_executes(tmp_path):
    """Angles [90.0] applies one real rotation (factor 1, geometry changes)."""
    result = _run_legacy(
        [_butane("seed-a")],
        {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [90.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 1


def test_all_excluded_still_fails_closed(tmp_path):
    """Every bond excluded via no_rotate keeps the fail-closed contract."""
    result = _run_legacy(
        [_butane("seed-a")],
        {
            "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0, 120.0]}],
            "no_rotate": ["1-2"],
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.FAILED
    assert "no rotatable bonds" in " ".join(d.message for d in result.diagnostics)


# -- defect 5: valid V4 YAML examples -------------------------------------------------


LEGACY_PATHS_YAML = """\
schema: confflow.workflow.v4
inputs:
  structures: {kind: structure, cardinality: many}
global:
  scientific_defaults: {charge: 0, multiplicity: 1}
steps:
  - id: s_gen
    executor: confgen
    bindings:
      structure:
        source: {run: structures}
    confgen:
      seed: 11
      paths:
        - {start: 1, end: 4, move: end, step: 120}
"""

TYPED_PATHS_YAML = """\
schema: confflow.workflow.v4
inputs:
  structures: {kind: structure, cardinality: many}
global:
  scientific_defaults: {charge: 0, multiplicity: 1}
steps:
  - id: s_gen
    executor: confgen
    bindings:
      structure:
        source: {run: structures}
    confgen:
      schema_version: 3
      index_base: 1
      paths:
        - {start: 1, end: 4, move: end, angles: [0.0, 120.0, 240.0]}
"""


def test_valid_yaml_examples_compile():
    """The documented YAML examples parse/compile via normal V4 entrypoints."""
    from confflow.workflow.v4.compiler import compile_workflow_text
    from confflow.workflow.v4.parser import parse_workflow_text_document

    for text in (LEGACY_PATHS_YAML, TYPED_PATHS_YAML):
        parsed = parse_workflow_text_document(text)
        assert parsed.ok, [str(d.message) for d in parsed.diagnostics]
        compiled = compile_workflow_text(text)
        assert compiled.ok, [str(d.message) for d in compiled.diagnostics]
        assert compiled.plan is not None


def test_bare_paths_compile_to_legacy_native(tmp_path):
    """Bare confgen.paths compiles to native and executes (no wrapper)."""
    from confflow.workflow.v4.compiler import compile_workflow_text

    compiled = compile_workflow_text(LEGACY_PATHS_YAML)
    assert compiled.ok
    native = compiled.plan.steps[0].scientific.native
    assert "schema_version" not in dict(native)
    assert list(native.get("paths") or []) != []
    item = _item("c1:g1", "c1", [_butane("seed-a")])
    result = ConfgenExecutor().execute(item, _ctx(_sci(seed=11, native=native), str(tmp_path)))
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 27


def test_bare_paths_without_seed_stays_legacy():
    """Seedless bare paths fail on the legacy seed rule (never typed sampling)."""
    from confflow.workflow.v4.compiler import compile_workflow_text

    text = LEGACY_PATHS_YAML.replace("      seed: 11\n", "")
    compiled = compile_workflow_text(text)
    assert not compiled.ok
    assert any("seed" in d.message for d in compiled.diagnostics)


def test_native_root_paths_conflict_rejected():
    """Declaring paths at both levels fails closed (no silent winner)."""
    from confflow.workflow.v4.compiler import compile_workflow_text

    text = LEGACY_PATHS_YAML.replace(
        "      seed: 11\n",
        "      seed: 11\n"
        "      native:\n"
        "        paths:\n"
        "          - {start: 1, end: 2, move: end}\n",
    )
    compiled = compile_workflow_text(text)
    assert not compiled.ok
    assert any("both the step level" in d.message for d in compiled.diagnostics)


def test_root_strict_flag_compiles_to_native():
    """Root strict_path_bond_check compiles; dual declaration conflicts."""
    from confflow.workflow.v4.compiler import compile_workflow_text

    text = LEGACY_PATHS_YAML.replace(
        "      seed: 11\n", "      seed: 11\n      strict_path_bond_check: true\n"
    )
    compiled = compile_workflow_text(text)
    assert compiled.ok
    assert compiled.plan.steps[0].scientific.native.get("strict_path_bond_check") is True
    dual = text.replace(
        "      strict_path_bond_check: true\n",
        "      strict_path_bond_check: true\n"
        "      native:\n"
        "        strict_path_bond_check: false\n",
    )
    conflicted = compile_workflow_text(dual)
    assert not conflicted.ok
    assert any("both the step level" in d.message for d in conflicted.diagnostics)


def test_normalize_spec_idempotent_with_paths():
    """normalize_spec is exactly idempotent (marker distinguishes internal)."""
    from confflow.science.confgen.planner import normalize_spec

    spec = {
        "schema_version": 3,
        "index_base": 1,
        "torsions": [
            {
                "id": "extra",
                "bond": [1, 2],
                "model": "relative_rotation_grid",
                "angles": [0.0, 180.0],
            }
        ],
        "paths": [{"start": 2, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        "topology": {"add_bond": [[1, 4]]},
    }
    once = normalize_spec(spec)
    twice = normalize_spec(once)
    assert twice == once
    assert twice["paths"][0]["source"] == "$.paths[0]"
    assert twice["paths"][0]["start"] == 1  # internal 0-based, shifted once


def test_build_context_on_normalized_spec():
    """build_context accepts an already-normalized spec (double normalize)."""
    from confflow.science.confgen.model import build_context
    from confflow.science.confgen.planner import normalize_spec

    spec = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [{"start": 2, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
    }
    context = build_context(_butane("idem"), normalize_spec(spec))
    assert [tuple(t["bond"]) for t in context.resolved_spec["torsions"]] == [
        (1, 2),
        (2, 3),
    ]


# -- defect 6: per-source route audits -------------------------------------------------


def test_legacy_declared_paths_audit(tmp_path):
    """Reports carry per-source routes, symbols, and driving identity."""
    record = StructureRecord(
        id="audit-me",
        atoms=("C", "C", "C", "C"),
        coordinates=_linear_coords(1.5, 1.52, 1.5),
        charge=0,
        multiplicity=1,
    )
    result = _run_legacy(
        [record],
        {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _legacy_report(tmp_path)
    resolved = payload["path_resolution"]
    assert resolved["driving_id"] == "audit-me"
    assert resolved["driving_geometry_digest"] == record.geometry_digest
    assert resolved["atom_symbols"] == ["C", "C", "C", "C"]
    (declared,) = resolved["declared_paths"]
    assert declared["source"] == "native.paths[0]"
    assert (declared["start"], declared["end"], declared["move"]) == (1, 4, "end")
    assert declared["route"] == [1, 2, 3, 4]
    info = next(d for d in result.diagnostics if d.code == "confgen_completed")
    assert info.details["path_rotors"] == 3
    assert info.details["topology_digest"] == resolved["topology_digest"]
    assert info.details["path_warnings"] == 0


def test_typed_declared_paths_audit(tmp_path):
    """Typed reports carry the same resolver-authority route audits."""
    record = StructureRecord(
        id="hex-audit",
        atoms=("C",) * 6,
        coordinates=tuple((float(i) * 1.5, 0.4 * (i % 2), 0.0) for i in range(6)),
        charge=0,
        multiplicity=1,
    )
    item = _item("c1:g1", "c1", [record])
    native = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 120.0]}],
    }
    result = ConfgenExecutor().execute(
        item, _ctx(_sci(seed=None, native=FrozenDict(native)), str(tmp_path))
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _ensemble_report(tmp_path)
    resolved = payload["path_resolution"]
    assert resolved["driving_id"] == "hex-audit"
    assert resolved["atom_symbols"] == ["C"] * 6
    (declared,) = resolved["declared_paths"]
    assert declared["route"] == [2, 3, 4, 5]
    assert declared["move"] == "end"
    info = next(d for d in result.diagnostics if d.code == "confgen_completed")
    assert info.details["path_rotors"] == 3


def test_upstream_geometry_audit_identity(tmp_path):
    """Upstream-generated driving geometry is audited by its own identity."""
    record = StructureRecord(
        id="upstream-product-7",
        atoms=("C", "C", "C", "C"),
        coordinates=_linear_coords(1.5, 1.52, 1.5),
        charge=0,
        multiplicity=1,
        parent_ids=("seed-a",),
    )
    result = _run_legacy(
        [record],
        {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _legacy_report(tmp_path)
    assert payload["driving_id"] == "upstream-product-7"
    assert payload["path_resolution"]["driving_geometry_digest"] == record.geometry_digest


@pytest.mark.parametrize("typed", [False, True])
def test_runtime_diagnostics_show_resolved_chain_and_moving_endpoint(tmp_path, typed):
    """Both modes expose the actual resolved atom route in unattended run logs."""
    from tests.v4.test_confgen_paths_phase0 import _hexane

    native = {"paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 120.0]}]}
    if typed:
        native.update(schema_version=3, index_base=1)
    item = _item("c1:g1", "c1", [_hexane("diagnostic-input")])
    result = ConfgenExecutor().execute(
        item,
        _ctx(_sci(seed=None if typed else 11, native=FrozenDict(native)), str(tmp_path)),
    )
    assert result.status is WorkItemStatus.COMPLETED
    (diagnostic,) = [d for d in result.diagnostics if d.code == "confgen_path_resolved"]
    assert "2(C)-3(C)-4(C)-5(C)" in diagnostic.message
    assert "move end endpoint 5" in diagnostic.message
    assert "rotors: 2-3, 3-4, 4-5" in diagnostic.message
    assert list(diagnostic.details["route"]) == [2, 3, 4, 5]
    assert diagnostic.details["topology_digest"].startswith("sha256:")
