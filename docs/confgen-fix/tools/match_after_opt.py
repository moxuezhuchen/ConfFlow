"""match_after_opt.py — user-side RMSD matcher v2 (DRAFT).

Runs ONLY on user-supplied, externally optimized XYZ. This script never
calls an optimizer and never installs anything. Usage (user local):

  python match_after_opt.py --seeds seeds_opt.xyz --refs crest_conformers.xyz --rmsd-cutoff 0.5 -o match_rmsd.csv

Recall AND redundancy are reported (count-level only):
  recall = references with some seed RMSD <= cutoff / N references.
  redundancy = seeds within cutoff of the same reference (extra seeds
  beyond the best one), plus unmatched-seed count. These counts do NOT
  prove physical basins; they only describe the optimized set.

Fail-closed: empty files, frame-length mismatch inside a file, element
mismatch between seeds/refs, non-finite coords, or a non-positive
cutoff all refuse with exit 2. Kabsch uses a proper rotation (det
correction: mirrored geometries are NOT matched).
"""

from __future__ import annotations

import argparse
import csv
import sys

import numpy as np


def read_xyz_frames(path: str) -> list[dict]:
    with open(path) as f:
        text = f.read().strip()
    if not text:
        raise ValueError(f"empty XYZ file: {path}")
    lines = text.splitlines()
    frames: list[dict] = []
    i = 0
    while i < len(lines):
        try:
            n = int(lines[i].strip())
        except ValueError as err:
            raise ValueError(f"bad header line {i + 1} in {path}") from err
        if n <= 0:
            raise ValueError(f"non-positive natoms at line {i + 1} in {path}")
        if i + 1 + n >= len(lines):
            raise ValueError(f"truncated frame at line {i + 1} in {path}")
        comment = lines[i + 1]
        atoms: list[str] = []
        coords: list[list[float]] = []
        for line in lines[i + 2 : i + 2 + n]:
            parts = line.split()
            if len(parts) < 4:
                raise ValueError(f"bad coord line in {path}: {line!r}")
            try:
                xyz = [float(x) for x in parts[1:4]]
            except ValueError as err:
                raise ValueError(f"non-numeric coord in {path}: {line!r}") from err
            if any(v != v or v in (float("inf"), float("-inf")) for v in xyz):
                raise ValueError(f"non-finite coord in {path}: {line!r}")
            atoms.append(parts[0])
            coords.append(xyz)
        frames.append({"atoms": atoms, "coords": np.array(coords), "comment": comment})
        i += 2 + n
    if not frames:
        raise ValueError(f"no frames in {path}")
    return frames


def kabsch_rmsd(a: np.ndarray, b: np.ndarray) -> float:
    """RMSD under the best proper rotation (no mirror matching)."""
    a = a - a.mean(0)
    b = b - b.mean(0)
    h = a.T @ b
    u, _, vt = np.linalg.svd(h)
    d = float(np.sign(np.linalg.det(u @ vt)))
    r = u @ np.diag([1.0, 1.0, d]) @ vt
    assert float(np.linalg.det(r)) > 0.0
    return float(np.sqrt(((a @ r - b) ** 2).sum() / len(a)))


def match(seeds: list[dict], refs: list[dict], cutoff: float) -> dict:
    if cutoff <= 0.0 or cutoff != cutoff:
        raise ValueError(f"cutoff must be positive, got {cutoff!r}")
    if any(len(f["atoms"]) != len(seeds[0]["atoms"]) for f in seeds):
        raise ValueError("seed frames disagree in natoms")
    if any(len(f["atoms"]) != len(refs[0]["atoms"]) for f in refs):
        raise ValueError("reference frames disagree in natoms")
    if any(f["atoms"] != seeds[0]["atoms"] for f in seeds):
        raise ValueError("seed frames disagree in elements")
    if any(f["atoms"] != refs[0]["atoms"] for f in refs):
        raise ValueError("reference frames disagree in elements")
    if seeds[0]["atoms"] != refs[0]["atoms"]:
        raise ValueError("seeds/refs element sequences differ; refusing cross-molecule match")
    ref_rows = []
    for j, rf in enumerate(refs):
        dists = sorted((kabsch_rmsd(sf["coords"], rf["coords"]), i) for i, sf in enumerate(seeds))
        best, bi = dists[0]
        n_within = sum(1 for d, _ in dists if d <= cutoff)
        ref_rows.append(
            {
                "ref": j,
                "best_seed": bi,
                "best_rmsd": round(best, 4),
                "hit": int(best <= cutoff),
                "n_seeds_within_cutoff": n_within,
                "redundant_extra": max(0, n_within - 1),
            }
        )
    seed_rows = []
    for i, sf in enumerate(seeds):
        dists = sorted((kabsch_rmsd(sf["coords"], rf["coords"]), j) for j, rf in enumerate(refs))
        best, bj = dists[0]
        seed_rows.append(
            {"seed": i, "best_ref": bj, "best_rmsd": round(best, 4), "matched": int(best <= cutoff)}
        )
    return {
        "refs": ref_rows,
        "seeds": seed_rows,
        "recall": f"{sum(r['hit'] for r in ref_rows)}/{len(ref_rows)}",
        "redundant_extra_total": sum(r["redundant_extra"] for r in ref_rows),
        "unmatched_seeds": sum(1 for r in seed_rows if not r["matched"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--rmsd-cutoff", type=float, default=0.5)
    ap.add_argument("-o", "--output", default="match_rmsd.csv")
    ap.add_argument("--seed-output", default="")
    args = ap.parse_args()
    try:
        seeds, refs = read_xyz_frames(args.seeds), read_xyz_frames(args.refs)
        res = match(seeds, refs, args.rmsd_cutoff)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    with open(args.output, "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ref",
                "best_seed",
                "best_rmsd",
                "hit",
                "n_seeds_within_cutoff",
                "redundant_extra",
            ],
        )
        w.writeheader()
        w.writerows(res["refs"])
    if args.seed_output:
        with open(args.seed_output, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["seed", "best_ref", "best_rmsd", "matched"])
            w.writeheader()
            w.writerows(res["seeds"])
    print(
        f"recall {res['recall']} redundant_extra {res['redundant_extra_total']} "
        f"unmatched_seeds {res['unmatched_seeds']} cutoff {args.rmsd_cutoff} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
