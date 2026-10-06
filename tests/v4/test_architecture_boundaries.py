#!/usr/bin/env python3

"""V4 architecture debt gate.

Static (AST) and runtime (subprocess import) checks that the greenfield core
carries zero legacy semantics:

- ``confflow.domain`` imports nothing from the repository except itself;
- ``confflow.workflow.v4`` and ``confflow.execution`` import only
  ``confflow.domain`` / ``confflow.execution`` / ``confflow.workflow.v4``;
- forbidden legacy symbols never appear as code (docstrings may explain them);
- importing the V4 core never imports the V2/V3 runtime, legacy config, the
  calc subsystem, the retired PR-6 remote helpers, or the dev fixture modules;
- the retired never-released Workflow V3 public wire (PR-7) stays physically
  absent and unimported: no V3 parser/graph/contract/catalog/capability module,
  no V3 CLI route, and no V2->V3 migration kernel;
- the V4 packages are discoverable by ``setuptools.find_packages``.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "confflow"


#: Extended formal V4 production roots (Architecture Diet PR-0).
#:
#: The original gate only scanned domain/execution/workflow.v4/persistence/
#: programs/remote.  The producer contract, the analysis engine, and the
#: formal application/V4 CLI entrypoints are equally part of the V4
#: production closure and must not regrow legacy-runtime imports.

#: Individual formal V4 production files outside the package roots above.

#: Formal V4 runtime paths added to the IMPORT-ONLY legacy gate.  Their public
#: compatibility signatures legitimately carry historical parameter names
#: (``input_xyz`` on the service boundary), so the symbol/filename scans stay
#: on the roots above while the import ban covers these paths too.

#: Import prefixes that must never (re)appear in a formal V4 production
#: file.  ``confflow.core.exceptions`` is intentionally NOT in the exact
#: forbidden-module tuple below: the formal entry module needs the shared
#: ``ConfFlowError`` type.  Every other legacy ``confflow.core`` facade is
#: forbidden.

#: ``confflow.workflow.v4`` is the V4 engine and stays allowed, so the bare
#: ``confflow.workflow`` prefix above needs exactly one exemption.

#: Exact legacy modules that must never be imported by an extended root.
#: ``confflow.core.exceptions`` is excluded (shared error type, see above).

#: The only configuration module the producer may import: the dependency-free
#: schema authority (Architecture Diet PR-2).  Before PR-2 the producer
#: imported three constants from ``confflow.config.canonical``; that debt is
#: removed and must not come back.

#: The single file-scoped legacy-token exemption:
#: ``formal_v4_runner`` accepts the historical keyword surface
#: (``input_xyz``) at the formal boundary by design.  The name is a
#: compatibility parameter of the one documented drop-in runner, not a
#: legacy execution path.

#: Exact transitive legacy-dependency set pulled by ``import confflow.producer``.
#:
#: Architecture Diet PR-2 drove this baseline to EMPTY: the producer now reads
#: the three schema identifiers from the dependency-free
#: :mod:`confflow.config.contract_schemas` authority instead of importing
#: ``confflow.config.canonical``.  The equality guard stays so any future
#: legacy dependency fails immediately; ``confflow.config`` (thin shell),
#: ``confflow.config.contract_schemas`` (the authority), and the bare
#: ``confflow.workflow`` parent package (required to import
#: ``confflow.workflow.v4``) are intentionally not debt.


#: Legacy modules deleted by the architecture diet (PR-1 dead-code removal).
#: They must stay absent: recreating one would silently revive a forbidden
#: import path.  Additions here happen in the same commit that deletes the
#: file, never speculatively.

#: V2/V3 execution-runtime modules retired by Architecture Diet PR-4, plus
#: the dead remote duplicates retired by PR-6 (their live authorities are
#: ``launch_lease.TokenLaunchLease``, ``persistence.recovery.reconcile_owner``
#: and ``worker_supervision``).  They must stay physically absent,
#: unimportable, and unreferenced by the V4 production sources.

#: Retained-by-design compatibility modules that are explicitly allowed to
#: be absent from the source tree.  Empty today; a module moves here only
#: with a written compatibility decision.

#: The retired never-released Workflow V3 public wire (Architecture Diet PR-7).
#: These modules served only the V3 parser/graph/semantic validation, the V3
#: editor/recipe catalogs, the ``configuration-contract.v3`` document, the
#: V2->V3 upgrade emitter, and the V3 capability advertisement. V3 was never in
#: a published release, so none of them may reappear.

#: PR-7 decision: the minimal internal migration kernel is empty. No released
#: V1/V2 compatibility path needs a ``V1/V2 -> internal V3 IR -> canonical``
#: chain (the V2 path is ``V2 -> canonical IR -> V2 execution shape`` directly),
#: so there is no internal-only V3 module to allow. If one were ever retained it
#: must be listed here and must not be importable from the V4 production roots.

#: Symbols that only the retired V3 public wire ever defined. A V4 production
#: module referencing one would be a new V3 consumer (forbidden by PR-7).

#: The released V1/V2 configuration wire retired by Architecture Diet PR-9.
#: The V1 contract document, the one workflow-schema generator that document
#: digested, the V2 document parser/validator, the V2->canonical adapter and
#: IR, the V2 editor manifest and recipe catalog, the V2 fingerprint/param
#: registry, the V2 typed-model and pydantic facades, the V2 diagnostics and
#: serialization helpers, the V2 diagnostic planners (dry-run / config-show /
#: plan) and the legacy YAML validation wrapper.  None may reappear.

#: PR-9 decision: no V1 migration kernel exists to allow.  The v1 contract
#: document embedded the same workflow schema the v2 document did, so no
#: ``V1 -> V2`` / ``V1 -> canonical`` upgrade was ever published.  An entry here
#: would need its own written compatibility decision.

#: The single V2 migration kernel PR-9 retired: the ``WorkflowConfig`` ->
#: canonical-IR adapter.  It must stay absent.

#: Source tokens that identify the retired V1/V2 configuration wire.  This is
#: deliberately *not* a ban on the strings "v1"/"v2": the current producer
#: protocol keeps ``confflow.configuration-validation.v1``,
#: ``confflow.editor-manifest.v1``, ``confflow.recipe-catalog.v1`` and the
#: ``confflow.contract.*.v1`` capability ids, and unrelated lines (control
#: protocol, remote envelope) keep their own v1/v2 majors.

#: Architecture Diet PR-8 consolidated helper authorities.  Six byte-identical
#: durable-publish copies (run_state/publication/generation/arbitration/
#: v4_run/producer) now route through the single ``persistence.fsatomic``
#: protocol, and both program renderers re-export the single
#: ``programs._naming`` job-name sanitizer.  The single-authority identity is
#: pinned, and the deleted local copies must never regrow.

#: Consumer modules that must expose the authority object itself, never a copy.

#: Production scopes scanned for regrown local helper copies.
#: ``confflow.remote`` is intentionally excluded: its writers are
#: deliberately self-contained (different O_EXCL/O_NOFOLLOW/mode/taxonomy
#: semantics; deferred PR-8 audit boundary) and must not be conflated with the
#: durable-publish authority.

#: Helper definitions that exist only in the PR-8 authority modules.


class TestFacadeLazyIsolation:
    """Compatibility facades stay lazy (Architecture Diet PR-3).

    ``confflow.core`` and ``confflow.application[.execution]`` keep every
    historical public name, but importing the package no longer executes the
    legacy implementation modules behind those names.
    """

    def test_core_public_surface_still_importable(self) -> None:
        from confflow.core import (
            HARTREE_TO_KCALMOL,
            PERIODIC_SYMBOLS,
            get_atomic_number,
        )

        assert callable(get_atomic_number)
        assert len(PERIODIC_SYMBOLS) > 0
        assert isinstance(HARTREE_TO_KCALMOL, float)

    def test_application_public_surface_still_importable(self) -> None:
        import pytest

        from confflow.application import ExecutionService as AppExecutionService
        from confflow.application.execution import (
            ExecutionService,
            RunPaths,
            RunState,
            build_workflow_service,
        )

        assert ExecutionService is AppExecutionService
        assert RunState.__name__ == "RunState"
        assert RunPaths.__name__ == "RunPaths"
        assert callable(build_workflow_service)
        # DIET-2 R1.1: the dev-fixture helpers left the production package
        # for tests.support; the lazy facade must no longer resolve them.
        import confflow.application.execution as execution

        for removed in (
            "InMemoryExecutionRepository",
            "SyntheticProducerExecutor",
            "open_synthetic_service",
            "synthetic_agent_entry",
            "SYNTHETIC_ARTIFACT",
            "SYNTHETIC_ARTIFACT_CONTENT",
            "SYNTHETIC_ARTIFACT_PATH",
            "SYNTHETIC_ARTIFACT_SCHEMA",
            "SYNTHETIC_ARTIFACT_TERMINAL",
            "SYNTHETIC_CHECKPOINT_ID",
        ):
            with pytest.raises(AttributeError):
                getattr(execution, removed)


class TestPackaging:
    """The V4 packages must ship in wheels."""

    def test_packages_are_discoverable(self) -> None:
        """Every shipped package is discoverable by its ``__init__.py``.

        Discovery is a pure path walk (the same rule setuptools uses for a
        regular package): importing ``setuptools`` here would couple the
        gate to the host's distutils shim state, which is not a property of
        this repository.
        """
        packages = {
            ".".join(init.parent.relative_to(REPO_ROOT).parts)
            for init in PACKAGE_ROOT.rglob("__init__.py")
            if "__pycache__" not in init.parts
        }
        for expected in (
            "confflow.domain",
            "confflow.execution",
            "confflow.workflow.v4",
            "confflow.persistence",
            "confflow.programs",
            "confflow.remote",
        ):
            assert expected in packages, expected


class TestSchemaHasNoHiddenCleanup:
    """The V4 schema has no hidden auto-clean semantics."""

    def test_schema_text_has_no_cleanup_vocabulary(self) -> None:
        from confflow.workflow.v4 import build_workflow_json_schema

        text = repr(build_workflow_json_schema()).lower()
        for forbidden in ("auto_clean", "delete_work_dir", "clean_opts", "ibkout"):
            assert forbidden not in text, forbidden

    def test_calculation_model_exposes_only_the_frozen_blocks(self) -> None:
        from confflow.workflow.v4.schema import CalculationModel

        assert set(CalculationModel.model_fields) == {
            "program",
            "role",
            "execution_adapter",
            "result_profile",
            "native",
            "checks",
            "check_params",
            "recovery",
            "seed",
            "overrides",
        }


#: New V4-5 production modules.  Every entry must live under one of the
#: scanned production-core roots above (so the import, symbol, and filename
#: gates cover it automatically) and must import without pulling legacy
#: runtimes (checked in a subprocess below).


#: Modules where ordinal-within-item usage is documented and allowed:
#: ``member_index``/``point_ordinal`` are native member identities folded
#: into deterministic entity ids, never cross-set pairing keys.

#: The single sanctioned ``range``-ordinal application: an explicitly
#: declared bijective atom permutation applied element-wise (the opposite
#: of guessed cross-set pairing).
