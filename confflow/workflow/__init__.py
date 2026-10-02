#!/usr/bin/env python3

"""Workflow compatibility surface.

The V2/V3 workflow execution runtime was removed by the post-closure
Architecture Diet, and the V2 diagnostic planners (``plan``,
``config_show``, ``dry_run``) were removed with the released V1/V2
configuration wire (PR-9).  The remaining input helpers (``helpers``,
``step_naming``, ``validation``) stay importable for ``--export`` and the
legacy tooling that shares them.

The exports below are resolved lazily (PEP 562) so that importing
``confflow.workflow.v4`` does not drag in the legacy helpers.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "pushd": (".helpers", "pushd"),
    "as_list": (".helpers", "as_list"),
    "count_conformers_any": (".helpers", "count_conformers_any"),
    "count_conformers_in_xyz": (".helpers", "count_conformers_in_xyz"),
    "validate_inputs_compatible": (".validation", "validate_inputs_compatible"),
}

__all__ = [*sorted(_LAZY_EXPORTS)]


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(export[0], package=__name__)
    value = getattr(module, export[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
