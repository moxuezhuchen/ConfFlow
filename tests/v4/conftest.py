#!/usr/bin/env python3

"""V4 suite configuration: re-exports only, no fixtures.

The guard lives in ``tests/conftest.py``; the names below are re-exported
so ``from tests.v4.conftest import ...`` keeps working. No fixtures here:
with an interleaved file list pytest builds two Package nodes for
``tests/v4`` and a fixture defined here would only attach to the first one
(the ``jobdesk`` fixture moved to ``tests/conftest.py`` for this reason).
"""

from __future__ import annotations

from tests.conftest import BLOCKED_NATIVE_PREFIXES as BLOCKED_NATIVE_PREFIXES
from tests.conftest import _scrubbed_path as _scrubbed_path
