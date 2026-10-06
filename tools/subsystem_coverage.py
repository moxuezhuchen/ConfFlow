"""Subsystem line-coverage floor for DIET-2 P0.1b.

Measure per-subsystem line coverage from cobertura XML with row-weighted
covered/valid counts (same line caliber as coverage.py). ConfGen kernel
covers top-level files under science/confgen/ only; the component
subpackages coordination/, ring/ and torsion/ (each with component.py,
spec.py, stage.py, scope.py) are excluded.

Usage: measure --xml F | check --xml F --baseline tools/coverage_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from typing import TypedDict

CONFGEN_COMPONENT_DIRS: tuple[str, ...] = ("coordination", "ring", "torsion")

SUBSYSTEMS: tuple[str, ...] = ("execution", "persistence", "workflow", "confgen_kernel")

DEFAULT_TOLERANCE_PP = 5.0


class SubStats(TypedDict):
    covered: int
    valid: int
    percent: float


def _normalize(filename: str) -> str:
    """Strip a coverage filename to a confflow-relative posix path."""
    p = filename.strip().replace("\\", "/")
    idx = p.find("confflow/")
    if idx != -1:
        p = p[idx + len("confflow/") :]
    return p.lstrip("./").lstrip("/")


def subsystem_of(filename: str) -> str | None:
    """Map a coverage filename to a subsystem name, or None to ignore."""
    rel = _normalize(filename)
    if rel == "execution/__init__.py" or rel.startswith("execution/"):
        return "execution"
    if rel.startswith("persistence/"):
        return "persistence"
    if rel.startswith("workflow/"):
        return "workflow"
    if rel == "science/confgen/__init__.py" or rel.startswith("science/confgen/"):
        rest = rel[len("science/confgen/") :] if rel.startswith("science/confgen/") else ""
        for comp in CONFGEN_COMPONENT_DIRS:
            if rest == comp or rest.startswith(comp + "/"):
                return None
        if "/" in rest:
            return None
        return "confgen_kernel"
    return None


def measure_xml(xml_path: str) -> dict[str, SubStats]:
    """Aggregate covered/valid line counts per subsystem from cobertura XML."""
    covered: dict[str, int] = {s: 0 for s in SUBSYSTEMS}
    valid: dict[str, int] = {s: 0 for s in SUBSYSTEMS}
    for cls in ET.parse(xml_path).getroot().iter("class"):
        sub = subsystem_of(cls.get("filename", ""))
        if sub is None:
            continue
        for ln in cls.iter("line"):
            valid[sub] += 1
            if int(ln.get("hits", "0")) > 0:
                covered[sub] += 1
    out: dict[str, SubStats] = {}
    for s in SUBSYSTEMS:
        v = valid[s]
        c = covered[s]
        out[s] = {"covered": c, "valid": v, "percent": round(100.0 * c / v, 2) if v else 0.0}
    return out


def evaluate(
    measured: dict[str, SubStats],
    baseline_subs: dict[str, dict[str, object]],
    tolerance_pp: float,
) -> list[str]:
    """Return failure lines for subsystems below baseline minus tolerance."""
    failures: list[str] = []
    for sub in SUBSYSTEMS:
        if sub not in baseline_subs:
            raise KeyError(f"baseline missing subsystem: {sub}")
        base = float(str(baseline_subs[sub].get("percent")))
        cur = float(measured[sub]["percent"])
        if cur < base - tolerance_pp - 1e-9:
            failures.append(f"{sub}: {cur:.2f}% < baseline {base:.2f}% - {tolerance_pp:.1f}pp")
    return failures


def _load_baseline(path: str) -> tuple[dict[str, dict[str, object]], float]:
    """Load subsystems and tolerance from a baseline JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    subs = data.get("subsystems", data)
    tol = float(data.get("tolerance_pp", DEFAULT_TOLERANCE_PP))
    return dict(subs), tol


def cmd_measure(xml_path: str) -> int:
    """Print subsystem stats JSON for the given coverage XML."""
    print(json.dumps(measure_xml(xml_path), indent=2, sort_keys=True))
    return 0


def cmd_check(xml_path: str, baseline_path: str) -> int:
    """Check XML against baseline floor; return 0 pass, 1 below floor, 2 error."""
    try:
        baseline_subs, tolerance_pp = _load_baseline(baseline_path)
        measured = measure_xml(xml_path)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (ET.ParseError, json.JSONDecodeError, ValueError) as e:
        print(f"error: invalid input: {e}", file=sys.stderr)
        return 2
    try:
        failures = evaluate(measured, baseline_subs, tolerance_pp)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for sub in SUBSYSTEMS:
        cur = measured[sub]
        raw = baseline_subs.get(sub, {})
        base = float(str(raw.get("percent", "nan"))) if isinstance(raw, dict) else float("nan")
        floor = base - tolerance_pp
        status = "OK" if cur["percent"] + 1e-9 >= floor else "FAIL"
        print(
            f"{sub}: {cur['percent']:.2f}% ({cur['covered']}/{cur['valid']}) "
            f"baseline {base:.2f}% floor {floor:.2f}% -> {status}"
        )
    if failures:
        print("subsystem coverage floor FAILED:", *failures, sep="\n  ")
        return 1
    print(f"subsystem coverage floor passed (tolerance {tolerance_pp:.1f}pp)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse measure/check subcommands and dispatch."""
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="print subsystem coverage JSON")
    m.add_argument("--xml", required=True)
    c = sub.add_parser("check", help="check XML against a frozen baseline")
    c.add_argument("--xml", required=True)
    c.add_argument("--baseline", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "measure":
        return cmd_measure(args.xml)
    return cmd_check(args.xml, args.baseline)


if __name__ == "__main__":
    sys.exit(main())
