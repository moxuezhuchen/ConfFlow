#!/usr/bin/env python3

"""R4 science proof tests (new, required by PLAN R4).

Proves with real outputs (no tolerance loosening, no input changes):
- rpdd two-basin recall (CREST1/2/3 ring CP each within 15 deg of some
  published seed) + ester planarity / angle / amplitude / C1 orientation;
- cyclohexane default 8 (2C+6TB) all published; explicit B family 6;
  precise B_2 single;
- cyclohexene constrained H at least one published;
- traversal shift/reverse state-set invariance via relabel + RMSD<0.01;
- rpdd truncated fragment: C2 never locked, distorted_input present.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from confflow.domain import StructureRecord
from confflow.science.confgen.model import ConfgenStateKey, WorkingRealization, build_context
from confflow.science.confgen.ring.geometry import rigid_rmsd
from confflow.science.confgen.ring.puckering import (
    canonical_forms,
    cp_distance,
    cp_to_coords,
    cremer_pople,
    relabel,
)
from confflow.science.confgen.ring.rigid_units import analyze_rigid_units
from confflow.science.confgen.ring.stage import RingStage

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


def _context_for(record: StructureRecord, graph: list[list[int]], extra: dict | None = None):
    spec: dict = {
        "index_base": 1,
        "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
    }
    if extra:
        spec.update(extra)
    return build_context(record, spec)


def _parent_of(record: StructureRecord) -> WorkingRealization:
    return WorkingRealization(structure=record, state_key=ConfgenStateKey(), provenance={})


def _ring_bonds_xyz(xyz: np.ndarray, order: list[int]) -> list[float]:
    return [
        float(np.linalg.norm(xyz[order[(k + 1) % len(order)]] - xyz[order[k]]))
        for k in range(len(order))
    ]


def test_rpdd_two_basin_recall_and_audits() -> None:
    """Real rpdd CREST1 input: two B basins recalled, audits hold."""
    els, crest1 = _load_xyz(CREST, 0)
    _, crest2 = _load_xyz(CREST, 1)
    _, crest3 = _load_xyz(CREST, 2)
    graph = _rpdd_adjacency()
    record = _record("rpdd-crest1", els, crest1)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": RING_RPDD}]})
    parent = _parent_of(record)
    # Enumeration must contain defaults (2C+6TB) plus 2 constrained B.
    targets = list(stage.enumerate_targets(parent, context))
    assert len(targets) == 10, f"expected 2C+6TB+2B=10, got {len(targets)}"
    # Realize all; collect published.
    published: list[tuple[dict, np.ndarray, dict]] = []
    for tgt in targets:
        res = stage.realize(parent, tgt, context)
        if res.status == "realized" and res.structure is not None:
            published.append(
                (
                    dict(tgt.state_value["r1"]),
                    np.asarray(res.structure.coordinates),
                    dict(res.evidence[0]),
                )
            )
    assert len(published) >= 2, "at least two B seeds must publish"
    # Reference ring CPs.
    refs = [
        cremer_pople(c[order])
        for c, order in [(crest1, RING_RPDD), (crest2, RING_RPDD), (crest3, RING_RPDD)]
    ]
    for ref in refs:
        dists = []
        for _, coords, _ in published:
            cp = cremer_pople(np.asarray(coords)[RING_RPDD])
            dists.append(float(cp_distance(ref, cp)))
        assert min(dists) < 15.0, f"reference basin not recalled, min dist {min(dists):.2f}"
    # Per-seed audits: ester planarity, angle drift, amplitude, C1 orientation.
    rigid = analyze_rigid_units(crest1, els, graph, RING_RPDD)
    pinned = sorted(
        {
            tuple(sorted(p))
            for u in rigid.units
            for p in u.pinned_bonds
            if p[0] in set(RING_RPDD) and p[1] in set(RING_RPDD)
        }
    )
    assert len(pinned) == 2, f"rpdd must pin two ester bonds, got {pinned}"
    # Input angles for drift reference.
    from confflow.science.confgen.ring.realization import _r3_interior_angles

    input_angles = _r3_interior_angles(np.asarray(crest1)[RING_RPDD])
    sp2 = set(int(v) for v in rigid.sp2_centers)
    # C1 locks (center 0).
    c1_locks = [lk for lk in rigid.local_orientation_locks if int(lk.center) == 0]
    assert c1_locks, "C1 must carry local orientation locks"
    for _state, coords, _audit in published:
        ring_xyz = np.asarray(coords)[RING_RPDD]
        # Ester planarity: each pinned bond deviation <15 deg.
        from confflow.science.confgen.ring.realization import _r3_pinned_torsion_list

        devs = _r3_pinned_torsion_list(ring_xyz, RING_RPDD, [(int(a), int(b)) for a, b in pinned])
        for _, dev in devs:
            assert float(dev) < 15.0, f"ester not planar: {dev:.2f}"
        # Angle drift within R3 gates.
        new_angles = _r3_interior_angles(ring_xyz)
        for k, atom in enumerate(RING_RPDD):
            dev = abs(float(new_angles[k]) - float(input_angles[k]))
            lim = 3.0 if atom in sp2 else 12.0
            assert dev <= lim + 1e-9, f"angle drift {dev:.2f} > {lim} at {atom}"
        # Amplitude gate.
        rbar = float(sum(_ring_bonds_xyz(np.asarray(coords), RING_RPDD)) / len(RING_RPDD))
        cp = cremer_pople(ring_xyz)
        assert float(cp.q / rbar) >= 0.05, "puckering_amplitude must pass"
        # C1 orientation preserved (sign of each lock).
        for lk in c1_locks:
            c, p, nn, s = int(lk.center), int(lk.prev), int(lk.next), int(lk.subst)
            old_vol = float(
                np.dot(
                    crest1[p] - crest1[c], np.cross(crest1[nn] - crest1[c], crest1[s] - crest1[c])
                )
            )
            new_vol = float(
                np.dot(
                    coords[p] - coords[c], np.cross(coords[nn] - coords[c], coords[s] - coords[c])
                )
            )
            assert (old_vol > 0) == (new_vol > 0), "C1 local orientation flipped"


def test_cyclohexane_default_explicit_precise() -> None:
    """Cyclohexane: default 8 all published; B family 6; B_2 single."""
    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    seed = cp_to_coords(forms["C_0"].cp_target)
    els = ["C"] * 6
    graph = [[(k - 1) % 6, (k + 1) % 6] for k in range(6)]
    graph = [sorted(row) for row in graph]

    def _run(spec_rings: list[dict]) -> list[tuple[dict, np.ndarray]]:
        rec = _record("chx", els, seed)
        ctx = _context_for(rec, graph)
        st = RingStage({"rings": spec_rings})
        par = _parent_of(rec)
        tgts = list(st.enumerate_targets(par, ctx))
        out = []
        for tgt in tgts:
            res = st.realize(par, tgt, ctx)
            if res.status == "realized" and res.structure is not None:
                out.append((dict(tgt.state_value["r1"]), np.asarray(res.structure.coordinates)))
        return tgts, out

    tgts, pubs = _run([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    assert len(tgts) == 8, f"default must be 2C+6TB=8, got {len(tgts)}"
    assert len(pubs) == 8, f"all 8 defaults must publish on saturated ring, got {len(pubs)}"
    tgts_b, pubs_b = _run([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["B"]}])
    assert len(tgts_b) == 6, f"family B must expand to 6, got {len(tgts_b)}"
    assert len(pubs_b) == 6, f"all 6 B must publish, got {len(pubs_b)}"
    tgts_1, pubs_1 = _run([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["B_2"]}])
    assert len(tgts_1) == 1, "precise B_2 must enumerate exactly one"
    assert pubs_1 and pubs_1[0][0] == {
        "form": "B",
        "index": 2,
        "anchor": 0,
        "direction": "as_given",
    }


def test_cyclohexene_constrained_adds_h() -> None:
    """Cyclohexene (full H topology): constrained rule adds H, >=1 publishes."""
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [2.3, -1.3, 0.0],
            [1.6, -2.8, 0.3],
            [-0.1, -2.7, -0.2],
            [-0.77, -1.335, 0.0],
            [-0.545, 0.944, 0.0],
            [1.885, 0.944, 0.0],
            [3.0, -0.7, 0.5],
            [2.6, -1.9, -0.7],
            [2.0, -2.5, 1.2],
            [1.9, -3.7, -0.3],
            [-0.3, -3.4, 0.6],
            [-0.6, -2.9, -1.1],
            [-0.5, -0.6, 0.8],
            [-1.7, -1.5, -0.5],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H"] * 10
    adj: dict[int, set[int]] = {i: set() for i in range(16)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (1, 7),
        (2, 8),
        (2, 9),
        (3, 10),
        (3, 11),
        (4, 12),
        (4, 13),
        (5, 14),
        (5, 15),
    ]:
        link(a, b)
    graph = [sorted(adj[i]) for i in range(16)]
    ring = [0, 1, 2, 3, 4, 5]
    rec = _record("chexene", els, xyz)
    ctx = _context_for(rec, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": ring}]})
    par = _parent_of(rec)
    tgts = list(stage.enumerate_targets(par, ctx))
    fams = {(dict(t.state_value["r1"])["form"]) for t in tgts}
    assert "H" in fams, f"constrained must add H, got families {sorted(fams)}"
    pubs = []
    for tgt in tgts:
        res = stage.realize(par, tgt, ctx)
        if res.status == "realized" and res.structure is not None:
            pubs.append(dict(tgt.state_value["r1"]))
    h_pubs = [s for s in pubs if s["form"] == "H"]
    assert h_pubs, "at least one H must publish for cyclohexene"


def test_traversal_shift_reverse_invariance() -> None:
    """Shift/reverse: relabel-mapped (family,index) sets identical, RMSD<0.01.

    n6: all 6 cyclic shifts (forward) + all 6 reversed writings about the
    shifted anchors; n6 seed is TB_0 ideal (non-trivial solving, not
    zero-cost) so anchor-independence is proved, not hidden by fixture swap.
    """
    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    seed = cp_to_coords(forms["TB_0"].cp_target)
    els = ["C"] * 6
    graph = [sorted([(k - 1) % 6, (k + 1) % 6]) for k in range(6)]

    def _published(atoms: list[int]) -> dict[tuple[str, int], np.ndarray]:
        rec = _record("inv", els, seed)
        ctx = _context_for(rec, graph)
        st = RingStage({"rings": [{"id": "r1", "atoms": atoms}]})
        par = _parent_of(rec)
        out: dict[tuple[str, int], np.ndarray] = {}
        for tgt in st.enumerate_targets(par, ctx):
            res = st.realize(par, tgt, ctx)
            if res.status == "realized" and res.structure is not None:
                s = dict(tgt.state_value["r1"])
                out[(str(s["form"]), int(s["index"]))] = np.asarray(res.structure.coordinates)
        return out

    pool = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    base = _published([0, 1, 2, 3, 4, 5])
    assert base, "base must publish"
    # All forward cyclic shifts.
    for shift in range(1, 6):
        atoms = [(i + shift) % 6 for i in [0, 1, 2, 3, 4, 5]]
        # atoms as left-shift by `shift`: [shift, ..., 5, 0, ..., shift-1]
        atoms = list(range(shift, 6)) + list(range(0, shift))
        got = _published(atoms)
        back: dict[tuple[str, int], np.ndarray] = {}
        for (fam, idx), coords in got.items():
            orig = relabel(pool[f"{fam}_{idx}"], shift=(6 - shift) % 6)
            back[(orig.family, orig.index)] = coords
        assert set(back) == set(base), f"shift {shift}: {sorted(back)} != {sorted(base)}"
        for key in base:
            assert rigid_rmsd(back[key], base[key]) < 0.01, f"shift {shift} RMSD {key}"
    # All reversed writings: [s, s-1, ..., ] about each shifted anchor s.
    for shift in range(6):
        anchor = shift
        # Reversed order about anchor: [anchor, anchor-1, ...] mod 6.
        atoms = [(anchor - i) % 6 for i in range(6)]
        got = _published(atoms)
        # Map back: reverse again then shift back. relabel applies shift
        # first then reverse, so inverse is reverse then shift-back.
        back_r: dict[tuple[str, int], np.ndarray] = {}
        for (fam, idx), coords in got.items():
            tmp = relabel(pool[f"{fam}_{idx}"], reverse=True)
            orig = relabel(tmp, shift=(6 - shift) % 6)
            back_r[(orig.family, orig.index)] = coords
        assert set(back_r) == set(base), f"reverse shift {shift}: mismatch"
        for key in base:
            assert rigid_rmsd(back_r[key], base[key]) < 0.01, f"reverse {shift} RMSD {key}"


def test_traversal_n5_shifts_and_reverse() -> None:
    """n5: cyclic shifts + both directions on a math fixture, RMSD<0.01."""
    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(5)}
    seed = cp_to_coords(forms["E_0"].cp_target)
    els = ["C"] * 5
    graph = [sorted([(k - 1) % 5, (k + 1) % 5]) for k in range(5)]

    def _published(atoms: list[int]) -> dict[tuple[str, int], np.ndarray]:
        rec = _record("inv5", els, seed)
        ctx = _context_for(rec, graph)
        st = RingStage({"rings": [{"id": "r1", "atoms": atoms}]})
        par = _parent_of(rec)
        out: dict[tuple[str, int], np.ndarray] = {}
        for tgt in st.enumerate_targets(par, ctx):
            res = st.realize(par, tgt, ctx)
            if res.status == "realized" and res.structure is not None:
                s = dict(tgt.state_value["r1"])
                out[(str(s["form"]), int(s["index"]))] = np.asarray(res.structure.coordinates)
        return out

    pool = {f"{f.family}_{f.index}": f for f in canonical_forms(5)}
    base = _published([0, 1, 2, 3, 4])
    assert base, "n5 base must publish"
    for shift in (1, 2, 3, 4):
        atoms = list(range(shift, 5)) + list(range(0, shift))
        got = _published(atoms)
        back = {}
        for (fam, idx), coords in got.items():
            orig = relabel(pool[f"{fam}_{idx}"], shift=(5 - shift) % 5)
            back[(orig.family, orig.index)] = coords
        assert set(back) == set(base), f"n5 shift {shift} mismatch"
        for key in base:
            assert rigid_rmsd(back[key], base[key]) < 0.01, f"n5 shift {shift} RMSD {key}"
    # Reverse about every anchor 0..4.
    for anchor in (0, 1, 2, 3, 4):
        atoms = [(anchor - i) % 5 for i in range(5)]
        got = _published(atoms)
        back_r = {}
        for (fam, idx), coords in got.items():
            tmp = relabel(pool[f"{fam}_{idx}"], reverse=True)
            orig = relabel(tmp, shift=(5 - anchor) % 5)
            back_r[(orig.family, orig.index)] = coords
        assert set(back_r) == set(base), f"n5 reverse anchor {anchor} mismatch"
        for key in base:
            assert rigid_rmsd(back_r[key], base[key]) < 0.01, f"n5 reverse {anchor} RMSD {key}"


def test_traversal_rpdd_substituent_invariance() -> None:
    """Rpdd with substituents: relabelled sets match; atom ids/planarity/locks hold."""
    els, crest1 = _load_xyz(CREST, 0)
    graph = _rpdd_adjacency()
    rec = _record("rpdd-base", els, crest1)
    ctx = _context_for(rec, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": RING_RPDD}]})
    par = _parent_of(rec)
    base_targets = list(stage.enumerate_targets(par, ctx))
    assert len(base_targets) == 10
    base_pubs: dict[tuple[str, int], np.ndarray] = {}
    for tgt in base_targets:
        res = stage.realize(par, tgt, ctx)
        if res.status == "realized" and res.structure is not None:
            s = dict(tgt.state_value["r1"])
            base_pubs[(str(s["form"]), int(s["index"]))] = np.asarray(res.structure.coordinates)
    assert len(base_pubs) >= 2
    # Shifted writing of the same 6-membered cycle (left-shift by 2 in
    # traversal order, same global atom ids).
    shifted_atoms = [
        RING_RPDD[2],
        RING_RPDD[3],
        RING_RPDD[4],
        RING_RPDD[5],
        RING_RPDD[0],
        RING_RPDD[1],
    ]
    stage2 = RingStage({"rings": [{"id": "r1", "atoms": shifted_atoms}]})
    shift_targets = list(stage2.enumerate_targets(par, ctx))
    assert len(shift_targets) == len(base_targets)
    pool6 = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    got: dict[tuple[str, int], np.ndarray] = {}
    for tgt in shift_targets:
        res = stage2.realize(par, tgt, ctx)
        if res.status == "realized" and res.structure is not None:
            s = dict(tgt.state_value["r1"])
            got[(str(s["form"]), int(s["index"]))] = np.asarray(res.structure.coordinates)
    back = {}
    for (fam, idx), coords in got.items():
        orig = relabel(pool6[f"{fam}_{idx}"], shift=4)
        back[(orig.family, orig.index)] = coords
    assert set(back) == set(base_pubs)
    for key in base_pubs:
        assert rigid_rmsd(back[key], base_pubs[key]) < 0.01, f"rpdd shift RMSD {key}"
    # Atom ids, ester planarity and C1 locks unchanged on a recalled seed.
    rigid = analyze_rigid_units(crest1, els, graph, RING_RPDD)
    pinned = sorted({tuple(sorted(p)) for u in rigid.units for p in u.pinned_bonds})
    assert len(pinned) == 2
    c1_locks = [lk for lk in rigid.local_orientation_locks if int(lk.center) == 0]
    assert c1_locks
    sample = base_pubs[next(iter(base_pubs))]
    assert sample.shape[0] == 22, "full 22-atom structure preserved"
    for a, b in pinned:
        from confflow.science.confgen.ring.realization import _r3_pinned_torsion_list

        devs = _r3_pinned_torsion_list(sample[RING_RPDD], RING_RPDD, [(int(a), int(b))])
        assert all(float(d) < 15.0 for _, d in devs)


def test_rpdd_fragment_c2_never_locked_and_distorted() -> None:
    """Truncated fragment: C2 (atom 1) never locked; distorted_input present."""
    els, frag = _load_xyz(FRAG, 0)
    graph = _rpdd_adjacency()
    res = analyze_rigid_units(frag, els, graph, RING_RPDD)
    assert not [
        lk for lk in res.local_orientation_locks if int(lk.center) == 1
    ], "C2 (sp2) must never lock"
    assert [
        d for d in res.distorted_input if int(d.atom) == 1
    ], "C2 must report distorted_input (339 deg sum)"
    # Stage-level: all enumerated seeds carry no C2 stereo state.
    record = _record("frag", els, frag)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": RING_RPDD}]})
    parent = _parent_of(record)
    for tgt in stage.enumerate_targets(parent, context):
        st = dict(tgt.state_value["r1"])
        assert "C2" not in str(st)
        res_stage = stage.realize(parent, tgt, context)
        # Either publishes or fails with explicit audit; never silent C2 stereo.
        if res_stage.status == "realized":
            assert res_stage.structure is not None


def _v3_doc(*, n: int, forms: list) -> dict:
    """Build a public ConfgenModelV3 document (1-based atoms, index_base 1)."""
    return {
        "schema_version": 3,
        "index_base": 1,
        "rings": [{"id": "r", "atoms": list(range(1, n + 1)), "forms": list(forms)}],
    }


def test_schema_all_61_precise_selectors_pass_public_validation() -> None:
    """All 61 canonical precise selectors pass RingGenerationSpec + V3 (v2 fix)."""
    from confflow.science.confgen.ring.forms import FORM_NAMES_BY_SIZE
    from confflow.workflow.v4.confgen_schema import ConfgenModelV3, RingGenerationSpec

    total = 0
    for n, names in sorted(FORM_NAMES_BY_SIZE.items()):
        for name in names:
            total += 1
            # Direct spec (0-based atoms) + full public V3 doc (1-based).
            RingGenerationSpec(id="r", atoms=list(range(n)), forms=[name])
            ConfgenModelV3.model_validate(_v3_doc(n=n, forms=[name]))
            # Run-side directory resolves identically for the same size.
            from confflow.science.confgen.ring.forms import expand_form_tokens

            got = expand_form_tokens([name], n)
            assert len(got) == 1 and f"{got[0].family}_{got[0].index}" == name
    assert total == 61
    # Explicit-P specials outside 20/38: P/P_0 valid for 4/5/6 via both paths.
    for n in (4, 5, 6):
        for token in ("P", "P_0"):
            RingGenerationSpec(id="r", atoms=list(range(n)), forms=[token])
            ConfgenModelV3.model_validate(_v3_doc(n=n, forms=[token]))


def test_schema_cross_size_and_invalid_rejected() -> None:
    """Cross-size, illegal, duplicate and conflict inputs stay fail-closed."""
    import pytest as _pt
    from pydantic import ValidationError as _VE

    from confflow.workflow.v4.confgen_schema import ConfgenModelV3, RingGenerationSpec

    # 5/6 shared E names resolve per size (v1 bug: n5 E_* misreported as size 6).
    for k in range(10):
        RingGenerationSpec(id="r", atoms=list(range(5)), forms=[f"E_{k}"])
        RingGenerationSpec(id="r", atoms=list(range(6)), forms=[f"E_{k}"])
        ConfgenModelV3.model_validate(_v3_doc(n=5, forms=[f"E_{k}"]))
        ConfgenModelV3.model_validate(_v3_doc(n=6, forms=[f"E_{k}"]))
    # Cross-size precise rejected: n6-only TB_0 on n5; n5-only T_0 on n6; C_0 on n5.
    for n, bad in ((5, "TB_0"), (6, "T_0"), (5, "C_0"), (4, "E_0")):
        size = 4 if bad == "E_0" and n == 4 else n
        with _pt.raises(_VE):
            RingGenerationSpec(id="r", atoms=list(range(size)), forms=[bad])
        with _pt.raises(_VE):
            ConfgenModelV3.model_validate(_v3_doc(n=size, forms=[bad]))
    # Illegal index/family rejected on both paths.
    for n, bad in ((6, "B_6"), (6, "X"), (6, "B_x"), (5, "H_0"), (4, "C_0")):
        with _pt.raises(_VE):
            RingGenerationSpec(id="r", atoms=list(range(n)), forms=[bad])
        with _pt.raises(_VE):
            ConfgenModelV3.model_validate(_v3_doc(n=n, forms=[bad]))
    # Duplicates rejected.
    with _pt.raises(_VE):
        RingGenerationSpec(id="r", atoms=list(range(6)), forms=["B_0", "B_0"])
    # templates+forms conflict rejected.
    with _pt.raises(_VE):
        RingGenerationSpec(id="r", atoms=list(range(6)), templates=["chair_A_6"], forms=["B_0"])
    # Bool / non-string rejected (StrictStr fail-closed).
    with _pt.raises(_VE):
        RingGenerationSpec(id="r", atoms=list(range(6)), forms=[True])  # type: ignore[list-item]
    with _pt.raises(_VE):
        RingGenerationSpec(id="r", atoms=list(range(6)), forms=[123])  # type: ignore[list-item]
    # Unknown template alias still rejected.
    with _pt.raises(_VE):
        RingGenerationSpec(id="r", atoms=list(range(6)), templates=["sofa_6"])


def test_planar_hexagon_negative_structured_failure() -> None:
    """Old planar 6C input (verbatim) fails structurally, never silently.

    Root-allowed companion to integration nested-rows: the executor units
    test moved to a credible CP chair, while this negative preserves the
    old planar hexagon byte-for-byte and proves R3/CP audits reject it
    with explicit reasons (no fabricated structures, no solver retuning).
    """
    import math as _math

    radius = 1.54
    coords = [
        (radius * _math.cos(k * _math.pi / 3), radius * _math.sin(k * _math.pi / 3), 0.0)
        for k in range(6)
    ]
    methyl = (radius + 1.54, 0.0, 0.0)
    coords.append(methyl)
    coords.append((methyl[0] + 1.09 * _math.cos(1.2), 1.09 * _math.sin(1.2), 0.35))
    els = ["C"] * 7 + ["H"]
    graph = [[1, 5, 6], [0, 2], [1, 3], [2, 4], [3, 5], [4, 0], [0, 7], [6]]
    graph = [sorted(row) for row in graph]
    rec = _record("planar-neg", els, np.asarray(coords, dtype=float))
    ctx = _context_for(rec, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    par = _parent_of(rec)
    targets = list(stage.enumerate_targets(par, ctx))
    assert len(targets) == 8
    published = 0
    reasons: list[str] = []
    for tgt in targets:
        res = stage.realize(par, tgt, ctx)
        if res.status == "realized":
            published += 1
        else:
            reasons.append(str(res.reason))
    assert published == 0, "planar input must not publish under CP audits"
    assert reasons and all(isinstance(r, str) and r for r in reasons)


def test_clash_threshold_passthrough_to_r3() -> None:
    """Stage/context clash_threshold priority reaches R3 clash_pairs.

    Old v1 called R3 with tolerances=None (repro: passed_tolerances None,
    default .65 despite .4 declared). v3 used non-default-wins mixing, which
    drops an explicit stage .65 against context .4 and lets context .5
    override an explicit stage .4. Explicit stage value (key present,
    including .65) always wins; omitted stage defers to context; both
    default stays .65. The spy runs through the real stage and asserts the
    value R3 actually audits with.
    """
    from confflow.science.confgen.ring import realization as _real

    seen: list[float] = []
    _orig = _real.clash_pairs

    def _spy(coords, elements, topo, threshold=0.65):
        seen.append(float(threshold))
        return _orig(coords, elements, topo, threshold=threshold)

    els = ["C"] * 6
    graph = [sorted([(k - 1) % 6, (k + 1) % 6]) for k in range(6)]
    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    seed = cp_to_coords(forms["C_0"].cp_target)
    cases: list[tuple[dict | None, dict | None, float]] = [
        ({"clash_threshold": 0.4}, {"clash_threshold": 0.4}, 0.4),
        ({"clash_threshold": 0.65}, {"clash_threshold": 0.4}, 0.65),
        ({"clash_threshold": 0.4}, {"clash_threshold": 0.5}, 0.4),
        (None, {"clash_threshold": 0.4}, 0.4),
        (None, None, 0.65),
    ]
    try:
        _real.clash_pairs = _spy  # type: ignore[method-assign]
        for raw_tol, ctx_tol, want in cases:
            seen.clear()
            raw: dict = {"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]}
            if raw_tol is not None:
                raw["tolerances"] = dict(raw_tol)
            rec = _record(f"clash-{want}-{len(seen)}", els, seed)
            ctx = _context_for(rec, graph, extra={"tolerances": dict(ctx_tol)} if ctx_tol else None)
            stage = RingStage(raw)
            par = _parent_of(rec)
            tgt = next(stage.enumerate_targets(par, ctx))
            stage.realize(par, tgt, ctx)
            assert seen and seen[-1] == want, (
                f"raw={raw_tol} ctx={ctx_tol}: R3 must audit with {want}, "
                f"got {seen[-1:] if seen else seen}"
            )
    finally:
        _real.clash_pairs = _orig
