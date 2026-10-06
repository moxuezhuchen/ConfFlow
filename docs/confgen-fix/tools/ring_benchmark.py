#!/usr/bin/env python3
"""R7 ring benchmark v2 (baseline/test-only, DRAFT).

Root counterexample (/tmp/fix1r-r7-root-unpublished-repro.log:
published=0 but recall=6/6) proved v1 seeds came from the wrong source.
This version builds seeds/CP/match/Q-gate ONLY from the single real
ConfgenEngine.run(...).leaves (actual published). No second solving pass
exists in this file.

Pipeline per case (exactly one engine run):
  read_xyz_file -> StructureRecord -> build_context -> engine.run(ctx)
Targets/stats come from run.target_records and run.report_json.
Per-leaf re-checks use measurement only (perceive / audit_target).

Same-run audit capture: during that single engine run a local
try/finally observer wraps the stage solver entry point. The wrapped
original is invoked exactly once per call and its result is returned
unmodified; the result evidence is filed by target id. After the run
the original is always restored. Published-leaf audits below come only
from captures whose target id matches an actual leaf; anything else is
reported as an explicit audit gap, never filled in.

Q/rbar 0.05 is FROZEN (ConfgenTolerances.phase_defined_q_min). Only a
value strictly below fails the run. [0.05, 0.10) is a REVIEW hint, never
a rejection threshold.

Parallel recall (2026-10-06 R7 revision): strict CP recall (<15 deg) stays
the SOLE gate and is reported exactly as before. Alongside it the tool
reports basin recall: each reference is assigned to its nearest
canonical_forms(n) precise form by cp_distance (existing puckering math
only), and basin_ok means that precise (family, index) commands at least
one published seed. Basin metrics are supplementary and never replace
the strict gate. Per-reference basin columns are appended to
reference_match.csv (existing columns keep order and values); aggregates
and the per-miss classification go to recall_summary.json.

Standard library + numpy only (via confflow APIs).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

import confflow  # noqa: E402
from confflow.core.io import read_xyz_file  # noqa: E402
from confflow.domain.structure import StructureRecord  # noqa: E402
from confflow.science.confgen import tolerances as _tolmod  # noqa: E402
from confflow.science.confgen.engine import ConfgenEngine  # noqa: E402
from confflow.science.confgen.model import (  # noqa: E402
    ConfgenStateKey,
    WorkingRealization,
    build_context,
)
from confflow.science.confgen.ring import forms as _formsmod  # noqa: E402
from confflow.science.confgen.ring import puckering as _puckmod  # noqa: E402
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    canonical_forms,
    cp_distance,
    cremer_pople,
)
from confflow.science.confgen.ring.stage import RingStage  # noqa: E402

CP_HIT_DEG = 15.0
QMIN = 0.05
NEAR_BAND = 0.10
MISSING = "missing:passed-not-stored"


def _miss_class(family: str, dtheta_deg: float) -> str:
    """Reporting-only miss taxonomy from the nearest ideal form.

    Chair misses pointing at the equator (dtheta < 0) are the flattened
    chair cluster; B/TB misses are the boat zone. This labels output rows
    only and never changes matching.
    """
    if family == "C":
        return "chair_flattened" if dtheta_deg < 0 else "chair_steepened"
    if family in ("B", "TB"):
        return "boat_zone"
    if family == "E":
        return "envelope_region"
    if family == "H":
        return "half_chair_region"
    if family == "T":
        return "twist_region"
    return f"{family}_region"


# Ring atoms are 0-based internal convention. Sources:
# thf/mch/chexene/nap_L from /tmp/fix1r-r7-root-reference-precheck.json
# (ring_0based); glc 0-based 1-6 per fixture MANIFEST citing
# ROOT-LOCAL-VERIFY.json (not rederived here); rpdd 0-based [0,1,3,4,5,7]
# from tests/v4/test_confgen_r4_cp_forms.py:RING_RPDD (= README 1-based
# 1-2-4-5-6-8). Charge/mult neutral singlet per fixture MANIFESTs.
SYSTEMS = {
    "thf": {"fixture": "thf", "ring": [0, 1, 2, 3, 4], "charge": 0, "mult": 1},
    "methylcyclohexane": {
        "fixture": "methylcyclohexane",
        "ring": [1, 2, 3, 4, 5, 6],
        "charge": 0,
        "mult": 1,
    },
    "cyclohexene": {"fixture": "cyclohexene", "ring": [0, 1, 2, 3, 4, 5], "charge": 0, "mult": 1},
    "n_acetyl_l_proline_methyl_ester": {
        "fixture": "n_acetyl_l_proline_methyl_ester",
        "ring": [4, 5, 6, 7, 8],
        "charge": 0,
        "mult": 1,
    },
    "beta_d_glucopyranose": {
        "fixture": "beta_d_glucopyranose",
        "ring": [1, 2, 3, 4, 5, 6],
        "charge": 0,
        "mult": 1,
    },
    "rpdd": {"fixture": "rpdd", "ring": [0, 1, 3, 4, 5, 7], "charge": 0, "mult": 1},
    "synthetic-cyclohexane": {"fixture": None, "ring": [0, 1, 2, 3, 4, 5], "charge": 0, "mult": 1},
}

EXPLICIT_FORMS_6 = ["C", "B", "TB", "E", "H"]
EXPLICIT_FORMS_5 = ["E", "T"]


def _refuse(msg: str) -> int:
    print(f"REFUSED: {msg}", file=sys.stderr)
    return 2


def check_binding() -> str:
    """Fail closed on the wrong source tree (old main lacks CP/forms)."""
    mod_file = str(Path(confflow.__file__).resolve())
    for attr in ["canonical_forms", "cremer_pople", "cp_distance", "cp_to_coords"]:
        if not hasattr(_puckmod, attr):
            raise RuntimeError(f"wrong tree binding ({mod_file}): puckering.{attr} absent")
    for attr in ["default_forms", "expand_form_tokens", "FORM_NAMES_BY_SIZE"]:
        if not hasattr(_formsmod, attr):
            raise RuntimeError(f"wrong tree binding ({mod_file}): forms.{attr} absent")
    if float(_tolmod.ConfgenTolerances.phase_defined_q_min) != QMIN:
        raise RuntimeError("wrong tree binding: phase_defined_q_min != 0.05")
    return mod_file


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain(value):
    """Recursively convert report evidence (FrozenDict/tuple) to plain data."""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (np.ndarray,)):
        return [float(v) for v in np.asarray(value).ravel().tolist()]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, np.generic):
        try:
            item = value.item()
        except Exception:
            return str(value)
        return plain(item)
    if isinstance(value, float):
        return value
    return str(value)


def ring_rbar(xyz: np.ndarray, ring: list[int]) -> float:
    n = len(ring)
    return float(np.mean([np.linalg.norm(xyz[ring[(i + 1) % n]] - xyz[ring[i]]) for i in range(n)]))


def _run_with_audit_capture(engine, ctx):
    """Run one real engine pass while filing solver evidence by target id.

    The observer delegates every call to the original entry point exactly
    once and returns its result unmodified. Restoration happens in
    ``finally``, including when the run raises. Returns ``(run, captured)``
    with ``captured[target_id]`` holding the original result object.
    """
    captured: dict = {}
    cls = RingStage
    orig = cls.realize

    def observing(self, parent, target, context):
        res = orig(self, parent, target, context)
        tid = getattr(target, "target_id", None)
        if tid is not None and tid not in captured:
            captured[tid] = res
        return res

    cls.realize = observing
    try:
        run = engine.run(ctx)
    finally:
        cls.realize = orig
    return run, captured


def _synthetic_cyclohexane_frames():
    """In-memory synthetic cyclohexane (NOT CREST, NOT a user fixture).

    Driving geometry: cp_to_coords of the R1 canonical C_0 chair with the
    default mean bond length. References: the two R1 canonical chair CPs
    (C_0/C_1). Both are fixed math, explicitly labeled synthetic.
    """
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    chairs = [f for f in canonical_forms(6) if f.family == "C"]
    chairs.sort(key=lambda f: f.index)
    xyz = np.asarray(cp_to_coords(chairs[0].cp_target), dtype=float)
    driving = {
        "atoms": ["C"] * 6,
        "coords": xyz,
        "comment": "synthetic chair (cp_to_coords C_0), not CREST",
    }
    refs = [
        {
            "k": f.index,
            "q": float(f.cp_target.q),
            "theta": float(f.cp_target.theta),
            "phi": float(f.cp_target.phi),
            "q_over_rbar": None,
            "rbar": None,
            "energy_hartree": "",
            "source": "synthetic:R1-canonical-C",
        }
        for f in chairs
    ]
    blob = (
        "synthetic-cyclohexane|C_0-driving|" + np.array2string(xyz, precision=6, separator=",")
    ).encode()
    return driving, refs, hashlib.sha256(blob).hexdigest()


def benchmark_case(
    system: str,
    input_mode: str,
    forms_mode: str,
    fixtures: str,
    outdir: str,
    crest_frame: int = 0,
    engine_factory=None,
) -> dict:
    mod_file = check_binding()
    out = Path(outdir)
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(f"outdir not new, refusing overwrite: {out}")
    out.mkdir(parents=True, exist_ok=True)
    if engine_factory is None:
        engine_factory = ConfgenEngine

    cfg = SYSTEMS[system]
    ring = list(cfg["ring"])
    n = len(ring)
    synthetic = cfg["fixture"] is None
    if synthetic:
        driving, refs, syn_sha = _synthetic_cyclohexane_frames()
        input_sha, crest_sha = syn_sha, syn_sha
        driving_src = "synthetic:C_0-driving (not CREST, not fixture)"
        energy_unit = "n/a (synthetic math geometry, no CREST energies)"
    else:
        fx = Path(fixtures) / cfg["fixture"]
        inp_p = fx / ("input.xyz" if cfg["fixture"] != "rpdd" else "input_ts_fragment.xyz")
        crest_p = fx / "crest_conformers.xyz"
        if not inp_p.is_file() or not crest_p.is_file():
            raise RuntimeError(f"missing fixture file under {fx}")
        input_sha, crest_sha = sha256(inp_p), sha256(crest_p)
        inp_frames = read_xyz_file(str(inp_p), strict=True)
        crest_frames = read_xyz_file(str(crest_p), strict=True)
        if input_mode == "original-input":
            driving = inp_frames[0]
            driving_src = f"{inp_p.name}:frame0"
        else:
            if not (0 <= crest_frame < len(crest_frames)):
                raise RuntimeError("crest-frame out of range")
            driving = crest_frames[crest_frame]
            driving_src = f"{crest_p.name}:frame{crest_frame}"
        refs = []
        for k, fr in enumerate(crest_frames):
            xyz0 = np.asarray(fr["coords"], dtype=float)
            cp0 = cremer_pople(xyz0[ring])
            rb0 = ring_rbar(xyz0, ring)
            refs.append(
                {
                    "k": k,
                    "q": float(cp0.q),
                    "theta": float(cp0.theta),
                    "phi": float(cp0.phi),
                    "q_over_rbar": float(cp0.q / rb0),
                    "rbar": rb0,
                    "energy_hartree": str(fr.get("comment", "")).strip(),
                    "source": "CREST",
                }
            )
        energy_unit = (
            "Hartree (Eh) for crest_conformers.xyz comment energies; "
            "crest.energies relative kcal/mol window not vendored"
        )

    if forms_mode == "default":
        rings_entry: dict = {"id": "r1", "atoms": list(ring)}
    else:
        toks = EXPLICIT_FORMS_6 if n == 6 else EXPLICIT_FORMS_5
        rings_entry = {"id": "r1", "atoms": list(ring), "forms": toks}

    rec = StructureRecord(
        id=f"r7:{system}:{input_mode}:{forms_mode}",
        atoms=tuple(driving["atoms"]),
        coordinates=tuple(map(tuple, np.asarray(driving["coords"], dtype=float))),
        charge=cfg["charge"],
        multiplicity=cfg["mult"],
    )
    ctx = build_context(rec, {"index_base": 0, "rings": [rings_entry]})

    # THE single real engine run, with same-run audit capture.
    # Everything below comes from `run` plus captures filed during it.
    engine = engine_factory() if isinstance(engine_factory, type) else engine_factory
    run, captured = _run_with_audit_capture(engine, ctx)

    targets = list(run.target_records)
    leaves = list(run.leaves)
    by_id = {t.target_id: t for t in targets}
    # Measurement-only stage handle (constructor spends no geometry).
    stage = RingStage({"rings": [dict(e) for e in ctx.resolved_spec["rings"]]})
    parent = WorkingRealization(structure=rec, state_key=ConfgenStateKey(), provenance={})

    def _audit_num(ev, *path):
        cur = ev
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return "missing-field:" + ".".join(path)
            cur = cur[key]
        return cur if isinstance(cur, (int, float)) else "missing-field:" + ".".join(path)

    seeds: list[dict] = []
    seed_audits: list[dict] = []
    audit_gaps: list[dict] = []
    for leaf in leaves:
        xyz = np.asarray(leaf.structure.coordinates, dtype=float)
        cp = cremer_pople(xyz[ring])
        rb = ring_rbar(xyz, ring)
        sk = leaf.state_key
        try:
            cmd = dict(getattr(sk, "rings", {}).get("r1", {}))
        except Exception:
            cmd = {}
        prov = plain(getattr(leaf, "provenance", {}))
        tid = prov.get("leaf_target_id") if isinstance(prov, dict) else None
        tgt = by_id.get(tid) if isinstance(tid, str) else None
        audit_ok: object = MISSING
        confidence: object = MISSING
        if tgt is not None:
            ok, _, _ = stage.audit_target(leaf.structure, tgt, parent, ctx)
            audit_ok = bool(ok)
        try:
            per = stage.perceive(leaf.structure, ctx)
            confidence = per.confidence
        except Exception:
            confidence = MISSING
        cap = captured.get(tid) if isinstance(tid, str) else None
        ev = plain(getattr(cap, "evidence", [])) if cap is not None else []
        audit = ev[0] if ev else None
        if not isinstance(audit, dict):
            audit_gaps.append(
                {"leaf_target_id": tid, "error": "solver audit missing for published leaf"}
            )
            seed_audits.append(
                {
                    "leaf_target_id": tid,
                    "commanded": cmd,
                    "measured": {
                        "Q": float(cp.q),
                        "theta": float(cp.theta),
                        "phi": float(cp.phi),
                        "q_over_rbar": float(cp.q / rb),
                    },
                    "solver_audit": None,
                    "error": "audit missing",
                    "audit_source": "same-run observer capture (no second solve)",
                }
            )
            fields = {
                "planarity_value": MISSING,
                "planarity_limit": MISSING,
                "planarity_passed": MISSING,
                "angle_rigid": MISSING,
                "angle_free": MISSING,
                "angle_passed": MISSING,
                "amplitude_passed": MISSING,
                "cp_distance": MISSING,
            }
        else:
            seed_audits.append(
                {
                    "leaf_target_id": tid,
                    "commanded": cmd,
                    "measured": {
                        "Q": float(cp.q),
                        "theta": float(cp.theta),
                        "phi": float(cp.phi),
                        "q_over_rbar": float(cp.q / rb),
                    },
                    "solver_audit": audit,
                    "audit_source": "same-run observer capture (no second solve)",
                }
            )
            fields = {
                "planarity_value": _audit_num(audit, "conjugation_planarity", "value"),
                "planarity_limit": _audit_num(audit, "conjugation_planarity", "limit"),
                "planarity_passed": (
                    (audit.get("conjugation_planarity", {}) or {}).get("passed", MISSING)
                    if isinstance(audit.get("conjugation_planarity"), dict)
                    else MISSING
                ),
                "angle_rigid": _audit_num(audit, "ring_angle_drift", "rigid_value"),
                "angle_free": _audit_num(audit, "ring_angle_drift", "free_value"),
                "angle_passed": (
                    (audit.get("ring_angle_drift", {}) or {}).get("passed", MISSING)
                    if isinstance(audit.get("ring_angle_drift"), dict)
                    else MISSING
                ),
                "amplitude_passed": (
                    (audit.get("puckering_amplitude", {}) or {}).get("passed", MISSING)
                    if isinstance(audit.get("puckering_amplitude"), dict)
                    else MISSING
                ),
                "cp_distance": _audit_num(audit, "cp_reached", "distance"),
            }
        seeds.append(
            {
                "leaf_target_id": tid,
                "cmd": cmd,
                "xyz": xyz,
                "q": float(cp.q),
                "theta": float(cp.theta),
                "phi": float(cp.phi),
                "q_over_rbar": float(cp.q / rb),
                "audit_ok": audit_ok,
                "confidence": confidence,
                "fields": fields,
            }
        )

    failed = []
    for t in targets:
        if str(getattr(t, "status", "")).endswith("PUBLISHED_LEAF"):
            continue
        failed.append(
            {
                "target_id": t.target_id,
                "state_value": plain(getattr(t, "state_value", {})),
                "status": str(getattr(t, "status", "")),
                "reason": str(getattr(t, "reason", "")),
                "evidence": plain(getattr(t, "evidence", [])),
            }
        )

    match_rows = []
    for r in refs:
        a = CPCoords(n=n, q=r["q"], theta=r["theta"], phi=r["phi"])
        best, bi = 1e9, -1
        for i, s in enumerate(seeds):
            b = CPCoords(n=n, q=s["q"], theta=s["theta"], phi=s["phi"])
            d = float(cp_distance(a, b))
            if d < best:
                best, bi = d, i
        match_rows.append(
            {
                "ref": r["k"],
                "ref_theta": r["theta"],
                "ref_phi": r["phi"],
                "ref_q_over_rbar": r["q_over_rbar"],
                "best_seed": bi,
                "min_cp_dist_deg": (best if bi >= 0 else ""),
                "hit_lt15": int(bi >= 0 and best < CP_HIT_DEG),
            }
        )

    # Basin recall (supplementary; strict <15 deg above stays the sole gate).
    # Nearest-form assignment uses existing canonical_forms/cp_distance only.
    # basin_ok means the nearest precise (family, index) commands at least
    # one published seed. dtheta/dphi are plain signed reporting offsets of
    # the reference from its nearest ideal (phi wrapped to [-180, 180)).
    forms = list(canonical_forms(n))
    seed_forms: set[tuple[str, int]] = set()
    for s in seeds:
        try:
            seed_forms.add((str(s["cmd"].get("form")), int(s["cmd"].get("index"))))
        except (TypeError, ValueError):
            continue
    for r, m in zip(refs, match_rows):
        a = CPCoords(n=n, q=r["q"], theta=r["theta"], phi=r["phi"])
        near = min(forms, key=lambda f: float(cp_distance(a, f.cp_target)))
        dist = float(cp_distance(a, near.cp_target))
        dtheta = float(r["theta"]) - float(near.cp_target.theta)
        dphi = (float(r["phi"]) - float(near.cp_target.phi) + 180.0) % 360.0 - 180.0
        m["basin_nearest_form"] = f"{near.family}_{near.index}"
        m["basin_dist_deg"] = dist
        m["basin_dtheta_deg"] = dtheta
        m["basin_dphi_deg"] = dphi
        m["basin_ok"] = int((near.family, near.index) in seed_forms)

    strict_hits = sum(r["hit_lt15"] for r in match_rows)
    basin_covered = sum(int(m["basin_ok"]) for m in match_rows)
    misses: list[dict] = []
    for m in match_rows:
        if m["hit_lt15"]:
            continue
        fam = str(m["basin_nearest_form"]).rsplit("_", 1)[0]
        dist_v = m["min_cp_dist_deg"]
        misses.append(
            {
                "ref": m["ref"],
                "min_cp_dist_deg": (round(dist_v, 4) if isinstance(dist_v, float) else dist_v),
                "basin_nearest_form": m["basin_nearest_form"],
                "basin_dist_deg": round(float(m["basin_dist_deg"]), 4),
                "basin_dtheta_deg": round(float(m["basin_dtheta_deg"]), 4),
                "basin_ok": bool(m["basin_ok"]),
                "miss_class": _miss_class(fam, float(m["basin_dtheta_deg"])),
            }
        )
    miss_class_counts = {
        c: sum(1 for m in misses if m["miss_class"] == c)
        for c in sorted({m["miss_class"] for m in misses})
    }
    recall_summary = {
        "system": system,
        "n": n,
        "input_mode": input_mode,
        "forms_mode": forms_mode,
        "strict": {
            "threshold_deg": CP_HIT_DEG,
            "recall": f"{strict_hits}/{len(refs)}",
            "authority": "sole gate; basin metrics are supplementary and never replace strict",
        },
        "basin": {
            "definition": (
                "each reference assigned to nearest canonical_forms(n) precise "
                "form by cp_distance; covered iff that (family, index) commands "
                "at least one published seed"
            ),
            "recall": f"{basin_covered}/{len(refs)}",
        },
        "misses": misses,
        "miss_class_counts": miss_class_counts,
    }

    qrefs = [r["q_over_rbar"] for r in refs if r["q_over_rbar"] is not None]
    qseeds = [s["q_over_rbar"] for s in seeds]
    gate_fail = any(v < QMIN for v in qrefs) or any(v < QMIN for v in qseeds)
    near = [v for v in qseeds if QMIN <= v < NEAR_BAND]
    verdict = "FAIL" if gate_fail else ("REVIEW-nearband-advisory" if near else "PASS")

    els = list(driving["atoms"])
    with open(out / "seeds.xyz", "w") as f:
        for i, s in enumerate(seeds):
            c = s["cmd"]
            f.write(f"{len(els)}\n")
            f.write(
                f"seed {i} {c.get('form')}_{c.get('index')} "
                f"Q={s['q']:.4f} theta={s['theta']:.2f} phi={s['phi']:.2f} "
                f"mode={input_mode}/{forms_mode} from=published-leaf\n"
            )
            for el, (x, y, z) in zip(els, s["xyz"]):
                f.write(f"{el} {x:.6f} {y:.6f} {z:.6f}\n")
    with open(out / "cp_table.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "seed",
                "leaf_target_id",
                "family",
                "index",
                "anchor",
                "direction",
                "Q",
                "theta",
                "phi",
                "q_over_rbar",
                "perceive_confidence",
                "cp_audit_ok",
                "planarity_value",
                "planarity_limit",
                "planarity_passed",
                "angle_rigid_value",
                "angle_free_value",
                "angle_passed",
                "amplitude_passed",
                "cp_reached_distance",
            ],
        )
        w.writeheader()
        for i, s in enumerate(seeds):
            c = s["cmd"]
            fl = s["fields"]
            w.writerow(
                {
                    "seed": i,
                    "leaf_target_id": s["leaf_target_id"],
                    "family": c.get("form"),
                    "index": c.get("index"),
                    "anchor": c.get("anchor"),
                    "direction": c.get("direction"),
                    "Q": round(s["q"], 4),
                    "theta": round(s["theta"], 3),
                    "phi": round(s["phi"], 3),
                    "q_over_rbar": round(s["q_over_rbar"], 4),
                    "perceive_confidence": s["confidence"],
                    "cp_audit_ok": s["audit_ok"],
                    "planarity_value": fl["planarity_value"],
                    "planarity_limit": fl["planarity_limit"],
                    "planarity_passed": fl["planarity_passed"],
                    "angle_rigid_value": fl["angle_rigid"],
                    "angle_free_value": fl["angle_free"],
                    "angle_passed": fl["angle_passed"],
                    "amplitude_passed": fl["amplitude_passed"],
                    "cp_reached_distance": fl["cp_distance"],
                }
            )
    with open(out / "seed_audits.json", "w") as f:
        json.dump(
            {"published_leaves": len(leaves), "audits": seed_audits, "audit_gaps": audit_gaps},
            f,
            indent=1,
        )
    with open(out / "reference_match.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ref",
                "ref_theta",
                "ref_phi",
                "ref_q_over_rbar",
                "best_seed",
                "min_cp_dist_deg",
                "hit_lt15",
                "basin_nearest_form",
                "basin_dist_deg",
                "basin_dtheta_deg",
                "basin_dphi_deg",
                "basin_ok",
            ],
        )
        w.writeheader()
        for r in match_rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    with open(out / "recall_summary.json", "w") as f:
        json.dump(recall_summary, f, indent=1)
    with open(out / "failures.json", "w") as f:
        json.dump(
            {
                "targets": len(targets),
                "published_leaves": len(leaves),
                "failed_targets": len(failed),
                "failed": failed,
            },
            f,
            indent=1,
        )
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        head = "unknown"
    meta = {
        "system": system,
        "ring_0based": ring,
        "input_mode": input_mode,
        "forms_mode": forms_mode,
        "driving_src": driving_src,
        "crest_frame": crest_frame,
        "references": len(refs),
        "reference_source": (
            "synthetic:R1-canonical-chairs (not CREST)"
            if synthetic
            else "CREST crest_conformers.xyz"
        ),
        "input_sha256": input_sha,
        "crest_sha256": crest_sha,
        "energy_unit": energy_unit,
        "confflow_file": mod_file,
        "HEAD": head,
        "qmin_frozen": QMIN,
        "seeds_from": "ConfgenEngine.run().leaves ONLY (single run)",
        "audits_from": "same-run observer capture matched by leaf_target_id (no second solve)",
        "audit_complete": (not audit_gaps and len(seed_audits) == len(leaves)),
        "recall": f"{sum(r['hit_lt15'] for r in match_rows)}/{len(refs)}",
        "verdict_qgate": verdict,
    }
    with open(out / "run_meta.json", "w") as f:
        json.dump(meta, f, indent=1)
    # Manifest is written LAST, after every other file exists.
    manifest = {"files": []}
    for name in [
        "seeds.xyz",
        "cp_table.csv",
        "seed_audits.json",
        "reference_match.csv",
        "failures.json",
        "run_meta.json",
        "recall_summary.json",
    ]:
        p = out / name
        manifest["files"].append({"path": name, "sha256": sha256(p), "bytes": p.stat().st_size})
    manifest["sources"] = {
        "input_sha256": input_sha,
        "crest_sha256": crest_sha,
        "confflow_binding": mod_file,
        "HEAD": head,
    }
    with open(out / "MANIFEST.json", "w") as f:
        json.dump(manifest, f, indent=1)

    hits = sum(r["hit_lt15"] for r in match_rows)
    summary = {
        "system": system,
        "targets": len(targets),
        "published": len(leaves),
        "recall": f"{hits}/{len(refs)}",
        "basin_recall": f"{basin_covered}/{len(refs)}",
        "miss_basin_outside": sum(1 for m in misses if not m["basin_ok"]),
        "failed": len(failed),
        "audit_complete": meta["audit_complete"],
        "audit_gaps": len(audit_gaps),
        "verdict_qgate": verdict,
        "outdir": str(out),
    }
    if not meta["audit_complete"]:
        print(
            f"AUDIT-INCOMPLETE: {len(audit_gaps)} published leaves lack solver audit; "
            f"not claiming complete",
            file=sys.stderr,
        )
        summary["verdict_qgate"] = "FAIL"
        summary["audit_failure"] = True
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="R7 ring benchmark v3 (published-only seeds + same-run audit capture)"
    )
    ap.add_argument("--system", required=True, choices=sorted(SYSTEMS))
    ap.add_argument("--input-mode", required=True, choices=["original-input", "crest-first"])
    ap.add_argument("--forms-mode", required=True, choices=["default", "explicit"])
    ap.add_argument("--crest-frame", type=int, default=0)
    ap.add_argument("--fixtures", default="tests/fixtures/confgen/ring")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        summary = benchmark_case(
            args.system,
            args.input_mode,
            args.forms_mode,
            args.fixtures,
            args.outdir,
            args.crest_frame,
        )
    except RuntimeError as exc:
        return _refuse(str(exc))
    print(json.dumps(summary))
    if summary["verdict_qgate"] == "FAIL":
        print("Q/rbar gate FAIL (value strictly below frozen 0.05)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
