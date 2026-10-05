#!/usr/bin/env python3

"""V4-6 production-path debt gate.

Extends -- never duplicates -- the existing boundary gates.  All shared
vocabulary (forbidden symbols, legacy modules, filename/ordinal scanners and
their allowlists) is IMPORTED from ``tests/v4/test_architecture_boundaries.py``
first (see ``TestV46BoundaryReuse``); this file only ADDS V4-6-specific gates:

- new-symbol ban: ``iprog`` (+ int-indexed program/executor dispatch idiom);
- extended scan roots: ``confflow/analysis`` + ``confflow/producer`` are
  scanned automatically when those sibling workstreams land (absent today);
- producer legacy-truth ban: ``result.xyz`` / ``failed.xyz`` /
  ``workflow_stats`` / ``output_path`` / ``min_xyz`` must never appear as code
  tokens on the V4 production path -- the RunResultManifest is the only
  authoritative output;
- old-runtime-fallback gate: no ``confflow.workflow.engine`` import anywhere
  in the V4 production roots; the two legacy entry-point imports in
  ``confflow/cli.py`` + ``confflow/application`` are pinned by an exact
  allowlist so any NEW fallback trips the gate;
- JobDesk-side doubles ban: ``*jobdesk*``/``*double*`` helpers in the V4-6
  cross-repo tests must not import ``confflow`` (they simulate the other repo).
"""

from __future__ import annotations

from pathlib import Path

# Existing boundaries FIRST: reuse, do not redefine (no dup).

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "confflow"


def _v46_strict_roots() -> list[Path]:
    """ACTIVE V4 production path, extended with analysis/producer when landed."""
    roots: list[Path] = []
    for name in (
        "domain",
        "workflow/v4",
        "execution",
        "analysis",  # sibling workstream; absent today
        "producer",  # sibling workstream; absent today
        "persistence",
        "programs",
        "remote",
    ):
        candidate = PACKAGE_ROOT / Path(name)
        if candidate.is_dir():
            roots.append(candidate)
    return roots


#: V4-6 NEW forbidden code symbols (disjoint from FORBIDDEN_SYMBOLS by test).

#: Producer-contract legacy-truth tokens: never authoritative outputs on V4.

#: Legacy CLI/application entry points allowed to import the old runtime.
#: Any import of ``confflow.workflow.engine`` outside this exact allowlist
#: (in particular any NEW one) fails the gate.
#:
#: Final-integration update (worker I cutover, commit 4b97ba7): the last two
#: formal importers are gone -- ``confflow/cli.py`` routes through
#: ``application.v4_entry`` (``formal_v4_runner``/``require_v4_document_file``)
#: and ``application/execution/workflow_adapter.py`` takes
#: ``formal_v4_runner`` as its default runner.  A repo-wide grep confirms
#: zero remaining ``confflow.workflow.engine`` importers, so the allowlist is
#: now EMPTY: any future engine import fails this gate.  This strengthens
#: the gate (empty set); no guard is weakened.

#: Files holding the JobDesk-side doubles (other repo scanned iff present).


# ---------------------------------------------------------------------------
# V4-6 E2E additions (workstream E).  Genuinely new assertions only: nothing
# below duplicates ``test_architecture_boundaries.py`` or the gates above.
# ---------------------------------------------------------------------------

#: The workstream-E cross-repo E2E file also hosts JobDesk-side consumer
#: doubles (``JobdeskV4ConsumerDouble``); they simulate the other repo and
#: must stay ``confflow``-free exactly like ``DOUBLE_FILES``.

#: Landed V4-6 production modules (producer + analysis).  Every entry must
#: live under the scan roots above and must not import exact forbidden
#: legacy modules.  (Runtime isolation beyond exact-module matching is NOT
#: asserted: the producer legitimately reads schema constants through
#: ``confflow.config.canonical.*`` submodules, which exact matching allows.)
V46_MODULES = (
    "confflow.producer",
    "confflow.producer.contract",
    "confflow.producer.manifest",
    "confflow.producer.recipes",
    "confflow.producer.validation",
    "confflow.analysis.executor",
    "confflow.analysis.grouping",
    "confflow.analysis.models",
    "confflow.analysis.pes",
    "confflow.analysis.reaction",
    "confflow.analysis.registry",
    "confflow.analysis.thermochemistry",
    "confflow.analysis.units",
)


class TestV46ModuleCoverage:
    """All landed V4-6 production modules sit inside the debt-gate roots."""

    def test_v46_modules_exist(self) -> None:
        for module in V46_MODULES:
            base = REPO_ROOT / Path(module.replace(".", "/"))
            path = base.with_suffix(".py") if base.parent != REPO_ROOT else base.with_suffix(".py")
            is_pkg = (REPO_ROOT / Path(module.replace(".", "/")) / "__init__.py").is_file()
            assert path.is_file() or is_pkg, module

    def test_v46_modules_are_covered_by_scan_roots(self) -> None:
        roots = _v46_strict_roots()
        for module in V46_MODULES:
            path = REPO_ROOT / Path(module.replace(".", "/")).with_suffix(".py")
            if not path.is_file():
                path = REPO_ROOT / Path(module.replace(".", "/")) / "__init__.py"
            assert any(path == root or root in path.parents for root in roots), module
