#!/usr/bin/env python3
"""K0 clash false-kill diagnostic (test-only, R3 frozen, real R x T).

Three real systems (rpdd CREST1 frame0, methylcyclohexane CREST frame0,
o-tolyl-cyclohexane RDKit ETKDGv3 seed=20261005 + MMFF500) are frozen as
private XYZ text below so CI needs no tmp/RDKit/file reads. Each
(ring_form, torsion_angle) leaf runs two paths: A production
(realize_cp_target with clash audit on) and B diagnostic (only
ring.realization.clash_pairs mocked to ([], False)). Leaves reuse the
real TorsionStage.realize integrity+clash audit. A target counts as
rescuable only when the whole ring target finally rejects with clash
and some B leaf fully realizes.
"""

from __future__ import annotations

import hashlib
from unittest import mock

import numpy as np

from confflow.science.confgen.model import (
    FrozenDict,
    GenerationTarget,
    StructureRecord,
    build_context,
)
from confflow.science.confgen.planner import normalize_spec
from confflow.science.confgen.registry import resolve_registry
from confflow.science.confgen.ring import realization as ring_realization
from confflow.science.confgen.ring.puckering import canonical_forms
from confflow.science.confgen.ring.realization import (
    _R3_CLASH_THRESHOLD,
    RingSpec,
)
from confflow.science.confgen.ring.rigid_units import analyze_rigid_units
from confflow.science.confgen.torsion.stage import TorsionStage

S1_FRAME_SHA = "c4d6ff0ca8016bdf0eb7d02acf2f0582e9d1bcd03fcff9c13f8ef055c29dabb0"
S2_FRAME_SHA = "b977c19ae39008062b75d58ea176a2ac27c5df739bee59185161ac320ae5d07f"
S3_SHA = "b86ffcf10ed8576743a8bba6dae72e7ab281320246d88cbe8565a4c824149c73"
RPDD_FULL_SHA = "2078aca84ff293021553e3046920b9ceb787f7d04193d1f9efdd433705a1c7b1"
MCH_FULL_SHA = "5db90a3c1eaf3f07039afd2068de6e39a6827a07deee0187e246868e6a57933d"
BASE_SHA = "f3c2eeffd06c83a159f5c7d4c21d1418661f31ab"

S1_XYZ = """22
        -44.25350192
 C         -0.4438864034        0.4713480032       -0.9597022973
 C         -0.9929946860        1.5496009861       -0.0177347660
 O         -0.7977865509        2.7145430012       -0.2011429977
 O         -1.6674369374        1.0881826971        1.0344768394
 C         -1.7863661988       -0.3067938133        1.2635080535
 C         -2.0093614844       -1.0882689613       -0.0295861338
 O         -2.7047248590       -2.0581114735       -0.0773650612
 O         -1.3559859339       -0.6110001276       -1.0887866240
 H         -2.6383783809       -0.4501055651        1.9330546349
 H         -0.3537736044        0.9048192085       -1.9642411834
 H         -0.8759266309       -0.6866340171        1.7475674153
 C          0.9250382353        0.0374462765       -0.4578284281
 C          1.8984986565        1.0134409231       -0.2534900728
 C          1.2229527658       -1.2983216664       -0.2107858978
 C          3.1571015920        0.6528115381        0.2018723323
 H          1.6685205404        2.0545397589       -0.4498686249
 C          2.4859560822       -1.6526120750        0.2442794916
 H          0.4801151937       -2.0670707520       -0.3862151112
 C          3.4524855956       -0.6802734606        0.4543674856
 H          3.9093165075        1.4170222013        0.3600374327
 H          2.7122669192       -2.6959571355        0.4322875142
 H          4.4367619674       -0.9605988744        0.8112227077"""

S2_XYZ = """21
        -22.39019388
 C         -2.4880088272        0.0000478570       -0.0812169551
 C         -1.0145392168       -0.0000029114        0.3340670178
 C         -0.3045097051        1.2565657997       -0.1876661067
 C          1.1709292955        1.2580277322        0.2210046647
 C          1.8761521471        0.0000002021       -0.2912027192
 C          1.1709041629       -1.2580051646        0.2210019829
 C         -0.3045536705       -1.2566336247       -0.1876392905
 H         -2.9912756639       -0.8851968900        0.3094200678
 H         -2.9912597028        0.8853777274        0.3093023825
 H         -2.5800685028       -0.0000171286       -1.1685118774
 H         -0.9624758769        0.0000197656        1.4325519693
 H         -0.7956168947        2.1487677565        0.2134087445
 H         -0.3824682624        1.2945762459       -1.2800936425
 H          1.2475633144        1.3017229939        1.3130888889
 H          1.6627473645        2.1473022385       -0.1848894695
 H          1.8750606182       -0.0000349285       -1.3867789998
 H          2.9180861995       -0.0000070543        0.0431235084
 H          1.6627181758       -2.1472785506       -0.1849141645
 H          1.2475608769       -1.3016495695        1.3130799592
 H         -0.7955908936       -2.1488556966        0.2134658100
 H         -0.3825475749       -1.2947256021       -1.2800582262"""

S3_XYZ = """31
S3 CC1=CC=CC=C1C2CCCCC2 ETKDGv3 seed=20261005 MMFF500 opt=0
C -2.9355819203 0.8784058801 -1.1503694495
C -2.4244976393 -0.4500529693 -0.6602031492
C -3.2857269142 -1.5599884894 -0.7292340360
C -2.8768282017 -2.8158693282 -0.2859903470
C -1.6005380440 -2.9808106802 0.2349139217
C -0.7316693492 -1.8915352030 0.3052050688
C -1.1160404789 -0.6149651122 -0.1492470612
C -0.1375324447 0.5530117210 -0.0437667356
C -0.1046514078 1.0919168556 1.3997721055
C 1.0631287389 2.0502081906 1.5995673413
C 2.4142150941 1.3730737712 1.3677722931
C 2.3346013056 0.1206231569 0.4953634075
C 1.2752905552 0.2442211669 -0.5965059362
H -2.8326324592 1.6404211252 -0.3713762689
H -2.3884417259 1.1873794752 -2.0465674532
H -3.9980525324 0.8297361998 -1.4125450689
H -4.2914734601 -1.4497218630 -1.1295765854
H -3.5562929267 -3.6615911700 -0.3441126854
H -1.2781144411 -3.9562097585 0.5888352634
H 0.2573160333 -2.0526828362 0.7257862229
H -0.5070894440 1.3647185579 -0.6820446659
H -1.0474023902 1.6068154523 1.6227380942
H -0.0240515891 0.2730960952 2.1265317475
H 1.0341839059 2.4771555067 2.6085598049
H 0.9575510606 2.8927946018 0.9043810340
H 3.0978900108 2.0996028449 0.9117448294
H 2.8613640404 1.1003981059 2.3314881614
H 2.1122327614 -0.7454087211 1.1292225248
H 3.3152433313 -0.0729232899 0.0454616840
H 1.5744379915 1.0708804673 -1.2559301413
H 1.2781828109 -0.6514597194 -1.2287173888"""

S1_RING = [0, 1, 3, 4, 5, 7]
S2_RING = [1, 2, 3, 4, 5, 6]
S3_RING = [7, 8, 9, 10, 11, 12]

S1_TORSION = {
    "id": "ph",
    "bond": [1, 12],
    "model": "relative_rotation_grid",
    "angles": [0, 60, 120, 180, 240, 300],
    "rotate_side": "right",
}
S2_TORSION = {
    "id": "me",
    "bond": [2, 1],
    "model": "relative_rotation_grid",
    "angles": [0, 120, 240],
    "rotate_side": "right",
}
S3_TORSION = {
    "id": "ph",
    "bond": [8, 7],
    "model": "relative_rotation_grid",
    "angles": [0, 60, 120, 180, 240, 300],
    "rotate_side": "right",
}

S1_SIDE = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
S2_SIDE = [7, 8, 9]
S3_SIDE = [0, 1, 2, 3, 4, 5, 13, 14, 15, 16, 17, 18, 19]


def _parse_xyz(text):
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    n = int(lines[0].strip())
    els = []
    xyz = []
    for ln in lines[2 : 2 + n]:
        parts = ln.split()
        els.append(parts[0])
        xyz.append([float(v) for v in parts[1:4]])
    assert len(els) == n
    return els, np.array(xyz, dtype=float)


def _reason_token(exc):
    return str(exc).split(":")[0].split()[0]


def _clash_detail(audit):
    node = (audit or {}).get("clash", {})
    pairs = node.get("pairs", []) if isinstance(node, dict) else []
    return pairs, bool(node.get("passed", False)) if isinstance(node, dict) else False


class _Parent:
    def __init__(self, sid, atoms, coords):
        self.structure = StructureRecord(
            id=sid, atoms=tuple(atoms), coordinates=np.asarray(coords, dtype=float)
        )


def _diagnose_one(sys_id, els, xyz, ring, torsion_decl):
    sr0 = StructureRecord(id=sys_id, atoms=tuple(els), coordinates=np.asarray(xyz))
    spec_dict = {"index_base": 1, "torsions": [dict(torsion_decl)]}
    ctx = build_context(sr0, spec_dict)
    resolved = normalize_spec(spec_dict, registry=resolve_registry(None))
    stage = TorsionStage(resolved)
    axes = stage._validated(ctx)
    assert len(axes) == 1
    axis = axes[0]
    side = sorted(stage._side(axis, ctx))
    adj = {i: set(v) for i, v in enumerate(ctx.adjacency)}
    res = analyze_rigid_units(np.asarray(xyz, dtype=float), list(els), adj, list(ring))
    rspec = RingSpec(id=sys_id, atoms=tuple(ring))
    if sys_id == "S1":
        forms = [
            f
            for f in canonical_forms(6)
            if f.family == "B" and {"1-2", "4-5"}.issubset(set(f.zero_torsion_bonds))
        ]
    else:
        forms = [f for f in canonical_forms(6) if f.family in ("C", "TB")]
    rows = []
    for form in forms:
        flabel = f"{form.family}{form.index}"
        try:
            _out_a, _, audit_a = ring_realization.realize_cp_target(
                np.asarray(xyz, dtype=float),
                list(els),
                adj,
                rspec,
                form,
                rigid_units=res,
            )
            a_pass = True
            a_reason = ""
            a_token = ""
        except Exception as exc:  # noqa: BLE001
            audit_a = getattr(exc, "audit", {}) or {}
            a_pass = False
            a_reason = str(exc)
            a_token = _reason_token(exc)
        a_clash = a_token == "clash"
        a_pairs, _ = _clash_detail(audit_a)
        with mock.patch.object(ring_realization, "clash_pairs", return_value=([], False)):
            try:
                out_b, _, audit_b = ring_realization.realize_cp_target(
                    np.asarray(xyz, dtype=float),
                    list(els),
                    adj,
                    rspec,
                    form,
                    rigid_units=res,
                )
                b_ring_pass = True
                b_ring_reason = ""
            except Exception as exc2:  # noqa: BLE001
                out_b = None
                audit_b = getattr(exc2, "audit", {}) or {}
                b_ring_pass = False
                b_ring_reason = str(exc2)
        if b_ring_pass:
            for name in (
                "ring_bond_drift",
                "ring_angle_drift",
                "conjugation_planarity",
                "puckering_amplitude",
                "cp_reached",
                "local_orientation",
                "clash",
            ):
                assert audit_b[name]["passed"] is True, (sys_id, flabel, name)
            assert audit_b["clash"]["count"] == 0
        for ang in torsion_decl["angles"]:
            tgt = GenerationTarget(
                axis="torsions",
                target_id="torsions:000000",
                state_value=FrozenDict({axis.axis_id: float(ang)}),
                ordinal=0,
                provenance=FrozenDict({"backend": "k0"}),
            )
            b_leaf = None
            if b_ring_pass:
                b_leaf = stage.realize(_Parent(sys_id + "/ringB", els, out_b), tgt, ctx)
                if b_leaf.status == "realized":
                    newc = np.asarray(b_leaf.structure.coordinates)
                    mx = max(float(np.linalg.norm(newc[i] - np.asarray(out_b)[i])) for i in ring)
                    assert mx <= 1e-6, (sys_id, flabel, ang, mx)
            rescuable = bool(
                a_clash and b_ring_pass and b_leaf is not None and b_leaf.status == "realized"
            )
            rows.append(
                {
                    "system": sys_id,
                    "form": flabel,
                    "angle": float(ang),
                    "a_pass": bool(a_pass),
                    "a_token": str(a_token),
                    "a_reason": str(a_reason),
                    "a_pairs": [
                        {
                            "i": int(p["i"]),
                            "j": int(p["j"]),
                            "dist": float(p["dist"]),
                            "limit": float(p["limit"]),
                        }
                        for p in a_pairs
                    ],
                    "b_ring_pass": bool(b_ring_pass),
                    "b_ring_reason": str(b_ring_reason),
                    "b_leaf_status": str(b_leaf.status) if b_leaf is not None else "no_parent",
                    "b_leaf_reason": str(b_leaf.reason) if b_leaf is not None else "no_parent",
                    "rescuable": bool(rescuable),
                }
            )
    return rows, side


def _load_system(sys_id):
    if sys_id == "S1":
        return _parse_xyz(S1_XYZ), S1_RING, S1_TORSION
    if sys_id == "S2":
        return _parse_xyz(S2_XYZ), S2_RING, S2_TORSION
    return _parse_xyz(S3_XYZ), S3_RING, S3_TORSION


class TestK0ClashDiagnostic:
    def test_provenance_hashes_frozen(self):
        assert (
            hashlib.sha256(
                (S1_XYZ if S1_XYZ.endswith("\n") else S1_XYZ + "\n").encode()
            ).hexdigest()
            == S1_FRAME_SHA
        )
        assert (
            hashlib.sha256(
                (S2_XYZ if S2_XYZ.endswith("\n") else S2_XYZ + "\n").encode()
            ).hexdigest()
            == S2_FRAME_SHA
        )
        assert (
            hashlib.sha256(
                (S3_XYZ if S3_XYZ.endswith("\n") else S3_XYZ + "\n").encode()
            ).hexdigest()
            == S3_SHA
        )

    def test_production_thresholds_frozen(self):
        from confflow.science.confgen.ring.geometry import TOPO_IGNORE_HOPS
        from confflow.science.confgen.tolerances import ConfgenTolerances

        assert ConfgenTolerances().clash_threshold == 0.65
        assert _R3_CLASH_THRESHOLD == 0.65
        assert TOPO_IGNORE_HOPS == 3

    def test_mock_does_not_pollute_production_audit(self):
        els, xyz = _parse_xyz(S1_XYZ)
        before = ring_realization.clash_pairs
        rows, _ = _diagnose_one("S1", els, xyz, S1_RING, S1_TORSION)
        assert ring_realization.clash_pairs is before
        assert len(rows) == 12

    def test_full_matrix_zero_rescuable(self):
        all_rows = []
        sides = {}
        for sys_id in ("S1", "S2", "S3"):
            (els, xyz), ring, decl = _load_system(sys_id)
            rows, side = _diagnose_one(sys_id, els, xyz, ring, decl)
            sides[sys_id] = side
            all_rows.extend(rows)
        assert sides["S1"] == S1_SIDE
        assert sides["S2"] == S2_SIDE
        assert sides["S3"] == S3_SIDE
        for sys_id, ring in (("S1", S1_RING), ("S2", S2_RING), ("S3", S3_RING)):
            assert all(i not in sides[sys_id] for i in ring)
        s1_rows = [r for r in all_rows if r["system"] == "S1"]
        s2_rows = [r for r in all_rows if r["system"] == "S2"]
        s3_rows = [r for r in all_rows if r["system"] == "S3"]
        assert len(s1_rows) == 12
        assert len(s2_rows) == 24
        assert len(s3_rows) == 48
        assert sum(1 for r in all_rows if r["rescuable"]) == 0
        assert sum(1 for r in s1_rows if r["a_token"] == "clash") == 0
        assert sum(1 for r in s2_rows if r["a_token"] == "clash") == 0
        assert sum(1 for r in s3_rows if r["a_token"] == "clash") == 0
        s3_realized = sum(1 for r in s3_rows if r["b_leaf_status"] == "realized")
        s3_clash = sum(1 for r in s3_rows if r["b_leaf_reason"] == "clash")
        assert s3_realized == 46
        assert s3_clash == 2
        assert sum(1 for r in s1_rows if r["b_leaf_status"] == "realized") == 12
        assert sum(1 for r in s2_rows if r["b_leaf_status"] == "realized") == 24
        for r in all_rows:
            if r["a_token"] == "clash":
                assert r["a_pairs"], r
