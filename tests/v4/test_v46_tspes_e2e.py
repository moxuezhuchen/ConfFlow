#!/usr/bin/env python3

"""Formal TSPES end-to-end (GAP2) and cross-repo chain (GAP6).

One WorkflowDocument, one :class:`V4RunApplication` run, twenty transition
states: TS opt, TS frequency, TS high-level SP, IRC, endpoint opt, endpoint
frequency, endpoint high-level SP, reaction-profile analysis.  The DAG step
count stays fixed at eight while items fan out within steps (20 TS items,
40 endpoint items).  No hand-injected endpoint Gibbs, no direct
``AnalysisExecutor`` bypass, no hand-built manifest: every value flows
through real ``StepResult`` / binding / ``WorkItemStore`` / resume seams,
and the durable manifest carries twenty rich reaction groups readable by
the REAL JobDesk consumer.

Native legs run behind ONE dispatching ``orca`` executable: the wrapper
inspects the real ``.inp`` keyword and selects the real-format fake behind
it (TS candidate, unshifted frequency measurement, single point, IRC),
so every leg still passes through the formal ``ProgramAdapter`` /
parser / runtime only.

The formal chain itself is ConfFlow-local and always runs.  Only the
JobDesk consumer assertions (marked ``cross_repo``) require the optional
private checkout, resolved lazily through the ``jobdesk`` fixture.
"""

from __future__ import annotations

import copy
import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from confflow.analysis.pes import pes_from_analysis_results  # noqa: E402
from confflow.application.v4_run import (  # noqa: E402
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict  # noqa: E402
from confflow.domain.canonical import canonical_json_bytes  # noqa: E402
from confflow.execution.process import NativeProcessSupervisor  # noqa: E402
from confflow.persistence.contracts import store_path  # noqa: E402
from confflow.producer import (  # noqa: E402
    check_manifest_against_contract,
    generate_contract_bytes,
    get_recipe_v4,
    validate_workflow_bytes,
    verify_artifact_bytes,
)
from confflow.producer.contract import (  # noqa: E402
    RESULT_MANIFEST_SCHEMA,
    contract_digest_of,
    run_result_json_schema,
)
from confflow.workflow.v4.assembly import RunInputs  # noqa: E402
from confflow.workflow.v4.compiler import compile_workflow  # noqa: E402

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
FAKE_IRC = FAKES_DIR / "fake_irc.py"

N_TS = 20

TSPES_IDS = (
    "ts",
    "ts_freq",
    "ts_sp",
    "irc",
    "endpoint_opt",
    "endpoint_freq",
    "endpoint_sp",
    "reaction_profile",
)

WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.760000 0.590000 0.000000
H 0.760000 -0.590000 0.000000
"""

# One dispatching executable: the real `.inp` keyword selects the
# real-format fake behind it.  TS saddle searches report a candidate with
# one imaginary mode; frequencies echo the input geometry (a measurement,
# so the production profile binds results to the input entity); single
# points report energy only; IRC sections come from the real-grammar IRC
# fake.  Banner order is reverse-first, proving direction never comes
# from order.
ORCA_DISPATCH_SCRIPT = """#!/bin/sh
inp="$1"
base=$(basename "$inp")
printf '%s\\n' "$base" >> "$ORCA_COUNT_FILE"
if grep -q "IRC" "$inp"; then
  exec python3 "$FAKE_IRC_REAL" "$@"
elif grep -q "Freq" "$inp"; then
  FAKE_MODE=success_freq_noshift exec python3 "$FAKE_ORCA_REAL" "$@"
elif grep -q "OptTS" "$inp"; then
  FAKE_MODE=ts_candidate exec python3 "$FAKE_ORCA_REAL" "$@"
elif grep -q " SP" "$inp"; then
  FAKE_MODE=success_sp exec python3 "$FAKE_ORCA_REAL" "$@"
else
  FAKE_MODE=success_opt exec python3 "$FAKE_ORCA_REAL" "$@"
fi
"""


def _install_dispatch_orca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Install the dispatching ``orca`` plus its invocation log."""
    bin_dir = tmp_path / "bin-orca"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(ORCA_DISPATCH_SCRIPT)
    wrapper.chmod(0o755)
    count_file = tmp_path / "orca.count"
    count_file.write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_ORCA_REAL", str(FAKE_ORCA))
    monkeypatch.setenv("FAKE_IRC_REAL", str(FAKE_IRC))
    monkeypatch.setenv("ORCA_COUNT_FILE", str(count_file))
    monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper, count_file


def _native_count(count_file: Path) -> int:
    """Return the number of logged native invocations."""
    return len([line for line in count_file.read_text().splitlines() if line.strip()])


def _ts_inputs(count: int) -> Any:
    """Build *count* TS run inputs with explicit reaction group keys."""
    structures = import_xyz(WATER_XYZ * count, source_name="ts20.xyz")
    keyed = [
        replace(record, group_key=f"rxn-{index:02d}", lineage_root_id=f"root-{index:02d}")
        for index, record in enumerate(tuple(structures))
    ]
    assert [record.group_key for record in keyed] == [f"rxn-{index:02d}" for index in range(count)]
    from confflow.domain import StructureSet

    return StructureSet.of(*keyed)


def _tspes_document() -> dict[str, Any]:
    """Return the catalog TSPES document plus the required global defaults."""
    document = copy.deepcopy(get_recipe_v4("tspes")["document"])
    document["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    return document


def _run_tspes(document: dict[str, Any], structures: Any, run_root: str) -> Any:
    """Run one WorkflowDocument through the formal application runtime."""
    compiled = compile_workflow(document)
    assert compiled.ok, [str(item) for item in compiled.diagnostics]
    assert compiled.plan is not None
    assert len(compiled.plan.steps) == 8, [step.step_id for step in compiled.plan.steps]
    assert sorted(step.step_id for step in compiled.plan.steps) == sorted(TSPES_IDS)
    report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=json.loads(canonical_json_bytes(document).decode("utf-8")),
            run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
            run_root=run_root,
        )
    )
    assert report.status == "completed", [
        (step.step_id, dict(step.summary)) for step in report.step_results
    ]
    return report


def _assert_counts(report: Any) -> Any:
    """Assert the 20/40 fanout lives in items, never in the DAG."""
    by_id = {result.step_id: result for result in report.step_results}
    assert sorted(by_id) == sorted(TSPES_IDS)
    for step_id in ("ts", "ts_freq", "ts_sp", "irc"):
        assert by_id[step_id].summary["completed"] == N_TS, step_id
    for step_id in ("endpoint_opt", "endpoint_freq", "endpoint_sp"):
        assert by_id[step_id].summary["completed"] == 2 * N_TS, step_id
        assert len(tuple(by_id[step_id].structures)) == 2 * N_TS, step_id
    assert len(tuple(by_id["irc"].structures)) == 2 * N_TS
    assert by_id["reaction_profile"].summary["completed"] == 1
    return by_id


def _assert_analysis(by_id: dict[str, Any]) -> Any:
    """Assert the real analysis emitted twenty complete group profiles."""
    analysis = by_id["reaction_profile"]
    profiles = [record for record in analysis.results if record.kind == "reaction_profile"]
    assert len(profiles) == 20, [record.kind for record in analysis.results]
    assert {record.kind for record in analysis.results} == {
        "barrier_forward_endpoint",
        "barrier_reverse_endpoint",
        "endpoint_gibbs_delta",
        "endpoint_energy_delta",
        "reaction_profile",
    }
    assert len(tuple(analysis.results)) == 20 * 5
    for record in profiles:
        assert isinstance(record.value, dict)
        assert record.value["assignment"] == "unassigned"
        assert record.value["endpoint_assignment"] == {
            "forward": "unassigned",
            "reverse": "unassigned",
        }
    combined = pes_from_analysis_results(analysis.results)
    assert combined["count"] == 20
    assert combined["group_keys"] == [f"rxn-{index:02d}" for index in range(N_TS)]
    return profiles


def _assert_durable_manifest(
    report: Any, run_root: str, definition_digest: str
) -> tuple[dict[str, Any], bytes]:
    """Assert the durable manifest validates and carries twenty rich groups."""
    import jsonschema

    manifest_path = Path(run_root) / RUN_RESULT_FILENAME
    assert manifest_path.exists()
    durable_bytes = manifest_path.read_bytes()
    durable = json.loads(durable_bytes.decode("utf-8"))
    assert durable["content_schema"] == RESULT_MANIFEST_SCHEMA
    assert durable["definition_digest"] == definition_digest
    assert canonical_json_bytes(durable) == durable_bytes
    jsonschema.validate(instance=durable, schema=run_result_json_schema())
    refs = [entry for entry in durable["analyses"] if "group_key" not in entry]
    groups = [entry for entry in durable["analyses"] if "group_key" in entry]
    assert refs == [{"capability": "analysis", "step_id": "reaction_profile"}]
    assert [entry["group_key"] for entry in groups] == [f"rxn-{index:02d}" for index in range(N_TS)]
    published_ids = {entry["result_id"] for entry in durable.get("results", [])}
    assert published_ids
    for entry in groups:
        assert entry["capability"] == "reaction_profile"
        assert entry["step_id"] == "reaction_profile"
        assert entry["ts_structure_id"]
        assert entry["forward_endpoint_id"] and entry["reverse_endpoint_id"]
        assert set(entry["barriers"]) == {"forward_endpoint", "reverse_endpoint"}
        for barrier in entry["barriers"].values():
            assert barrier["value"] == pytest.approx(0.0, abs=1e-9)
            assert barrier["unit"] == "hartree"
        assert set(entry["energies"]) >= {"electronic_energy", "gibbs_energy"}
        for section in ("electronic_energy", "gibbs_energy"):
            assert set(entry["energies"][section]) == {"ts", "forward", "reverse"}
        assert entry["assignment"] == "unassigned"
        assert entry["endpoint_assignment"] == {"forward": "unassigned", "reverse": "unassigned"}
        assert len(entry["source_result_ids"]) == 6
        assert set(entry["source_result_ids"]) <= published_ids
    return durable, durable_bytes


def _assert_jobdesk_view(jobdesk: Any, durable: dict[str, Any], durable_bytes: bytes) -> Any:
    """Assert the REAL JobDesk consumer reads all twenty groups verbatim."""
    view = jobdesk.parse_result_bytes(durable_bytes)
    assert view.run_id == durable["run_id"]
    assert view.status == "completed"
    assert len(view.steps) == len(durable["steps"])
    assert len(view.groups) == 20
    assert [group.group_key for group in view.groups] == [
        f"rxn-{index:02d}" for index in range(N_TS)
    ]
    manifest_groups = {
        entry["group_key"]: entry for entry in durable["analyses"] if "group_key" in entry
    }
    for group in view.groups:
        entry = manifest_groups[group.group_key]
        assert group.ts_structure_id == entry["ts_structure_id"]
        assert group.forward_endpoint_id == entry["forward_endpoint_id"]
        assert group.reverse_endpoint_id == entry["reverse_endpoint_id"]
        assert dict(group.barriers)
        assert dict(group.energy_entries)
        assert group.assignment_display == "unassigned"
        assert set(group.source_result_ids) == set(entry["source_result_ids"])
    return view


class TestFormalTspesE2E:
    """GAP2: the full formal TSPES chain over twenty transition states."""

    def test_twenty_ts_one_document_one_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, count_file = _install_dispatch_orca(tmp_path, monkeypatch)
        document = _tspes_document()
        payload = canonical_json_bytes(document)
        validated = validate_workflow_bytes(payload)
        assert validated.ok, validated.diagnostics
        assert validated.definition_digest is not None
        structures = _ts_inputs(N_TS)
        run_root = str(tmp_path / "run")
        report = _run_tspes(document, structures, run_root)
        assert report.definition_digest == validated.definition_digest
        # 20 TS + 20 freq + 20 SP + 20 IRC + 40 opt + 40 freq + 40 SP.
        assert _native_count(count_file) == 200
        by_id = _assert_counts(report)
        _assert_analysis(by_id)
        durable, durable_bytes = _assert_durable_manifest(
            report, run_root, validated.definition_digest
        )
        # Real WorkItemStore evidence: every step ran through the durable
        # claim -> execute -> commit lifecycle.
        for step_id in TSPES_IDS:
            assert Path(store_path(run_root, step_id)).exists(), step_id
        # Durable artifacts verify against their bytes; manifest provenance
        # matches the run that produced it.
        assert verify_artifact_bytes(durable, run_root)
        assert durable["provenance"]["package"] == "confflow"

    def test_resume_reuses_every_item(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, count_file = _install_dispatch_orca(tmp_path, monkeypatch)
        document = _tspes_document()
        structures = _ts_inputs(N_TS)
        run_root = str(tmp_path / "run")
        first = _run_tspes(document, structures, run_root)
        assert first.status == "completed"
        assert _native_count(count_file) == 200
        second = _run_tspes(document, structures, run_root)
        assert second.status == "completed"
        assert second.definition_digest == first.definition_digest
        assert _native_count(count_file) == 200


class TestJobdeskConsumerView:
    """Cross-repo: the same durable bytes, read by the REAL JobDesk parser."""

    pytestmark = pytest.mark.cross_repo

    def test_jobdesk_reads_twenty_groups_from_durable_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobdesk: Any
    ) -> None:
        _, count_file = _install_dispatch_orca(tmp_path, monkeypatch)
        document = _tspes_document()
        validated = validate_workflow_bytes(canonical_json_bytes(document))
        assert validated.ok, validated.diagnostics
        assert validated.definition_digest is not None
        structures = _ts_inputs(N_TS)
        run_root = str(tmp_path / "run")
        report = _run_tspes(document, structures, run_root)
        assert _native_count(count_file) == 200
        durable, durable_bytes = _assert_durable_manifest(
            report, run_root, validated.definition_digest
        )
        view = _assert_jobdesk_view(jobdesk, durable, durable_bytes)
        assert len(view.groups) == 20


class TestCrossRepoChain:
    """GAP6: producer contract -> JobDesk recipe -> same bytes -> run -> view."""

    pytestmark = pytest.mark.cross_repo

    def test_jobdesk_tspes_bytes_yield_twenty_groups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobdesk: Any
    ) -> None:
        import confflow

        _, count_file = _install_dispatch_orca(tmp_path, monkeypatch)
        # (1) Producer contract bytes; (2) JobDesk verifies them.
        producer_version = getattr(confflow, "__version__", "unknown")
        contract_bytes = generate_contract_bytes(producer_version=producer_version)
        envelope = json.loads(contract_bytes.decode("utf-8"))
        assert envelope["contract_digest"] == contract_digest_of(envelope)
        contract = jobdesk.parse_v4_contract_bytes(contract_bytes)
        # (3) JobDesk authors the TSPES document from its recipe catalog.
        authored = jobdesk.author_v4_document(contract, "tspes")
        payload = authored.to_mapping()
        assert payload["schema"] == "confflow.workflow.v4"
        assert [step["id"] for step in payload["steps"]] == list(TSPES_IDS)
        edited = copy.deepcopy(payload)
        edited["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
        workflow_bytes = canonical_json_bytes(edited)
        # (4) ConfFlow validates the EXACT SAME BYTES; (5) digests agree.
        validated = validate_workflow_bytes(workflow_bytes)
        assert validated.ok, validated.diagnostics
        assert validated.definition_digest is not None
        assert canonical_json_bytes(json.loads(workflow_bytes.decode("utf-8"))) == workflow_bytes
        # (6) One formal run; (7) durable manifest; (8) JobDesk view.
        structures = _ts_inputs(N_TS)
        run_root = str(tmp_path / "run")
        report = _run_tspes(json.loads(workflow_bytes.decode("utf-8")), structures, run_root)
        assert report.definition_digest == validated.definition_digest
        assert _native_count(count_file) == 200
        by_id = _assert_counts(report)
        _assert_analysis(by_id)
        durable, durable_bytes = _assert_durable_manifest(
            report, run_root, validated.definition_digest
        )
        view = _assert_jobdesk_view(jobdesk, durable, durable_bytes)
        # Checksums/locators verify against the run bytes; the manifest
        # provenance matches the submitting contract producer.
        assert verify_artifact_bytes(durable, run_root)
        check_manifest_against_contract(
            durable,
            {"producer": dict(envelope["producer"])},
            validated.definition_digest,
        )
        assert view.producer.get("package") == "confflow"
