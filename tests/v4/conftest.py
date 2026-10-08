#!/usr/bin/env python3

"""Shared fixtures for the V4 test suite.

Pure in-memory tests do not need fixtures beyond pytest's ``tmp_path``; the
conftest exists so future file-backed document corpora can live here without
touching the legacy test configuration.

Native-isolation guard: the fake suite must never accidentally launch a
system ORCA/Gaussian install. The guard itself (vendor prefixes, the
``PATH`` scrub helper and the autouse fixture) is defined once in
``tests/conftest.py`` so every test under ``tests/`` runs with vendor
install directories scrubbed from ``PATH`` regardless of collection order.
The names below are re-exported here so the existing
``from tests.v4.conftest import ...`` path keeps working. The only escape
hatch is ``CONFFLOW_ALLOW_REAL_QC=1`` for an explicit real-native suite;
nothing in the fake suite sets it.
"""

from __future__ import annotations

import pytest

from tests.conftest import BLOCKED_NATIVE_PREFIXES as BLOCKED_NATIVE_PREFIXES
from tests.conftest import _scrubbed_path as _scrubbed_path

from . import jobdesk_integration


@pytest.fixture
def jobdesk() -> jobdesk_integration.JobDeskIntegration:
    """Return the optional JobDesk consumer surfaces.

    Cross-repo tests request this fixture.  When the private checkout is
    absent (GitHub-hosted CI), only those tests skip; ConfFlow-local tests
    never request it and always run.  A checkout at the wrong revision
    fails closed instead of producing false cross-repo evidence.
    """
    try:
        return jobdesk_integration.load_jobdesk()
    except jobdesk_integration.JobDeskUnavailable as exc:
        pytest.skip(str(exc))
    except jobdesk_integration.JobDeskMisconfigured as exc:
        pytest.fail(str(exc), pytrace=False)
    except jobdesk_integration.JobDeskShaMismatch as exc:
        pytest.fail(str(exc), pytrace=False)
