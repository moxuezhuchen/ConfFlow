#!/usr/bin/env python3

"""V4-5 GOAT/ensemble capability: block rendering and member parsing.

All multi-line log fixtures below speak real ORCA 6.1 GOAT grammar — the
``# Final ensemble info #`` table, the ``Lowest energy conformer ... Eh``
line, and multi-structure ``.finalensemble.xyz`` files whose comments read
``<energy> converged=<bool>`` — verified line-for-line against an
installed-binary butane/HF-3c GOAT run.  Fixtures are synthetic in content
but grammatical in form; no test shells out to ORCA.  The seed tests prove
the single-authority chain end to end: the typed step seed is required
workflow identity (digest/envelope), and the program adapter renders the
native boolean ``RANDOMSEED`` switch deterministically as ``false`` —
ORCA 6.1 defines no numeric stream-selection semantics for this key, so
distinct step seeds share native bytes by design and differ only in
digest/envelope identity.
"""

from __future__ import annotations

import pytest

from confflow.programs.orca import ensemble_parse as eparse
from confflow.programs.orca import goat

ATOMS = ("O", "H", "H")


def _real_log(lowest: float, rel_kcals: tuple[float, ...]) -> str:
    """Build a real-grammar final-ensemble table plus lowest-energy line."""
    lines = [
        "         # Final ensemble info #",
        "         Conformer     Energy     Degen.   % total   % cumul.",
        "                       (kcal/mol)",
        "         ------------------------------------------------------",
    ]
    for index, rel in enumerate(rel_kcals):
        lines.append(f"                 {index}     {rel:.3f}         1      50.00      50.00")
    lines.append("")
    lines.append(f"         Lowest energy conformer    : {lowest:.6f} Eh")
    lines.append("         Writing final ensemble to job.finalensemble.xyz")
    lines.append("****ORCA TERMINATED NORMALLY****")
    return "\n".join(lines) + "\n"


def _real_xyz(members: tuple[tuple[float, tuple], ...]) -> str:
    """Build a real-grammar multi-structure ensemble XYZ document.

    Each member is ``(comment_energy_or_None, ((symbol, x, y, z), ...))``.
    """
    blocks = []
    for energy, rows in members:
        comment = f"{energy:.6f} converged=true" if energy is not None else "no energy here"
        body = "\n".join(f"{symbol}  {x:.6f}  {y:.6f}  {z:.6f}" for symbol, x, y, z in rows)
        blocks.append(f"{len(rows)}\n{comment}\n{body}")
    return "\n".join(blocks) + "\n"


def _water_rows(shift: float = 0.0) -> tuple[tuple[str, float, float, float], ...]:
    """Water coordinates with a deterministic x-shift."""
    return tuple(
        (symbol, x + shift, y, z)
        for symbol, x, y, z in (
            ("O", 0.0, 0.0, 0.0),
            ("H", 0.757, 0.586, 0.0),
            ("H", -0.757, 0.586, 0.0),
        )
    )


def _two_member_pair() -> tuple[str, str]:
    """Real-grammar log plus XYZ for two conformers."""
    lowest = -76.41
    rel = (0.0, (-76.40 - lowest) * 627.5094740631)
    return (
        _real_log(lowest, rel),
        _real_xyz(((-76.41, _water_rows(0.0)), (-76.40, _water_rows(0.03)))),
    )


class TestRenderGoatBlocks:
    """``%goat`` rendering goldens and fail-closed rejections."""

    def test_golden_sorted_keys(self) -> None:
        text = goat.render_goat_blocks(
            {
                "goat": {
                    "RANDOMSEED": False,
                    "MaxIter": 50,
                }
            }
        )
        assert text == ("%goat\n" "  MaxIter 50\n" "  RANDOMSEED false\n" "end\n")
        assert goat.render_goat_blocks({"goat": {"RANDOMSEED": True}}) == (
            "%goat\n  RANDOMSEED true\nend\n"
        )

    def test_golden_single_key(self) -> None:
        assert goat.render_goat_blocks({"goat": {"MaxIter": 3}}) == ("%goat\n  MaxIter 3\nend\n")

    def test_empty_mapping_renders_bare_block(self) -> None:
        assert goat.render_goat_blocks({"goat": {}}) == "%goat\nend\n"

    def test_key_order_deterministic(self) -> None:
        first = goat.render_goat_blocks({"goat": {"RANDOMSEED": False, "MaxIter": 2}})
        second = goat.render_goat_blocks({"goat": {"MaxIter": 2, "RANDOMSEED": False}})
        assert first == second

    def test_missing_mapping_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"GOAT requires native\['goat'\]"):
            goat.render_goat_blocks({})
        with pytest.raises(ValueError, match=r"GOAT requires native\['goat'\]"):
            goat.render_goat_blocks({"goat": None})
        with pytest.raises(ValueError, match=r"GOAT requires native\['goat'\]"):
            goat.render_goat_blocks({"goat": [("MaxIter", 3)]})

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"NConformers": 5}})
        try:
            goat.render_goat_blocks({"goat": {"MaxIter": 5, "Bogus": 1}})
        except ValueError as exc:
            assert "native_input_error" in str(exc)
            assert "Bogus" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")

    def test_bad_types_rejected(self) -> None:
        for bad in ("50", 5.5, True, None):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"MaxIter": bad}})
        # RANDOMSEED is a boolean switch: numbers parse on the native
        # binary but carry no documented seed semantics, so only actual
        # booleans are accepted (stricter than native, never looser).
        for bad in ("false", 7.5, 7, 0, -3, None):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"RANDOMSEED": bad}})

    def test_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"MaxIter": 0}})
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"MaxIter": -2}})

    def test_allowlist_contents(self) -> None:
        assert goat.GOAT_BLOCK_KEYS == frozenset({"MaxIter", "RANDOMSEED"})

    def test_invented_seed_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"Seed": 7}})

    def test_invented_conformer_keys_rejected(self) -> None:
        # ``MaxConformers`` and ``EnergyWindow`` are not native ORCA 6.1
        # vocabulary: the installed binary rejects both with
        # "Unknown identifier", so rendering refuses them here.
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"MaxConformers": 5}})
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"EnergyWindow": 5.0}})


class TestValidateGoatInputs:
    """GOAT structure shape gates."""

    def test_valid_inputs_return_none(self) -> None:
        goat.validate_goat_inputs(["O", "H"], [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)], 0, 1)

    def test_charge_and_multiplicity_unchecked(self) -> None:
        goat.validate_goat_inputs(["O"], [(0.0, 0.0, 0.0)], None, None)
        goat.validate_goat_inputs(["O"], [(0.0, 0.0, 0.0)], "x", "y")

    def test_empty_structure_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty structure"):
            goat.validate_goat_inputs([], [], 0, 1)

    def test_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="count"):
            goat.validate_goat_inputs(["O", "H"], [(0.0, 0.0, 0.0)], 0, 1)

    def test_bad_coordinates_rejected(self) -> None:
        with pytest.raises(ValueError, match="triples"):
            goat.validate_goat_inputs(["O"], [(0.0, 0.0)], 0, 1)
        with pytest.raises(ValueError, match="finite"):
            goat.validate_goat_inputs(["O"], [(float("nan"), 0.0, 0.0)], 0, 1)
        with pytest.raises(ValueError, match="finite"):
            goat.validate_goat_inputs(["O"], [(True, 0.0, 0.0)], 0, 1)


class TestGoatArtifactNames:
    """Deterministic GOAT artifact file names."""

    def test_names(self) -> None:
        assert goat.goat_artifact_names("s_opt_s0") == {
            "trajectory_xyz": "s_opt_s0.goat.xyz",
            "log": "s_opt_s0.goat.out",
        }

    def test_deterministic(self) -> None:
        assert goat.goat_artifact_names("job") == goat.goat_artifact_names("job")

    def test_no_directories(self) -> None:
        names = goat.goat_artifact_names("job")
        assert all("/" not in value and "\\" not in value for value in names.values())

    def test_bad_job_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.goat_artifact_names("")
        with pytest.raises(ValueError, match="no directories"):
            goat.goat_artifact_names("a/b")
        with pytest.raises(ValueError, match="no directories"):
            goat.goat_artifact_names("..")


class TestParseGoatMembers:
    """Real-format member parsing over grammatical fixtures."""

    def test_multi_member(self) -> None:
        log, xyz = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        assert len(members) == 2
        assert members[0].member_index == 0
        assert members[1].member_index == 1
        assert members[0].energy_hartree == pytest.approx(-76.41)
        assert members[1].energy_hartree == pytest.approx(-76.40)
        assert members[0].geometry.atoms == ATOMS
        assert members[0].geometry.coordinates[0] == pytest.approx((0.0, 0.0, 0.0))

    def test_count_mismatch_rejected(self) -> None:
        log, xyz = _two_member_pair()
        lines = xyz.split("\n")
        single = "\n".join(lines[:5]) + "\n"
        with pytest.raises(ValueError, match="XYZ holds"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=single, atoms=ATOMS)

    def test_broken_index_sequence_rejected(self) -> None:
        log = _real_log(-76.41, (0.0, 0.627)).replace(
            "                 1     ", "                 5     ", 1
        )
        _, xyz = _two_member_pair()
        with pytest.raises(ValueError, match="0..n-1"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_missing_lowest_line_rejected(self) -> None:
        log, xyz = _two_member_pair()
        log = "\n".join(line for line in log.split("\n") if "Lowest energy" not in line)
        with pytest.raises(ValueError, match="lowest-energy"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_energy_disagreement_rejected(self) -> None:
        log, xyz = _two_member_pair()
        xyz = xyz.replace("-76.400000 converged=true", "-75.000000 converged=true", 1)
        with pytest.raises(ValueError, match="disagrees"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_missing_comment_energy_rejected(self) -> None:
        xyz = _real_xyz(((None, _water_rows(0.0)),))
        log = _real_log(-76.41, (0.0,))
        with pytest.raises(ValueError, match="no energy"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_atom_mismatch_rejected(self) -> None:
        log, xyz = _two_member_pair()
        with pytest.raises(ValueError, match="symbols"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=("O", "H"))

    def test_identical_geometries_preserved(self) -> None:
        log = _real_log(-76.41, (0.0, 0.0))
        xyz = _real_xyz(((-76.41, _water_rows(0.0)), (-76.41, _water_rows(0.0))))
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        assert len(members) == 2
        assert [m.member_index for m in members] == [0, 1]
        assert members[0].geometry.coordinates == members[1].geometry.coordinates

    def test_non_string_rejected(self) -> None:
        with pytest.raises(TypeError):
            eparse.parse_goat_ensemble(None, ensemble_xyz_text="x", atoms=ATOMS)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            eparse.parse_goat_ensemble("x", ensemble_xyz_text=None, atoms=ATOMS)  # type: ignore[arg-type]

    def test_empty_table_parses_empty(self) -> None:
        log = (
            "         # Final ensemble info #\n"
            "         Conformer     Energy     Degen.   % total   % cumul.\n"
            "                       (kcal/mol)\n"
            "         ------------------------------------------------------\n"
            "         Lowest energy conformer    : -76.410000 Eh\n"
        )
        assert eparse.parse_goat_ensemble(log, ensemble_xyz_text="", atoms=ATOMS) == ()


class TestOrderingHelpers:
    """Energy-table and canonical-ordering conveniences."""

    def test_energy_table(self) -> None:
        _, xyz = _two_member_pair()
        log, _ = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        table = eparse.ensemble_energy_table(members)
        assert table[0] == pytest.approx(-76.41)
        assert table[1] == pytest.approx(-76.40)

    def test_energy_table_duplicate_rejected(self) -> None:
        log, xyz = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        with pytest.raises(ValueError, match="duplicate"):
            eparse.ensemble_energy_table(tuple(members) + tuple(members))

    def test_member_ordering_sorted(self) -> None:
        log, xyz = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        assert eparse.member_ordering(members) == (0, 1)
        assert eparse.member_ordering(()) == ()


class TestGoatTrajectoryFacts:
    """Best-effort trajectory facts over real-grammar fixtures."""

    def test_facts(self) -> None:
        log, _ = _two_member_pair()
        facts = eparse.goat_trajectory_facts(log)
        assert facts["member_count"] == 2
        assert facts["member_indices"] == [0, 1]
        assert facts["energy_min_hartree"] == pytest.approx(-76.41)
        assert facts["energy_max_hartree"] == pytest.approx(-76.40)
        assert facts["truncated"] is False

    def test_no_table_tolerant(self) -> None:
        facts = eparse.goat_trajectory_facts("")
        assert facts["member_count"] == 0
        assert facts["member_indices"] == []
        assert facts["truncated"] is True
        garbage = eparse.goat_trajectory_facts("total garbage ((( \n")
        assert garbage["member_count"] == 0


class TestGoatSeedSingleAuthority:
    """One seed end to end: step seed -> digest -> RANDOMSEED false -> envelope.

    The typed step seed is the single stochastic authority.  It folds
    into the scientific digest (so retry/resume and remote reuse never
    mix seeds), the program adapter requires it and renders the native
    boolean ``RANDOMSEED`` switch deterministically as ``false`` —
    ORCA 6.1 defines no numeric stream selection, so distinct step
    seeds share native bytes by design and differ only in
    digest/envelope identity.  Recovery preserves the seed across
    attempts, and the remote handoff carries it with the work-item
    digest.  Any user-supplied native ``RANDOMSEED`` (even matching,
    even boolean) is a second authority and fails closed; the
    invented ``Seed`` key never existed natively and is rejected as
    unknown vocabulary.
    """

    def _goat_inputs(self, seed: int | None, native_goat: dict | None = None):
        from confflow.domain._immutable import FrozenDict
        from confflow.domain.resources import ResourceRequest
        from confflow.domain.structure import StructureRecord
        from confflow.execution.native import ResolvedCalculationInputs

        structure = StructureRecord(
            id="s",
            atoms=ATOMS,
            coordinates=((0.0, 0.0, 0.0), (0.757, 0.586, 0.0), (-0.757, 0.586, 0.0)),
            charge=0,
            multiplicity=1,
        )
        user_goat = {"MaxIter": 5} if native_goat is None else dict(native_goat)
        return ResolvedCalculationInputs(
            structure=structure,
            charge=0,
            multiplicity=1,
            freeze=(),
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2 * 1024**3),
            native=FrozenDict({"keyword": "HF-3c GOAT", "goat": user_goat}),
            checkpoints=(),
            extra_structures=FrozenDict({}),
            step_id="s",
            work_item_id="w",
            logical_key="job",
            seed=seed,
        )

    def test_step_seed_renders_deterministic_randomseed(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        materialized = OrcaProgramAdapter().materialize_native_input(self._goat_inputs(7))
        content = next(entry.content for entry in materialized.files if entry.name == "job.inp")
        # The integer step seed is workflow identity, never a native
        # stream selector: the adapter always renders the deterministic
        # boolean flag.
        assert "\n  RANDOMSEED false\n" in content
        assert "RANDOMSEED 7" not in content

    def test_same_seed_renders_byte_identical_input(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        first = OrcaProgramAdapter().materialize_native_input(self._goat_inputs(7))
        second = OrcaProgramAdapter().materialize_native_input(self._goat_inputs(7))
        assert [entry.content for entry in first.files] == [entry.content for entry in second.files]

    def test_distinct_seeds_share_deterministic_native_input(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter
        from confflow.workflow.v4.document import ScientificDefinition

        # ORCA 6.1 exposes no per-stream selection, so distinct step
        # seeds share native bytes by design; identity separation lives
        # in the digest, never in the .inp text.
        first = OrcaProgramAdapter().materialize_native_input(self._goat_inputs(7))
        second = OrcaProgramAdapter().materialize_native_input(self._goat_inputs(8))
        assert [entry.content for entry in first.files] == [entry.content for entry in second.files]
        assert (
            ScientificDefinition(seed=7).to_payload() != ScientificDefinition(seed=8).to_payload()
        )

    def test_missing_seed_fails_closed(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        with pytest.raises(ValueError, match="native_input_error.*seed"):
            OrcaProgramAdapter().materialize_native_input(self._goat_inputs(None))

    def test_native_randomseed_second_authority_rejected(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        with pytest.raises(ValueError, match="native_input_error.*second seed"):
            OrcaProgramAdapter().materialize_native_input(
                self._goat_inputs(7, native_goat={"MaxIter": 5, "RANDOMSEED": 7})
            )
        # Even a boolean matching the rendered flag is a second
        # authority: only the step seed may drive the switch.
        with pytest.raises(ValueError, match="native_input_error.*second seed"):
            OrcaProgramAdapter().materialize_native_input(
                self._goat_inputs(7, native_goat={"MaxIter": 5, "RANDOMSEED": False})
            )

    def test_seed_folds_into_scientific_payload(self) -> None:
        from confflow.workflow.v4.document import ScientificDefinition

        assert ScientificDefinition(seed=7).to_payload()["seed"] == 7
        assert (
            ScientificDefinition(seed=7).to_payload() != ScientificDefinition(seed=8).to_payload()
        )

    def test_retry_preserves_seed(self) -> None:
        from confflow.execution.recovery import RecoveryContext
        from confflow.execution.recovery_standard import TsRescueScanPolicy
        from confflow.programs.gaussian.adapter import GaussianProgramAdapter

        context = RecoveryContext(
            profile_name="standard",
            work_item_id="w",
            step_id="s",
            logical_key="job",
            inputs=self._goat_inputs(7),
            failed_native_result=None,
        )
        # Rescue input rendering is adapter-owned: seed preservation flows
        # through the bound program adapter, and an unbound policy declines.
        assert (
            TsRescueScanPolicy()._modified_inputs(
                context,
                ((0.0, 0.0, 0.0), (0.757, 0.586, 0.0), (-0.757, 0.586, 0.0)),
                "HF-3c GOAT",
            )
            is None
        )
        rebuilt = TsRescueScanPolicy(adapter=GaussianProgramAdapter())._modified_inputs(
            context,
            ((0.0, 0.0, 0.0), (0.757, 0.586, 0.0), (-0.757, 0.586, 0.0)),
            "HF-3c GOAT",
        )
        assert rebuilt is not None
        assert rebuilt.seed == 7

    def test_remote_envelope_carries_seed_and_digest(self) -> None:
        import hashlib

        from confflow.domain.resources import ResourceRequest
        from confflow.remote.transport import build_execution_definition

        digest = "sha256:" + hashlib.sha256(b"goat-seed-7").hexdigest()

        definition = build_execution_definition(
            executor="native",
            program="orca",
            native={"keyword": "HF-3c GOAT", "goat": {"MaxIter": 5}},
            seed=7,
            transform=None,
            execution_adapter="standard",
            result_profile="ensemble",
            checks=(),
            check_params={},
            recovery="none",
            recovery_params={},
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2 * 1024**3),
            handoff_executable=None,
            handoff_env={},
            handoff_walltime_seconds=None,
            charge=0,
            multiplicity=1,
            freeze=None,
            step_semantic_digest=digest,
            contract_versions={},
        )
        assert definition.seed == 7
        assert "goat" in definition.native
        assert "RANDOMSEED" not in definition.native["goat"]
