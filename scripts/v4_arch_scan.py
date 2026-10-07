#!/usr/bin/env python3

"""V4 production architecture scanner (worker H).

Thin CLI adapter over the unified static policy
(``tools/architecture_policy.py``): this module keeps the public command
surface (default root, output lines, ``ArchHit`` fields, ``scan()``) and
maps the ``legacy_cli`` compat profile (``AP-088/089/090/091/092``) back
to the historical hit format.  No AST parsing or scan loop lives here.

Post-cutover scope (V4 Core Closure, ``44b478d``):

- SCANNED formal V4 production: ``confflow/v4cli.py``,
  ``confflow/application/v4_run.py``, ``confflow/application/v4_entry.py``,
  ``confflow/application/execution`` (durable control-service adapter),
  ``confflow/producer`` (contract, manifest, recipes, validation, run
  result), ``confflow/analysis``, and the control-protocol entry
  ``confflow/control.py``.  (R2.3f: ``confflow/remote`` was deleted and left
  the scan scope; its regrowth is guarded by ``RETIRED_RUNTIME_MODULES``.)
- NOT YET SCANNED (known legacy-helper debt, tracked for the architecture
  diet follow-ups; these are formal entries now, not legacy shims):
  ``confflow/cli.py`` (path helpers still come from the legacy line) and
  ``confflow/control_worker.py`` (worker-side legacy helpers).
- RETIRED and physically removed (guarded by ``RETIRED_RUNTIME_MODULES`` and
  the architecture-boundary tests): ``confflow/calc`` (legacy calculation
  tooling), ``confflow.confts`` / ``confflow.blocks`` (standalone legacy
  CLIs), the ``confflow export`` reader, the V2/V3 workflow *execution*
  runtime (Architecture Diet PR-4), the V1/V2 diagnostic planners and
  configuration wire (PR-9), and the never-released V3 *public wire* (V3
  parser/graph/semantic validation, the ``configuration-contract.v3``
  document, the V3 catalogs and capability advertisement, and the
  ``workflow upgrade`` emitter; PR-7, flagged by ``RETIRED_V3_WIRE_MODULES``
  below).  Any retired runtime module reappearing on disk is flagged by
  ``RETIRED_RUNTIME_MODULES``, which PR-6 extended with the dead remote
  duplicates (``remote.lease`` / ``remote.supervision`` /
  ``remote.schema``) and R2.3f extended with the deleted remote package
  itself (``remote`` / ``remote.envelope``).

Contract-source imports (``confflow.config.canonical.contract`` / editor
manifest / recipes) are the producer's recorded PR-2 decoupling debt and are
pinned by the test-level guardrail
(``tests/v4/test_architecture_boundaries.py::TestProducerImportIsolation``);
execution-path modules are forbidden below.  ``input_xyz`` control-service
fields are protocol data, not legacy truth, and are not flagged.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_policy():
    """Load the policy module from this script's own tree (fail closed)."""
    import importlib.util

    candidate = Path(__file__).resolve().parent.parent / "tools" / "architecture_policy.py"
    if not candidate.is_file():
        raise FileNotFoundError(
            f"authoritative policy not found: {candidate} "
            "(refusing to fall back to another tree's tools)"
        )
    spec = importlib.util.spec_from_file_location("l0_thin_cli_policy", str(candidate))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_policy = _load_policy()

# Re-exported scanner data (single authority: the policy module).
SCOPE: tuple[str, ...] = tuple(_policy.SCANNER_SCOPE)
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = tuple(_policy.SCANNER_FORBIDDEN_IMPORT_PREFIXES)
RETIRED_RUNTIME_MODULES: tuple[str, ...] = tuple(_policy.SCANNER_RETIRED_RUNTIME_MODULES)
RETIRED_V3_WIRE_MODULES: tuple[str, ...] = tuple(_policy.SCANNER_RETIRED_V3_WIRE_MODULES)
RETIRED_V1_V2_WIRE_MODULES: tuple[str, ...] = tuple(_policy.SCANNER_RETIRED_V1_V2_WIRE_MODULES)
RETIRED_V1_V2_WIRE_TOKENS: tuple[str, ...] = tuple(_policy.SCANNER_RETIRED_V1_V2_WIRE_TOKENS)
PATTERNS: tuple[tuple[str, str], ...] = tuple(_policy.SCANNER_PATTERNS)

_LEGACY_RULE_IDS = tuple(_policy.LEGACY_CLI_RULE_IDS)


@dataclass(frozen=True, slots=True)
class ArchHit:
    path: str
    line: int
    check: str
    text: str


def _adapt(violations: list[dict]) -> list[ArchHit]:
    """Map unified-policy legacy violations to historical ``ArchHit`` rows."""
    hits: list[ArchHit] = []
    for violation in violations:
        rule = violation["rule"]
        if rule == "AP-088":
            hits.append(
                ArchHit(
                    violation["path"],
                    violation["line"],
                    "legacy-import",
                    violation["detail"],
                )
            )
        elif rule == "AP-089":
            hits.append(
                ArchHit(
                    violation["path"],
                    violation["line"],
                    "legacy-analysis-persistence",
                    violation["detail"],
                )
            )
        elif rule == "AP-090":
            hits.append(
                ArchHit(
                    violation["path"],
                    violation["line"],
                    violation["detail"],
                    violation.get("match", violation["detail"]),
                )
            )
        elif rule == "AP-091":
            hits.append(
                ArchHit(
                    violation["path"],
                    violation["line"],
                    "retired-runtime-present",
                    violation["detail"],
                )
            )
        elif rule == "AP-092":
            hits.append(
                ArchHit(
                    violation["path"],
                    violation["line"],
                    "retired-v1v2-token",
                    violation["detail"],
                )
            )
    return sorted(hits, key=lambda hit: (hit.path, hit.line, hit.check))


def scan(root: Path | str | None = None) -> list[ArchHit]:
    """Scan all scoped entry paths; return sorted hits."""
    base = Path(root) if root is not None else REPO_ROOT
    violations = _policy.scan(base, rule_ids=_LEGACY_RULE_IDS, profile="legacy_cli")
    return _adapt(violations)


def _iter_files(root: Path | str | None = None) -> list[Path]:
    base = Path(root) if root is not None else REPO_ROOT
    return _policy.scope_files(base, profile="legacy_cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="Repository root to scan.")
    parser.add_argument(
        "--cf",
        default=None,
        help="Repository root to scan (control-flow alias for --root).",
    )
    args, _unknown = parser.parse_known_args(argv)
    root = Path(args.cf or args.root) if (args.cf or args.root) else None
    hits = scan(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}:{hit.check}:{hit.text}")
    if hits:
        print(f"FAIL: {len(hits)} legacy execution path hits", file=sys.stderr)
        return 1
    print(f"OK: {len(_iter_files(root))} entry files clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
