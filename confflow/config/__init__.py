#!/usr/bin/env python3
"""ConfFlow configuration package.

Owns one public wire: dependency-free schema-id authority in `config.contract_schemas` shared
by V4 contract, editor manifest, recipe catalog, validation response (write id once).
Only V4 wire supported (`confflow.configuration-contract.v4`, `confflow.workflow.v4`); V1/V2/V3
fail closed with `unsupported_workflow_version` at outermost discriminator; V1/V2 contracts,
schemas, parsers, manifests, adapters and `from confflow.config import X` facade retired (PR-9).
Importing this package stays dependency-free so producer never loads a configuration runtime;
command surface lives in `confflow.config.cli`.
"""

from __future__ import annotations

__all__: list[str] = []
