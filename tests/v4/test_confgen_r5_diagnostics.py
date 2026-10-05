#!/usr/bin/env python3
"""R5 input-diagnostics regression (new, required by PLAN R5 + ROOT-HOOK-ADDENDUM).

Real-implementation coverage (not mock-dict only):
- rpdd truncated fragment C2 (global atom 1) distorted ~338.75 expected >=350,
  never a direction lock (R2 authority);
- stage pure helper on driving context (covalent_graph + typed_edges),
  preserve + enumerate covered, index_base=0 explicit, deterministic sort,
  no solver re-mapping;
- perception default-empty compat, empty not serialized;
- engine report component_diagnostics segment (non-empty only) + donor path intact;
- executor ensemble warnings on success AND zero-leaf structured failure;
- CREST / no-ring empty reports omit the key (bytes unchanged);
- engine reuse leaves no residue; rotation/reverse/index_base keep global ids;
- cancel probe preserved; generic mock hook flows without kernel componentization.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.domain import StructureRecord
from confflow.science.confgen.model import build_context
from confflow.science.confgen.ring.perception import perceive_ring, ring_diagnostics
from confflow.science.confgen.ring.rigid_units import analyze_rigid_units

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "ring" / "rpdd"
FRAG = FIX / "input_ts_fragment.xyz"
CREST = FIX / "crest_conformers.xyz"
RING_RPDD = [0, 1, 3, 4, 5, 7]


def _load_xyz(path: Path, frame: int = 0) -> tuple[list[str], np.ndarray]:
    lines = path.read_text().splitlines()
    n = int(lines[0].strip())
    base = frame * (n + 2)
    els: list[str] = []
    xyz: list[list[float]] = []
    for line in lines[base + 2 : base + 2 + n]:
        parts = line.split()
        els.append(parts[0])
        xyz.append([float(x) for x in parts[1:4]])
    return els, np.array(xyz, dtype=float)


def _rpdd_adjacency() -> list[list[int]]:
    adj: dict[int, set[int]] = {i: set() for i in range(22)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (0, 7),
        (0, 9),
        (0, 11),
        (1, 2),
        (1, 3),
        (3, 4),
        (4, 5),
        (4, 8),
        (4, 10),
        (5, 6),
        (5, 7),
        (11, 12),
        (11, 13),
        (12, 14),
        (13, 16),
        (14, 18),
        (16, 18),
        (12, 15),
        (13, 17),
        (14, 19),
        (16, 20),
        (18, 21),
    ]:
        link(a, b)
    return [sorted(adj[i]) for i in range(22)]


def _bonds_of(graph: list[list[int]]) -> list[list[int]]:
    seen: set[tuple[int, int]] = set()
    out: list[list[int]] = []
    for a, row in enumerate(graph):
        for b in row:
            key = (min(a, b), max(a, b))
            if key not in seen:
                seen.add(key)
                out.append([a, b])
    return out


def _record(rid: str, els: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=rid,
        atoms=tuple(els),
        coordinates=tuple(tuple(float(v) for v in row) for row in coords),
    )


def _frag_context(extra_rings: Any = None, coords_override: Any = None) -> Any:
    els, frag = _load_xyz(FRAG, 0)
    if coords_override is not None:
        frag = np.asarray(coords_override, dtype=float)
    graph = _rpdd_adjacency()
    record = _record("frag", els, frag)
    rings = (
        extra_rings
        if extra_rings is not None
        else [{"id": "r1", "atoms": [a + 1 for a in RING_RPDD]}]
    )
    spec: dict[str, Any] = {
        "schema_version": 3,
        "index_base": 1,
        "seed": 5,
        "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
        "rings": rings,
    }
    return build_context(record, spec)


def _crest_context() -> Any:
    els, crest1 = _load_xyz(CREST, 0)
    graph = _rpdd_adjacency()
    record = _record("crest1", els, crest1)
    spec: dict[str, Any] = {
        "schema_version": 3,
        "index_base": 1,
        "seed": 5,
        "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
        "rings": [{"id": "r1", "atoms": [a + 1 for a in RING_RPDD]}],
    }
    return build_context(record, spec)


# ---------------------------------------------------------------- group A: R2
def test_r5_rpdd_fragment_c2_distorted_no_lock() -> None:
    els, frag = _load_xyz(FRAG, 0)
    res = analyze_rigid_units(frag, els, _rpdd_adjacency(), RING_RPDD)
    assert 1 in res.sp2_centers
    assert [lk for lk in res.local_orientation_locks if int(lk.center) == 1] == []
    d1 = [d for d in res.distorted_input if int(d.atom) == 1]
    assert len(d1) == 1
    assert d1[0].expected == ">=350"
    assert float(d1[0].observed) == pytest.approx(338.75, abs=2.0)
    assert float(d1[0].observed) < 350.0
    # global 0-based: atom field is the full-structure index, not a solver ordinal
    assert int(d1[0].atom) == 1


# ------------------------------------------------- group B: stage helper + perception
def test_r5_stage_helper_fragment_reports_global_atom() -> None:
    from confflow.science.confgen.ring.stage import analyze_ring_input_diagnostics

    ctx = _frag_context()
    out = analyze_ring_input_diagnostics(ctx)
    assert out is not None
    assert int(out["index_base"]) == 0
    rows = list(out["distorted_input"])
    assert rows, "fragment must yield at least one distorted entry"
    by_atom = {int(r["atom"]): r for r in rows}
    assert 1 in by_atom
    assert float(by_atom[1]["observed"]) == pytest.approx(338.75, abs=2.0)
    assert str(by_atom[1]["expected"]) == ">=350"
    assert str(by_atom[1]["ring_id"]) == "r1"
    # deterministic sort by (ring_id, atom)
    keys = [(str(r["ring_id"]), int(r["atom"])) for r in rows]
    assert keys == sorted(keys)


def test_r5_stage_helper_crest_empty_is_none() -> None:
    from confflow.science.confgen.ring.stage import analyze_ring_input_diagnostics

    assert analyze_ring_input_diagnostics(_crest_context()) is None


def test_r5_stage_helper_preserve_and_enumerate_covered() -> None:
    from confflow.science.confgen.ring.stage import analyze_ring_input_diagnostics

    ctx = _frag_context(
        extra_rings=[
            {"id": "rp", "atoms": [a + 1 for a in RING_RPDD], "treatment": "preserve_input"},
            {"id": "re", "atoms": [a + 1 for a in RING_RPDD]},
        ]
    )
    out = analyze_ring_input_diagnostics(ctx)
    assert out is not None
    ids = {str(r["ring_id"]) for r in out["distorted_input"]}
    assert ids == {"rp", "re"}


def test_r5_stage_helper_analysis_error_is_raised_not_empty() -> None:
    import dataclasses

    from confflow.science.confgen.ring.stage import analyze_ring_input_diagnostics

    # Out-of-range ring atom is a real analysis error: it must raise,
    # never collapse to silent empty (the component hook then maps it
    # to an explicit {"error": ...} payload). Build a valid context
    # then swap in bad atoms so construction itself stays valid.
    good = _frag_context()
    bad_resolved = dict(dict(good.resolved_spec).copy())
    bad_resolved["rings"] = [{"id": "r1", "atoms": [0, 1, 999]}]
    from confflow.domain._immutable import FrozenDict

    bad_ctx = dataclasses.replace(good, resolved_spec=FrozenDict(bad_resolved))
    with pytest.raises(ValueError):
        analyze_ring_input_diagnostics(bad_ctx)
    # And the component hook surfaces it explicitly instead of omitting.
    from confflow.science.confgen.ring.component import _report_diagnostics

    err = _report_diagnostics(bad_ctx)
    assert isinstance(err, dict) and "error" in err


def test_r5_perception_empty_not_serialized_old_call_compatible() -> None:
    els, crest1 = _load_xyz(CREST, 0)
    from confflow.science.confgen.ring.stage import RingStage

    ring_coords = np.asarray(crest1, dtype=float)[RING_RPDD]
    perception = perceive_ring(ring_coords)
    diag = ring_diagnostics(perception)  # old single-arg call must keep working
    assert "distorted_input" not in diag
    assert set(diag) >= {"confidence", "measured_cp", "alternatives"}
    # explicit non-empty payload serializes
    diag2 = ring_diagnostics(perception, [{"atom": 1, "observed": 338.75, "expected": ">=350"}])
    assert diag2["distorted_input"] == [{"atom": 1, "observed": 338.75, "expected": ">=350"}]
    _ = RingStage  # keep stage import live for purity probes


# ---------------------------------------------------------------- group C: engine
def test_r5_engine_fragment_report_has_ring_segment() -> None:
    from confflow.science.confgen.engine import ConfgenEngine

    ctx = _frag_context()
    run = ConfgenEngine().run(ctx)
    report = run.report_json()
    assert run.certificate.equations_ok is True
    comp = report.get("component_diagnostics")
    assert isinstance(comp, dict) and comp, "fragment must expose component diagnostics"
    # find the rings-owned payload generically (kernel never names it; test may)
    payload = None
    for _cid, _val in comp.items():
        if isinstance(_val, dict) and "distorted_input" in _val:
            payload = _val
            break
    assert payload is not None
    assert int(payload["index_base"]) == 0
    rows = list(payload["distorted_input"])
    hit = [r for r in rows if int(r["atom"]) == 1]
    assert hit and float(hit[0]["observed"]) == pytest.approx(338.75, abs=2.0)
    assert str(hit[0]["expected"]) == ">=350"
    # state keys stay canonical; donor path intact
    assert "donor_configuration" in report.get("scope", {})
    for record in run.target_records:
        sv = dict(record.state_value)
        for _rid, entry in sv.items():
            if isinstance(entry, dict) and "form" in entry:
                assert set(entry) == {"form", "index", "anchor", "direction"}


def test_r5_engine_crest_empty_omits_key() -> None:
    from confflow.science.confgen.engine import ConfgenEngine

    report = ConfgenEngine().run(_crest_context()).report_json()
    assert "component_diagnostics" not in report


def test_r5_engine_no_ring_empty_omits_key() -> None:
    from tests.v4._helpers.repair import _butane

    rec = _butane("s")
    ctx = build_context(rec, {"index_base": 0})
    from confflow.science.confgen.engine import ConfgenEngine

    report = ConfgenEngine(allow_preserve_input=True).run(ctx).report_json()
    assert "component_diagnostics" not in report


def test_r5_engine_reuse_leaves_no_residue() -> None:
    from confflow.science.confgen.engine import ConfgenEngine

    engine = ConfgenEngine()
    first = engine.run(_frag_context()).report_json()
    assert "component_diagnostics" in first
    second = engine.run(_crest_context()).report_json()
    assert "component_diagnostics" not in second
    third = engine.run(_frag_context()).report_json()
    assert "component_diagnostics" in third


def test_r5_engine_cancel_probe_preserved() -> None:
    from confflow.science.confgen.engine import ConfgenEngine, EngineCancelledError

    ctx = _frag_context()
    calls = {"n": 0}

    def _cancel() -> bool:
        calls["n"] += 1
        return True

    with pytest.raises(EngineCancelledError):
        ConfgenEngine().run(ctx, should_cancel=_cancel)
    assert calls["n"] >= 1


# ------------------------------------------------------- group D: global indexing
def test_r5_global_atom_stable_under_rotation_reverse_indexbase() -> None:
    from confflow.science.confgen.ring.stage import analyze_ring_input_diagnostics

    def _ctx_for(atoms_0based: list[int], index_base: int) -> Any:
        els, frag = _load_xyz(FRAG, 0)
        graph = _rpdd_adjacency()
        record = _record("frag", els, frag)
        if index_base == 1:
            atoms_decl = [a + 1 for a in atoms_0based]
            bonds_decl = [[a + 1, b + 1] for a, b in _bonds_of(graph)]
        else:
            atoms_decl = list(atoms_0based)
            bonds_decl = _bonds_of(graph)
        spec: dict[str, Any] = {
            "schema_version": 3,
            "index_base": index_base,
            "seed": 5,
            "topology": {"bonds": bonds_decl},
            "rings": [{"id": "r1", "atoms": list(atoms_decl)}],
        }
        return build_context(record, spec)

    base0 = [a for a in RING_RPDD]  # 0-based
    rot0 = base0[2:] + base0[:2]
    rev0 = [base0[0]] + list(reversed(base0[1:]))
    outs = []
    outs.append(analyze_ring_input_diagnostics(_ctx_for(base0, 1)))
    outs.append(analyze_ring_input_diagnostics(_ctx_for(rot0, 0)))
    outs.append(analyze_ring_input_diagnostics(_ctx_for(rev0, 0)))
    outs.append(analyze_ring_input_diagnostics(_ctx_for(rot0, 1)))
    atoms_sets = []
    for out in outs:
        assert out is not None and int(out["index_base"]) == 0
        atoms_sets.append(sorted(int(r["atom"]) for r in out["distorted_input"]))
    assert atoms_sets[0] == atoms_sets[1] == atoms_sets[2] == atoms_sets[3]
    assert 1 in atoms_sets[0]


# ------------------------------------------------------------- group E: executor
def _executor_ring_native(rings_1based: list[dict[str, Any]]) -> dict[str, Any]:
    from confflow.workflow.v4.confgen_schema import ConfgenModelV3

    doc = {"schema_version": 3, "rings": rings_1based}
    return ConfgenModelV3.model_validate(doc).scientific_native()


def _run_executor(driving: StructureRecord, native: dict[str, Any], tmp: str) -> Any:
    from confflow.execution.confgen_executor import ConfgenExecutor
    from tests.v4._helpers.repair import _ctx, _item, _sci

    item = _item("r5:g1", "r5", [driving])
    sci = _sci(seed=5, native=native)
    return ConfgenExecutor().execute(item, _ctx(sci, tmp))


def test_r5_executor_success_carries_warnings_and_report(tmp_path) -> None:
    import json
    import os

    from confflow.domain.completion import WorkItemStatus
    from confflow.domain.diagnostics import DiagnosticSeverity
    from confflow.execution.confgen_executor import CONFGEN_REPORT_ROLE

    els, frag = _load_xyz(FRAG, 0)
    driving = _record("frag-drive", els, frag)
    native = _executor_ring_native([{"id": "r1", "atoms": [a + 1 for a in RING_RPDD]}])
    out = _run_executor(driving, native, str(tmp_path))
    assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
    codes = [d.code for d in out.diagnostics]
    assert "confgen_distorted_input" in codes
    hit = [d for d in out.diagnostics if d.code == "confgen_distorted_input"]
    assert any(int(dict(d.details).get("atom", -1)) == 1 for d in hit)
    rt = [d for d in hit if int(dict(d.details).get("atom", -1)) == 1][0]
    assert rt.severity is DiagnosticSeverity.WARNING
    assert int(dict(rt.details).get("index_base", -1)) == 0
    assert "0-based" in str(rt.message)
    # ensemble artifact carries the visible segment (not a hash substitute)
    ref = {r.role: r for r in out.artifacts}[CONFGEN_REPORT_ROLE]
    payload = json.loads(open(os.path.join(str(tmp_path), ref.locator.path)).read())
    comp = payload.get("component_diagnostics")
    assert isinstance(comp, dict) and comp
    found = False
    for _cid, _val in comp.items():
        if isinstance(_val, dict):
            for row in _val.get("distorted_input", []):
                if isinstance(row, dict) and int(row.get("atom", -1)) == 1:
                    found = True
    assert found
    # PLAN R5: serialized artifact warnings (runtime alone is not enough).
    warnings = payload.get("warnings")
    assert isinstance(warnings, list) and warnings
    art = [
        w
        for w in warnings
        if isinstance(w, dict)
        and w.get("code") == "confgen_distorted_input"
        and int(dict(w.get("details", {})).get("atom", -1)) == 1
    ]
    assert art, f"artifact warnings miss C2 entry: {warnings}"
    first = art[0]
    assert str(first.get("severity")) == DiagnosticSeverity.WARNING.value
    details = dict(first.get("details", {}))
    assert int(details.get("index_base", -1)) == 0
    assert int(details.get("atom", -1)) == 1
    assert str(details.get("ring_id")) == "r1"
    assert float(details.get("observed")) == pytest.approx(338.75, abs=2.0)
    assert str(details.get("expected")) == ">=350"
    # runtime/artifact consistency on the same global atom.
    assert int(dict(rt.details).get("atom")) == int(details.get("atom"))
    assert str(dict(rt.details).get("ring_id")) == str(details.get("ring_id"))


def test_r5_executor_zero_leaf_failure_still_warns_structured(tmp_path) -> None:
    import json
    import os

    from confflow.domain.completion import WorkItemStatus
    from confflow.execution.confgen_executor import CONFGEN_REPORT_ROLE

    # Planar P_0 target on the distorted fragment is unrealizable: zero
    # leaves (structured FAILED) while the driving input still carries
    # the real C2 distortion, so warnings must still flow.
    els, frag = _load_xyz(FRAG, 0)
    driving = _record("frag-drive", els, frag)
    native = _executor_ring_native(
        [{"id": "r1", "atoms": [a + 1 for a in RING_RPDD], "forms": ["P_0"]}]
    )
    out = _run_executor(driving, native, str(tmp_path))
    assert out.status is WorkItemStatus.FAILED, out.diagnostics
    codes = [d.code for d in out.diagnostics]
    assert "confgen_no_realized_structures" in codes
    assert "confgen_distorted_input" in codes
    ref = {r.role: r for r in out.artifacts}[CONFGEN_REPORT_ROLE]
    payload = json.loads(open(os.path.join(str(tmp_path), ref.locator.path)).read())
    assert isinstance(payload.get("component_diagnostics"), dict)
    # PLAN R5: zero-leaf artifacts carry the same visible warnings.
    from confflow.domain.diagnostics import DiagnosticSeverity

    warnings = payload.get("warnings")
    assert isinstance(warnings, list) and warnings
    art = [
        w
        for w in warnings
        if isinstance(w, dict)
        and w.get("code") == "confgen_distorted_input"
        and int(dict(w.get("details", {})).get("atom", -1)) == 1
    ]
    assert art, f"artifact warnings miss C2 entry: {warnings}"
    first = art[0]
    assert str(first.get("severity")) == DiagnosticSeverity.WARNING.value
    details = dict(first.get("details", {}))
    assert int(details.get("index_base", -1)) == 0
    assert int(details.get("atom", -1)) == 1
    assert str(details.get("ring_id")) == "r1"
    assert float(details.get("observed")) == pytest.approx(338.75, abs=2.0)
    assert str(details.get("expected")) == ">=350"
    rt_hit = [d for d in out.diagnostics if d.code == "confgen_distorted_input"]
    assert rt_hit
    assert int(dict(rt_hit[0].details).get("atom")) == 1
    assert int(dict(rt_hit[0].details).get("index_base", -1)) == 0


# ------------------------------------------------------- group F: generic hook
def test_r5_generic_mock_hook_flows_kernel_stays_generic() -> None:
    from confflow.science.confgen.engine import ConfgenEngine
    from confflow.science.confgen.registry import ComponentDescriptor, build_default_registry
    from tests.v4._helpers.repair import _butane

    def _mock_diag(context: Any) -> dict[str, Any]:
        return {"index_base": 0, "note": "mock-visible"}

    mock = ComponentDescriptor(
        id="mockprobe",
        order=999,
        spec_keys=(),
        state_merge="merge",
        is_active=lambda _resolved: False,
        factory=lambda _resolved: (_ for _ in ()).throw(AssertionError("never built")),
        report_diagnostics=_mock_diag,
    )
    registry = build_default_registry().with_component(mock)
    rec = _butane("s")
    from confflow.science.confgen.model import build_context as _build

    ctx = _build(rec, {"index_base": 0}, registry=registry)
    engine = ConfgenEngine(registry=registry, allow_preserve_input=True)
    from confflow.science.confgen.kernel_records import ComponentStateKey

    kre = engine.run_kernel(ctx, initial_key=ComponentStateKey(components={}))
    rep = kre.report.thaw() if hasattr(kre.report, "thaw") else dict(kre.report)
    assert rep.get("component_diagnostics", {}).get("mockprobe") == {
        "index_base": 0,
        "note": "mock-visible",
    }
    # kernel purity: no component literal / import in engine source
    src = (
        Path(__file__).resolve().parents[2] / "confflow" / "science" / "confgen" / "engine.py"
    ).read_text()
    tree = ast.parse(src)
    lits = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value in ("coordination", "rings", "torsions")
    ]
    assert lits == [], f"kernel axis literals: {lits}"
    imports = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module)
    bad = [
        m
        for m in imports
        if m.startswith("confflow.science.confgen.ring")
        or m.startswith("confflow.science.confgen.torsion.")
        or m.startswith("confflow.science.confgen.coordination")
    ]
    assert bad == [], f"kernel component imports: {bad}"
