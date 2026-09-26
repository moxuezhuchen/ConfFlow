#!/usr/bin/env python3

"""V4 application runtime: orchestrator, importer, CLI (main-owned).

Proves the formal production path: XYZ import → compile → whole-workflow
orchestration with resume → manifest publication → CLI surface with
legacy fail-closed behavior.  All native execution uses fakes.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict, StructureSet
from confflow.domain.errors import DomainError
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._builders import calc_step, structure, v4_doc

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
FAKE_IRC = FAKES_DIR / "fake_irc.py"

WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.760000 0.590000 0.000000
H 0.760000 -0.590000 0.000000
"""


def _wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str, fake: Path = FAKE_ORCA
) -> Path:
    """Install a fake executable behind a counting wrapper."""
    bin_dir = tmp_path / f"bin-{tag}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(
        "#!/bin/sh\n"
        'base=$(basename "$1")\n'
        f'printf \'%s\\n\' "$base" >> "{tmp_path / f"{tag}.count"}"\n'
        f'exec python3 "{fake}" "$@"\n'
    )
    wrapper.chmod(0o755)
    (tmp_path / f"{tag}.count").write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper


def _two_step_doc() -> dict[str, Any]:
    """Build an opt → sp two-step document."""
    opt = calc_step(
        "s_opt",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 2},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    sp = calc_step(
        "s_sp",
        program="orca",
        bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
        native={"keyword": "B3LYP SP"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 2},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc(
        [opt, sp],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        global_config={"scientific_defaults": {"charge": 0, "multiplicity": 1}},
    )


class TestXyzImporter:
    """XYZ text becomes stable StructureSets, never path identities."""

    def test_single_molecule(self) -> None:
        # Final contract (freeze §1/D-1): fresh imports mint opaque entity
        # IDs (UUID); position is never scientific identity.
        structures = import_xyz(WATER_XYZ, source_name="water.xyz")
        assert len(structures) == 1
        (record,) = tuple(structures)
        assert record.id and record.id != "xyz:0"
        assert record.atoms == ("O", "H", "H")
        assert record.metadata["import_source"] == "water.xyz"

    def test_multi_block(self) -> None:
        # Final contract: independent opaque IDs, distinct even for
        # identical geometry (no geometry-merge).
        structures = import_xyz(WATER_XYZ + WATER_XYZ, source_name="two.xyz")
        ids = [record.id for record in structures]
        assert len(ids) == 2
        assert ids[0] != ids[1]
        assert all(not value.startswith("xyz:") for value in ids)

    def test_malformed(self) -> None:
        with pytest.raises(DomainError):
            import_xyz("", source_name="empty.xyz")
        with pytest.raises(DomainError):
            import_xyz("2\ncomment\nO 0 0 0\n", source_name="short.xyz")
        with pytest.raises(DomainError):
            import_xyz("not a count\ncomment\n", source_name="bad.xyz")


class TestV4RunApplication:
    """Whole-workflow orchestration with resume and manifest."""

    def test_opt_sp_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper = _wrapper(tmp_path, monkeypatch, "run1")
        run_root = str(tmp_path / "run")
        structures = import_xyz(WATER_XYZ + WATER_XYZ, source_name="in.xyz")
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=_two_step_doc(),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=run_root,
                executables=FrozenDict({"orca": str(wrapper)}),
            )
        )
        assert report.status == "completed"
        assert [result.step_id for result in report.step_results] == ["s_opt", "s_sp"]
        assert len(report.step_results[1].structures) == 2
        manifest = report.manifest.thaw()
        assert manifest["content_schema"] == "confflow.run_result_manifest.v1"
        assert manifest["run_id"] == "run"
        assert manifest["status"] == "completed"
        assert [step["id"] for step in manifest["steps"]] == ["s_opt", "s_sp"]

    def test_resume_reuses_published(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper = _wrapper(tmp_path, monkeypatch, "run2")
        run_root = str(tmp_path / "run")
        structures = import_xyz(WATER_XYZ, source_name="in.xyz")
        first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=_two_step_doc(),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=run_root,
                executables=FrozenDict({"orca": str(wrapper)}),
            )
        )
        assert first.status == "completed"
        before = (tmp_path / "run2.count").read_text()
        second = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=_two_step_doc(),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=run_root,
                executables=FrozenDict({"orca": str(wrapper)}),
            )
        )
        assert second.status == "completed"
        # No new native executions: published steps load from disk.
        assert (tmp_path / "run2.count").read_text() == before
        assert second.definition_digest == first.definition_digest

    def test_uncompilable_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(DomainError, match="does not compile"):
            V4RunApplication().run(
                V4RunRequest(
                    workflow_document={"schema": "confflow.workflow.v4", "steps": []},
                    run_inputs=RunInputs(
                        structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})
                    ),
                    run_root=str(tmp_path / "run"),
                    executables=FrozenDict({"orca": "orca"}),
                )
            )

    def test_missing_executable_fails_closed(self, tmp_path: Path) -> None:
        # Final contract: executable resolution is planned-wins → defaults →
        # adapter default, and measurement fails closed on unresolvable
        # binaries.  The fixture uses a nonexistent binary so the test never
        # depends on (or launches) a real installed ORCA.
        import copy

        structures = import_xyz(WATER_XYZ, source_name="in.xyz")
        doc = _two_step_doc()
        doc = copy.deepcopy(doc)
        for step in doc["steps"]:
            step["execution"] = {
                "binding_id": "test",
                "executable": "/nonexistent/no-such-orca",
            }
        with pytest.raises(DomainError, match="cannot measure|no executable"):
            V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                V4RunRequest(
                    workflow_document=doc,
                    run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                    run_root=str(tmp_path / "run"),
                    executables=FrozenDict({}),
                )
            )


class TestV4AnalysisStep:
    """Analysis through the orchestrator with deterministic subject ids."""

    def _analysis_doc(self) -> dict[str, Any]:
        """Build a one-TS IRC + reaction-profile document."""
        irc = calc_step(
            "s_irc",
            program="orca",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP IRC", "irc": {"direction": "both"}},
            profile="path_endpoints",
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 1},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        analysis = {
            "id": "s_an",
            "executor": "analysis",
            "bindings": {
                "structures": {"source": {"step": "s_irc", "port": "structures"}},
                "ts_structures": {"source": {"run": "structures"}},
                "results": {"source": {"run": "gibbs"}},
                "ts_results": {"source": {"run": "gibbs"}},
            },
            "analysis": {
                "native": {"method": "reaction_profile", "energy_mode": "direct"},
                "checks": [],
            },
        }
        return v4_doc(
            [irc, analysis],
            inputs={
                "structures": {"kind": "structure", "cardinality": "many"},
                "gibbs": {"kind": "result", "cardinality": "many"},
            },
            global_config={"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        )

    def test_irc_analysis_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from confflow.domain.result import ResultSet, ScientificResult, make_result_id
        from confflow.domain.units import Unit

        def _seeded(
            kind: str, value: float, subject: str, unit: Any = Unit.HARTREE
        ) -> ScientificResult:
            # Production calc results always carry producer-scoped ResultRef
            # identity; the run-input seeds mirror that stamping so the
            # analysis profile can cite real ResultRef ids.
            return ScientificResult(
                kind=kind,
                value=value,
                unit=unit,
                subject_structure_id=subject,
                source_step_id="seed",
                source_work_item_id="seed-0",
                result_id=make_result_id(
                    step_id="seed",
                    work_item_id="seed-0",
                    kind=kind,
                    subject_structure_id=subject,
                    producer_digest="sha256:" + "0" * 64,
                ),
            )

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        wrapper = _wrapper(tmp_path, monkeypatch, "an", fake=FAKE_IRC)
        run_root = str(tmp_path / "run")
        forward_id = "s_irc:ts00:structure:path_endpoint_forward:0"
        reverse_id = "s_irc:ts00:structure:path_endpoint_reverse:0"
        structures = StructureSet.of(structure("ts00", group_key="rxn-00"))
        gibbs = ResultSet.of(
            _seeded("gibbs_energy", -76.0, "ts00"),
            _seeded("gibbs_energy", -76.40, forward_id),
            _seeded("gibbs_energy", -76.38, reverse_id),
            _seeded("energy", -76.02, "ts00"),
            _seeded("energy", -76.42, forward_id),
            _seeded("energy", -76.40, reverse_id),
        )
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=self._analysis_doc(),
                run_inputs=RunInputs(
                    structures=FrozenDict({"structures": structures}),
                    results=FrozenDict({"gibbs": gibbs}),
                ),
                run_root=run_root,
                executables=FrozenDict({"orca": str(wrapper)}),
            )
        )
        assert report.status == "completed"
        by_id = {result.step_id: result for result in report.step_results}
        assert by_id["s_an"].status.value == "completed"
        kinds = {record.kind for record in by_id["s_an"].results}
        assert {"barrier_forward_endpoint", "barrier_reverse_endpoint", "reaction_profile"} <= kinds
        manifest = report.manifest.thaw()
        # GAP1: the analysis step keeps its minimal ref AND projects one
        # rich reaction-group entry from the actual analysis StepResult.
        assert len(manifest["analyses"]) == 2
        refs = [entry for entry in manifest["analyses"] if "group_key" not in entry]
        groups = [entry for entry in manifest["analyses"] if "group_key" in entry]
        assert refs == [{"capability": "analysis", "step_id": "s_an"}]
        assert len(groups) == 1
        group = groups[0]
        assert group["capability"] == "reaction_profile"
        assert group["step_id"] == "s_an"
        assert group["group_key"] == "rxn-00"
        assert group["ts_structure_id"] == "ts00"
        assert group["forward_endpoint_id"] == forward_id
        assert group["reverse_endpoint_id"] == reverse_id
        assert set(group["barriers"]) == {"forward_endpoint", "reverse_endpoint"}
        assert set(group["energies"]) >= {"electronic_energy", "gibbs_energy"}
        assert group["assignment"] == "unassigned"
        assert group["endpoint_assignment"] == {"forward": "unassigned", "reverse": "unassigned"}
        assert group["source_result_ids"] == sorted(group["source_result_ids"])
        assert group["source_result_ids"]
        # The citations are the analysis's actual source ResultRef ids: the
        # run-input seeds this analysis consumed (a fully step-sourced flow
        # cites published step results instead; the 20-group TSPES E2E pins
        # that case).
        assert set(group["source_result_ids"]) == {
            record.result_id for record in gibbs if record.result_id is not None
        }
        # The durable bytes validate against the actual producer schema
        # and the REAL JobDesk consumer reads exactly one group with the
        # TS/endpoints/barriers/assignment/source ids above.
        import json as _json
        import sys as _sys
        from pathlib import Path as _Path

        import jsonschema as _jsonschema

        from confflow.domain.canonical import canonical_json_bytes as _canonical
        from confflow.producer.contract import run_result_json_schema as _schema

        manifest_path = _Path(run_root) / "run_result.json"
        durable_bytes = manifest_path.read_bytes()
        assert _canonical(_json.loads(durable_bytes.decode("utf-8"))) == durable_bytes
        _jsonschema.validate(instance=_json.loads(durable_bytes.decode("utf-8")), schema=_schema())
        _jobdesk_src = _Path("/opt/jobdesk-v2-v4/src")
        if str(_jobdesk_src) not in _sys.path:
            _sys.path.insert(0, str(_jobdesk_src))
        from jobdesk_v2.application.runs.v4_results import parse_result_bytes as _parse

        view = _parse(durable_bytes)
        assert len(view.groups) == 1
        seen = view.groups[0]
        assert seen.group_key == "rxn-00"
        assert seen.ts_structure_id == "ts00"
        assert seen.forward_endpoint_id == forward_id
        assert seen.reverse_endpoint_id == reverse_id
        assert dict(seen.barriers)
        assert dict(seen.energy_entries)
        assert seen.assignment_display == "unassigned"
        assert set(seen.source_result_ids) == set(group["source_result_ids"])
        assert len(view.analyses) == 2
        assert (view.analyses[0].capability, view.analyses[0].step_id) == ("analysis", "s_an")
        assert (view.analyses[1].capability, view.analyses[1].step_id) == (
            "reaction_profile",
            "s_an",
        )


class TestV4Cli:
    """CLI surface: machine JSON, legacy fail-closed."""

    def test_contract_command(self, capsys: Any) -> None:
        from confflow.v4cli import main

        assert main(["contract", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["content_schema"] == "confflow.configuration-contract.v4"

    def test_validate_command(self, tmp_path: Path, capsys: Any) -> None:
        from confflow.v4cli import main

        workflow = tmp_path / "flow.json"
        workflow.write_text(json.dumps(_two_step_doc()))
        assert main(["validate", "--workflow", str(workflow), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "confflow.configuration-validation.v1"

    def test_run_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        from confflow.v4cli import main

        wrapper = _wrapper(tmp_path, monkeypatch, "cli")
        workflow = tmp_path / "flow.json"
        workflow.write_text(json.dumps(_two_step_doc()))
        xyz = tmp_path / "in.xyz"
        xyz.write_text(WATER_XYZ)
        code = main(
            [
                "run",
                "--workflow",
                str(workflow),
                "--inputs",
                f"structures={xyz}",
                "--run-root",
                str(tmp_path / "run"),
                "--executable",
                f"orca={wrapper}",
                "--json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "completed"
        assert [step["id"] for step in payload["manifest"]["steps"]] == ["s_opt", "s_sp"]

    def test_legacy_rejected(self, tmp_path: Path, capsys: Any) -> None:
        from confflow.v4cli import main

        legacy = tmp_path / "legacy.yaml"
        legacy.write_text("global:\n  iprog: g16\n")
        assert main(["run", "--workflow", str(legacy), "--run-root", str(tmp_path / "r")]) == 1
        assert "legacy_workflow_not_executable" in capsys.readouterr().err
