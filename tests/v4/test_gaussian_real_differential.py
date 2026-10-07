"""Real-Gaussian differential gates for the energy-semantics safety model.

These tests execute the installed Gaussian binary end to end.  They are
skipped when no binary is available (for example in CI) and are the
producer-side evidence for the scientific-reliability remediation: for every
method the capability model accepts, the published energy must equal the
method's own final energy in the raw log, and every method the model refuses
must be refused before execution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.domain import FrozenDict, StructureRecord, StructureSet
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._helpers.v42_executors import calculation_doc

G16 = Path("/opt/g16/g16")

pytestmark = pytest.mark.skipif(not G16.exists(), reason="real Gaussian binary not available")


def _run_gaussian(keyword: str, run_root: Path):
    water = StructureRecord(
        id="water",
        atoms=("O", "H", "H"),
        coordinates=((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (-0.76, 0.59, 0.0)),
        charge=0,
        multiplicity=1,
    )
    doc = calculation_doc(
        "gaussian",
        {"keyword": keyword},
        str(G16),
        checks=["normal_termination"],
        resources={"cores_per_item": 1, "memory_per_item": "256MB"},
    )
    report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=RunInputs(structures=FrozenDict({"structures": StructureSet.of(water)})),
            run_root=str(run_root),
        )
    )
    return report


@pytest.mark.parametrize("keyword", ["HF/STO-3G SP", "B3LYP/STO-3G SP", "PBE1PBE/STO-3G SP"])
def test_real_g16_scf_methods_publish_their_own_final_energy(tmp_path, keyword):
    run_root = tmp_path / "run"
    report = _run_gaussian(keyword, run_root)
    assert report.status == "completed"
    energies = [
        result.value
        for step in report.step_results
        for result in step.results
        if result.kind == "energy"
    ]
    assert len(energies) == 1
    log = next(run_root.rglob("*.log"))
    text = log.read_text(errors="ignore")
    scf_lines = [line for line in text.splitlines() if "SCF Done" in line]
    expected = float(scf_lines[-1].replace("D", "E").split()[4])
    assert energies[0] == pytest.approx(expected)


@pytest.mark.parametrize(
    "keyword",
    [
        "G4MP2",
        "CCSD(T)/STO-3G SP",
    ],
)
def test_real_g16_unsupported_methods_are_refused_before_execution(keyword):
    from confflow.programs.gaussian import GaussianProgramAdapter

    errors = GaussianProgramAdapter().validate_native_definition({"keyword": keyword})
    assert errors, f"{keyword!r} must be refused before execution"
    assert any("ConfFlow cannot extract" in message for message in errors)


def test_real_g16_pbe0_is_refused_by_the_runtime_proof(tmp_path):
    """Gaussian 16 Rev C.02 executes ``PBE0`` as the PBE0DH double hybrid.

    Static validation accepts the common spelling (other revisions run it as a
    proper hybrid), so the runtime publication proof must fail the item closed
    instead of publishing the reference energy.  ``PBE1PBE`` is the hybrid
    control that must still publish its own final energy.
    """
    report = _run_gaussian("PBE0/STO-3G SP", tmp_path / "run")
    assert report.status != "completed"
    item = report.step_results[0].item_results[0]
    assert not item.is_completed
    assert item.error is not None
    assert item.error.details["reason"] == ("post_scf_final_energy_marker:double_hybrid_e2")
    assert item.results.is_empty
