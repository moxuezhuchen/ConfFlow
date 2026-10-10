#!/usr/bin/env python3

"""V4 confgen executor: DG-search conformer-generation ensemble production.

Pure executor: never shells to Gaussian/ORCA, calls no calculation pipeline,
imports no legacy runner code. Only the typed v4 scope runs; any scope
without ``schema_version: 4`` fails closed (a ``schema_version: 3`` document
fails with the engine-removal message shared with the workflow schema). The
v4 path runs the DG search (``dg_seed`` -> ``search`` ->
``confgen_search_worker``), publishing passing structures as plain members;
deterministic for a fixed seed, which is required. Members use frozen
``conformer_output_id`` with stable rank ordinal as index and single-parent
lineage from the seed; no energies are invented. Artifacts carry run-relative
locators plus checksums. The executor never raises.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..domain.errors import DomainError
from ..domain.structure import StructureRecord
from ..domain.work_item import WorkItem, WorkItemResult
from ..science.confgen.search_spec import REMOVED_ENGINES_MESSAGE
from .work_item_executor import (
    ItemExecutionContext,
    pure_cancelled_result,
    pure_fail_result,
)

__all__ = [
    "ConfgenExecutor",
]


class ConfgenExecutor:
    """Conformer-generation executor (DG-search engine only)."""

    def execute(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one confgen work item and return its result (never raises)."""
        wall_start = time.time()
        monotonic_start = time.monotonic()
        try:
            return self._run(work_item, context, wall_start, monotonic_start, should_cancel)
        except DomainError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except ValueError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except Exception as exc:  # fail closed; executors never raise into batch
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"confgen internal failure: {exc}",
            )

    def _run(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        scientific = context.scientific
        native = dict(scientific.native)
        if native.get("schema_version") == 3:
            return self._fail(
                work_item, context, wall_start, monotonic_start, REMOVED_ENGINES_MESSAGE
            )
        if native.get("schema_version") == 4:
            from .confgen_search_run import run_search_item

            return run_search_item(
                self, work_item, context, wall_start, monotonic_start, should_cancel
            )
        return self._fail(
            work_item,
            context,
            wall_start,
            monotonic_start,
            "confgen requires a typed schema_version 4 scope",
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _reject_freeze(work_item: WorkItem, context: ItemExecutionContext) -> None:
        """Fail closed on unsupported frozen-atom declarations."""
        overrides = context.scientific.overrides
        try:
            override_freeze = overrides.get("freeze")
        except Exception:
            override_freeze = None
        if override_freeze:
            raise DomainError("confgen does not support freeze overrides; declare an empty freeze")
        defaults = getattr(context, "scientific_defaults", None)
        if defaults is not None and getattr(defaults, "freeze", None):
            raise DomainError(
                "confgen does not support frozen atoms; the run-level freeze "
                "default must be empty for confgen steps"
            )

    @staticmethod
    def _driving(work_item: WorkItem) -> StructureRecord:
        sets = work_item.named_inputs.structures
        if "structure" in sets and len(sets["structure"]) == 1:
            record = sets["structure"][0]
            assert isinstance(record, StructureRecord)
            return record
        raise DomainError(
            "confgen requires exactly one 'structure' input per work item; "
            f"observed ports {sorted(sets)}"
        )

    def _fail(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        message: str,
    ) -> WorkItemResult:
        return pure_fail_result(
            work_item,
            step_id=context.step_id,
            wall_start=wall_start,
            monotonic_start=monotonic_start,
            message=message,
        )

    def _cancelled(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
    ) -> WorkItemResult:
        return pure_cancelled_result(
            work_item,
            step_id=context.step_id,
            wall_start=wall_start,
            monotonic_start=monotonic_start,
        )
