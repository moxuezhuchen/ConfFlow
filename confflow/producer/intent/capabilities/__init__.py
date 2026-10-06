#!/usr/bin/env python3

"""Producer intent capabilities package (L1-C2).

Light package marker only: no handler/solver/registry imports at package
import time so schema/catalog paths stay pure (no import side effects).
Capability modules are imported lazily by the compiler entry point and by
explicit ``build_default_intent_registry()`` (function-local), never at
top level here.
"""

from __future__ import annotations
