#!/usr/bin/env python3
"""Canonical Architecture Diet reachability metrics (pure analysis tool).

This script fixes ONE metric definition for every Architecture Diet PR.  It
never imports ``confflow``; it only parses the source tree, so it runs
identically against any revision.

Fixed definitions (PR-2.6):

- ``PHYSICAL_PRODUCTION_MODULES`` / ``PHYSICAL_PRODUCTION_LOC``: every module
  under ``<root>/confflow``.  Tests, fixtures, and scripts live outside the
  package and are excluded by construction.
- ``V4_REACHABLE_MODULES`` / ``V4_REACHABLE_LOC``: the static import closure
  from the formal V4 production roots:
    * ``confflow.v4cli`` — V4 command surface;
    * ``confflow.application.v4_entry`` — formal runner authority;
    * ``confflow.application.execution.workflow_adapter`` — formal workflow
      service boundary;
    * ``confflow.control_worker`` — control-worker production path.
  The closure counts module-level imports, package ``__init__`` execution
  (importing a submodule pulls its parent packages), and statically visible
  function-local imports.  PEP 562 ``importlib`` lazy exports and other
  dynamic indirection are not statically resolvable and are excluded.
- ``LEGACY_OR_NON_V4_REACHABLE_LOC`` = ``PHYSICAL_PRODUCTION_LOC`` -
  ``V4_REACHABLE_LOC``.
- ``CALC_MODULES`` / ``CALC_LOC``: every module under
  ``confflow/calc`` (the legacy calculation tooling).
- ``CALC_V4_REACHABLE_MODULES``: calc modules inside the fixed V4 closure.
  Must stay ``0`` — the formal V4 runtime never imports calc.
- ``CALC_PUBLIC_TOOLING_MODULES``: calc modules reachable from the public
  tooling roots (the ``confts`` CLI, the ``confrefine`` package, the refine
  composition bridge) plus the declared PEP 562 lazy exports of
  ``confflow/__init__.py`` and ``confflow/calc/__init__.py``.  These are the
  calc modules with an explicit current consumer.
- ``CALC_DEAD_MODULES``: calc modules that are neither in the V4 closure nor
  reachable from any public tooling root or declared lazy export.  Must stay
  ``0``.
- ``V3_PUBLIC_MODULES`` / ``V3_PUBLIC_LOC``: modules of the never-released
  Workflow V3 public wire (V3 parser/graph/validation, V3 catalogs, the
  ``configuration-contract.v3`` document, the V2->V3 upgrade emitter, the V3
  capability advertisement, the V3 CLI route).  Retired by PR-7; must stay
  ``0`` / ``0``.  The fixed list is ``V3_PUBLIC_WIRE_MODULES``.
- ``V3_INTERNAL_MIGRATION_MODULES`` / ``V3_INTERNAL_MIGRATION_LOC``: the
  minimal internal V3 migration kernel PR-7 may keep when released V1/V2
  compatibility needs a ``V1/V2 -> internal V3 IR -> canonical`` chain.  PR-7
  proved none is necessary, so the list is empty and the counts must stay
  ``0`` / ``0``.
- ``V3_V4_REACHABLE_MODULES``: V4-closure modules whose source references a
  retired V3 public-wire token (schema id or retired symbol).  Must stay ``0``:
  the formal V4 runtime must never consume or advertise V3.

Usage::

    python scripts/architecture_metrics.py [--root PATH] [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from collections import defaultdict, deque

#: Formal V4 production entrypoints; the fixed metric never changes these.
V4_ROOTS: tuple[str, ...] = (
    "confflow.v4cli",
    "confflow.application.v4_entry",
    "confflow.application.execution.workflow_adapter",
    "confflow.control_worker",
)

#: Public tooling entrypoints that consume calc; used by the CALC metrics.
CALC_PUBLIC_TOOLING_ROOTS: tuple[str, ...] = (
    "confflow.confts",
    "confflow.blocks.refine",
    "confflow.workflow.composition",
    "confflow.calc",
)

#: ``(file, package)`` pairs whose PEP 562 ``_LAZY_EXPORTS`` declare public
#: tooling names; the declared concrete modules count as consumed.
_LAZY_EXPORT_FILES: tuple[tuple[str, str], ...] = (
    ("confflow/__init__.py", "confflow"),
    ("confflow/calc/__init__.py", "confflow.calc"),
)

#: The retired never-released Workflow V3 public wire (Architecture Diet PR-7).
#: The metric counts any of these reappearing on disk; it must stay empty.
V3_PUBLIC_WIRE_MODULES: tuple[str, ...] = (
    "confflow.config.canonical.v3_parser",
    "confflow.config.canonical.v3_graph",
    "confflow.config.canonical.upgrade",
    "confflow.config.canonical.structured",
    "confflow.config.canonical.theory",
    "confflow.config.canonical.extensions",
    "confflow.config.canonical.yaml_io",
    "confflow.config.canonical.execution_versions",
    "confflow.config.workflow_cli",
)

#: Internal-only V3 migration kernel allowed to survive for released V1/V2
#: compatibility.  PR-7 proved none is necessary, so this is empty by decision.
V3_INTERNAL_MIGRATION_KERNEL: tuple[str, ...] = ()

#: Source tokens that identify the retired V3 public wire.
V3_WIRE_TOKENS: tuple[str, ...] = (
    "confflow.workflow.v3",
    "v3_parser",
    "v3_graph",
    "workflow_json_schema_v3",
    "validate_workflow_v3",
    "build_configuration_contract_v3",
    "instantiate_recipe_v3",
    "upgrade_v2_to_v3",
    "execution_versions",
)

#: The released **V1** configuration wire retired by Architecture Diet PR-9.
#: ``contract`` published the v1 contract document (and its digest of the one
#: workflow schema the v1 envelope embedded); ``schema`` was that schema's only
#: generator.  Both must stay absent: the V1 wire was replaced by V4 (and, for
#: the validation response that JobDesk's V4 path consumes, by the frozen
#: ``confflow.configuration-validation.v1`` identifier in
#: :mod:`confflow.config.contract_schemas`).
V1_PUBLIC_WIRE_MODULES: tuple[str, ...] = (
    "confflow.config.canonical.contract",
    "confflow.config.canonical.schema",
)

#: The released **V2** configuration wire retired by Architecture Diet PR-9:
#: the V2 document parser/validator, the V2->canonical adapter and IR, the V2
#: editor manifest and recipe catalog, the V2 fingerprint/param registry, the
#: V2 typed-model and pydantic facades, the V2 diagnostics/serialization
#: helpers, the V2 diagnostic planners (dry-run / config-show / plan) and the
#: legacy YAML validation wrapper.  All must stay absent.
V2_PUBLIC_WIRE_MODULES: tuple[str, ...] = (
    "confflow.config.canonical",
    "confflow.config.canonical.parser",
    "confflow.config.canonical.validation",
    "confflow.config.canonical.v2_adapter",
    "confflow.config.canonical.workflow",
    "confflow.config.canonical.editor_manifest",
    "confflow.config.canonical.recipes",
    "confflow.config.canonical.fingerprint",
    "confflow.config.canonical.param_fields",
    "confflow.config.canonical.pydantic",
    "confflow.config.canonical.diagnostics",
    "confflow.config.canonical.serialization",
    "confflow.config.models",
    "confflow.shared.config_validation",
    "confflow.core.types",
    "confflow.workflow.plan",
    "confflow.workflow.config_show",
    "confflow.workflow.dry_run",
)

#: PR-9 decision: there is no V1 migration kernel to allow.  No ``V1 -> V2``
#: (or ``V1 -> canonical``) upgrade emitter was ever published -- the v1
#: contract document embedded the same workflow schema the v2 document did, so
#: there was nothing to convert.  A future entry here needs a written decision,
#: exactly like :data:`V3_INTERNAL_MIGRATION_KERNEL`.
V1_MIGRATION_MODULES: tuple[str, ...] = ()

#: The single V2 migration kernel the diet retired: the ``WorkflowConfig`` ->
#: canonical-IR adapter that let a released V2 document be planned and
#: validated.  It must stay absent.  ``confflow.config.canonical.upgrade`` (the
#: V2->V3 emitter) was already retired by PR-7 and is counted there.
V2_MIGRATION_MODULES: tuple[str, ...] = ("confflow.config.canonical.v2_adapter",)

#: Source tokens that identify the retired **V1** configuration wire and can
#: belong to no other line.  These are deliberately *not* a blanket ban on the
#: strings "v1"/"v2": the current producer protocol keeps
#: ``confflow.configuration-validation.v1``, ``confflow.editor-manifest.v1``,
#: ``confflow.recipe-catalog.v1``, the ``confflow.contract.*.v1`` capability
#: ids and the ``.v3`` remote capability ids, and the ``v1``/``v2`` protocol
#: majors of unrelated lines (control protocol, remote envelope) are current
#: truth.  Only a token that could only belong to the retired wire is listed.
V1_WIRE_TOKENS: tuple[str, ...] = (
    "confflow.workflow.v1",
    "confflow.configuration-contract.v1",
    "build_configuration_contract_v1",
    "CONFIGURATION_CONTRACT_V1_SCHEMA",
)

#: Source tokens that identify the retired **V2** configuration wire and can
#: belong to no other line.  The V2->canonical adapter and its binding surface
#: were V2-only; the shared ``confflow.config.canonical`` package path is in
#: :data:`V1_V2_SHARED_WIRE_TOKENS`.
V2_WIRE_TOKENS: tuple[str, ...] = (
    "confflow.workflow.v2",
    "confflow.configuration-contract.v2",
    "build_configuration_contract_v2",
    "CONFIGURATION_CONTRACT_V2_SCHEMA",
    "confflow.config.canonical.v2_adapter",
    "to_canonical_workflow",
    "canonical_workflow_payload",
    "parse_workflow_binding",
    "build_workflow_binding",
    "WorkflowConfigBinding",
    "WorkflowBindingCompatibilityError",
    "WORKFLOW_BINDING_SCHEMA",
    "v2_calc_keys",
)

#: Source tokens shared by the retired V1 and V2 configuration wires (the
#: canonical package that published both contract documents, the V2 document
#: parser/validator entrypoints and their typed facades, the diagnostic
#: planners and the legacy YAML validation wrapper).
V1_V2_SHARED_WIRE_TOKENS: tuple[str, ...] = (
    # retired module paths
    "confflow.config.canonical",
    "confflow.config.models",
    "confflow.workflow.plan",
    "confflow.workflow.config_show",
    "confflow.workflow.dry_run",
    "confflow.shared.config_validation",
    "confflow.core.types",
    # retired public parser / validator / resolver entrypoints
    "parse_canonical_workflow",
    "parse_workflow_mapping",
    "load_raw_mapping",
    "load_workflow_definition",
    "load_workflow_model",
    "detect_schema_version",
    "detect_workflow_file_version",
    "validate_workflow_definition",
    "calc_input_diagnostics",
    "resolve_calc_step",
    "resolve_global_options",
    # retired V2 fingerprint surface shared through the canonical package
    "WorkflowFingerprintError",
    "workflow_fingerprint",
    # retired V1/V2 contract and catalog generation
    "CONFIGURATION_CONTRACT_BUILDERS",
    "build_editor_manifest(",
    "build_recipe_catalog(",
    "calc_param_fields",
    "confgen_param_fields",
    "canonical_step_name",
    "CANONICALIZATION_VERSION",
)

#: The full retired-wire vocabulary (V1-only, V2-only and shared).
V1_V2_WIRE_TOKENS: tuple[str, ...] = (
    V1_WIRE_TOKENS + V2_WIRE_TOKENS + V1_V2_SHARED_WIRE_TOKENS
)


def _module_name(root: str, path: str) -> str:
    relative = os.path.relpath(path, root)
    if relative.endswith("__init__.py"):
        relative = os.path.dirname(relative)
    else:
        relative = relative[:-3]
    return relative.replace(os.sep, ".")


def _discover_modules(root: str) -> dict[str, str]:
    modules: dict[str, str] = {}
    package_root = os.path.join(root, "confflow")
    for dirpath, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".py"):
                path = os.path.join(dirpath, filename)
                modules[_module_name(root, path)] = path
    return modules


def _resolve_import(modules: dict[str, str], base: str, module: str, name: str) -> str:
    """Resolve one ``from base.module import name`` target to a module name."""
    if module:
        target = f"{base}.{module}" if base else module
        candidate = f"{target}.{name}"
        return candidate if candidate in modules else target
    candidate = f"{base}.{name}" if base else name
    return candidate if candidate in modules else (base or name)


def _parse_imports(modules: dict[str, str], module: str, path: str) -> set[str]:
    is_init = path.endswith("__init__.py")
    package = module if is_init else module.rsplit(".", 1)[0]
    found: set[str] = set()
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("confflow"):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                up = node.level - 1
                if up:
                    parts = parts[:-up] if up <= len(parts) else []
                base = ".".join(parts)
            else:
                base = ""
            imported = node.module or ""
            if node.names and all(alias.name == "*" for alias in node.names):
                target = f"{base}.{imported}" if base or imported else "confflow"
                if target.startswith("confflow"):
                    found.add(target)
                continue
            for alias in node.names:
                resolved = _resolve_import(modules, base, imported, alias.name)
                if resolved.startswith("confflow"):
                    found.add(resolved)
    return found


def _parents(modules: dict[str, str], module: str) -> set[str]:
    """Return the parent packages whose ``__init__`` executes on import."""
    parts = module.split(".")
    return {
        ".".join(parts[:index])
        for index in range(1, len(parts))
        if ".".join(parts[:index]) in modules
    }


def _closure(modules: dict[str, str], imports: dict[str, set[str]], roots: list[str]) -> set[str]:
    """Compute the static import closure from *roots*, including parents."""
    seen: set[str] = set()
    queue: deque[str] = deque()
    for root in roots:
        if root not in modules:
            raise SystemExit(f"closure root module not found: {root}")
        for candidate in [root, *sorted(_parents(modules, root))]:
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    while queue:
        module = queue.popleft()
        for target in imports.get(module, ()):
            for candidate in [target, *sorted(_parents(modules, target))]:
                if candidate in modules and candidate not in seen:
                    seen.add(candidate)
                    queue.append(candidate)
    return seen


def _reachable(modules: dict[str, str], imports: dict[str, set[str]]) -> set[str]:
    return _closure(modules, imports, list(V4_ROOTS))


def _lazy_export_targets(root: str, relative_path: str, package: str) -> set[str]:
    """Return the concrete modules referenced by a PEP 562 lazy export map."""
    path = os.path.join(root, *relative_path.split("/"))
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        assigned = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in assigned):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for value in node.value.values:
            if not isinstance(value, ast.Tuple) or not value.elts:
                continue
            relative = value.elts[0]
            if not isinstance(relative, ast.Constant) or not isinstance(relative.value, str):
                continue
            targets.add(package if relative.value == "." else f"{package}{relative.value}")
    return targets


def _loc(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _v3_wire_modules(modules: dict[str, str], names: tuple[str, ...]) -> list[str]:
    """Return the retired V3 modules from *names* that exist on disk."""
    return sorted(name for name in names if name in modules)


def _v3_wire_loc(modules: dict[str, str], names: list[str]) -> int:
    return sum(_loc(modules[name]) for name in names)


def _module_mentions_tokens(path: str, tokens: tuple[str, ...]) -> bool:
    """Return whether *path* references any source token in *tokens*."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return any(token in text for token in tokens)


def _module_mentions_v3_wire(path: str) -> bool:
    """Return whether *path* references a retired V3 public-wire token."""
    return _module_mentions_tokens(path, V3_WIRE_TOKENS)


def _module_mentions_v1_wire(path: str) -> bool:
    """Return whether *path* references a retired V1 configuration token.

    A shared V1/V2 token counts for both versions on purpose: it is a token of
    the retired wire the V4 closure must not reach through either line.
    """
    return _module_mentions_tokens(path, V1_WIRE_TOKENS + V1_V2_SHARED_WIRE_TOKENS)


def _module_mentions_v2_wire(path: str) -> bool:
    """Return whether *path* references a retired V2 configuration token."""
    return _module_mentions_tokens(path, V2_WIRE_TOKENS + V1_V2_SHARED_WIRE_TOKENS)


def _retired_wire_counts(
    modules: dict[str, str],
    names: tuple[str, ...],
) -> tuple[list[str], int]:
    """Return the retired-wire modules still on disk and their total LOC."""
    present = _v3_wire_modules(modules, names)
    return present, _v3_wire_loc(modules, present)


def _wire_reachable(
    modules: dict[str, str],
    reachable: set[str],
    mentions,
) -> list[str]:
    """Return V4-closure modules whose source references a retired wire token."""
    return sorted(
        module for module in reachable if module in modules and mentions(modules[module])
    )


def collect(root: str) -> dict[str, object]:
    modules = _discover_modules(root)
    imports: dict[str, set[str]] = defaultdict(set)
    for module, path in modules.items():
        imports[module] = _parse_imports(modules, module, path)
    reachable = _reachable(modules, imports)
    physical_loc = sum(_loc(path) for path in modules.values())
    reachable_loc = sum(_loc(modules[module]) for module in reachable)

    calc_modules = sorted(
        module
        for module in modules
        if module == "confflow.calc" or module.startswith("confflow.calc.")
    )
    calc_set = set(calc_modules)
    calc_loc = sum(_loc(modules[module]) for module in calc_modules)

    tooling_roots = set(CALC_PUBLIC_TOOLING_ROOTS)
    for relative_path, package in _LAZY_EXPORT_FILES:
        tooling_roots |= _lazy_export_targets(root, relative_path, package)
    tooling_roots &= set(modules)
    tooling_reach = _closure(modules, imports, sorted(tooling_roots))
    calc_public = sorted(calc_set & tooling_reach)
    calc_v4 = sorted(calc_set & reachable)
    calc_dead = sorted(calc_set - tooling_reach - reachable)

    v3_public = _v3_wire_modules(modules, V3_PUBLIC_WIRE_MODULES)
    v3_internal = _v3_wire_modules(modules, V3_INTERNAL_MIGRATION_KERNEL)
    v3_v4 = _wire_reachable(modules, reachable, _module_mentions_v3_wire)

    v1_public, v1_public_loc = _retired_wire_counts(modules, V1_PUBLIC_WIRE_MODULES)
    v2_public, v2_public_loc = _retired_wire_counts(modules, V2_PUBLIC_WIRE_MODULES)
    v1_migration, v1_migration_loc = _retired_wire_counts(modules, V1_MIGRATION_MODULES)
    v2_migration, v2_migration_loc = _retired_wire_counts(modules, V2_MIGRATION_MODULES)
    v1_v4 = _wire_reachable(modules, reachable, _module_mentions_v1_wire)
    v2_v4 = _wire_reachable(modules, reachable, _module_mentions_v2_wire)

    return {
        "root": os.path.abspath(root),
        "V4_ROOTS": list(V4_ROOTS),
        "PHYSICAL_PRODUCTION_MODULES": len(modules),
        "PHYSICAL_PRODUCTION_LOC": physical_loc,
        "V4_REACHABLE_MODULES": len(reachable),
        "V4_REACHABLE_LOC": reachable_loc,
        "LEGACY_OR_NON_V4_REACHABLE_LOC": physical_loc - reachable_loc,
        "CALC_MODULES": len(calc_modules),
        "CALC_LOC": calc_loc,
        "CALC_V4_REACHABLE_MODULES": len(calc_v4),
        "CALC_PUBLIC_TOOLING_MODULES": len(calc_public),
        "CALC_DEAD_MODULES": len(calc_dead),
        "V3_PUBLIC_MODULES": len(v3_public),
        "V3_PUBLIC_LOC": _v3_wire_loc(modules, v3_public),
        "V3_INTERNAL_MIGRATION_MODULES": len(v3_internal),
        "V3_INTERNAL_MIGRATION_LOC": _v3_wire_loc(modules, v3_internal),
        "V3_V4_REACHABLE_MODULES": len(v3_v4),
        "V1_PUBLIC_MODULES": len(v1_public),
        "V1_PUBLIC_LOC": v1_public_loc,
        "V2_PUBLIC_MODULES": len(v2_public),
        "V2_PUBLIC_LOC": v2_public_loc,
        "V1_MIGRATION_MODULES": len(v1_migration),
        "V1_MIGRATION_LOC": v1_migration_loc,
        "V2_MIGRATION_MODULES": len(v2_migration),
        "V2_MIGRATION_LOC": v2_migration_loc,
        "V1_V4_REACHABLE_MODULES": len(v1_v4),
        "V2_V4_REACHABLE_MODULES": len(v2_v4),
        "v4_reachable": sorted(reachable),
        "calc_public_tooling": calc_public,
        "calc_dead": calc_dead,
        "v3_public": v3_public,
        "v3_internal_migration": v3_internal,
        "v3_v4_reachable": v3_v4,
        "v1_v4_reachable": v1_v4,
        "v2_v4_reachable": v2_v4,
        "v1_public": v1_public,
        "v2_public": v2_public,
        "v1_migration": v1_migration,
        "v2_migration": v2_migration,
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
    print("LEGACY_OR_NON_V4_REACHABLE_LOC=" f"{metrics['LEGACY_OR_NON_V4_REACHABLE_LOC']}")
    print(f"CALC_MODULES={metrics['CALC_MODULES']}")
    print(f"CALC_LOC={metrics['CALC_LOC']}")
    print(f"CALC_V4_REACHABLE_MODULES={metrics['CALC_V4_REACHABLE_MODULES']}")
    print(f"CALC_PUBLIC_TOOLING_MODULES={metrics['CALC_PUBLIC_TOOLING_MODULES']}")
    print(f"CALC_DEAD_MODULES={metrics['CALC_DEAD_MODULES']}")
    print(f"V3_PUBLIC_MODULES={metrics['V3_PUBLIC_MODULES']}")
    print(f"V3_PUBLIC_LOC={metrics['V3_PUBLIC_LOC']}")
    print(f"V3_INTERNAL_MIGRATION_MODULES={metrics['V3_INTERNAL_MIGRATION_MODULES']}")
    print(f"V3_INTERNAL_MIGRATION_LOC={metrics['V3_INTERNAL_MIGRATION_LOC']}")
    print(f"V3_V4_REACHABLE_MODULES={metrics['V3_V4_REACHABLE_MODULES']}")
    print(f"V1_PUBLIC_MODULES={metrics['V1_PUBLIC_MODULES']}")
    print(f"V1_PUBLIC_LOC={metrics['V1_PUBLIC_LOC']}")
    print(f"V2_PUBLIC_MODULES={metrics['V2_PUBLIC_MODULES']}")
    print(f"V2_PUBLIC_LOC={metrics['V2_PUBLIC_LOC']}")
    print(f"V1_MIGRATION_MODULES={metrics['V1_MIGRATION_MODULES']}")
    print(f"V1_MIGRATION_LOC={metrics['V1_MIGRATION_LOC']}")
    print(f"V2_MIGRATION_MODULES={metrics['V2_MIGRATION_MODULES']}")
    print(f"V2_MIGRATION_LOC={metrics['V2_MIGRATION_LOC']}")
    print(f"V1_V4_REACHABLE_MODULES={metrics['V1_V4_REACHABLE_MODULES']}")
    print(f"V2_V4_REACHABLE_MODULES={metrics['V2_V4_REACHABLE_MODULES']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
