#!/usr/bin/env python3

"""V4-2 adapter conformance: render, parse, resources, artifacts, failures.

Covers both program adapters (:class:`GaussianProgramAdapter` and
:class:`OrcaProgramAdapter`) against the frozen
:class:`~confflow.execution.native.ProgramAdapter` interface using only
``tmp_path`` fixtures:

- program resolution through aliases and strict ``DomainError`` rejection,
- native input rendering (resources, keywords, strict native vocabulary,
  charge/multiplicity gating, freeze handling, checkpoint staging),
- execution-request construction,
- native output parsing (termination, SCF/archive fallback, frequency commit
  rules, missing-log diagnostics),
- artifact discovery (sha256 checksums, run-relative locators),
- failure taxonomy and environment probing,
- fake-executable conformance: every ``FAKE_MODE`` log satisfies the real
  V4 parsers (plus a legacy-parser cross-check).

Static parse tests build fixture logs with the pure rendering helpers from
:mod:`tests.v4.fakes` (no subprocess); only the fake-conformance class
launches the fake executables.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from confflow.domain import FrozenDict, ResourceRequest
from confflow.domain.errors import DomainError
from confflow.execution.native import (
    GeometryOutput,
    ProgramAdapter,
    ProgramName,
    ResolvedCalculationInputs,
    StagedArtifact,
)
from confflow.programs.gaussian import GaussianProgramAdapter
from confflow.programs.orca import OrcaProgramAdapter
from confflow.programs.registry import PROGRAM_ALIASES, get_program_adapter
from tests.v4._builders import structure
from tests.v4.fakes import fake_g16, fake_orca

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_G16 = FAKES_DIR / "fake_g16.py"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"

SUCCESS_MODES = (
    "success_opt",
    "success_sp",
    "success_freq",
    "abnormal",
    "missing_geometry",
    "missing_freq",
    "ts_candidate",
)

WATER_ATOMS = ("O", "H", "H")
WATER_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))


def gaussian_inputs(**overrides: object) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs for the Gaussian adapter."""
    params: dict[str, object] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        "native": FrozenDict({"keyword": "B3LYP/6-31G* Opt"}),
        "checkpoints": (),
        "step_id": "s_opt",
        "work_item_id": "wi:s_opt:item0",
        "logical_key": "s_opt:item0",
    }
    params.update(overrides)
    return ResolvedCalculationInputs(**params)  # type: ignore[arg-type]


def orca_inputs(**overrides: object) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs for the ORCA adapter."""
    params: dict[str, object] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        "native": FrozenDict({"keyword": "B3LYP D3BJ def2-SVP Opt"}),
        "checkpoints": (),
        "step_id": "s_opt",
        "work_item_id": "wi:s_opt:item0",
        "logical_key": "s_opt:item0",
    }
    params.update(overrides)
    return ResolvedCalculationInputs(**params)  # type: ignore[arg-type]


def run_fake(fake: Path, work_dir: Path, input_name: str, mode: str) -> int:
    """Run a fake executable in *work_dir* and return its exit code."""
    env = {"PATH": os.environ.get("PATH", ""), "FAKE_MODE": mode}
    completed = subprocess.run(
        [str(fake), input_name],
        cwd=str(work_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode


class TestProgramResolution:
    """Program-name resolution and adapter identity."""

    def test_aliases_cover_both_programs(self) -> None:
        assert PROGRAM_ALIASES["g16"] == ProgramName.GAUSSIAN.value
        assert PROGRAM_ALIASES["gaussian"] == ProgramName.GAUSSIAN.value
        assert PROGRAM_ALIASES["orca"] == ProgramName.ORCA.value

    def test_unknown_program_raises_domain_error(self) -> None:
        with pytest.raises(DomainError):
            get_program_adapter("mopac")

    def test_gaussian_aliases_resolve(self) -> None:
        for alias in ("g16", "gaussian", "g03", "g09"):
            adapter = get_program_adapter(alias)
            assert isinstance(adapter, GaussianProgramAdapter)
            assert adapter.program_name is ProgramName.GAUSSIAN

    def test_orca_alias_resolves(self) -> None:
        adapter = get_program_adapter("orca")
        assert isinstance(adapter, OrcaProgramAdapter)
        assert adapter.program_name is ProgramName.ORCA

    def test_adapter_identities(self) -> None:
        gaussian = GaussianProgramAdapter()
        orca = OrcaProgramAdapter()
        assert gaussian.input_extension == "gjf"
        assert gaussian.log_extension == "log"
        assert gaussian.default_executable == "g16"
        assert orca.input_extension == "inp"
        assert orca.log_extension == "out"
        assert orca.default_executable == "orca"
        assert gaussian.adapter_version == "confflow.program.gaussian.v1"
        assert gaussian.parser_version == "confflow.program.gaussian.parser.v1"
        assert orca.adapter_version == "confflow.program.orca.v1"
        assert orca.parser_version == "confflow.program.orca.parser.v1"

    def test_adapters_satisfy_the_program_adapter_protocol(self) -> None:
        assert isinstance(GaussianProgramAdapter(), ProgramAdapter)
        assert isinstance(OrcaProgramAdapter(), ProgramAdapter)


class TestFakeExecutables:
    """Fake scripts are executable and honor every documented mode."""

    def test_fakes_are_executable_with_shebang(self) -> None:
        for fake in (FAKE_G16, FAKE_ORCA):
            assert os.access(fake, os.X_OK)
            assert fake.read_text(encoding="utf-8").splitlines()[0].startswith("#!")

    def test_unknown_mode_exits_nonzero(self, tmp_path: Path) -> None:
        assert run_fake(FAKE_G16, tmp_path, "missing.gjf", "nope") == 3
        assert run_fake(FAKE_ORCA, tmp_path, "missing.inp", "nope") == 3


class TestGaussianRender:
    """Gaussian native input rendering from resolved resources."""

    def test_resources_map_to_nproc_and_mem(self) -> None:
        materialized = GaussianProgramAdapter().materialize_native_input(gaussian_inputs())
        assert materialized.program is ProgramName.GAUSSIAN
        assert materialized.main_input_name == "s_opt_item0.gjf"
        content = materialized.files[0].content
        assert "%nprocshared=4" in content
        assert "%mem=16GB" in content
        assert "%Chk=s_opt_item0.chk" in content

    def test_keyword_p_request_preserved(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({"keyword": "#p B3LYP Opt"}))
        content = GaussianProgramAdapter().materialize_native_input(inputs).files[0].content
        assert "#p B3LYP Opt" in content

    def test_keyword_normalized_without_p(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({"keyword": "# B3LYP Opt"}))
        content = GaussianProgramAdapter().materialize_native_input(inputs).files[0].content
        assert "# B3LYP Opt" in content

    def test_missing_keyword_rejected(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({}))
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_unknown_native_key_rejected(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({"keyword": "B3LYP Opt", "bogus": 1}))
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_dict_blocks_rejected_with_guidance(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({"keyword": "B3LYP Opt", "blocks": {}}))
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_string_blocks_rejected(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({"keyword": "B3LYP Opt", "blocks": "x"}))
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_missing_charge_rejected(self) -> None:
        inputs = gaussian_inputs(charge=None)
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_missing_multiplicity_rejected(self) -> None:
        inputs = gaussian_inputs(multiplicity=None)
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_missing_cores_rejected(self) -> None:
        inputs = gaussian_inputs(resources=ResourceRequest(memory_per_item_bytes=2**34))
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_missing_memory_rejected(self) -> None:
        inputs = gaussian_inputs(resources=ResourceRequest(cores_per_item=2))
        with pytest.raises(ValueError, match="native_input_error"):
            GaussianProgramAdapter().materialize_native_input(inputs)

    def test_freeze_flags_rendered(self) -> None:
        inputs = gaussian_inputs(freeze=(1,))
        content = GaussianProgramAdapter().materialize_native_input(inputs).files[0].content
        assert "O  -1 " in content
        assert "H  0 " in content

    def test_checkpoint_staged_as_oldchk(self) -> None:
        staged = StagedArtifact(
            local_name="staged/input-checkpoint-0.chk",
            role="checkpoint",
            subject_structure_id="s0",
        )
        inputs = gaussian_inputs(checkpoints=(staged,))
        content = GaussianProgramAdapter().materialize_native_input(inputs).files[0].content
        assert "%OldChk=staged/input-checkpoint-0.chk" in content

    def test_write_chk_disabled(self) -> None:
        inputs = gaussian_inputs(native=FrozenDict({"keyword": "B3LYP Opt", "write_chk": "false"}))
        content = GaussianProgramAdapter().materialize_native_input(inputs).files[0].content
        assert "%Chk=" not in content

    def test_execution_request_shape(self) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        request = adapter.build_execution_request(
            materialized,
            executable="/opt/g16/g16",
            work_dir="/tmp/work",
            env={"PATH": "/usr/bin"},
            walltime_seconds=600.0,
        )
        assert request.argv == ("/opt/g16/g16", "s_opt_item0.gjf")
        assert request.stdout_file == "s_opt_item0.log"
        assert request.stderr_file == "s_opt_item0.err"
        assert request.env["GAUSS_EXEDIR"] == "/opt/g16"
        assert request.walltime_seconds == 600.0

    def test_execution_request_bare_executable_keeps_env(self) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        request = adapter.build_execution_request(
            materialized,
            executable="g16",
            work_dir="/tmp/work",
            env={"PATH": "/usr/bin"},
            walltime_seconds=None,
        )
        assert "GAUSS_EXEDIR" not in request.env
        assert request.walltime_seconds is None


class TestOrcaRender:
    """ORCA native input rendering from resolved resources."""

    def test_resources_map_to_pal_and_maxcore(self) -> None:
        materialized = OrcaProgramAdapter().materialize_native_input(orca_inputs())
        assert materialized.program is ProgramName.ORCA
        assert materialized.main_input_name == "s_opt_item0.inp"
        content = materialized.files[0].content
        assert "%pal nprocs 4 end" in content
        assert "%maxcore 4000" in content

    def test_keyword_line_rendered(self) -> None:
        materialized = OrcaProgramAdapter().materialize_native_input(orca_inputs())
        assert "! B3LYP D3BJ def2-SVP Opt" in materialized.files[0].content

    def test_blocks_mapping_rendered(self) -> None:
        native = FrozenDict({"keyword": "B3LYP Opt", "blocks": {"scf": {"MaxIter": 200}}})
        content = (
            OrcaProgramAdapter()
            .materialize_native_input(orca_inputs(native=native))
            .files[0]
            .content
        )
        assert "%scf" in content
        assert "MaxIter 200" in content

    def test_maxcore_override_passes_through(self) -> None:
        native = FrozenDict({"keyword": "B3LYP Opt", "maxcore": "2000"})
        content = (
            OrcaProgramAdapter()
            .materialize_native_input(orca_inputs(native=native))
            .files[0]
            .content
        )
        assert "%maxcore 2000" in content

    def test_missing_keyword_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            OrcaProgramAdapter().materialize_native_input(orca_inputs(native=FrozenDict({})))

    def test_unknown_native_key_rejected(self) -> None:
        native = FrozenDict({"keyword": "B3LYP Opt", "bogus": 1})
        with pytest.raises(ValueError, match="native_input_error"):
            OrcaProgramAdapter().materialize_native_input(orca_inputs(native=native))

    def test_missing_charge_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            OrcaProgramAdapter().materialize_native_input(orca_inputs(charge=None))

    def test_missing_multiplicity_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            OrcaProgramAdapter().materialize_native_input(orca_inputs(multiplicity=None))

    def test_missing_cores_rejected(self) -> None:
        inputs = orca_inputs(resources=ResourceRequest(memory_per_item_bytes=2**34))
        with pytest.raises(ValueError, match="native_input_error"):
            OrcaProgramAdapter().materialize_native_input(inputs)

    def test_freeze_renders_geom_constraints(self) -> None:
        content = (
            OrcaProgramAdapter()
            .materialize_native_input(orca_inputs(freeze=(1, 2)))
            .files[0]
            .content
        )
        assert "%geom Constraints" in content
        assert "{ C 0 C }" in content
        assert "{ C 1 C }" in content

    def test_freeze_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            OrcaProgramAdapter().materialize_native_input(orca_inputs(freeze=(99,)))

    def test_execution_request_shape(self) -> None:
        adapter = OrcaProgramAdapter()
        materialized = adapter.materialize_native_input(orca_inputs())
        request = adapter.build_execution_request(
            materialized,
            executable="/opt/orca/orca",
            work_dir="/tmp/work",
            env={"PATH": "/usr/bin"},
            walltime_seconds=None,
        )
        assert request.argv == ("/opt/orca/orca", "s_opt_item0.inp")
        assert request.stdout_file == "s_opt_item0.out"
        assert request.stderr_file == "s_opt_item0.err"


def _parse_gaussian_static(tmp_path: Path, mode: str) -> tuple[object, Path]:
    """Write a static Gaussian fixture log and parse it with the adapter."""
    adapter = GaussianProgramAdapter()
    materialized = adapter.materialize_native_input(gaussian_inputs())
    log_text = fake_g16.render_log_text(mode, WATER_ATOMS, WATER_COORDS)
    (tmp_path / materialized.main_input_name).write_text("fake input", encoding="utf-8")
    (tmp_path / "s_opt_item0.log").write_text(log_text, encoding="utf-8")
    if mode != "abnormal":
        (tmp_path / "s_opt_item0.chk").write_bytes(b"")
    result = adapter.parse_native_result(
        work_dir=str(tmp_path), log_file_name="s_opt_item0.log", materialized=materialized
    )
    return result, tmp_path


def _parse_orca_static(tmp_path: Path, mode: str) -> tuple[object, Path]:
    """Write a static ORCA fixture log and parse it with the adapter."""
    adapter = OrcaProgramAdapter()
    materialized = adapter.materialize_native_input(orca_inputs())
    log_text = fake_orca.render_log_text(mode, WATER_ATOMS, WATER_COORDS)
    (tmp_path / materialized.main_input_name).write_text("fake input", encoding="utf-8")
    (tmp_path / "s_opt_item0.out").write_text(log_text, encoding="utf-8")
    if mode not in ("success_sp", "missing_geometry"):
        atoms, coords = fake_orca.shift_coordinates(WATER_ATOMS, WATER_COORDS)
        (tmp_path / "s_opt_item0.xyz").write_text(
            fake_orca.render_xyz_text(atoms, coords), encoding="utf-8"
        )
    if mode != "abnormal":
        (tmp_path / "s_opt_item0.gbw").write_bytes(b"FAKE-ORCA-GBW\n")
    result = adapter.parse_native_result(
        work_dir=str(tmp_path), log_file_name="s_opt_item0.out", materialized=materialized
    )
    return result, tmp_path


class TestGaussianParse:
    """Gaussian parser facts from static fixture logs (no subprocess)."""

    def test_success_opt_facts(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "success_opt")
        assert result.program is ProgramName.GAUSSIAN
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.PRODUCED
        assert result.final_geometry is not None
        assert result.final_geometry.atoms == WATER_ATOMS
        assert result.energy == pytest.approx(fake_g16.ENERGY_HARTREE)
        assert result.frequencies_cm == ()
        assert result.log_file_name == "s_opt_item0.log"

    def test_success_sp_has_no_geometry(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "success_sp")
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.NONE
        assert result.final_geometry is None
        assert result.energy == pytest.approx(fake_g16.ENERGY_HARTREE)

    def test_success_freq_facts(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "success_freq")
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.PRODUCED
        assert len(result.frequencies_cm) == len(fake_g16.REAL_FREQUENCIES)
        assert result.energies_hartree["gibbs"] == pytest.approx(fake_g16.gibbs_energy())
        assert result.energies_hartree["gibbs_correction"] == pytest.approx(
            fake_g16.GIBBS_CORRECTION
        )

    def test_abnormal_terminates_false_with_geometry(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "abnormal")
        assert result.terminated_normally is False
        assert result.geometry_output is GeometryOutput.PRODUCED
        assert result.energy is None

    def test_missing_geometry(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "missing_geometry")
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.NONE
        assert result.energy == pytest.approx(fake_g16.ENERGY_HARTREE)

    def test_trailing_partial_frequencies_discarded(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "missing_freq")
        assert result.frequencies_cm == ()

    def test_ts_candidate_has_one_imaginary_mode(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "ts_candidate")
        assert len(result.frequencies_cm) == len(fake_g16.REAL_FREQUENCIES)
        assert sum(1 for value in result.frequencies_cm if value < 0.0) == 1

    def test_committed_section_wins_over_trailing_partial(self, tmp_path: Path) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        first = fake_g16.render_frequencies_block((111.0, 222.0, 333.0))
        second = fake_g16.render_frequencies_block((444.0, 555.0))
        text = (
            " SCF Done:  E(RB3LYP) =  -76.0000000000     A.U. after   5 cycles\n"
            + first
            + " Zero-point correction= 0.01 (Hartree/Particle)\n"
            + second
            + " Normal termination of Gaussian 16\n"
        )
        (tmp_path / "s_opt_item0.log").write_text(text, encoding="utf-8")
        result = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.log", materialized=materialized
        )
        assert tuple(result.frequencies_cm) == (111.0, 222.0, 333.0)

    def test_archive_fallback_for_electronic_energy(self, tmp_path: Path) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        text = (
            " Entering Gaussian System, Link 0=g16\n"
            " 1\\1\\GINC-QC\\HF=-76.123456\\Dipole=0.,0.,0.\\Gibbs=-76.100000\\@\n"
            " Normal termination of Gaussian 16\n"
        )
        (tmp_path / "s_opt_item0.log").write_text(text, encoding="utf-8")
        result = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.log", materialized=materialized
        )
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.NONE
        assert result.energy == pytest.approx(-76.123456)
        assert result.energies_hartree["gibbs"] == pytest.approx(-76.1)
        assert result.native_metadata["electronic_source"] == "archive_hf"
        assert result.native_metadata["gibbs_source"] == "archive_gibbs"

    def test_scf_preferred_over_archive(self, tmp_path: Path) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        text = (
            " SCF Done:  E(RB3LYP) =  -76.5000000000     A.U. after   5 cycles\n"
            " 1\\1\\GINC-QC\\HF=-76.123456\\@\n"
            " Normal termination of Gaussian 16\n"
        )
        (tmp_path / "s_opt_item0.log").write_text(text, encoding="utf-8")
        result = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.log", materialized=materialized
        )
        assert result.energy == pytest.approx(-76.5)
        assert result.native_metadata["electronic_source"] == "scf_done"

    def test_missing_log_yields_parse_diagnostic(self, tmp_path: Path) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        result = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="absent.log", materialized=materialized
        )
        assert result.terminated_normally is False
        assert result.geometry_output is GeometryOutput.NONE
        assert result.energy is None
        assert len(result.parser_diagnostics) == 1
        assert result.parser_diagnostics[0].code == "native_parse_error"

    def test_produced_files_listed(self, tmp_path: Path) -> None:
        result, _ = _parse_gaussian_static(tmp_path, "success_opt")
        roles = {entry.name: entry.role for entry in result.produced_files}
        assert roles["s_opt_item0.log"] == "native_output"
        assert roles["s_opt_item0.gjf"] == "native_input"
        assert roles["s_opt_item0.chk"] == "checkpoint"
        assert roles["s_opt_item0.err"] == "stderr"


class TestOrcaParse:
    """ORCA parser facts from static fixture logs (no subprocess)."""

    def test_success_opt_facts(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "success_opt")
        assert result.program is ProgramName.ORCA
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.PRODUCED
        assert result.final_geometry is not None
        assert result.final_geometry.atoms == WATER_ATOMS
        assert result.energy == pytest.approx(fake_orca.ENERGY_HARTREE)
        assert result.frequencies_cm == ()

    def test_xyz_companion_preferred(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "success_opt")
        assert result.final_geometry is not None
        assert result.final_geometry.coordinates[0][0] == pytest.approx(
            WATER_COORDS[0][0] + fake_orca.SHIFT_ANGSTROM
        )

    def test_success_sp_has_no_geometry(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "success_sp")
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.NONE
        assert result.energy == pytest.approx(fake_orca.ENERGY_HARTREE)

    def test_success_freq_facts(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "success_freq")
        assert result.terminated_normally is True
        assert len(result.frequencies_cm) == len(fake_orca.REAL_FREQUENCIES)
        assert result.energies_hartree["gibbs"] == pytest.approx(fake_orca.gibbs_energy())
        assert result.energies_hartree["gibbs_correction"] == pytest.approx(
            fake_orca.GIBBS_CORRECTION
        )
        assert result.native_metadata["num_imaginary_frequencies"] == 0

    def test_abnormal_terminates_false(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "abnormal")
        assert result.terminated_normally is False
        assert result.energy is None
        assert result.native_metadata["error_details"].startswith("Abnormal program termination")

    def test_missing_geometry(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "missing_geometry")
        assert result.terminated_normally is True
        assert result.geometry_output is GeometryOutput.NONE
        assert result.energy == pytest.approx(fake_orca.ENERGY_HARTREE)

    def test_uncommitted_frequencies_discarded(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "missing_freq")
        assert result.frequencies_cm == ()

    def test_ts_candidate_has_one_imaginary_mode(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "ts_candidate")
        assert sum(1 for value in result.frequencies_cm if value < 0.0) == 1

    def test_log_block_fallback_without_xyz(self, tmp_path: Path) -> None:
        result, _ = _parse_orca_static(tmp_path, "success_opt")
        assert result.final_geometry is not None
        (tmp_path / "s_opt_item0.xyz").unlink()
        adapter = OrcaProgramAdapter()
        materialized = adapter.materialize_native_input(orca_inputs())
        reread = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.out", materialized=materialized
        )
        assert reread.geometry_output is GeometryOutput.PRODUCED
        assert reread.final_geometry is not None
        assert reread.final_geometry.atoms == WATER_ATOMS

    def test_missing_log_yields_parse_diagnostic(self, tmp_path: Path) -> None:
        adapter = OrcaProgramAdapter()
        materialized = adapter.materialize_native_input(orca_inputs())
        result = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="absent.out", materialized=materialized
        )
        assert result.terminated_normally is False
        assert result.geometry_output is GeometryOutput.NONE
        assert len(result.parser_diagnostics) == 1


class TestArtifactDiscovery:
    """Checksum and locator rules for discovered artifacts."""

    def test_gaussian_chk_discovery(self, tmp_path: Path) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        for name, content in (
            ("s_opt_item0.gjf", b"fake input"),
            ("s_opt_item0.log", b"log text"),
            ("s_opt_item0.chk", b""),
            ("s_opt_item0.err", b""),
        ):
            (tmp_path / name).write_bytes(content)
        parsed = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.log", materialized=materialized
        )
        found = adapter.discover_artifacts(
            work_dir=str(tmp_path),
            run_relative_prefix="items/s_opt/s_opt_item0",
            native_result=parsed,
            step_id="s_opt",
            work_item_id="wi:s_opt:item0",
            subject_structure_id="s0",
        )
        by_name = {ref.id.rsplit("/", 1)[-1]: ref for ref in found}
        assert set(by_name) == {
            "s_opt_item0.gjf",
            "s_opt_item0.log",
            "s_opt_item0.chk",
            "s_opt_item0.err",
        }
        chk = by_name["s_opt_item0.chk"]
        assert chk.role == "checkpoint"
        assert chk.checksum == "sha256:" + hashlib.sha256(b"").hexdigest()
        assert chk.locator.path == "items/s_opt/s_opt_item0/s_opt_item0.chk"
        assert chk.producer_step_id == "s_opt"
        assert chk.producer_work_item_id == "wi:s_opt:item0"
        assert chk.subject_structure_id == "s0"
        assert chk.program == ProgramName.GAUSSIAN.value

    def test_gaussian_missing_files_skipped(self, tmp_path: Path) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        (tmp_path / "s_opt_item0.log").write_text("log", encoding="utf-8")
        parsed = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.log", materialized=materialized
        )
        found = adapter.discover_artifacts(
            work_dir=str(tmp_path),
            run_relative_prefix="items/s_opt/s_opt_item0",
            native_result=parsed,
            step_id="s_opt",
            work_item_id="wi:s_opt:item0",
            subject_structure_id="s0",
        )
        assert {ref.id.rsplit("/", 1)[-1] for ref in found} == {"s_opt_item0.log"}

    def test_orca_gbw_discovery(self, tmp_path: Path) -> None:
        adapter = OrcaProgramAdapter()
        materialized = adapter.materialize_native_input(orca_inputs())
        payloads = {
            "s_opt_item0.inp": b"fake input",
            "s_opt_item0.out": b"log text",
            "s_opt_item0.xyz": b"3\nc\nO 0 0 0\n",
            "s_opt_item0.gbw": b"FAKE-ORCA-GBW\n",
        }
        for name, content in payloads.items():
            (tmp_path / name).write_bytes(content)
        parsed = adapter.parse_native_result(
            work_dir=str(tmp_path), log_file_name="s_opt_item0.out", materialized=materialized
        )
        found = adapter.discover_artifacts(
            work_dir=str(tmp_path),
            run_relative_prefix="items/s_opt/s_opt_item0",
            native_result=parsed,
            step_id="s_opt",
            work_item_id="wi:s_opt:item0",
            subject_structure_id="s0",
        )
        by_name = {ref.id.rsplit(":", 1)[-1]: ref for ref in found}
        assert by_name["s_opt_item0.gbw"].role == "checkpoint_wavefunction"
        assert (
            by_name["s_opt_item0.gbw"].checksum
            == "sha256:" + hashlib.sha256(b"FAKE-ORCA-GBW\n").hexdigest()
        )
        assert by_name["s_opt_item0.gbw"].locator.path == (
            "items/s_opt/s_opt_item0/s_opt_item0.gbw"
        )
        assert by_name["s_opt_item0.xyz"].role == "native_geometry"


class TestEnvironmentProbe:
    """Adapters carry no safe probe; file identity owns the digest."""

    def test_gaussian_probe_empty(self) -> None:
        assert GaussianProgramAdapter().environment_probe("g16") == {}

    def test_orca_probe_empty(self) -> None:
        assert OrcaProgramAdapter().environment_probe("orca") == {}


class TestFakeConformance:
    """Every fake mode satisfies the real V4 adapter parsers end to end."""

    @pytest.mark.parametrize("mode", SUCCESS_MODES)
    def test_gaussian_modes(self, tmp_path: Path, mode: str) -> None:
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(gaussian_inputs())
        (tmp_path / materialized.main_input_name).write_text(
            materialized.files[0].content, encoding="utf-8"
        )
        exit_code = run_fake(FAKE_G16, tmp_path, materialized.main_input_name, mode)
        assert exit_code == (2 if mode == "abnormal" else 0)
        result = adapter.parse_native_result(
            work_dir=str(tmp_path),
            log_file_name="s_opt_item0.log",
            materialized=materialized,
        )
        assert result.terminated_normally == (mode != "abnormal")
        if mode in ("success_sp", "missing_geometry"):
            assert result.geometry_output is GeometryOutput.NONE
        else:
            assert result.geometry_output is GeometryOutput.PRODUCED
        if mode == "abnormal":
            assert result.energy is None
        else:
            assert result.energy == pytest.approx(fake_g16.ENERGY_HARTREE)
        if mode in ("success_freq", "ts_candidate"):
            assert len(result.frequencies_cm) == len(fake_g16.REAL_FREQUENCIES)
        else:
            assert result.frequencies_cm == ()
        if mode != "abnormal":
            assert (tmp_path / "s_opt_item0.chk").exists()

    @pytest.mark.parametrize("mode", SUCCESS_MODES)
    def test_orca_modes(self, tmp_path: Path, mode: str) -> None:
        adapter = OrcaProgramAdapter()
        materialized = adapter.materialize_native_input(orca_inputs())
        (tmp_path / materialized.main_input_name).write_text(
            materialized.files[0].content, encoding="utf-8"
        )
        exit_code = run_fake(FAKE_ORCA, tmp_path, materialized.main_input_name, mode)
        assert exit_code == (2 if mode == "abnormal" else 0)
        result = adapter.parse_native_result(
            work_dir=str(tmp_path),
            log_file_name="s_opt_item0.out",
            materialized=materialized,
        )
        assert result.terminated_normally == (mode != "abnormal")
        if mode in ("success_sp", "missing_geometry"):
            assert result.geometry_output is GeometryOutput.NONE
            assert not (tmp_path / "s_opt_item0.xyz").exists()
        else:
            assert result.geometry_output is GeometryOutput.PRODUCED
            assert (tmp_path / "s_opt_item0.xyz").exists()
        if mode == "abnormal":
            assert result.energy is None
        else:
            assert result.energy == pytest.approx(fake_orca.ENERGY_HARTREE)
            assert (tmp_path / "s_opt_item0.gbw").exists()
        if mode in ("success_freq", "ts_candidate"):
            assert len(result.frequencies_cm) == len(fake_orca.REAL_FREQUENCIES)
        else:
            assert result.frequencies_cm == ()


class TestLegacyParserCrossCheck:
    """Fake logs also satisfy the legacy policy parsers they were modeled on."""

    def test_gaussian_legacy_parse_output(self, tmp_path: Path) -> None:
        from confflow.calc.policies.gaussian import GAUSSIAN_POLICY

        log_text = fake_g16.render_log_text("success_freq", WATER_ATOMS, WATER_COORDS)
        log_path = tmp_path / "job.log"
        log_path.write_text(log_text, encoding="utf-8")
        parsed = GAUSSIAN_POLICY.parse_output(str(log_path), {"itask": "opt_freq"})
        assert parsed["e_low"] == pytest.approx(fake_g16.ENERGY_HARTREE)
        assert parsed["g_low"] == pytest.approx(fake_g16.gibbs_energy())
        assert parsed["num_imag_freqs"] == 0
        assert parsed["final_coords"] is not None and len(parsed["final_coords"]) == 3
        assert GAUSSIAN_POLICY.check_termination(str(log_path)) is True

    def test_orca_legacy_parse_output(self, tmp_path: Path) -> None:
        from confflow.calc.policies.orca import ORCA_POLICY

        log_text = fake_orca.render_log_text("success_freq", WATER_ATOMS, WATER_COORDS)
        log_path = tmp_path / "job.out"
        log_path.write_text(log_text, encoding="utf-8")
        parsed = ORCA_POLICY.parse_output(str(log_path), {"itask": "opt_freq"})
        assert parsed["g_low"] == pytest.approx(fake_orca.gibbs_energy())
        assert parsed["g_corr"] == pytest.approx(fake_orca.GIBBS_CORRECTION)
        assert parsed["num_imag_freqs"] == 0
        assert ORCA_POLICY.check_termination(str(log_path)) is True
