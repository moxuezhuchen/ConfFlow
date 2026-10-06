#!/usr/bin/env python3

"""Producer intent package (L1-C0, mechanical).

:mod:`confflow.producer.intent` was ``confflow/producer/intent.py``; C0 moves
the implementation verbatim to :mod:`.compiler` (only the relative-import
level changes) and keeps this module as a compatible facade.  The four
public symbols and the actually-imported private
``_recipe_cards_to_role_cards`` are the same objects (``is`` holds); their
``__module__`` is kept as ``"confflow.producer.intent"`` as mechanical
compatibility metadata so pickling and ``__module__``-based checks observe
the old module path.  No new parallel types, no behavior change, no empty
capability shells.  Heavy imports stay function-local in ``compiler`` so
module-load purity is unchanged.
"""

from __future__ import annotations

from .compiler import (
    INTENT_SCHEMA,
    IntentCompilationError,
    _recipe_cards_to_role_cards,  # noqa: F401  (mechanical private re-export, not in __all__)
    compile_intent,
    intent_catalog,
)

__all__ = [
    "INTENT_SCHEMA",
    "IntentCompilationError",
    "compile_intent",
    "intent_catalog",
]

_OLD_MODULE = "confflow.producer.intent"

for _name in (
    "IntentCompilationError",
    "compile_intent",
    "intent_catalog",
    "_recipe_cards_to_role_cards",
):
    _obj = globals()[_name]
    try:
        _obj.__module__ = _OLD_MODULE
    except (AttributeError, TypeError):
        pass
del _name, _obj, _OLD_MODULE
