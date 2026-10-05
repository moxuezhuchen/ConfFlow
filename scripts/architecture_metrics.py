#!/usr/bin/env python3
"""Architecture reachability metrics (pure analysis tool).

Thin CLI adapter over the unified static policy
(``tools/architecture_policy.py::metrics_snapshot``, AP-095): the metric
definitions live in the policy module; this script only forwards
``collect(root)`` and renders the command output.

Fixed definitions:

- ``PHYSICAL_PRODUCTION_MODULES`` / ``PHYSICAL_PRODUCTION_LOC``: every module
  under ``<root>/confflow``.  Tests, fixtures, and scripts live outside the
  package and are excluded by construction.
- ``V4_REACHABLE_MODULES`` / ``V4_REACHABLE_LOC``: the static import closure
  from the formal V4 production roots:
    * ``confflow.v4cli`` -- V4 command surface;
    * ``confflow.application.v4_entry`` -- formal runner authority;
    * ``confflow.application.execution.workflow_adapter`` -- formal workflow
      service boundary;
    * ``confflow.control_worker`` -- control-worker production path.
  The closure counts module-level imports, package ``__init__`` execution
  (importing a submodule pulls its parent packages), and statically visible
  function-local imports.  PEP 562 ``importlib`` lazy exports and other
  dynamic indirection are not statically resolvable and are excluded.
- ``LEGACY_OR_NON_V4_REACHABLE_LOC`` = ``PHYSICAL_PRODUCTION_LOC`` -
  ``V4_REACHABLE_LOC``.

The retired legacy lines (the ``confflow/calc`` tooling, the ``confts`` /
``confrefine`` / ``blocks`` CLIs, and the V1/V2/V3 configuration wires) are
physically removed.  Their reappearance is guarded by
``scripts/v4_arch_scan.py`` and ``tests/v4/test_architecture_boundaries.py``,
not by this script.

Usage::

    python scripts/architecture_metrics.py [--root PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

#: Formal V4 production entrypoints; the fixed metric never changes these.
V4_ROOTS: tuple[str, ...] = (
    "confflow.v4cli",
    "confflow.application.v4_entry",
    "confflow.application.execution.workflow_adapter",
    "confflow.control_worker",
)


def _load_policy():
    """Load the policy module from this script's own tree (fail closed)."""
    import importlib.util

    candidate = Path(__file__).resolve().parent.parent / "tools" / "architecture_policy.py"
    if not candidate.is_file():
        raise FileNotFoundError(
            f"authoritative policy not found: {candidate} "
            "(refusing to fall back to another tree's tools)"
        )
    spec = importlib.util.spec_from_file_location("l0_thin_metrics_policy", str(candidate))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_policy = _load_policy()


def collect(root: str) -> dict[str, object]:
    snapshot = _policy.metrics_snapshot(Path(root))
    return {
        "root": os.path.abspath(root),
        "V4_ROOTS": list(V4_ROOTS),
        "PHYSICAL_PRODUCTION_MODULES": snapshot["PHYSICAL_PRODUCTION_MODULES"],
        "PHYSICAL_PRODUCTION_LOC": snapshot["PHYSICAL_PRODUCTION_LOC"],
        "V4_REACHABLE_MODULES": snapshot["V4_REACHABLE_MODULES"],
        "V4_REACHABLE_LOC": snapshot["V4_REACHABLE_LOC"],
        "LEGACY_OR_NON_V4_REACHABLE_LOC": snapshot["LEGACY_OR_NON_V4_REACHABLE_LOC"],
        "v4_reachable": snapshot["v4_reachable"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Repository root to analyse (default: this script's parent repo).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    metrics = collect(args.root)
    if args.json:
        print(json.dumps(metrics, indent=1, sort_keys=True))
        return 0
    print(f"root={metrics['root']}")
    print(f"PHYSICAL_PRODUCTION_MODULES={metrics['PHYSICAL_PRODUCTION_MODULES']}")
    print(f"PHYSICAL_PRODUCTION_LOC={metrics['PHYSICAL_PRODUCTION_LOC']}")
    print(f"V4_REACHABLE_MODULES={metrics['V4_REACHABLE_MODULES']}")
    print(f"V4_REACHABLE_LOC={metrics['V4_REACHABLE_LOC']}")
    print(f"LEGACY_OR_NON_V4_REACHABLE_LOC={metrics['LEGACY_OR_NON_V4_REACHABLE_LOC']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
