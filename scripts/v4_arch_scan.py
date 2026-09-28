#!/usr/bin/env python3

"""V4 production architecture scanner (worker H).

Scans the formal V4 production paths for legacy execution paths.  Exits 1
with a hit list when any legacy pattern is found in live code; comments and
docstrings are ignored so defensive "never X" documentation never counts as
a hit.

Post-cutover scope (V4 Core Closure, ``44b478d``):

- SCANNED formal V4 production: ``confflow/v4cli.py``,
  ``confflow/application/v4_run.py``, ``confflow/application/v4_entry.py``,
  ``confflow/application/execution`` (durable control-service adapter),
  ``confflow/producer`` (contract, manifest, recipes, validation, run
  result), ``confflow/analysis``, and the control-protocol entries
  ``confflow/control.py`` / ``confflow/remote``.
- NOT YET SCANNED (known legacy-helper debt, tracked for the architecture
  diet follow-ups; these are formal entries now, not legacy shims):
  ``confflow/cli.py`` (dry-run/config-show/export diagnostics and path
  helpers still come from the legacy line) and ``confflow/control_worker.py``
  (worker-side legacy helpers).
- RETIRED READ-ONLY / separate tools (out of scope by design):
  ``confflow/calc`` (legacy calculation tooling), ``confflow.confts`` /
  ``confflow.blocks`` (standalone legacy CLIs), and the V1/V2/V3
  configuration-contract emission (JobDesk wire compatibility).  The
  V2/V3 workflow *execution* runtime was physically removed by Architecture
  Diet PR-4; the diagnostic planners (dry-run / config-show / export) and the
  canonical V1/V2/V3 readers remain.  Any retired runtime module reappearing
  on disk is flagged by ``RETIRED_RUNTIME_MODULES`` below.

Contract-source imports (``confflow.config.canonical.contract`` / editor
manifest / recipes) are the producer's recorded PR-2 decoupling debt and are
pinned by the test-level guardrail
(``tests/v4/test_architecture_boundaries.py::TestProducerImportIsolation``);
execution-path modules are forbidden below.  ``input_xyz`` control-service
fields are protocol data, not legacy truth, and are not flagged.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCOPE: tuple[str, ...] = (
    "confflow/v4cli.py",
    "confflow/application/__init__.py",
    "confflow/application/v4_entry.py",
    "confflow/application/v4_run.py",
    "confflow/application/execution",
    "confflow/control.py",
    "confflow/producer",
    "confflow/remote",
    "confflow/analysis",
)

FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "confflow.calc",
    "confflow.blocks",
    "confflow.shared",
    "confflow.cli",
    "confflow.main",
    "confflow.confts",
    "confflow.workflow.engine",
    "confflow.workflow.state",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.step_handlers",
    "confflow.workflow.binding_v2",
    "confflow.workflow.execution_context",
    "confflow.workflow.finalize",
    "confflow.workflow.resume_validation",
    "confflow.workflow.runtime_context",
    "confflow.workflow.stats",
    "confflow.workflow.presenter",
    "confflow.workflow.dag",
    "confflow.workflow.plan",
    "confflow.workflow.rerun_failed",
    "confflow.workflow.supervisor",
    "confflow.core.models",
    "confflow.core.types",
    "confflow.core.parsers",
    "confflow.core.path_policy",
    "confflow.core.io",
)

#: V2/V3 execution-runtime modules retired by Architecture Diet PR-4.  They
#: must not exist as source files and must not be importable from scoped
#: files; the historically public names resolve to fail-closed stubs.
RETIRED_RUNTIME_MODULES: tuple[str, ...] = (
    "confflow.workflow.engine",
    "confflow.workflow.state",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.step_handlers",
    "confflow.workflow.binding_v2",
    "confflow.workflow.stats",
    "confflow.workflow.presenter",
    "confflow.workflow.execution_context",
    "confflow.workflow.finalize",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.resume_validation",
    "confflow.workflow.runtime_context",
    "confflow.workflow.dag",
    "confflow.workflow.dag.explicit",
    "confflow.workflow.dag.legacy",
)

PATTERNS: tuple[tuple[str, str], ...] = (
    ("legacy-TaskRunner", r"\bTaskRunner\b"),
    ("legacy-CalcStepRunner", r"\bCalcStepRunner\b"),
    ("legacy-ResultsDB", r"\bResultsDB\b"),
    ("legacy-WorkflowState", r"\bWorkflowState\w*\b"),
    ("legacy-iprog", r"\biprog\b"),
    ("legacy-itask", r"\bitask\b|\bget_itask\b"),
    ("legacy-chk-from-step", r"\bchk_from_step\b"),
    ("legacy-result-xyz", r"result\.xyz|failed\.xyz|\boutput_xyz\b"),
    ("legacy-output-path", r"\boutput_path\b"),
    ("legacy-orca-fallback", r"(?i)default[_\s-]*orca|orca[_\s-]*default"),
    ("legacy-first-match", r"first-match|first_match"),
    ("legacy-fake-marker", r"FAKE_MODE|fake_native|native_marker|CONFFLOW_FAKE"),
    ("legacy-stepresult-shortcut", r"\bStepResult\s*\("),
    (
        "legacy-duplicate-authority",
        r"register_executor|register_program|build_default_registry|\bExecutionRegistry\s*\(",
    ),
    ("legacy-silent-fallback", r"silent.*fallback|fallback.*silent|\bor\s+[\"']orca[\"']"),
)


@dataclass(frozen=True, slots=True)
class ArchHit:
    path: str
    line: int
    check: str
    text: str


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCOPE:
        candidate = REPO_ROOT / entry
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*.py")):
                if "__pycache__" not in path.parts:
                    files.append(path)
    return files


def _docstring_spans(tree: ast.AST) -> set[int]:
    """Return line numbers covered by module/class/function docstrings."""
    spans: set[int] = set()
    nodes: list[ast.AST] = [tree]
    nodes.extend(ast.walk(tree))
    for node in nodes:
        body: list[ast.stmt] | None = None
        if isinstance(node, ast.Module):
            body = node.body
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            start = first.lineno
            end = getattr(first, "end_lineno", start) or start
            spans.update(range(start, end + 1))
    return spans


def _strip_comments_and_docstrings(source: str, tree: ast.AST) -> list[str]:
    """Return source lines with comments and docstrings blanked."""
    lines = source.splitlines()
    doc_lines = _docstring_spans(tree)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        tokens = []
    comment_lines = {token.start[0] for token in tokens if token.type == tokenize.COMMENT}
    stripped: list[str] = []
    for number, line in enumerate(lines, start=1):
        if number in doc_lines or number in comment_lines:
            stripped.append("")
        else:
            stripped.append(line.split("#")[0] if False else line)
    # Remove trailing comments precisely via token columns for kept lines.
    by_line: dict[int, list[tokenize.TokenInfo]] = {}
    for token in tokens:
        if token.type == tokenize.COMMENT:
            by_line.setdefault(token.start[0], []).append(token)
    out = list(stripped)
    for number, comments in by_line.items():
        if 1 <= number <= len(out) and number not in doc_lines:
            col = min(token.start[1] for token in comments)
            out[number - 1] = out[number - 1][:col]
    return out


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """Resolve a relative import to an absolute dotted name."""
    if level == 0:
        return module or ""
    parts = package.split(".") if package else []
    up = level - 1
    base = parts[: len(parts) - up] if up <= len(parts) else []
    if module:
        return ".".join([*base, module])
    return ".".join(base)


def _import_hits(path: Path, tree: ast.AST) -> list[ArchHit]:
    """Flag forbidden legacy imports and analysis persistence bypass."""
    hits: list[ArchHit] = []
    package = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1])
    if path.name == "__init__.py":
        package = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1])
    rel = str(path.relative_to(REPO_ROOT))
    in_analysis = rel.startswith("confflow/analysis/")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    hits.append(ArchHit(rel, node.lineno, "legacy-import", name))
                if in_analysis and name.startswith("confflow.persistence"):
                    hits.append(ArchHit(rel, node.lineno, "legacy-analysis-persistence", name))
        elif isinstance(node, ast.ImportFrom):
            absolute = _resolve_relative(package, node.level, node.module)
            if absolute.startswith(FORBIDDEN_IMPORT_PREFIXES):
                hits.append(ArchHit(rel, node.lineno, "legacy-import", absolute))
            if in_analysis and absolute.startswith("confflow.persistence"):
                hits.append(ArchHit(rel, node.lineno, "legacy-analysis-persistence", absolute))
    return hits


def _pattern_hits(path: Path, lines: list[str]) -> list[ArchHit]:
    """Flag legacy code patterns on stripped lines."""
    import re

    hits: list[ArchHit] = []
    rel = str(path.relative_to(REPO_ROOT))
    compiled = [(check, re.compile(pattern)) for check, pattern in PATTERNS]
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        for check, regex in compiled:
            match = regex.search(line)
            if match:
                hits.append(ArchHit(rel, number, check, match.group(0).strip()))
    return hits


def _retired_module_hits() -> list[ArchHit]:
    """Flag any retired V2/V3 execution-runtime module that reappears."""
    hits: list[ArchHit] = []
    for module in RETIRED_RUNTIME_MODULES:
        candidate = REPO_ROOT / Path(module.replace(".", "/"))
        for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if path.exists():
                hits.append(
                    ArchHit(
                        str(path.relative_to(REPO_ROOT)),
                        1,
                        "retired-runtime-present",
                        module,
                    )
                )
                break
    return hits


def scan() -> list[ArchHit]:
    """Scan all scoped entry paths; return sorted hits."""
    hits: list[ArchHit] = list(_retired_module_hits())
    for path in _iter_files():
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        hits.extend(_import_hits(path, tree))
        hits.extend(_pattern_hits(path, _strip_comments_and_docstrings(source, tree)))
    return sorted(hits, key=lambda hit: (hit.path, hit.line, hit.check))


def main() -> int:
    hits = scan()
    for hit in hits:
        print(f"{hit.path}:{hit.line}:{hit.check}:{hit.text}")
    if hits:
        print(f"FAIL: {len(hits)} legacy execution path hits", file=sys.stderr)
        return 1
    print(f"OK: {len(_iter_files())} entry files clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
