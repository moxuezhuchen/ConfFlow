#!/usr/bin/env python3

"""Cross-repository acceptance: real ConfFlow bytes into the real JobDesk parser.

This is the gate for the whole feature. Everything else in this branch proves the
producer document is *internally* consistent; this file proves the consumer
actually accepts it.

The chain exercised here is the real one, with no shortcuts:

    confflow config contract --json --version 2     (a real subprocess)
        -> stdout bytes
        -> jobdesk_v2.application.editor.contract.parse_contract_bytes
        -> VerifiedEditorContract (source=producer, level=C, no findings)
        -> WorkflowEditorService
        -> New Run's draft
        -> confflow parse_workflow_mapping / resolve_calc_step

Neither project becomes a dependency of the other. JobDesk is located at run time
(``JOBDESK_V2_ROOT``, else a default checkout path) and the whole module is
skipped when it is not present, so ConfFlow's own test run is unaffected on a
machine that does not have it.

The last two classes are the ones that matter for producer/GUI decoupling:

* a producer default must arrive as ``FieldState(origin="default")`` and must
  **not** be written into the workflow document;
* a recipe must travel back out through ConfFlow's own validator.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from confflow.config.canonical import (
    ConfigValidationError,
    parse_workflow_mapping,
    resolve_calc_step,
)
from confflow.shared.defaults import DEFAULT_PROGRAM, DEFAULT_TASK

#: Where to find a JobDesk V2 checkout.  Explicit configuration wins, so a
#: reviewer can point this at any commit they want to test against.
JOBDESK_ENV_VAR = "JOBDESK_V2_ROOT"
DEFAULT_JOBDESK_ROOT = pathlib.Path("/mnt/c/dft/tool/jobdesk-v2")

#: The field ids the consumer's own level-C fixture publishes.  The producer must
#: provide the same identities: a field id is what a recipe references and what a
#: user's mental model is built on, so it is an interface, not a label.
EXPECTED_FIELD_IDS = {
    "global.charge",
    "global.multiplicity",
    "global.cores_per_task",
    "global.total_memory",
    "global.max_parallel_jobs",
    "global.freeze",
    "global.energy_window",
    "global.scan_coarse_step",
    "global.ts_bond_drift_threshold",
    "global.gaussian_path",
    "global.orca_path",
    "calc.program",
    "calc.task",
    "calc.keyword",
    "calc.cores_per_task",
    "calc.total_memory",
    "calc.max_parallel_jobs",
    "calc.orca_maxcore",
    "calc.energy_window",
    "calc.ts_bond_atoms",
    "calc.ts_rescue_scan",
    "calc.scan_coarse_step",
    "calc.ts_bond_drift_threshold",
    "calc.blocks",
    "confgen.chains",
    "confgen.angle_step",
    "confgen.bond_multiplier",
}

EXPECTED_RECIPE_IDS = {"optimize", "opt_freq", "single_point"}

#: Invokes the real console entry point without depending on the venv's scripts
#: directory being on PATH.  ``confflow.main:main`` is what the console script
#: calls, and it delegates to ``confflow.cli.main``.
_CLI_ENTRY = "import sys; from confflow.main import main; sys.exit(main())"


def _jobdesk_root() -> pathlib.Path | None:
    configured = os.environ.get(JOBDESK_ENV_VAR)
    candidate = pathlib.Path(configured) if configured else DEFAULT_JOBDESK_ROOT
    if (candidate / "src" / "jobdesk_v2").is_dir():
        return candidate
    return None


JOBDESK_ROOT = _jobdesk_root()

pytestmark = pytest.mark.skipif(
    JOBDESK_ROOT is None,
    reason=(
        "JobDesk V2 checkout not found; set "
        f"{JOBDESK_ENV_VAR} to a jobdesk-v2 working copy to run the cross-repo gate"
    ),
)


@contextlib.contextmanager
def _jobdesk_importable():
    """Temporarily make JobDesk importable, then leave ``sys.modules`` alone.

    The path is removed again so importing JobDesk here cannot influence any other
    test in this suite; the already-imported modules stay cached, which is fine.
    """
    assert JOBDESK_ROOT is not None
    src = str(JOBDESK_ROOT / "src")
    sys.path.insert(0, src)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(src)


def _run_cli(*args: str) -> bytes:
    """Run the real CLI and return its stdout bytes."""
    result = subprocess.run(
        [sys.executable, "-c", _CLI_ENTRY, *args],
        capture_output=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b"", f"the machine-readable CLI must be quiet: {result.stderr!r}"
    assert result.stdout.strip(), "the CLI produced no document"
    return result.stdout


@pytest.fixture(scope="module")
def v2_bytes() -> bytes:
    """Capture the real ``--version 2`` output once for the module."""
    return _run_cli("config", "contract", "--json", "--version", "2")


@pytest.fixture(scope="module")
def v1_bytes() -> bytes:
    """Capture the real default output once for the module."""
    return _run_cli("config", "contract", "--json")


@pytest.fixture(scope="module")
def jobdesk() -> Any:
    """Return the JobDesk consumer API imported from the located checkout."""
    try:
        with _jobdesk_importable():
            from jobdesk_v2.application.editor import WorkflowEditorService
            from jobdesk_v2.application.editor.commands import SetStepField
            from jobdesk_v2.application.editor.contract import (
                FallbackArtifacts,
                parse_contract_bytes,
            )
    except ImportError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"JobDesk V2 is not importable: {exc}")

    def parse(payload: bytes) -> Any:
        return parse_contract_bytes(payload, fallback=FallbackArtifacts.shipped())

    # A namespace rather than a class: a class body cannot read the enclosing
    # function's locals for a name it also assigns, which is exactly the shape of
    # the attributes below.
    return SimpleNamespace(
        WorkflowEditorService=WorkflowEditorService,
        SetStepField=SetStepField,
        parse=parse,
    )


class TestTheCliIsWellFormed:
    def test_v2_is_one_json_document(self, v2_bytes: bytes) -> None:
        document = json.loads(v2_bytes.decode("utf-8"))

        assert document["schema"] == "confflow.configuration-contract.v2"

    def test_the_default_is_still_v1(self, v1_bytes: bytes) -> None:
        assert json.loads(v1_bytes.decode("utf-8"))["schema"] == (
            "confflow.configuration-contract.v1"
        )

    def test_v1_does_not_carry_the_editor_model(self, v1_bytes: bytes) -> None:
        document = json.loads(v1_bytes.decode("utf-8"))

        assert "editor_manifest" not in document
        assert "recipe_catalog" not in document

    def test_the_embedded_artifacts_declare_their_schemas(self, v2_bytes: bytes) -> None:
        document = json.loads(v2_bytes.decode("utf-8"))

        assert document["editor_manifest"]["schema"] == "confflow.editor-manifest.v1"
        assert document["recipe_catalog"]["schema"] == "confflow.recipe-catalog.v1"


class TestRealBytesAreAccepted:
    """The acceptance gate: level C, no findings, from the real producer."""

    def test_it_resolves_to_a_level_c_producer_contract(self, jobdesk, v2_bytes: bytes) -> None:
        contract = jobdesk.parse(v2_bytes)

        assert contract.schema == "confflow.configuration-contract.v2"
        assert contract.level.value == "C"
        assert contract.source == "producer"
        assert contract.is_authoritative
        assert contract.editor_manifest_source == "producer"
        assert contract.recipe_catalog_source == "producer"

    def test_the_consumer_reports_no_finding_at_all(self, jobdesk, v2_bytes: bytes) -> None:
        """Not merely "no errors": a real producer must produce no warning either.

        A warning here would mean the manifest and the contract disagree about the
        workflow schema, or a recipe names a field the manifest does not describe.
        """
        contract = jobdesk.parse(v2_bytes)

        assert contract.diagnostics == ()
        assert contract.errors == ()
        assert contract.warnings == ()

    def test_the_capabilities_are_unrestricted(self, jobdesk, v2_bytes: bytes) -> None:
        contract = jobdesk.parse(v2_bytes)

        assert contract.capabilities.can_edit_fields
        assert contract.capabilities.can_use_recipes
        assert not contract.capabilities.is_restricted
        assert contract.capabilities.blocked_reasons == ()

    def test_the_producer_block_is_exactly_what_the_consumer_exposes(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        import confflow

        contract = jobdesk.parse(v2_bytes)

        assert set(contract.producer) == {"package", "version", "commit", "dirty"}
        assert contract.producer["package"] == "confflow"
        assert contract.producer_version == confflow.__version__
        assert contract.describe_source() == f"Using ConfFlow contract {confflow.__version__}"

    def test_the_consumer_never_receives_a_raw_producer_mapping(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        contract = jobdesk.parse(v2_bytes)

        # The contract keeps the schema's version and digest, not a second copy.
        assert not hasattr(contract, "workflow_schema")


class TestExpectedFieldsAndRecipesAreAvailable:
    def test_every_expected_field_id_is_present(self, jobdesk, v2_bytes: bytes) -> None:
        contract = jobdesk.parse(v2_bytes)
        available = set(contract.editor_manifest.field_ids())

        assert EXPECTED_FIELD_IDS <= available

    def test_the_manifest_and_catalog_agree_with_the_contract(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        document = json.loads(v2_bytes.decode("utf-8"))
        contract = jobdesk.parse(v2_bytes)

        assert contract.editor_manifest.workflow_schema_version == (
            contract.workflow_schema_version
        )
        assert contract.workflow_schema_version == document["workflow_schema_version"]

    def test_every_expected_recipe_is_present(self, jobdesk, v2_bytes: bytes) -> None:
        contract = jobdesk.parse(v2_bytes)

        assert set(contract.recipe_catalog.ids()) == EXPECTED_RECIPE_IDS

    def test_recipe_documents_are_real_workflow_documents(self, jobdesk, v2_bytes: bytes) -> None:
        contract = jobdesk.parse(v2_bytes)

        for recipe in contract.recipe_catalog.recipes:
            assert recipe.document.payload["steps"]
            assert "global" in recipe.document.payload

    def test_the_contract_key_is_stable_and_names_the_producer(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        first = jobdesk.parse(v2_bytes)
        second = jobdesk.parse(v2_bytes)

        assert first.contract_key == second.contract_key
        assert first.contract_key.startswith("confflow.configuration-contract.v2/producer/")
        assert first.contract_key == (
            f"{first.schema}/{first.source}"
            f"/manifest:{first.editor_manifest_sha256[:12]}"
            f"/recipes:{first.recipe_catalog_sha256[:12]}"
        )


class TestTheCliAgreesWithTheLibrary:
    def test_the_v2_bytes_match_the_in_process_builder(self, v2_bytes: bytes) -> None:
        from confflow.config.canonical.contract import build_configuration_contract_for_version

        document = json.loads(v2_bytes.decode("utf-8"))
        expected = build_configuration_contract_for_version(
            2,
            producer_version=document["producer"]["version"],
            producer_commit=document["producer"]["commit"],
            producer_dirty=document["producer"]["dirty"],
        )

        assert document == expected


class TestDefaultProvenanceThroughTheRealContract:
    """A producer default must be reported, never written into the document."""

    def _service(self, jobdesk, v2_bytes: bytes, recipe_id: str):
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        return service, service.create_from_recipe(recipe_id)

    def test_the_program_arrives_as_a_producer_default(self, jobdesk, v2_bytes: bytes) -> None:
        service, draft = self._service(jobdesk, v2_bytes, "opt_freq")
        step_id = service.step_views(draft)[0]["step_id"]

        state = service.field_state(draft, "calc.program", step_id=step_id)

        assert state is not None
        assert state.origin == "default"
        assert state.explicit_value is None
        assert state.effective_value == DEFAULT_PROGRAM

    def test_the_task_arrives_as_a_producer_default(self, jobdesk, v2_bytes: bytes) -> None:
        service, draft = self._service(jobdesk, v2_bytes, "optimize")
        step_id = service.step_views(draft)[0]["step_id"]

        state = service.field_state(draft, "calc.task", step_id=step_id)

        assert state is not None
        assert state.explicit_value == "opt"
        assert state.origin == "explicit"

    def test_a_default_is_never_written_into_the_payload(self, jobdesk, v2_bytes: bytes) -> None:
        _, draft = self._service(jobdesk, v2_bytes, "opt_freq")
        params = draft.payload["steps"][0]["params"]

        assert "iprog" not in params
        assert "keyword" not in params
        assert DEFAULT_PROGRAM not in params.values()

    def test_serialising_the_draft_still_omits_the_default(self, jobdesk, v2_bytes: bytes) -> None:
        service, draft = self._service(jobdesk, v2_bytes, "opt_freq")

        text = service.serialize_text(draft)

        assert "iprog" not in text
        assert DEFAULT_PROGRAM not in text

    def test_the_draft_records_the_producer_contract_identity(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        draft = service.create_from_recipe("opt_freq")

        assert draft.contract_key == contract.contract_key


class TestRecipeRoundTripThroughTheProducer:
    """ConfFlow recipe -> JobDesk draft/edit -> ConfFlow validation."""

    def test_a_recipe_edited_in_the_gui_is_accepted_by_confflow(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        draft = service.create_from_recipe("opt_freq")
        step_id = service.step_views(draft)[0]["step_id"]

        # Fill in what the recipe deliberately leaves to the user.
        draft = service.apply(
            draft, jobdesk.SetStepField(step_id, "calc.keyword", "B3LYP/6-31G(d)")
        )

        text = service.serialize_text(draft)
        mapping = yaml.safe_load(text)

        # The producer accepts the document the GUI produced.
        config = parse_workflow_mapping(mapping)
        assert len(config.steps) == 1
        assert config.steps[0].type == "calc"

        resolved = resolve_calc_step(config.steps[0].params, config.global_options)
        assert resolved.task == "opt_freq"
        assert resolved.program == DEFAULT_PROGRAM
        assert resolved.keyword == "B3LYP/6-31G(d)"

    def test_the_gui_document_carries_no_editor_metadata(self, jobdesk, v2_bytes: bytes) -> None:
        """Editor state must not leak into what ConfFlow would read."""
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        draft = service.create_from_recipe("single_point")

        mapping = yaml.safe_load(service.serialize_text(draft))
        parsed = parse_workflow_mapping(mapping)

        assert parsed.raw == mapping
        assert "editor_metadata" not in mapping
        assert "editor_metadata" not in parsed.steps[0].params

    @pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPE_IDS))
    def test_every_published_recipe_survives_the_round_trip(
        self, jobdesk, v2_bytes: bytes, recipe_id: str
    ) -> None:
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        draft = service.create_from_recipe(recipe_id)
        step_id = service.step_views(draft)[0]["step_id"]
        draft = service.apply(
            draft, jobdesk.SetStepField(step_id, "calc.keyword", "B3LYP/6-31G(d)")
        )

        mapping = yaml.safe_load(service.serialize_text(draft))
        config = parse_workflow_mapping(mapping)
        resolved = resolve_calc_step(config.steps[0].params, config.global_options)

        assert resolved.keyword == "B3LYP/6-31G(d)"
        assert resolved.task in {"opt", "opt_freq", "sp"}

    def test_a_recipe_alone_is_a_legal_document_but_not_yet_runnable(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        """A recipe is a starting point, not a finished workflow.

        ``parse_workflow_mapping`` accepts it -- it is a legal ConfFlow document --
        while ``resolve_calc_step`` refuses it, because the keyword has not been
        supplied.  That is exactly what the recipe's ``required_fields``
        (``calc.program``, ``calc.keyword``) declares, so the refusal is the
        contract working rather than a defect.
        """
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        draft = service.create_from_recipe("opt_freq")

        mapping = yaml.safe_load(service.serialize_text(draft))
        config = parse_workflow_mapping(mapping)

        assert config.steps[0].type == "calc"
        assert "keyword" not in config.steps[0].params

        with pytest.raises(ConfigValidationError, match="keyword"):
            resolve_calc_step(config.steps[0].params, config.global_options)

    def test_the_task_the_recipe_pinned_is_the_task_confflow_resolves(
        self, jobdesk, v2_bytes: bytes
    ) -> None:
        contract = jobdesk.parse(v2_bytes)
        service = jobdesk.WorkflowEditorService(contract=contract)
        draft = service.create_from_recipe("opt_freq")
        step_id = service.step_views(draft)[0]["step_id"]
        draft = service.apply(
            draft, jobdesk.SetStepField(step_id, "calc.keyword", "B3LYP/6-31G(d)")
        )

        mapping = yaml.safe_load(service.serialize_text(draft))
        config = parse_workflow_mapping(mapping)
        resolved = resolve_calc_step(config.steps[0].params, config.global_options)

        assert resolved.task == "opt_freq"
        assert resolved.task == DEFAULT_TASK
