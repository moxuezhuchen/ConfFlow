#!/usr/bin/env python3
"""R3 CP-constrained realization (logic card, behavior-none vs production).

Only ``realize_cp_target`` (plus its private helpers) is exercised here;
the legacy ``realize_single_system``/``realize_rings`` path is untouched and
covered by the pre-existing suite. Fixtures are read-only (F1); no new
fixture files are added. Thresholds are asserted, never tuned.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import numpy as np
import pytest

from confflow.science.confgen.ring.puckering import (
    CanonicalForm,
    CPCoords,
    canonical_forms,
    cp_distance,
    cp_to_coords,
    cremer_pople,
)
from confflow.science.confgen.ring.realization import (
    _R3_AUDIT_ORDER,
    _R3_CP_REACHED_MAX_DEG,
    _R3_PHASE_DEFINED_Q_MIN,
    _R3_SOLVER_FTOL,
    _R3_SOLVER_GTOL,
    _R3_SOLVER_MAX_NFEV,
    _R3_SOLVER_XTOL,
    RingGeometryFailure,
    RingNumericalFailure,
    RingSpec,
    RingUnsupported,
    _propagate_substituents_rigid,
    realize_cp_target,
)
from confflow.science.confgen.ring.rigid_units import analyze_rigid_units

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "ring" / "rpdd"
FRAG = FIX / "input_ts_fragment.xyz"
CREST = FIX / "crest_conformers.xyz"

RING_RPDD = [0, 1, 3, 4, 5, 7]
SEVEN = (
    "ring_bond_drift",
    "ring_angle_drift",
    "conjugation_planarity",
    "puckering_amplitude",
    "cp_reached",
    "local_orientation",
    "clash",
)


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


def _rpdd_adjacency() -> dict[int, set[int]]:
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
    return adj


def _reason_token(exc: BaseException) -> str:
    text = str(exc)
    assert text and not text[0].isspace()
    return text.split()[0].split(":")[0]


def _cyclohexane_full(cp) -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = cp_to_coords(cp)
    full = np.zeros((18, 3))
    full[:6] = xyz
    for k in range(6):
        x, y, z = xyz[k]
        full[6 + 2 * k] = [x * 1.2, y * 1.2, z + 0.9]
        full[6 + 2 * k + 1] = [x * 1.2, y * 1.2, z - 0.9]
    els = ["C"] * 6 + ["H"] * 12
    adj: dict[int, set[int]] = {i: set() for i in range(18)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for k in range(6):
        link(k, (k + 1) % 6)
        link(k, 6 + 2 * k)
        link(k, 6 + 2 * k + 1)
    return full, els, adj, [0, 1, 2, 3, 4, 5]


def _cyclobutane_full(cp) -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = cp_to_coords(cp)
    full = np.zeros((12, 3))
    full[:4] = xyz
    for k in range(4):
        x, y, z = xyz[k]
        full[4 + 2 * k] = [x * 1.2, y * 1.2, z + 0.9]
        full[4 + 2 * k + 1] = [x * 1.2, y * 1.2, z - 0.9]
    els = ["C"] * 4 + ["H"] * 8
    adj: dict[int, set[int]] = {i: set() for i in range(12)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for k in range(4):
        link(k, (k + 1) % 4)
        link(k, 4 + 2 * k)
        link(k, 4 + 2 * k + 1)
    return full, els, adj, [0, 1, 2, 3]


class TestRpddCrest1:
    def test_two_b_forms_succeed_with_ester_zero_torsions(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        before = xyz.copy()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        pins = sorted(b for u in res.units for b in u.pinned_bonds)
        assert (1, 3) in pins and (5, 7) in pins
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        # Ring positions of the two ester pins: (1,3)->(1,2)="1-2",
        # (5,7)->(4,5)="4-5" in traversal-position labels.
        wanted = {"1-2", "4-5"}
        wins: list[str] = []
        for form in canonical_forms(6):
            if form.family != "B":
                continue
            if not wanted.issubset(set(form.zero_torsion_bonds)):
                continue
            out, state, audit = realize_cp_target(xyz, els, adj, spec, form, rigid_units=res)
            assert audit["ring_id"] == "rpdd"
            for name in SEVEN:
                assert name in audit, name
                assert audit[name]["passed"] is True, (form.family, form.index, name)
            back = cremer_pople(np.asarray(out)[RING_RPDD])
            assert cp_distance(back, form.cp_target) <= 15.0
            wins.append(f"{form.family}{form.index}")
        assert len(wins) == 2
        assert np.array_equal(xyz, before)

    def test_c_and_tb_fail_with_puckering_amplitude(self) -> None:
        # v2 correction: v1 predicted `conjugation_planarity` for all 8
        # C/TB targets, but the frozen-weight measurement shows all 8 fail
        # with `puckering_amplitude` (q/rbar 0.035-0.048 < 0.05). The old
        # exact-reason prediction does not hold; see v2 card correction
        # with the v1 failure evidence (EVIDENCE-T1-gap.json: 8 amplitude
        # failures, cp_reached falsely passed in v1, now not_evaluated).
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        targets = [f for f in canonical_forms(6) if f.family in ("C", "TB")]
        assert len(targets) == 8
        for form in targets:
            with pytest.raises(RingGeometryFailure) as excinfo:
                realize_cp_target(xyz, els, adj, spec, form, rigid_units=res)
            # Explicit true-reason check: not a broad "any exception" pass.
            assert _reason_token(excinfo.value) == "puckering_amplitude"
            audit = excinfo.value.audit
            for name in SEVEN:
                assert name in audit, (form.family, form.index, name)
            amp = audit["puckering_amplitude"]
            assert amp["passed"] is False
            assert amp["exempt"] is False
            assert amp["q_over_rbar"] < 0.05
            # No false cp pass after an amplitude failure.
            cp = audit["cp_reached"]
            assert cp["passed"] is False, (form.family, form.index)
            assert cp.get("status") == "not_evaluated", (form.family, form.index)


class TestCyclohexane:
    def test_two_c_six_b_six_tb_all_succeed(self) -> None:
        forms6 = canonical_forms(6)
        chair = next(f for f in forms6 if (f.family, f.index) == ("C", 0))
        full, els, adj, ring = _cyclohexane_full(chair.cp_target)
        before = full.copy()
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        targets = [f for f in forms6 if f.family in ("C", "B", "TB")]
        assert len(targets) == 14
        for form in targets:
            out, state, audit = realize_cp_target(full, els, adj, spec, form, rigid_units=res)
            for name in SEVEN:
                assert audit[name]["passed"] is True, (form.family, form.index, name)
            amp = audit["puckering_amplitude"]
            assert amp["exempt"] is False
            assert amp["q_over_rbar"] >= 0.05
            back = cremer_pople(np.asarray(out)[ring])
            assert cp_distance(back, form.cp_target) <= 15.0
        assert np.array_equal(full, before)

    def test_explicit_b_off_default_amplitude_succeeds(self) -> None:
        forms6 = canonical_forms(6)
        chair = next(f for f in forms6 if (f.family, f.index) == ("C", 0))
        ref_b = next(f for f in forms6 if (f.family, f.index) == ("B", 0))
        full, els, adj, ring = _cyclohexane_full(chair.cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        explicit = CanonicalForm(
            n=6,
            family="B",
            index=99,
            cp_target=CPCoords(n=6, q=0.45, theta=ref_b.cp_target.theta, phi=ref_b.cp_target.phi),
            zero_torsion_bonds=(),
        )
        out, state, audit = realize_cp_target(full, els, adj, spec, explicit, rigid_units=res)
        assert audit["cp_reached"]["passed"] is True
        assert audit["puckering_amplitude"]["q_over_rbar"] >= 0.05


class TestDegenerateThreshold:
    def test_planar_input_to_tb_either_puckers_or_reports_amplitude(self) -> None:
        rbar = 1.45
        radius = rbar / (2.0 * np.sin(np.pi / 6))
        ring_xyz = np.array(
            [
                [radius * np.cos(k * np.pi / 3), radius * np.sin(k * np.pi / 3), 0.0]
                for k in range(6)
            ]
        )
        full = np.zeros((18, 3))
        full[:6] = ring_xyz
        for k in range(6):
            x, y = ring_xyz[k, 0], ring_xyz[k, 1]
            full[6 + 2 * k] = [x * 1.3, y * 1.3, 0.9]
            full[6 + 2 * k + 1] = [x * 1.3, y * 1.3, -0.9]
        els = ["C"] * 6 + ["H"] * 12
        adj: dict[int, set[int]] = {i: set() for i in range(18)}

        def link(a: int, b: int) -> None:
            adj[a].add(b)
            adj[b].add(a)

        for k in range(6):
            link(k, (k + 1) % 6)
            link(k, 6 + 2 * k)
            link(k, 6 + 2 * k + 1)
        ring = [0, 1, 2, 3, 4, 5]
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="flat", atoms=tuple(ring))
        target = next(f for f in canonical_forms(6) if (f.family, f.index) == ("TB", 0))
        try:
            out, state, audit = realize_cp_target(full, els, adj, spec, target, rigid_units=res)
        except RingGeometryFailure as exc:
            assert _reason_token(exc) == "puckering_amplitude"
            assert exc.audit["puckering_amplitude"]["q_over_rbar"] < 0.05
            # v2: an amplitude failure must not claim a cp pass.
            assert exc.audit["cp_reached"]["passed"] is False
            assert exc.audit["cp_reached"].get("status") == "not_evaluated"
        else:
            assert audit["puckering_amplitude"]["q_over_rbar"] >= 0.05
            assert audit["cp_reached"]["passed"] is True

    def test_never_low_amplitude_with_cp_pass(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        target = next(f for f in canonical_forms(6) if (f.family, f.index) == ("C", 0))
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(xyz, els, adj, spec, target, rigid_units=res)
        audit = excinfo.value.audit
        qor = audit["puckering_amplitude"]["q_over_rbar"]
        assert qor < 0.05
        # Audit order regression: amplitude precedes cp_reached, so a
        # low-amplitude outcome is reported as an amplitude failure and
        # never as a silent pass.
        assert _reason_token(excinfo.value) == "puckering_amplitude"
        assert audit["puckering_amplitude"]["passed"] is False
        # v2 completeness: cp_reached is not evaluated after amplitude fail.
        assert audit["cp_reached"]["passed"] is False
        assert audit["cp_reached"].get("status") == "not_evaluated"
        assert audit["cp_reached"].get("skipped_after") == "puckering_amplitude"


class TestFragmentNoC2Lock:
    def test_gjf_fragment_c2_sp2_without_lock_all_forms_audited(self) -> None:
        els, xyz = _load_xyz(FRAG)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        assert 1 in res.sp2_centers
        assert [lk for lk in res.local_orientation_locks if lk.center == 1] == []
        assert len([lk for lk in res.local_orientation_locks if lk.center == 0]) >= 1
        spec = RingSpec(id="frag", atoms=tuple(RING_RPDD))
        n_pass = 0
        for form in canonical_forms(6):
            try:
                out, state, audit = realize_cp_target(xyz, els, adj, spec, form, rigid_units=res)
            except (RingGeometryFailure, RingNumericalFailure, RingUnsupported) as exc:
                audit = getattr(exc, "audit", None)
                assert audit is not None
                for name in SEVEN:
                    assert name in audit, (form.family, form.index, name)
                continue
            n_pass += 1
            pairs = audit["local_orientation"]["locks_checked"]
            assert all(c != 1 for c, _s in pairs)
            assert set(state) == {"ring_id", "family", "index", "n", "rbar", "start_index"}
        assert n_pass >= 1


class TestN4Caliber:
    def test_cp_distance_is_angstrom_and_sign_path(self) -> None:
        forms4 = canonical_forms(4)
        table = {f.family: f for f in forms4}
        gap = cp_distance(table["B+"].cp_target, table["B-"].cp_target)
        assert gap > 0.0
        assert gap == pytest.approx(0.6, abs=1e-9)
        full, els, adj, ring = _cyclobutane_full(table["B+"].cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c4", atoms=tuple(ring))
        out, state, audit = realize_cp_target(full, els, adj, spec, table["B+"], rigid_units=res)
        assert audit["puckering_amplitude"]["exempt"] is False
        assert audit["puckering_amplitude"]["q_over_rbar"] >= 0.05
        reached = audit["cp_reached"]
        assert "distance" not in reached
        assert (reached["want_sign"], reached["got_sign"]) == (1, 1)

    def test_planar_p_exempt_while_b_requires_amplitude(self) -> None:
        forms4 = canonical_forms(4)
        table = {f.family: f for f in forms4}
        full_p, els, adj, ring = _cyclobutane_full(table["P"].cp_target)
        res = analyze_rigid_units(full_p, els, adj, ring)
        spec = RingSpec(id="c4", atoms=tuple(ring))
        out, state, audit = realize_cp_target(full_p, els, adj, spec, table["P"], rigid_units=res)
        assert audit["puckering_amplitude"]["exempt"] is True
        assert audit["cp_reached"]["passed"] is True
        assert audit["cp_reached"]["q_over_rbar"] <= 0.05

    def test_n4_sign_only_no_degree_gate(self) -> None:
        forms4 = canonical_forms(4)
        table = {f.family: f for f in forms4}
        full, els, adj, ring = _cyclobutane_full(table["B-"].cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c4", atoms=tuple(ring))
        out, state, audit = realize_cp_target(full, els, adj, spec, table["B+"], rigid_units=res)
        assert audit["start_index"] == 1
        assert audit["cp_reached"]["got_sign"] == 1


class TestExplicitP:
    def test_n4_p_from_puckered_input_flattens_without_amplitude_kill(self) -> None:
        forms4 = canonical_forms(4)
        table = {f.family: f for f in forms4}
        full, els, adj, ring = _cyclobutane_full(table["B+"].cp_target)
        before = full.copy()
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c4", atoms=tuple(ring))
        out, state, audit = realize_cp_target(full, els, adj, spec, table["P"], rigid_units=res)
        assert audit["puckering_amplitude"]["exempt"] is True
        assert audit["cp_reached"]["q_over_rbar"] <= 0.05
        assert np.array_equal(full, before)

    def test_n6_custom_p_special_call_compatible(self) -> None:
        forms6 = canonical_forms(6)
        chair = next(f for f in forms6 if (f.family, f.index) == ("C", 0))
        full, els, adj, ring = _cyclohexane_full(chair.cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        explicit_p = CanonicalForm(
            n=6,
            family="P",
            index=0,
            cp_target=CPCoords(n=6, q=0.0, theta=0.0, phi=0.0),
            zero_torsion_bonds=(),
        )
        out, state, audit = realize_cp_target(full, els, adj, spec, explicit_p, rigid_units=res)
        assert audit["puckering_amplitude"]["exempt"] is True
        assert audit["cp_reached"]["passed"] is True


def _link(adj: dict[int, set[int]], a: int, b: int) -> None:
    adj[a].add(b)
    adj[b].add(a)


class TestFollowEquivalence:
    def _oracle_propagate(self, old, new, ring_order, substituents):
        from confflow.science.confgen.ring.geometry import (
            local_frame,
            rotation_between_frames,
        )

        size = len(ring_order)
        for position, atom in enumerate(ring_order):
            prev_old = old[ring_order[(position - 1) % size]]
            next_old = old[ring_order[(position + 1) % size]]
            prev_new = new[ring_order[(position - 1) % size]]
            next_new = new[ring_order[(position + 1) % size]]
            frame_old = local_frame(prev_old, old[atom], next_old)
            frame_new = local_frame(prev_new, new[atom], next_new)
            step = rotation_between_frames(frame_old, frame_new)
            for sub in substituents:
                if sub.anchor != atom:
                    continue
                for member in sub.members:
                    new[member] = new[atom] + step @ (old[member] - old[atom])

    def test_new_helper_matches_legacy_oracle(self) -> None:
        from confflow.science.confgen.ring.geometry import partition_substituents

        rng = np.random.default_rng(20261005)
        for _trial in range(3):
            base = np.array([[np.cos(k * np.pi / 3), np.sin(k * np.pi / 3), 0.0] for k in range(6)])
            ring_xyz = base + rng.normal(0, 0.15, size=(6, 3))
            n_atoms = 6 + 4
            adj = {i: set() for i in range(n_atoms)}
            for k in range(6):
                _link(adj, k, (k + 1) % 6)
            for j, anchor in enumerate([0, 2, 3, 5]):
                _link(adj, anchor, 6 + j)
            old = np.zeros((n_atoms, 3))
            old[:6] = ring_xyz
            for j, anchor in enumerate([0, 2, 3, 5]):
                old[6 + j] = ring_xyz[anchor] + rng.normal(0, 0.5, size=3) + [0.0, 0.0, 1.0]
            graph = [sorted(adj[i]) for i in range(n_atoms)]
            subs, multi = partition_substituents(n_atoms, graph, frozenset(range(6)))
            assert multi == []
            ring_order = list(range(6))
            shift = rng.normal(0, 0.1, size=(6, 3))
            new_a = old.copy()
            new_a[ring_order] = old[ring_order] + shift
            new_b = new_a.copy()
            _propagate_substituents_rigid(old, new_a, ring_order, subs)
            self._oracle_propagate(old, new_b, ring_order, subs)
            assert np.allclose(new_a, new_b, rtol=1e-12, atol=0.0)

    def test_multi_anchor_stays_unsupported(self) -> None:
        from confflow.science.confgen.ring.geometry import partition_substituents

        els = ["C"] * 6 + ["C"]
        adj = {i: set() for i in range(7)}

        def link(a: int, b: int) -> None:
            adj[a].add(b)
            adj[b].add(a)

        for k in range(6):
            link(k, (k + 1) % 6)
        link(0, 6)
        link(3, 6)
        graph = [sorted(adj[i]) for i in range(7)]
        _subs, multi = partition_substituents(7, graph, frozenset(range(6)))
        assert len(multi) == 1
        full = np.zeros((7, 3))
        full[:6] = cp_to_coords(next(f for f in canonical_forms(6) if f.family == "B").cp_target)
        full[6] = [5.0, 5.0, 5.0]
        res = analyze_rigid_units(full, els, adj, [0, 1, 2, 3, 4, 5])
        spec = RingSpec(id="bridge", atoms=(0, 1, 2, 3, 4, 5))
        form = next(f for f in canonical_forms(6) if f.family == "B")
        with pytest.raises(RingUnsupported):
            realize_cp_target(full, els, adj, spec, form, rigid_units=res)

    def test_inputs_are_never_mutated(self) -> None:
        forms6 = canonical_forms(6)
        chair = next(f for f in forms6 if (f.family, f.index) == ("C", 0))
        boat = next(f for f in forms6 if (f.family, f.index) == ("B", 0))
        full, els, adj, ring = _cyclohexane_full(chair.cp_target)
        coords_before = full.copy()
        els_before = list(els)
        adj_before = copy.deepcopy(adj)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        realize_cp_target(full, els, adj, spec, boat, rigid_units=res)
        assert np.array_equal(full, coords_before)
        assert els == els_before
        assert adj == adj_before


class TestMultistart:
    def test_first_passing_start_wins(self) -> None:
        forms6 = canonical_forms(6)
        chair = next(f for f in forms6 if (f.family, f.index) == ("C", 0))
        full, els, adj, ring = _cyclohexane_full(chair.cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        _out, _state, audit = realize_cp_target(full, els, adj, spec, chair, rigid_units=res)
        assert audit["start_index"] == 0

    def test_mirror_start_wins_when_input_fails(self) -> None:
        forms6 = canonical_forms(6)
        b0 = next(f for f in forms6 if (f.family, f.index) == ("B", 0))
        b3 = next(f for f in forms6 if (f.family, f.index) == ("B", 3))
        full, els, adj, ring = _cyclohexane_full(b0.cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        _out, _state, audit = realize_cp_target(full, els, adj, spec, b3, rigid_units=res)
        assert audit["start_index"] == 1
        assert audit["cp_reached"]["passed"] is True

    def test_all_fail_reports_min_residual_audit(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        target = next(f for f in canonical_forms(6) if (f.family, f.index) == ("C", 0))
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(xyz, els, adj, spec, target, rigid_units=res)
        audit = excinfo.value.audit
        assert audit["start_index"] in (0, 1)
        assert "residual_cost" in audit
        assert set(audit["solver"]) >= {"cost", "nfev"}

    def test_bad_additional_starts_rejected(self) -> None:
        forms6 = canonical_forms(6)
        chair = next(f for f in forms6 if (f.family, f.index) == ("C", 0))
        full, els, adj, ring = _cyclohexane_full(chair.cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        with pytest.raises(RingNumericalFailure):
            realize_cp_target(
                full, els, adj, spec, chair, rigid_units=res, additional_starts=[np.zeros((5, 3))]
            )


class TestReasonNaming:
    def test_reason_first_token_is_audit_item(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        seen: set[str] = set()
        for form in canonical_forms(6):
            try:
                realize_cp_target(xyz, els, adj, spec, form, rigid_units=res)
            except RingGeometryFailure as exc:
                token = _reason_token(exc)
                assert token in SEVEN, token
                seen.add(token)
        assert "puckering_amplitude" in seen

    def test_clash_reason_names_clash_item(self) -> None:
        forms6 = canonical_forms(6)
        b0 = next(f for f in forms6 if (f.family, f.index) == ("B", 0))
        full, els, adj, ring = _cyclohexane_full(b0.cp_target)
        res = analyze_rigid_units(full, els, adj, ring)
        spec = RingSpec(id="c6", atoms=tuple(ring))
        full[12] = full[6].copy()
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(full, els, adj, spec, b0, rigid_units=res)
        assert _reason_token(excinfo.value) == "clash"


class TestLocalOrientationDegenerate:
    """v2 root fix: locked zero/sign-mismatch must fail (no new_vol==0 skip).

    Real trigger: an R2 orientation lock (|V|>0.3, sign fixed) on a nearly
    planar seed. The protective negatives below inject degenerate external
    coordinates (coincident / mirrored substituent) so the solver still
    runs on real ring variables; the failure is a genuine
    ``local_orientation`` audit failure, not a mocked-solver pass.
    No 0.3 output magnitude threshold is added; the old-volume non-zero
    condition comes from the lock itself.
    """

    def _b_targets(self):  # type: ignore[no-untyped-def]
        forms6 = canonical_forms(6)
        return [
            f
            for f in forms6
            if f.family == "B" and {"1-2", "4-5"}.issubset(set(f.zero_torsion_bonds))
        ]

    def test_zero_external_volume_fails_not_skipped(self) -> None:
        from confflow.science.confgen.ring.realization import _signed_volume

        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        target = self._b_targets()[0]
        # Positive control first: this B target genuinely passes.
        _out, _state, good = realize_cp_target(xyz, els, adj, spec, target, rigid_units=res)
        assert good["local_orientation"]["passed"] is True
        assert good["local_orientation"]["checked"] == 4
        # Degenerate injection: collapse subst 9 onto its center 0 so the
        # locked volume is exactly 0.0. Old code `if old==0 or new==0:
        # continue` would have silently skipped this lock (checked 3,
        # passed True); v2 must fail with local_orientation.
        bad = xyz.copy()
        bad[9] = xyz[0]
        lock = next(lk for lk in res.local_orientation_locks if (lk.center, lk.subst) == (0, 9))
        assert (
            _signed_volume(xyz[lock.center], xyz[lock.prev], xyz[lock.next], xyz[lock.subst]) != 0.0
        )
        assert (
            _signed_volume(bad[lock.center], bad[lock.prev], bad[lock.next], bad[lock.subst]) == 0.0
        )
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(bad, els, adj, spec, target, rigid_units=res)
        assert _reason_token(excinfo.value) == "local_orientation"
        audit = excinfo.value.audit
        assert audit["local_orientation"]["passed"] is False
        assert 0 in audit["local_orientation"]["degenerate"]

    def test_input_lock_sign_mismatch_fails(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        target = self._b_targets()[0]
        # Mirror subst 9 across its center: old volume sign flips vs the
        # lock (+1 -> -1) while staying non-zero. Old code compared only
        # old-vs-new (both -1 after rigid follow, so it passed); v2
        # compares input-vs-lock and must fail.
        bad = xyz.copy()
        bad[9] = xyz[0] - (xyz[9] - xyz[0])
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(bad, els, adj, spec, target, rigid_units=res)
        assert _reason_token(excinfo.value) == "local_orientation"
        audit = excinfo.value.audit
        assert audit["local_orientation"]["passed"] is False
        assert 0 in audit["local_orientation"]["input_inconsistent"]

    def test_positive_locks_all_checked(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        for form in self._b_targets():
            _out, _state, audit = realize_cp_target(xyz, els, adj, spec, form, rigid_units=res)
            loc = audit["local_orientation"]
            assert loc["passed"] is True
            assert loc["checked"] == 4
            assert loc["degenerate"] == []
            assert loc["input_inconsistent"] == []
            assert loc["flipped"] == []


class TestAuditCompleteness:
    def test_amplitude_failure_gates_later_items_as_not_evaluated(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        target = next(f for f in canonical_forms(6) if (f.family, f.index) == ("C", 0))
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(xyz, els, adj, spec, target, rigid_units=res)
        audit = excinfo.value.audit
        # All fixed items are recorded.
        for name in SEVEN:
            assert name in audit, name
        # Earlier gates passed with real values.
        assert audit["ring_bond_drift"]["passed"] is True
        assert audit["ring_angle_drift"]["passed"] is True
        assert audit["conjugation_planarity"]["passed"] is True
        assert audit["puckering_amplitude"]["passed"] is False
        # First-failure order is preserved.
        assert _reason_token(excinfo.value) == "puckering_amplitude"
        assert _R3_AUDIT_ORDER.index("puckering_amplitude") < _R3_AUDIT_ORDER.index("cp_reached")
        # Later items are explicitly not evaluated, never falsely passed.
        for name in ("cp_reached", "local_orientation", "clash"):
            assert audit[name]["passed"] is False, name
            assert audit[name].get("status") == "not_evaluated", name
            assert audit[name].get("skipped_after") == "puckering_amplitude", name

    def test_full_failure_reports_min_residual_complete_audit(self) -> None:
        els, xyz = _load_xyz(CREST, frame=0)
        adj = _rpdd_adjacency()
        res = analyze_rigid_units(xyz, els, adj, RING_RPDD)
        spec = RingSpec(id="rpdd", atoms=tuple(RING_RPDD))
        target = next(f for f in canonical_forms(6) if (f.family, f.index) == ("C", 0))
        with pytest.raises(RingGeometryFailure) as excinfo:
            realize_cp_target(xyz, els, adj, spec, target, rigid_units=res)
        audit = excinfo.value.audit
        assert audit["start_index"] in (0, 1)
        assert "residual_cost" in audit
        assert set(audit["solver"]) >= {"cost", "nfev"}
        for name in SEVEN:
            assert name in audit, name
            assert "passed" in audit[name], name
        assert _reason_token(excinfo.value) in SEVEN


class TestFrozenDiscipline:
    def test_audit_order_and_gates_frozen(self) -> None:
        assert _R3_AUDIT_ORDER == SEVEN
        assert _R3_AUDIT_ORDER.index("puckering_amplitude") < _R3_AUDIT_ORDER.index("cp_reached")
        assert _R3_PHASE_DEFINED_Q_MIN == 0.05
        assert _R3_CP_REACHED_MAX_DEG == 15.0

    def test_solver_settings_frozen(self) -> None:
        assert _R3_SOLVER_MAX_NFEV == 2000
        assert _R3_SOLVER_FTOL == 1e-8
        assert _R3_SOLVER_XTOL == 1e-8
        assert _R3_SOLVER_GTOL == 1e-8

    def test_phase_gate_is_classvar_not_a_field(self) -> None:
        import dataclasses

        from confflow.science.confgen.tolerances import ConfgenTolerances

        assert ConfgenTolerances.phase_defined_q_min == 0.05
        assert "phase_defined_q_min" not in [f.name for f in dataclasses.fields(ConfgenTolerances)]

    def test_new_entrypoint_isolated_from_stage(self) -> None:
        import confflow.science.confgen.ring.stage as stage

        tree = ast.parse(Path(stage.__file__).read_text())
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "realize_cp_target":
                found.append("Name")
            elif isinstance(node, ast.Attribute) and node.attr == "realize_cp_target":
                found.append("Attribute")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name.split(".")[-1] == "realize_cp_target":
                        found.append("Import")
                    elif alias.asname == "realize_cp_target":
                        found.append("Import")
        assert found == []

        def _kinds(sample: str) -> list[str]:
            kinds: list[str] = []
            for sub in ast.walk(ast.parse(sample)):
                if isinstance(sub, ast.Name) and sub.id == "realize_cp_target":
                    kinds.append("Name")
                elif isinstance(sub, ast.Attribute) and sub.attr == "realize_cp_target":
                    kinds.append("Attribute")
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        if alias.name.split(".")[-1] == "realize_cp_target":
                            kinds.append("Import")
                        elif alias.asname == "realize_cp_target":
                            kinds.append("Import")
            return kinds

        assert _kinds("x = realize_cp_target(y)\n") == ["Name"]
        assert _kinds("from m import realize_cp_target\n") == ["Import"]
        assert _kinds('"""realize_cp_target"""\n# realize_cp_target\nx = 1\n') == []
