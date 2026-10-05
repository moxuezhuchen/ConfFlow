#!/usr/bin/env python3

"""V4-2 production-core debt gate.

Static checks that the V4-2 production core (``confflow.domain``,
``confflow.execution``, ``confflow.workflow.v4``, ``confflow.programs``)
carries zero legacy semantics:

- forbidden legacy symbols never appear as code (docstrings and comments are
  exempt, matching the V4-1 gate),
- forbidden filename/config contracts never appear as code tokens,
- ``confflow.programs`` imports only the V4 core plus outside packages.

The shared V4-1 gate in ``test_architecture_boundaries.py`` owns the domain,
execution, and workflow import boundaries; this file extends the same rules
to the program adapters introduced in V4-2.
"""

from __future__ import annotations
