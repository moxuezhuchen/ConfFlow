#!/usr/bin/env python3
"""Boundary coverage for the checkpoint-reuse producer helper.

Exercises uncovered semantic branches of
``confflow.producer.checkpoints.wire_checkpoint_reuse`` only through its
public surface (plus ``compile_workflow`` as the strict-validation oracle):
document/step shape guards, Gaussian/adapter/program vocabulary, Link0
handling, ``Opt=`` assignment route edits, ``IRC=``/``IRC=(...)`` vote
spans, charge/spin lineage proof (declared values, unknown states,
duplicate/ambiguous roots, disabled producers, malformed bindings,
same-lineage identity), native payload compatibility, provenance, and
compiler-rejection integration.
"""

from __future__ import annotations

import copy

import pytest

from confflow.domain.errors import DomainError
from confflow.execution.registry import RegistryLookupError, default_registry
from confflow.producer.checkpoints import CHECKPOINT_REUSE_VERSION, wire_checkpoint_reuse
from tests.v4._builders import calc_step, compile_doc, v4_doc

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _doc(
    source_keyword: str = "B3LYP/6-31G* freq",
    target_keyword: str = "B3LYP/6-31G* opt",
    *,
    source_native: dict | None = None,
    target_native: dict | None = None,
    source_program: str = "g16",
    source_adapter: str = "standard",
    target_adapter: str = "standard",
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
                adapter=target_adapter,
                native=target_block,
                overrides=target_overrides,
            ),
        ],
        inputs=STRUCTURE_INPUTS,
    )


def _no_global_doc(steps: list[dict], inputs: dict) -> dict:
    return v4_doc(steps, inputs=inputs, global_config={"scientific_defaults": {}})


class TestDocumentShapeGuards:
    def test_empty_steps_refused(self) -> None:
        doc = v4_doc([], inputs=STRUCTURE_INPUTS)
        with pytest.raises(DomainError, match="no steps"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_missing_steps_refused(self) -> None:
        doc = {"schema": "confflow.workflow.v4"}
        with pytest.raises(DomainError, match="no steps"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_non_mapping_step_refused(self) -> None:
        doc = _doc()
        doc["steps"] = ["not-a-mapping", *doc["steps"]]
        with pytest.raises(DomainError, match="must be a mapping"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_non_calculation_end_refused(self) -> None:
        from tests.v4._builders import transform_step

        doc = v4_doc(
            [
                transform_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* opt"},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        with pytest.raises(DomainError, match="not a calculation step"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_missing_program_refused(self) -> None:
        doc = _doc()
        del doc["steps"][0]["calculation"]["program"]
        with pytest.raises(DomainError, match="no calculation program"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_blank_program_refused(self) -> None:
        doc = _doc()
        doc["steps"][0]["calculation"]["program"] = "   "
        with pytest.raises(DomainError, match="no calculation program"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_missing_native_refused(self) -> None:
        doc = _doc()
        del doc["steps"][1]["calculation"]["native"]
        with pytest.raises(DomainError, match="no native mapping"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_missing_keyword_refused(self) -> None:
        doc = _doc()
        doc["steps"][1]["calculation"]["native"] = {}
        with pytest.raises(DomainError, match="no native route keyword"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_blank_keyword_refused(self) -> None:
        doc = _doc()
        doc["steps"][1]["calculation"]["native"]["keyword"] = "  "
        with pytest.raises(DomainError, match="no native route keyword"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_document_must_be_mapping(self) -> None:
        with pytest.raises(DomainError, match="must be a mapping"):
            wire_checkpoint_reuse(["not", "a", "mapping"], "s_opt", "s_freq")

    def test_empty_target_id_refused(self) -> None:
        with pytest.raises(DomainError, match="target step id"):
            wire_checkpoint_reuse(_doc(), "", "s_freq")

    def test_empty_source_id_refused(self) -> None:
        with pytest.raises(DomainError, match="source step id"):
            wire_checkpoint_reuse(_doc(), "s_opt", "")

    def test_allow_method_change_must_be_bool(self) -> None:
        with pytest.raises(DomainError, match="must be a boolean"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", allow_method_change="yes")  # type: ignore[arg-type]

    def test_disabled_source_refused(self) -> None:
        doc = _doc()
        doc["steps"][0]["enabled"] = False
        with pytest.raises(DomainError, match="disabled"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_disabled_target_refused(self) -> None:
        doc = _doc()
        doc["steps"][1]["enabled"] = False
        with pytest.raises(DomainError, match="disabled"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_target_without_bindings_mapping_refused(self) -> None:
        doc = _doc()
        doc["steps"][1]["bindings"] = "not-a-mapping"
        with pytest.raises(DomainError, match="no bindings mapping"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_unknown_program_refused(self) -> None:
        doc = _doc(source_program="definitely-unknown-program-xyz")
        with pytest.raises(DomainError, match="unknown program"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_target_qst_refused(self) -> None:
        doc = _doc(target_keyword="B3LYP/6-31G* QST3 opt")
        with pytest.raises(DomainError, match="QST"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")


class TestRegistryRoleBoundaries:
    def test_missing_standard_adapter_refused(self) -> None:
        real = default_registry()

        class _NoStandard:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                raise RegistryLookupError(f"unknown execution adapter: {name!r}")

            def resolve_executor(self, capability):  # noqa: D102
                return real.resolve_executor(capability)

            def resolve_profile(self, name):  # noqa: D102
                return real.resolve_profile(name)

        with pytest.raises(DomainError, match="not registered"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", registry=_NoStandard())  # type: ignore[arg-type]

    def test_checkpoint_role_missing_on_adapter_refused(self) -> None:
        real = default_registry()

        class _BarePort:
            roles: tuple = ()

        class _BareAdapter:
            def input_port(self, name):  # noqa: D102
                assert name == "checkpoint"
                return _BarePort()

        class _Reg:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                assert name == "standard"
                return _BareAdapter()

            def resolve_executor(self, capability):  # noqa: D102
                return real.resolve_executor(capability)

            def resolve_profile(self, name):  # noqa: D102
                return real.resolve_profile(name)

        with pytest.raises(DomainError, match="no 'checkpoint' role"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", registry=_Reg())  # type: ignore[arg-type]

    def test_checkpoint_port_absent_refused(self) -> None:
        real = default_registry()

        class _NoPort:
            def input_port(self, name):  # noqa: D102
                assert name == "checkpoint"
                return None

        class _Reg:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                return _NoPort()

            def resolve_executor(self, capability):  # noqa: D102
                return real.resolve_executor(capability)

            def resolve_profile(self, name):  # noqa: D102
                return real.resolve_profile(name)

        with pytest.raises(DomainError, match="no 'checkpoint' role"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", registry=_Reg())  # type: ignore[arg-type]

    def test_unresolvable_executor_contract_refused(self) -> None:
        real = default_registry()

        class _Reg:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                return real.resolve_adapter(name)

            def resolve_executor(self, capability):  # noqa: D102
                raise RegistryLookupError("no such executor")

            def resolve_profile(self, name):  # noqa: D102
                return real.resolve_profile(name)

        with pytest.raises(DomainError, match="cannot resolve the calculation executor"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", registry=_Reg())  # type: ignore[arg-type]

    def test_artifacts_role_missing_refused(self) -> None:
        real = default_registry()

        class _BarePort:
            roles: tuple = ("energy",)

        class _Contract:
            def output_port(self, name):  # noqa: D102
                assert name == "artifacts"
                return _BarePort()

        class _Reg:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                return real.resolve_adapter(name)

            def resolve_executor(self, capability):  # noqa: D102
                return _Contract()

            def resolve_profile(self, name):  # noqa: D102
                return real.resolve_profile(name)

        with pytest.raises(DomainError, match="no 'checkpoint' role"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", registry=_Reg())  # type: ignore[arg-type]

    def test_artifacts_port_absent_refused(self) -> None:
        real = default_registry()

        class _Contract:
            def output_port(self, name):  # noqa: D102
                assert name == "artifacts"
                return None

        class _Reg:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                return real.resolve_adapter(name)

            def resolve_executor(self, capability):  # noqa: D102
                return _Contract()

            def resolve_profile(self, name):  # noqa: D102
                return real.resolve_profile(name)

        with pytest.raises(DomainError, match="no 'checkpoint' role"):
            wire_checkpoint_reuse(_doc(), "s_opt", "s_freq", registry=_Reg())  # type: ignore[arg-type]


class TestLink0Boundaries:
    def test_empty_link0_accepts(self) -> None:
        doc = _doc(target_native={"link0": []})
        assert compile_doc(doc).ok
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok
        assert wired["steps"][1]["bindings"]["checkpoint"]["cardinality"] == "one"

    def test_benign_link0_accepts(self) -> None:
        doc = _doc(target_native={"link0": ["%NProcShared=4", "%Mem=2GB"]})
        assert compile_doc(doc).ok
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_benign_string_link0_accepts(self) -> None:
        doc = _doc(target_native={"link0": "%NProcShared=4"})
        assert compile_doc(doc).ok
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_source_benign_link0_accepts(self) -> None:
        doc = _doc(source_native={"link0": ["%NProcShared=8"]})
        assert compile_doc(doc).ok
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert wired["steps"][0]["calculation"]["native"]["write_chk"] is True
        assert compile_doc(wired).ok

    def test_oldchk_refused_on_target(self) -> None:
        with pytest.raises(DomainError, match="link0"):
            wire_checkpoint_reuse(
                _doc(target_native={"link0": ["%OldChk=hand.chk"]}), "s_opt", "s_freq"
            )


class TestOptAssignRouteEdits:
    def test_opt_assign_appends_readfc(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(target_keyword="B3LYP/6-31G* Opt=Tight"), "s_opt", "s_freq", mode="readfc"
        )
        keyword = wired["steps"][1]["calculation"]["native"]["keyword"]
        assert "ReadFC" in keyword and "Tight" in keyword
        assert compile_doc(wired).ok

    def test_opt_assign_idempotent_readfc(self) -> None:
        keyword = "B3LYP/6-31G* Opt=ReadFC"
        wired = wire_checkpoint_reuse(
            _doc(target_keyword=keyword), "s_opt", "s_freq", mode="readfc"
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == keyword
        assert compile_doc(wired).ok

    def test_opt_assign_opposed_calcfc_refused(self) -> None:
        with pytest.raises(DomainError, match="opposed"):
            wire_checkpoint_reuse(
                _doc(target_keyword="B3LYP/6-31G* Opt=CalcFC"), "s_opt", "s_freq", mode="readfc"
            )

    def test_opt_assign_opposed_rcfc_refused(self) -> None:
        with pytest.raises(DomainError, match="opposed"):
            wire_checkpoint_reuse(
                _doc(target_keyword="B3LYP/6-31G* Opt=RCFC"), "s_opt", "s_freq", mode="readfc"
            )

    def test_opt_paren_rcfc_opposed_refused(self) -> None:
        with pytest.raises(DomainError, match="opposed"):
            wire_checkpoint_reuse(
                _doc(target_keyword="B3LYP/6-31G* Opt(RCFC)"), "s_opt", "s_freq", mode="readfc"
            )


class TestIrcEqualsRouteEdits:
    def test_irc_equals_token_votes_rcfc(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* IRC",
                target_keyword="B3LYP/6-31G* IRC=MaxPoints",
            ),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        keyword = wired["steps"][1]["calculation"]["native"]["keyword"]
        assert "RCFC" in keyword and "MaxPoints" in keyword
        assert compile_doc(wired).ok

    def test_irc_equals_paren_votes_rcfc(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* IRC",
                target_keyword="B3LYP/6-31G* IRC=(MaxPoints=20)",
            ),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        keyword = wired["steps"][1]["calculation"]["native"]["keyword"]
        assert "RCFC" in keyword and "MaxPoints=20" in keyword
        assert compile_doc(wired).ok

    def test_irc_paren_with_options_votes_rcfc(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* IRC",
                target_keyword="B3LYP/6-31G* IRC(MaxPoints=5)",
            ),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        assert wired["steps"][1]["calculation"]["native"]["keyword"] == (
            "B3LYP/6-31G* IRC(MaxPoints=5,RCFC)"
        )
        assert compile_doc(wired).ok

    def test_irc_reverse_direction_refused(self) -> None:
        with pytest.raises(DomainError, match="explicit IRC direction"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* IRC",
                    target_keyword="B3LYP/6-31G* IRC(Reverse)",
                ),
                "s_opt",
                "s_freq",
                mode="rcfc",
            )

    def test_irc_unknown_option_refused(self) -> None:
        with pytest.raises(DomainError, match="invalid IRC route"):
            wire_checkpoint_reuse(
                _doc(
                    source_keyword="B3LYP/6-31G* IRC",
                    target_keyword="B3LYP/6-31G* IRC(BogusXYZ)",
                ),
                "s_opt",
                "s_freq",
                mode="rcfc",
            )


class TestChargeSpinLineageBranches:
    def test_matching_explicit_overrides_accept(self) -> None:
        doc = _doc(
            source_overrides={"charge": 1, "multiplicity": 2},
            target_overrides={"charge": 1, "multiplicity": 2},
        )
        assert compile_doc(doc).ok
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok
        assert wired["steps"][1]["bindings"]["checkpoint"]["cardinality"] == "one"

    def test_source_override_without_target_proof_refused(self) -> None:
        doc = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures_a"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                    overrides={"charge": 1, "multiplicity": 2},
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
        with pytest.raises(DomainError, match="does not prove the same inherited state"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_known_input_states_equal_accept(self) -> None:
        inputs = {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 0,
                "multiplicity": 1,
            }
        }
        doc = _no_global_doc(
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
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_known_input_states_differ_refused(self) -> None:
        doc = _no_global_doc(
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
                    overrides={"charge": 9, "multiplicity": 1},
                ),
            ],
            {
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "charge": 0,
                    "multiplicity": 1,
                }
            },
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_malformed_target_bindings_refused_closed(self) -> None:
        doc = _doc()
        doc["steps"][1]["bindings"] = {"structure": "not-a-mapping"}
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_malformed_binding_source_refused_closed(self) -> None:
        doc = _doc()
        doc["steps"][1]["bindings"] = {"structure": {"source": "not-a-mapping"}}
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_unknown_run_input_refused_closed(self) -> None:
        doc = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "missing_input"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_non_structure_input_kind_refused_closed(self) -> None:
        doc = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "blobs"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {
                "blobs": {"kind": "artifact", "cardinality": "many"},
                "structures": {"kind": "structure", "cardinality": "many"},
            },
        )
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_input_without_declared_state_refused_closed(self) -> None:
        doc = _no_global_doc(
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
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        # Same-lineage identity still accepts here (target consumes source
        # records directly), so force independent roots to require declared
        # values on both ends.
        doc["steps"][1]["bindings"] = {"structure": {"source": {"run": "structures"}}}
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_disabled_lineage_producer_refused_closed(self) -> None:
        from tests.v4._builders import transform_step

        doc = _no_global_doc(
            [
                transform_step(
                    "s_mid",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    enabled=False,
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_mid", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="inherited|charge|spin|disabled|compiler"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_unknown_executor_lineage_refused_closed(self) -> None:
        doc = _no_global_doc(
            [
                {
                    "id": "s_mid",
                    "executor": "definitely-unknown-executor",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                },
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_mid", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_unrelated_side_branch_artifact_edge_skipped(self) -> None:
        # R2.2: the unrelated side branch is a retained transform step
        # (analysis retired); the checkpoint-wiring proof is unchanged.
        from tests.v4._builders import transform_step

        doc = v4_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                transform_step(
                    "s_side",
                    kind="deduplicate",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* opt"},
                ),
            ],
            inputs=dict(STRUCTURE_INPUTS),
        )
        assert compile_doc(doc).ok
        # Target still reaches the checkpoint source through structures, so
        # the unrelated side branch must not disturb the proof.
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_override_on_lineage_path_breaks_identity(self) -> None:
        from tests.v4._builders import transform_step

        doc = _no_global_doc(
            [
                transform_step(
                    "s_mid",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_mid", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_mid", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                    overrides={"charge": 3, "multiplicity": 1},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="charge|spin|inherited"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_independent_run_root_refused(self) -> None:
        doc = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")


class TestMethodPayloadBoundaries:
    def test_matching_method_with_benign_link0_accepts(self) -> None:
        doc = _doc(
            source_keyword="B3LYP/6-31G* opt",
            target_keyword="B3LYP/6-31G* opt",
            source_native={"link0": ["%NProcShared=4"]},
            target_native={"link0": ["%Mem=2GB"]},
        )
        assert compile_doc(doc).ok
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert compile_doc(wired).ok

    def test_native_payload_mismatch_needs_override(self) -> None:
        doc = _doc(source_native={"extra_sections": "gen 5d 7f"})
        assert compile_doc(doc).ok
        with pytest.raises(DomainError, match="native scientific payload"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq", allow_method_change=True)
        assert (
            wired["steps"][1]["annotations"]["confflow.checkpoint_reuse"]["allow_method_change"]
            is True
        )
        assert compile_doc(wired).ok

    def test_keyword_mismatch_needs_override(self) -> None:
        doc = _doc(target_keyword="M06-2X/def2-TZVP opt")
        assert compile_doc(doc).ok
        with pytest.raises(DomainError, match="differing method"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq", allow_method_change=True)
        assert compile_doc(wired).ok

    def test_modredundant_mismatch_refused(self) -> None:
        doc = _doc(source_native={"modredundant": "B 1 2 F"})
        assert compile_doc(doc).ok
        with pytest.raises(DomainError, match="native scientific payload"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")


class TestProvenanceAndCompilerIntegration:
    def test_existing_annotations_preserved(self) -> None:
        doc = _doc()
        doc["steps"][1]["annotations"] = {"team.note": "keep-me"}
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        annotations = wired["steps"][1]["annotations"]
        assert annotations["team.note"] == "keep-me"
        assert annotations["confflow.checkpoint_reuse"]["version"] == CHECKPOINT_REUSE_VERSION
        assert annotations["confflow.checkpoint_reuse"]["mode"] == "checkpoint"
        assert compile_doc(wired).ok

    def test_rcfc_provenance_mode_recorded(self) -> None:
        wired = wire_checkpoint_reuse(
            _doc(
                source_keyword="B3LYP/6-31G* IRC",
                target_keyword="B3LYP/6-31G* IRC",
            ),
            "s_opt",
            "s_freq",
            mode="rcfc",
        )
        annotation = wired["steps"][1]["annotations"]["confflow.checkpoint_reuse"]
        assert annotation == {
            "source_step": "s_freq",
            "mode": "rcfc",
            "allow_method_change": False,
            "version": CHECKPOINT_REUSE_VERSION,
        }
        assert compile_doc(wired).ok

    def test_wired_document_compiles_and_input_untouched(self) -> None:
        doc = _doc()
        snapshot = copy.deepcopy(doc)
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq", mode="readfc")
        assert doc == snapshot
        compiled = compile_doc(wired)
        assert compiled.ok, [(d.code, d.message) for d in compiled.diagnostics]

    def test_compiler_rejection_names_compiler(self) -> None:
        doc = v4_doc(
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
            wire_checkpoint_reuse(doc, "s_a", "s_b")

    def test_duplicate_checkpoint_binding_refused(self) -> None:
        doc = _doc()
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        with pytest.raises(DomainError, match="already declares"):
            wire_checkpoint_reuse(wired, "s_opt", "s_freq")


class TestSelectiveRegistryLineageProof:
    """Registries that pass endpoint checks but fail lineage lookups."""

    def _selective_registry(self, fail: str):  # noqa: D102
        real = default_registry()

        class _Reg:
            def resolve_program(self, name):  # noqa: D102
                return real.resolve_program(name)

            def resolve_adapter(self, name):  # noqa: D102
                if fail == "adapter":
                    raise RegistryLookupError("no such adapter")
                return real.resolve_adapter(name)

            def resolve_executor(self, capability):  # noqa: D102
                from confflow.execution.contracts import ExecutorCapability

                try:
                    cap = capability if isinstance(capability, ExecutorCapability) else None
                except Exception:
                    cap = None
                if fail == "executor-other" and cap is not None and cap.value != "calculation":
                    raise RegistryLookupError("no such executor")
                return real.resolve_executor(capability)

            def resolve_profile(self, name):  # noqa: D102
                if fail == "profile":
                    raise RegistryLookupError("no such profile")
                return real.resolve_profile(name)

        return _Reg()

    def test_lineage_adapter_lookup_failure_fails_closed(self) -> None:
        from tests.v4._builders import transform_step

        doc = _no_global_doc(
            [
                transform_step(
                    "s_mid",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_mid", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="not registered|inherited|charge|spin"):
            wire_checkpoint_reuse(
                doc, "s_opt", "s_freq", registry=self._selective_registry("adapter")  # type: ignore[arg-type]
            )

    def test_lineage_profile_lookup_failure_fails_closed(self) -> None:
        doc = _no_global_doc(
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
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(
                doc, "s_opt", "s_freq", registry=self._selective_registry("profile")  # type: ignore[arg-type]
            )

    def test_confgen_override_propagates_declared_state(self) -> None:
        from tests.v4._builders import confgen_step

        doc = _no_global_doc(
            [
                confgen_step(
                    "s_gen",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    overrides={"charge": 1, "multiplicity": 2},
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
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        wired = wire_checkpoint_reuse(doc, "s_opt", "s_freq")
        assert wired["steps"][2]["bindings"]["checkpoint"]["cardinality"] == "one"

    def test_empty_source_mapping_fails_closed(self) -> None:
        doc = _doc()
        doc["steps"][1]["bindings"] = {"structure": {"source": {}}}
        with pytest.raises(DomainError, match="inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_missing_bindings_mapping_fails_closed(self) -> None:
        doc = _no_global_doc(
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
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        doc["steps"][1]["bindings"] = "not-a-mapping"
        with pytest.raises(DomainError, match="no bindings mapping|inherited|charge|spin"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")

    def test_unknown_producer_port_skipped_gracefully(self) -> None:
        doc = _no_global_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP/6-31G* freq"},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "no-such-port"}}},
                    native={"keyword": "B3LYP/6-31G*"},
                ),
            ],
            {"structures": {"kind": "structure", "cardinality": "many"}},
        )
        with pytest.raises(DomainError, match="inherited|charge|spin|compiler"):
            wire_checkpoint_reuse(doc, "s_opt", "s_freq")
