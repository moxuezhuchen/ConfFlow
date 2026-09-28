#!/usr/bin/env python3
"""ConfFlow configuration package.

This package owns exactly one current public wire surface: the
dependency-free schema-identifier authority in
:mod:`confflow.config.contract_schemas`, which the V4 producer contract, the
V4 editor manifest, the V4 recipe catalog and the V4 validation response all
read so that a schema id can never be written down twice.

There is no V1/V2 configuration wire here any more.  The released V1 and V2
configuration-contract documents, the V2 workflow JSON schema, the public V2
parser/validator entrypoints, the V2 editor manifest and recipe catalog, the
V2->canonical adapter and the historical ``from confflow.config import X``
model facade were retired by the Architecture Diet PR-9.  The only supported
configuration wire is V4 (``confflow.configuration-contract.v4``,
``confflow.workflow.v4``); a V1/V2/V3 document fails closed with
``unsupported_workflow_version`` at the outermost version discriminator.

The command surface lives in :mod:`confflow.config.cli`; importing this
package must stay dependency-free so the producer never loads a configuration
runtime.
"""

from __future__ import annotations

__all__: list[str] = []
