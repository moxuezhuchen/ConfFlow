#!/usr/bin/env python3

"""Focused tests for the Phase 6 checkpoint/Hessian reuse helper.

Covers the wired edge shape (``artifacts``/``checkpoint`` role selector,
cardinality ``one``, ``by_subject`` pairing), the explicit source
``write_chk`` record, the ``readfc``/``rcfc`` route edits (including
idempotent accepts and ``CalcFC``/``CalcAll`` opposition), the
charge/spin/method compatibility refusals with the ``allow_method_change``
override, compiler rejection of the wired document (cycles and all other
semantic rules), native rendering of ``%Chk``/``%OldChk`` through the
current Gaussian adapter, and the ``artifact_flow`` consumption guard.
"""

from __future__ import annotations

import copy

import pytest

from confflow.domain.errors import DomainError
from confflow.producer.checkpoints import (
    CHECKPOINT_REUSE_VERSION,
    wire_checkpoint_reuse,
)
from confflow.workflow.v4.artifact_flow import (
    ARTIFACT_SUBJECT_MISSING,
    ArtifactFlowError,
    verify_binding_cardinality,
)
from tests.v4._builders import calc_step, compile_doc, structure, v4_doc

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _doc(
    source_keyword: str = "B3LYP/6-31G* freq",
    target_keyword: str = "B3LYP/6-31G* opt",
    *,
    source_native: dict | None = None,
    target_native: dict | None = None,
    source_program: str = "g16",
    source_adapter: str = "standard",
    source_overrides: dict | None = None,
    target_overrides: dict | None = None,
    target_bindings: dict | None = None,
) -> dict:
    source_block = {"keyword": source_keyword}
    if source_native:
        source_block.update(source_native)
    target_block = {"keyword": target_keyword}
    if target_native:
        target_block.update(target_native)
    bindings = {"structure": {"source": {"step": "s_freq", "port": "structures"}}}
    if target_bindings:
        bindings.update(target_bindings)
    return v4_doc(
        [
            calc_step(
                "s_freq",
                bindings={"structure": {"source": {"run": "structures"}}},
                program=source_program,
                adapter=source_adapter,
                native=source_block,
                overrides=source_overrides,
            ),
            calc_step(
                "s_opt",
                bindings=bindings,
                native=target_block,
                overrides=target_overrides,
            ),
        ],
        inputs=STRUCTURE_INPUTS,
    )


def _edge(document: dict):
    compiled = compile_doc(document)
    assert compiled.ok, [(d.code, d.message) for d in compiled.diagnostics]
    assert compiled.plan is not None and compiled.plan.graph is not None
    edges = [
        edge
        for edge in compiled.plan.graph.edges
        if edge.target_step_id == "s_opt" and edge.target_port.name == "checkpoint"
    ]
    assert len(edges) == 1
    return edges[0]


class TestWiredEdge:
    """The wired edge is digest-covered and promises consumption."""

    def test_checkpoint_binding_shape(self) -> None:
        document = _doc()
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        binding = wired["steps"][1]["bindings"]["checkpoint"]
        assert binding == {
            "source": {
                "step": "s_freq",
                "port": "artifacts",
                "select": {"role": "checkpoint"},
            },
            "cardinality": "one",
        }

    def test_edge_resolves_one_by_subject(self) -> None:
        edge = _edge(wire_checkpoint_reuse(_doc(), "s_opt", "s_freq"))
        assert edge.cardinality.value == "one"
        assert edge.pairing.value == "by_subject"
        assert edge.source.selector.role == "checkpoint"

    def test_source_write_chk_made_explicit(self) -> None:
        wired = wire_checkpoint_reuse(_doc(), "s_opt", "s_freq")
        assert wired["steps"][0]["calculation"]["native"]["write_chk"] is True

    def test_checkpoint_mode_leaves_target_route_verbatim(self) -> None:
        wired = wire_checkpoint_reuse(_doc(), "s_opt", "s_freq")
        assert wired["steps"][1]["calculation"]["native"] == {"keyword": "B3LYP/6-31G* opt"}

    def test_input_document_never_mutated(self) -> None:
        document = _doc()
        snapshot = copy.deepcopy(document)
        wire_checkpoint_reuse(document, "s_opt", "s_freq")
        assert document == snapshot

    def test_provenance_annotation_recorded(self) -> None:
        wired = wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", mode="readfc")
        assert wired["steps"][1]["annotations"]["confflow.checkpoint_reuse"] == {
            "source_step": "s_freq",
            "mode": "readfc",
            "allow_method_change": False,
            "version": CHECKPOINT_REUSE_VERSION,
        }

    def test_binding_moves_definition_digest(self) -> None:
        document = _doc()
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        base_digest = compile_doc(document).plan.definition_digest
        wired_digest = compile_doc(wired).plan.definition_digest
        assert base_digest != wired_digest


class TestRouteModes:
    """``readfc`` edits Opt routes; ``rcfc`` edits IRC routes; nothing else."""

    def test_readfc_appends_into_opt_group(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(target_keyword="B3LYP/6-31G* opt(tight)"), "s_opt", "s_freq", mode="readfc"
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == (
            "B3LYP/6-31G* opt(tight,ReadFC)"
        )

    def test_readfc_bare_opt(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(target_keyword="B3LYP/6-31G* opt"), "s_opt", "s_freq", mode="readfc"
        )
        assert "ReadFC" in wired["steps"][1]["calculation"]["native"]["keyword"]

    def test_readfc_accepts_existing_option_idempotently(self) -> None:
        keyword = "B3LYP/6-31G* opt(ReadFC,tight)"
        wired = wire_checkpoint_reuse(
            _doc(target_keyword=keyword), "s_opt", "s_freq", mode="readfc"
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == keyword

    def test_readfc_rejects_opposed_force_constant_options(self) -> None:
        for keyword in ("B3LYP/6-31G* opt(CalcFC)", "B3LYP/6-31G* opt(CalcAll)"):
            with pytest.raises(DomainError, match="opposed"):
                wire_checkpoint_reuse(
                    _doc(target_keyword=keyword), "s_opt", "s_freq", mode="readfc"
                )

    def test_readfc_needs_an_opt_route(self) -> None:
        with pytest.raises(DomainError, match="no Opt route item"):
            wire_checkpoint_reuse(
                _doc(target_keyword="B3LYP/6-31G*"), "s_opt", "s_freq", mode="readfc"
            )

    def test_rcfc_votes_both_directions(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* IRC",
                target_keyword="B3LYP/6-31G* IRC(MaxPoints=20)",
            ),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == (
            "B3LYP/6-31G* IRC(MaxPoints=20,RCFC)"
        )

    def test_rcfc_bare_irc(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* IRC",
                target_keyword="B3LYP/6-31G* IRC",
            ),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == ("B3LYP/6-31G* IRC(RCFC)")

    def test_rcfc_accepts_existing_vote_idempotently(self) -> None:
        keyword = "B3LYP/6-31G* IRC(RCFC,MaxPoints=20)"
        wired = wire_checkpoint_reuse(
            _doc(source_keyword="B3LYP/6-31G* IRC", target_keyword=keyword),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == keyword

    def test_rcfc_rejects_explicit_direction_votes(self) -> None:
        with pytest.raises(DomainError, match="explicit IRC direction"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* IRC",
                    target_keyword="B3LYP/6-31G* IRC(Forward)",
                ),
                "s_opt",
                "s_freq",
                mode="rcfc",
            )

    def test_rcfc_rejects_opposed_force_constant_options(self) -> None:
        with pytest.raises(DomainError, match="opposed"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* IRC",
                    target_keyword="B3LYP/6-31G* IRC(CalcFC)",
                ),
                "s_opt",
                "s_freq",
                mode="rcfc",
            )

    def test_rcfc_needs_an_irc_route(self) -> None:
        with pytest.raises(DomainError, match="invalid IRC route"):
            wire_checkpoint_reuse(
                _doc(target_keyword="B3LYP/6-31G* opt"),
                "s_opt",
                "s_freq",
                mode="rcfc",
            )

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(DomainError, match="unknown reuse mode"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", mode="guess")


class TestCompatibilityRefusals:
    """Known charge/spin/method mismatches refuse without guessing."""

    def test_charge_mismatch_refused(self) -> None:
        with pytest.raises(DomainError, match="charge"):
            wire_checkpoint_reuse(
                _doc(source_overrides={"charge": 0}, target_overrides={"charge": 1}),
                "s_opt",
                "s_freq",
            )

    def test_spin_mismatch_refused(self) -> None:
        with pytest.raises(DomainError, match="multiplicity"):
            wire_checkpoint_reuse(
                _doc(
                    source_overrides={"multiplicity": 1},
                    target_overrides={"multiplicity": 2},
                ),
                "s_opt",
                "s_freq",
            )

    def test_differing_keyword_method_refused_by_default(self) -> None:
        with pytest.raises(DomainError, match="differing method"):
            wire_checkpoint_reuse(_doc(target_keyword="M06-2X/def2-TZVP opt"), "s_opt", "s_freq")

    def test_differing_extra_sections_refused_by_default(self) -> None:
        with pytest.raises(DomainError, match="native scientific payload"):
            wire_checkpoint_reuse(
                _doc(source_native={"extra_sections": "gen 5d 7f"}),
                "s_opt",
                "s_freq",
            )

    def test_equal_payload_with_managed_edits_accepted(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* freq",
                target_keyword="B3LYP/6-31G* opt",
            ),
            "s_opt",
            "s_freq",
            mode="readfc",
        )
        assert "ReadFC" in wired["steps"][1]["calculation"]["native"]["keyword"]

    def test_method_override_accepted_and_recorded(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(target_keyword="M06-2X/def2-TZVP opt"),
            "s_opt",
            "s_freq",
            allow_method_change=True,
        )
        annotation = wired["steps"][1]["annotations"]["confflow.checkpoint_reuse"]
        assert annotation["allow_method_change"] is True
        assert annotation["mode"] == "checkpoint"

    def test_unsupported_method_family_refused(self) -> None:
        with pytest.raises(DomainError, match="whose final energy"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="MP2/6-31G* freq",
                    target_keyword="MP2/6-31G* opt",
                ),
                "s_opt",
                "s_freq",
            )


class TestSpJobType:
    """Explicit ``SP`` is a job type, not a method change (SP defaults)."""

    def test_opt_to_sp_plain_checkpoint_succeeds(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* Opt",
                target_keyword="B3LYP/6-31G* SP",
            ),
            "s_opt",
            "s_freq",
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == "B3LYP/6-31G* SP"

    def test_sp_equivalent_to_implicit_single_point(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G*",
                target_keyword="B3LYP/6-31G* SP",
            ),
            "s_opt",
            "s_freq",
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == "B3LYP/6-31G* SP"
        wired_reverse = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* SP",
                target_keyword="B3LYP/6-31G*",
            ),
            "s_opt",
            "s_freq",
        )
        assert wired_reverse["steps"][1]["calculation"]["native"]["keyword"] == "B3LYP/6-31G*"

    def test_different_functional_or_basis_with_sp_still_refused(self) -> None:
        with pytest.raises(DomainError, match="differing method"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* Opt",
                    target_keyword="M06-2X/6-31G* SP",
                ),
                "s_opt",
                "s_freq",
            )
        with pytest.raises(DomainError, match="differing method"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* Opt",
                    target_keyword="B3LYP/def2-TZVP SP",
                ),
                "s_opt",
                "s_freq",
            )

    def test_sp_letters_inside_scientific_tokens_untouched(self) -> None:
        with pytest.raises(DomainError, match="differing method"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* Opt",
                    target_keyword="B3LYP/6-31G* CSP",
                ),
                "s_opt",
                "s_freq",
            )
        with pytest.raises(DomainError, match="differing method"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* Opt",
                    target_keyword="B3LYP/SP SP",
                ),
                "s_opt",
                "s_freq",
            )


class TestEndpointRefusals:
    """Both ends must be Gaussian standard-adapter calculations."""

    def test_orca_refused(self) -> None:
        with pytest.raises(DomainError, match="no checkpoint input vocabulary"):
            wire_checkpoint_reuse(_doc(source_program="orca"), "s_opt", "s_freq")

    def test_named_adapter_refused(self) -> None:
        with pytest.raises(DomainError, match="'standard' adapter"):
            wire_checkpoint_reuse(_doc(source_adapter="named_structures"), "s_opt", "s_freq")

    def test_qst_route_refused(self) -> None:
        with pytest.raises(DomainError, match="QST"):
            wire_checkpoint_reuse(_doc(source_keyword="B3LYP/6-31G* QST2 opt"), "s_opt", "s_freq")

    def test_disabled_write_chk_source_refused(self) -> None:
        with pytest.raises(DomainError, match="write_chk"):
            wire_checkpoint_reuse(_doc(source_native={"write_chk": False}), "s_opt", "s_freq")

    def test_user_managed_link0_refused(self) -> None:
        with pytest.raises(DomainError, match="link0"):
            wire_checkpoint_reuse(
                _doc(target_native={"link0": ["%Chk=hand.chk"]}), "s_opt", "s_freq"
            )

    def test_unknown_steps_refused(self) -> None:
        with pytest.raises(DomainError, match="unknown target step"):
            wire_checkpoint_reuse(_doc(), "s_missing", "s_freq")
        with pytest.raises(DomainError, match="unknown source step"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_missing")

    def test_self_link_refused(self) -> None:
        with pytest.raises(DomainError, match="own checkpoint"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_opt")

    def test_existing_binding_never_overwritten(self) -> None:
        with pytest.raises(DomainError, match="already declares"):
            wire_checkpoint_reuse(
                _doc(
                    target_bindings={
                        "checkpoint": {"source": {"step": "s_freq", "port": "artifacts"}}
                    }
                ),
                "s_opt",
                "s_freq",
            )

    def test_compiler_rejects_cycles(self) -> None:
        # Explicit matching overrides prove charge/spin so the compiler owns
        # the cycle refusal (charge proof must not mask topology errors).
        document = v4_doc(
            [
                calc_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    overrides={"charge": 1, "multiplicity": 2},
                ),
                calc_step(
                    "s_b",
                    bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}},
                    overrides={"charge": 1, "multiplicity": 2},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        with pytest.raises(DomainError, match="compiler"):
            wire_checkpoint_reuse(document, "s_a", "s_b")


class TestNativeRendering:
    """The current adapter renders the promised checkpoint consumption."""

    def test_oldchk_and_chk_rendered(self) -> None:
        from confflow.domain import FrozenDict, ResourceRequest
        from confflow.execution.native import ResolvedCalculationInputs, StagedArtifact
        from confflow.programs.gaussian.adapter import GaussianProgramAdapter

        record = structure("s0")
        staged = StagedArtifact(
            local_name="staged/input-checkpoint-0.chk",
            role="checkpoint",
            subject_structure_id="s0",
            checksum="sha256:" + "a" * 64,
        )
        materialized = GaussianProgramAdapter().materialize_native_input(
            ResolvedCalculationInputs(
                structure=record,
                charge=0,
                multiplicity=1,
                freeze=None,
                resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=2 * 1024**3),
                native=FrozenDict({"keyword": "B3LYP/6-31G* opt", "write_chk": True}),
                checkpoints=(staged,),
                logical_key="s_opt:s0",
            )
        )
        content = materialized.files[0].content
        assert "%Chk=" in content
        assert "%OldChk=staged/input-checkpoint-0.chk" in content

    def test_qst_with_checkpoint_refused_at_render(self) -> None:
        from confflow.domain import FrozenDict, ResourceRequest, StructureSet
        from confflow.execution.native import ResolvedCalculationInputs, StagedArtifact
        from confflow.programs.gaussian.adapter import GaussianProgramAdapter

        members = StructureSet.of(structure("s0"), structure("s1"))
        staged = StagedArtifact(local_name="staged/input-checkpoint-0.chk", role="checkpoint")
        # R2.3d (G18): QST slots are retired, so the retired-slots gate fires
        # before the old checkpoint-vocabulary gate; still fail-closed.
        with pytest.raises(ValueError, match="retired"):
            GaussianProgramAdapter().materialize_native_input(
                ResolvedCalculationInputs(
                    structure=members[0],
                    charge=0,
                    multiplicity=1,
                    freeze=None,
                    resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=1024**3),
                    native=FrozenDict({"keyword": "B3LYP/6-31G* QST2 opt"}),
                    checkpoints=(staged,),
                    extra_structures=FrozenDict(
                        {"reactant": (members[0],), "product": (members[1],)}
                    ),
                    logical_key="s_qst:s0",
                )
            )


class TestConsumptionGuard:
    """Cardinality ``one`` fails absent checkpoints through ``artifact_flow``."""

    def test_absent_checkpoint_fails_one_guard(self) -> None:
        from confflow.domain import ArtifactSet

        with pytest.raises(ArtifactFlowError) as caught:
            verify_binding_cardinality(
                artifacts=ArtifactSet(()),
                port_name="checkpoint",
                cardinality="one",
                subject_structure_id="s0",
                step_id="s_opt",
            )
        assert caught.value.code == ARTIFACT_SUBJECT_MISSING

    def test_optional_would_silently_pass(self) -> None:
        from confflow.domain import ArtifactSet

        verify_binding_cardinality(
            artifacts=ArtifactSet(()),
            port_name="checkpoint",
            cardinality="optional",
            subject_structure_id="s0",
            step_id="s_opt",
        )


@pytest.mark.parametrize(
    "keyword", ["B3LYP/6-31G* Opt(ReadFC,CalcFC)", "B3LYP/6-31G* Opt(ReadFC,CalcAll)"]
)
def test_existing_readfc_still_rejects_opposed_force_constants(keyword):
    with pytest.raises(DomainError, match="opposed"):
        wire_checkpoint_reuse(_doc(target_keyword=keyword), "s_opt", "s_freq", "readfc")


def test_compiled_checkpoint_edge_assembles_stages_and_renders(tmp_path):
    import hashlib

    from confflow.domain import (
        ArtifactLocator,
        ArtifactRef,
        ArtifactSet,
        FrozenDict,
        ResourceRequest,
        StructureSet,
    )
    from confflow.domain.completion import StepStatus
    from confflow.execution.native import ResolvedCalculationInputs
    from confflow.execution.registry import default_registry
    from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
    from confflow.programs.gaussian.adapter import GaussianProgramAdapter
    from confflow.workflow.v4.assembly import (
        MaterializedOutputs,
        RunInputs,
        StepOutputs,
        assemble_work_items,
    )

    wired = wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", "readfc")
    compiled = compile_doc(wired)
    assert compiled.ok and compiled.plan is not None
    plan = compiled.plan
    output_record = structure("freq_output", parent_ids=("initial",), lineage_root_id="initial")
    payload = b"checkpoint with force constants\n"
    checkpoint_file = tmp_path / "source.chk"
    checkpoint_file.write_bytes(payload)
    artifact = ArtifactRef(
        id="freq_checkpoint",
        role="checkpoint",
        locator=ArtifactLocator.run_relative("source.chk"),
        checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
        subject_structure_id=output_record.id,
        producer_step_id="s_freq",
    )
    outputs = MaterializedOutputs(
        FrozenDict(
            {
                "s_freq": StepOutputs(
                    step_id="s_freq",
                    structures=StructureSet.of(output_record),
                    artifacts=ArtifactSet.of(artifact),
                    status=StepStatus.COMPLETED,
                )
            }
        )
    )
    inputs = RunInputs(structures=FrozenDict({"structures": StructureSet.of(structure("initial"))}))
    assembled = assemble_work_items(plan, inputs, materialized=outputs)
    assert assembled.ok, [(d.code, d.message) for d in assembled.diagnostics]
    (item,) = assembled.for_step("s_opt")
    (target,) = [step for step in plan.steps if step.step_id == "s_opt"]
    adapter = GaussianProgramAdapter()
    context = ItemExecutionContext(
        step_id="s_opt",
        scientific=target.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=default_registry().resolve_profile("standard"),
        run_root=str(tmp_path),
    )
    item_dir = tmp_path / "target"
    item_dir.mkdir()
    staged = WorkItemExecutor()._stage_checkpoints(item, context, str(item_dir))
    assert len(staged) == 1
    assert (item_dir / staged[0].local_name).read_bytes() == payload
    materialized = adapter.materialize_native_input(
        ResolvedCalculationInputs(
            structure=output_record,
            charge=0,
            multiplicity=1,
            freeze=None,
            resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=2 * 1024**3),
            native=target.scientific.native,
            checkpoints=tuple(staged),
            logical_key=item.logical_key,
        )
    )
    content = materialized.files[0].content
    assert "%OldChk=staged/input-checkpoint-0.chk" in content
    assert "ReadFC" in content


def test_source_checkpoint_paths_are_owned_by_existing_renderer():
    with pytest.raises(DomainError, match="manages checkpoint paths"):
        wire_checkpoint_reuse(
            _doc(source_native={"link0": ["%Chk=user-managed.chk"]}), "s_opt", "s_freq"
        )


@pytest.mark.parametrize("keyword", ["B3LYP/6-31G* SP(custom)", "B3LYP/6-31G* SP=custom"])
def test_sp_comparison_preserves_unrecognized_attached_options(keyword):
    with pytest.raises(DomainError, match="known differing method"):
        wire_checkpoint_reuse(
            _doc(source_keyword="B3LYP/6-31G* Opt", target_keyword=keyword),
            "s_opt",
            "s_freq",
            "checkpoint",
        )


def test_sp_comparison_uses_existing_gaussian_route_marker_authority():
    wired = wire_checkpoint_reuse(
        _doc(source_keyword="#p B3LYP/6-31G* Opt", target_keyword="# SP B3LYP/6-31G*"),
        "s_opt",
        "s_freq",
        "checkpoint",
    )
    assert wired["steps"][1]["calculation"]["native"]["keyword"] == "# SP B3LYP/6-31G*"


def _no_global_doc(steps: list[dict], inputs: dict) -> dict:
    return v4_doc(steps, inputs=inputs, global_config={"scientific_defaults": {}})


class TestInheritedStateProof:
    """Bound-lineage declared-value proof for Hessian charge/spin reuse."""

    def test_source_override_target_inherit_accepts(self) -> None:
        document = _doc(source_overrides={"charge": 1, "multiplicity": 2})
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        binding = wired["steps"][1]["bindings"]["checkpoint"]
        assert binding["cardinality"] == "one"
        assert compile_doc(wired).ok

    def test_source_override_target_inherit_assembly_evidence(self) -> None:
        from confflow.domain import (
            ArtifactLocator,
            ArtifactRef,
            ArtifactSet,
            FrozenDict,
            StructureSet,
        )
        from confflow.domain.completion import StepStatus
        from confflow.execution.native import (
            GeometryOutput,
            NativeResult,
            ParsedGeometry,
            ProgramName,
            ResolvedCalculationInputs,
        )
        from confflow.execution.profile_standard import StandardResultProfile
        from confflow.execution.profiles import ProfileContext
        from confflow.programs.gaussian.adapter import GaussianProgramAdapter
        from confflow.workflow.v4.assembly import (
            MaterializedOutputs,
            RunInputs,
            StepOutputs,
            assemble_work_items,
        )
        from confflow.workflow.v4.scientific import resolve_scientific_parameters

        wired = wire_checkpoint_reuse(
            _doc(source_overrides={"charge": 1, "multiplicity": 2}), "s_opt", "s_freq"
        )
        compiled = compile_doc(wired)
        assert compiled.ok and compiled.plan is not None
        plan = compiled.plan
        (source_step,) = [step for step in plan.steps if step.step_id == "s_freq"]
        (target,) = [step for step in plan.steps if step.step_id == "s_opt"]
        initial = structure("initial", charge=0, multiplicity=1)
        source_state, _ = resolve_scientific_parameters(
            structure=initial,
            overrides=source_step.scientific.overrides,
            defaults=plan.scientific_defaults,
        )
        assert (source_state.charge, source_state.multiplicity) == (1, 2)
        profile = StandardResultProfile()
        profile_output = profile.apply(
            ProfileContext(
                work_item_id="wi:s_freq:initial",
                step_id="s_freq",
                logical_key="s_freq:initial",
                profile_name="standard",
                profile_version=profile.contract_version,
                native_result=NativeResult(
                    program=ProgramName.GAUSSIAN,
                    terminated_normally=True,
                    geometry_output=GeometryOutput.PRODUCED,
                    final_geometry=ParsedGeometry(
                        atoms=initial.atoms, coordinates=initial.coordinates
                    ),
                ),
                inputs=ResolvedCalculationInputs(
                    structure=initial,
                    charge=source_state.charge,
                    multiplicity=source_state.multiplicity,
                    freeze=None,
                    resources=source_step.resources,
                    native=source_step.scientific.native,
                ),
            )
        )
        (output_record,) = profile_output.structures
        assert (output_record.charge, output_record.multiplicity) == (1, 2)
        assert output_record.parent_ids == (initial.id,)
        artifact = ArtifactRef(
            id="freq_checkpoint",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("source.chk"),
            checksum="sha256:" + "a" * 64,
            subject_structure_id=output_record.id,
            producer_step_id="s_freq",
        )
        outputs = MaterializedOutputs(
            FrozenDict(
                {
                    "s_freq": StepOutputs(
                        step_id="s_freq",
                        structures=profile_output.structures,
                        artifacts=ArtifactSet.of(artifact),
                        status=StepStatus.COMPLETED,
                    )
                }
            )
        )
        assembled = assemble_work_items(
            plan,
            RunInputs(structures=FrozenDict({"structures": StructureSet.of(initial)})),
            materialized=outputs,
        )
        assert assembled.ok, [(d.code, d.message) for d in assembled.diagnostics]
        (item,) = assembled.for_step("s_opt")
        (record,) = item.named_inputs.structures["structure"]
        effective, _ = resolve_scientific_parameters(
            structure=record,
            overrides=target.scientific.overrides,
            defaults=plan.scientific_defaults,
        )
        assert (effective.charge, effective.multiplicity) == (1, 2)
        materialized = GaussianProgramAdapter().materialize_native_input(
            ResolvedCalculationInputs(
                structure=record,
                charge=effective.charge,
                multiplicity=effective.multiplicity,
                freeze=None,
                resources=target.resources,
                native=target.scientific.native,
            )
        )
        assert "1 2" in materialized.files[0].content.splitlines()
        assert record.atoms == initial.atoms

    def test_explicit_target_override_mismatch_rejects(self) -> None:
        document = _doc(
            source_overrides={"charge": 1, "multiplicity": 2},
            target_overrides={"charge": 0, "multiplicity": 1},
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(document, "s_opt", "s_freq")

    def test_allow_method_change_cannot_bypass_charge(self) -> None:
        document = _doc(
            source_keyword="B3LYP/6-31G* freq",
            target_keyword="M06-2X/def2-TZVP opt",
            source_overrides={"charge": 1, "multiplicity": 2},
            target_overrides={"charge": 0, "multiplicity": 1},
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(document, "s_opt", "s_freq", allow_method_change=True)

    def test_target_explicit_vs_unknown_source_refused(self) -> None:
        document = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                    overrides={"charge": 1, "multiplicity": 2},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(document, "s_opt", "s_freq")

    def test_input_declared_states_no_globals_accepted(self) -> None:
        inputs = {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 1,
                "multiplicity": 2,
            }
        }
        document = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            inputs,
        )
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_inherited_input_overrules_global(self) -> None:
        from confflow.domain import FrozenDict
        from confflow.workflow.v4.scientific import resolve_scientific_parameters

        inputs = {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 1,
                "multiplicity": 2,
            }
        }
        document = v4_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            inputs=inputs,
        )
        assert document["global"]["scientific_defaults"] == {"charge": 0, "multiplicity": 1}
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        compiled = compile_doc(wired)
        assert compiled.ok and compiled.plan is not None
        (target,) = [step for step in compiled.plan.steps if step.step_id == "s_opt"]
        record = structure("s0", charge=1, multiplicity=2)
        effective, _ = resolve_scientific_parameters(
            structure=record,
            overrides=(
                target.scientific.overrides if target.scientific is not None else FrozenDict()
            ),
            defaults=compiled.plan.scientific_defaults,
        )
        assert (effective.charge, effective.multiplicity) == (1, 2)

    def test_transform_passthrough_lineage_accepts(self) -> None:
        from tests.v4._builders import transform_step

        inputs = {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 1,
                "multiplicity": 2,
            }
        }
        document = _no_global_doc(
            [
                transform_step(
                    "s_xform",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_xform", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            inputs,
        )
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_confgen_passthrough_lineage_accepts(self) -> None:
        from tests.v4._builders import confgen_step

        inputs = {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 1,
                "multiplicity": 2,
            }
        }
        document = _no_global_doc(
            [
                confgen_step(
                    "s_gen",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_gen", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            inputs,
        )
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_unknown_same_lineage_accepts_without_globals(self) -> None:
        # Globals satisfy validation's boolean proof, but the checkpoint
        # itself proves compatibility only through same-lineage identity
        # (no absolute charge is fabricated from the globals).
        document = _doc(
            source_keyword="B3LYP/6-31G* freq",
            target_keyword="B3LYP/6-31G*",
        )
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_unknown_different_roots_refused_not_picked(self) -> None:
        document = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures_a"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures_b"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {
                "structures_a": {"kind": "structure", "cardinality": "many"},
                "structures_b": {"kind": "structure", "cardinality": "many"},
            },
        )
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(document, "s_opt", "s_freq")

    def test_ambiguous_different_input_states_refused(self) -> None:
        document = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures_a"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures_b"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {
                "structures_a": {
                    "kind": "structure",
                    "cardinality": "many",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "structures_b": {
                    "kind": "structure",
                    "cardinality": "many",
                    "charge": 1,
                    "multiplicity": 2,
                },
            },
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(document, "s_opt", "s_freq")

    def test_unknown_same_lineage_readfc_accepted(self) -> None:
        document = _doc(
            source_keyword="B3LYP/6-31G* freq",
            target_keyword="B3LYP/6-31G* opt",
        )
        wired = wire_checkpoint_reuse(document, "s_opt", "s_freq", mode="readfc")
        assert "ReadFC" in wired["steps"][1]["calculation"]["native"]["keyword"]
        assert compile_doc(wired).ok

    def test_unknown_source_target_explicit_readfc_rejected(self) -> None:
        document = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* opt"},
                    overrides={"charge": 1, "multiplicity": 2},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(document, "s_opt", "s_freq", mode="readfc")


class TestCompileIntentInheritedState:
    """End-to-end intent proof: global 0/1, source 1/2, target inherits."""

    def test_compile_intent_source_override_target_inherit_accepts(self) -> None:
        from confflow.producer.intent import INTENT_SCHEMA, compile_intent
        from confflow.workflow.v4.compiler import compile_workflow

        document = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1},
                "steps": [
                    {
                        "id": "opt",
                        "card": "opt@v1",
                        "program": "gaussian",
                        "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                        "overrides": {"charge": 1, "multiplicity": 2},
                    },
                    {
                        "id": "sp",
                        "card": "sp@v1",
                        "program": "gaussian",
                        "native": {"keyword": "B3LYP/6-31G(d) SP"},
                        "reuse_checkpoint": {"step": "opt", "mode": "checkpoint"},
                    },
                ],
            }
        )
        assert document["steps"][1]["bindings"]["checkpoint"]["cardinality"] == "one"
        assert compile_workflow(document).ok

    def test_compile_intent_target_explicit_mismatch_rejects(self) -> None:
        from confflow.producer.intent import (
            INTENT_SCHEMA,
            IntentCompilationError,
            compile_intent,
        )

        with pytest.raises(IntentCompilationError, match="charge|spin|inherited|reuse"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": {"charge": 0, "multiplicity": 1},
                    "steps": [
                        {
                            "id": "opt",
                            "card": "opt@v1",
                            "program": "gaussian",
                            "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                            "overrides": {"charge": 1, "multiplicity": 2},
                        },
                        {
                            "id": "sp",
                            "card": "sp@v1",
                            "program": "gaussian",
                            "native": {"keyword": "B3LYP/6-31G(d) SP"},
                            "overrides": {"charge": 0, "multiplicity": 1},
                            "reuse_checkpoint": {"step": "opt", "mode": "checkpoint"},
                        },
                    ],
                }
            )


def test_inherited_state_proof_uses_strict_default_execution_adapter():
    document = _doc(source_overrides={"charge": 1, "multiplicity": 2})
    for step in document["steps"]:
        step["calculation"].pop("execution_adapter", None)
    wired = wire_checkpoint_reuse(document, "s_opt", "s_freq")
    assert wired["steps"][1]["bindings"]["checkpoint"]["cardinality"] == "one"


@pytest.mark.parametrize("allow_method_change", [False, True])
def test_unknown_hessian_state_is_refused_on_otherwise_valid_workflow(allow_method_change):
    document = _doc(target_overrides={"charge": 0, "multiplicity": 1})
    assert compile_doc(document).ok
    with pytest.raises(DomainError, match="does not prove charge"):
        wire_checkpoint_reuse(
            document,
            "s_opt",
            "s_freq",
            "readfc",
            allow_method_change=allow_method_change,
        )
