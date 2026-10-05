"""INHERITED-REPORT compat: project_v3 restores legacy audited/basis from entries."""

from confflow.domain._immutable import FrozenDict
from confflow.science.confgen.kernel_records import KernelRun
from confflow.science.confgen.wire_v3 import project_v3

CARRIED_FRESH = (
    "carried torsion locks re-measured on every fresh geometry " "against prior absolute frames"
)
CARRIED_INPUT = (
    "carried torsion locks re-measured on the input geometry " "against prior absolute frames"
)
EMPTY_BASIS = "no incoming chained state"


def _kernel_run(inherited: dict | None, certificate: object = None) -> KernelRun:
    report: dict = {"schema_version": 3}
    if inherited is not None:
        report["inherited"] = dict(inherited)
    return KernelRun(
        leaves=(),
        target_records=(),
        report=FrozenDict(report),
        certificate=certificate,
    )


def test_empty_entries_projects_to_unaudited() -> None:
    cert = object()
    kernel = _kernel_run(
        {"entries": [], "audited": True, "basis": CARRIED_FRESH},
        certificate=cert,
    )
    projected = project_v3(kernel)
    inherited = dict(projected.report_json()["inherited"])
    assert inherited["entries"] == []
    assert inherited["audited"] is False
    assert inherited["basis"] == EMPTY_BASIS
    # Kernel original report untouched; certificate object identical.
    assert dict(kernel.report_json()["inherited"])["audited"] is True
    assert projected.certificate is cert


def test_nonempty_entries_projects_to_audited() -> None:
    cert = object()
    entry = {
        "axis": "torsions.T1",
        "model": "absolute_dihedral_grid",
        "frame": [0, 1, 2, 3],
        "expected_absolute": 60.0,
    }
    kernel = _kernel_run(
        {"entries": [entry], "audited": False, "basis": EMPTY_BASIS},
        certificate=cert,
    )
    projected = project_v3(kernel)
    inherited = dict(projected.report_json()["inherited"])
    assert inherited["entries"] == [entry]
    assert inherited["audited"] is True
    assert inherited["basis"] == CARRIED_FRESH
    assert projected.certificate is cert


def test_missing_inherited_section_passes_through() -> None:
    cert = object()
    kernel = _kernel_run(None, certificate=cert)
    projected = project_v3(kernel)
    assert "inherited" not in projected.report_json()
    assert projected.certificate is cert
