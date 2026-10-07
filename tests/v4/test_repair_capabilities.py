#!/usr/bin/env python3

"""Wave-1 repair tests (worker A): unified capability registry resolution.

Every capability the compiler, planner, and producer contract advertise
must resolve through :class:`ExecutionRegistry` to a descriptor backed by
a real implementation.  Unknown programs and unimplemented or incompatible
combinations fail closed at compile time with structured diagnostics.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "tests/v4")

from _builders import calc_step, compile_doc, confgen_step, reasons, v4_doc  # noqa: E402

from confflow.execution.registry import (  # noqa: E402
    ExecutionRegistry,
    RegistryLookupError,
    build_default_registry,
    default_registry,
)
from confflow.producer.contract import build_configuration_contract_v4  # noqa: E402

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


class TestUnifiedResolution:
    def test_resolve_executor_accepts_enum_and_string(self) -> None:
        registry = default_registry()
        from confflow.execution.contracts import ExecutorCapability

        by_enum = registry.resolve_executor(ExecutorCapability.CALCULATION)
        by_string = registry.resolve_executor("calculation")
        assert by_enum.capability.value == "calculation"
        assert by_string.contract_version == by_enum.contract_version
        for capability in ("calculation", "confgen", "structure_transform"):
            assert registry.resolve_executor(capability).capability.value == capability

    def test_resolve_executor_rejects_unknown(self) -> None:
        registry = default_registry()
        with pytest.raises(RegistryLookupError):
            registry.resolve_executor("magic")
        with pytest.raises(RegistryLookupError):
            registry.resolve_executor("")

    def test_resolve_profile_check_recovery_adapter(self) -> None:
        registry = default_registry()
        assert registry.resolve_profile("standard").name == "standard"
        # R2.2: ensemble stays as the retained ConfGen result profile;
        # path_endpoints and named_structures are retired.
        assert registry.resolve_profile("ensemble").name == "ensemble"
        with pytest.raises(RegistryLookupError):
            registry.resolve_profile("path_endpoints")
        assert registry.resolve_check("normal_termination").name == "normal_termination"
        assert registry.resolve_recovery("none").name == "none"
        assert registry.resolve_recovery("ts_rescue_scan").name == "ts_rescue_scan"
        assert registry.resolve_adapter("standard").name == "standard"
        with pytest.raises(RegistryLookupError):
            registry.resolve_adapter("named_structures")
        with pytest.raises(RegistryLookupError):
            registry.resolve_profile("magic")
        with pytest.raises(RegistryLookupError):
            registry.resolve_check("magic")
        with pytest.raises(RegistryLookupError):
            registry.resolve_recovery("magic")
        with pytest.raises(RegistryLookupError):
            registry.resolve_adapter("magic")

    def test_resolve_program_through_real_registry(self) -> None:
        registry = default_registry()
        assert registry.resolve_program("orca").program_name.value == "orca"
        assert registry.resolve_program("gaussian").program_name.value == "gaussian"
        assert registry.resolve_program("g16").program_name.value == "gaussian"
        with pytest.raises(RegistryLookupError):
            registry.resolve_program("no-such-program")

    def test_unexecutable_capabilities_are_omitted_not_published(self) -> None:
        registry = default_registry()
        # ``opaque`` and ``native_template`` have no runtime implementation,
        # so they are omitted from the vocabulary entirely: never
        # declared-but-unexecutable.
        assert "opaque" not in registry.profile_names
        assert "native_template" not in registry.adapter_names
        with pytest.raises(RegistryLookupError):
            registry.resolve_profile("opaque")
        with pytest.raises(RegistryLookupError):
            registry.resolve_adapter("native_template")

    def test_implementation_accessors_return_real_objects(self) -> None:
        registry = default_registry()
        assert registry.profile_implementation("standard").name == "standard"
        assert registry.check_implementation("normal_termination").name == "normal_termination"
        assert callable(registry.adapter_implementation("standard"))
        assert registry.program_adapter("orca").program_name.value == "orca"

    # R2.3a (G18): test_orphaned_analysis_implementation_stays_importable
    # retired with confflow.analysis (R2.2 orphan, now deleted).

    def test_executor_implementations_resolve_from_same_entry(self) -> None:
        registry = default_registry()
        from confflow.execution.confgen_executor import ConfgenExecutor
        from confflow.execution.transform_executor import TransformExecutor
        from confflow.execution.work_item_executor import WorkItemExecutor

        assert registry.executor_implementation("calculation") is WorkItemExecutor
        assert registry.executor_implementation("confgen") is ConfgenExecutor
        assert registry.executor_implementation("structure_transform") is TransformExecutor
        # R2.2: the analysis executor is unregistered (implementation
        # orphaned for R2.3a).
        with pytest.raises(RegistryLookupError):
            registry.executor_implementation("analysis")
        with pytest.raises(RegistryLookupError):
            registry.executor_implementation("magic")

    def test_recovery_factory_binds_adapter(self) -> None:
        from confflow.execution.recovery_standard import NoneRecoveryPolicy, TsRescueScanPolicy

        registry = default_registry()
        adapter = registry.resolve_program("gaussian")
        bound = registry.recovery_implementation("ts_rescue_scan", adapter=adapter)
        assert isinstance(bound, TsRescueScanPolicy)
        unbound = registry.recovery_implementation("ts_rescue_scan", adapter=None)
        assert isinstance(unbound, TsRescueScanPolicy)
        assert isinstance(
            registry.recovery_implementation("none", adapter=adapter), NoneRecoveryPolicy
        )


class TestOmittedCapabilitiesExcluded:
    def test_producer_contract_omits_unexecutable_capabilities(self) -> None:
        # R2.2 声明：path_endpoints/named_structures/analysis 退役；
        # ensemble 保留（留存 ConfGen 结果剖面）。
        envelope = build_configuration_contract_v4(producer_version="test")
        assert [entry["name"] for entry in envelope["result_profiles"]] == [
            "ensemble",
            "standard",
        ]
        assert [entry["name"] for entry in envelope["execution_adapters"]] == [
            "standard",
        ]
        assert [entry["program"] for entry in envelope["programs"]] == ["gaussian", "orca"]
        assert [entry["capability"] for entry in envelope["executors"]] == [
            "calculation",
            "confgen",
            "structure_transform",
        ]


class TestCompileRejections:
    def test_unknown_program_rejected(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    program="no-such-program",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_program" in reasons(result.errors)

    def test_program_alias_still_compiles(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    program="g16",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        assert compile_doc(doc).ok

    def test_opaque_profile_no_longer_published(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                    profile="opaque",
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_result_profile" in reasons(result.errors)

    def test_native_template_adapter_no_longer_published(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    adapter="native_template",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_execution_adapter" in reasons(result.errors)

    def test_role_never_dispatches(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    role="whatever-role",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        assert compile_doc(doc).ok


class TestSeedValidation:
    def test_plain_calculation_seed_allowed(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                    seed=7,
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        assert compile_doc(doc).ok

    def test_confgen_seed_rules_preserved(self) -> None:
        bindings = {"structure": {"source": {"run": "structures"}}}

        def doc_with(seed: int | None, *, cap: int | None) -> dict:
            step = confgen_step("s_conf", bindings=bindings, seed=seed)
            if cap is not None:
                step["confgen"]["sampling"] = {"cap": cap}
            return v4_doc([step], inputs=STRUCTURE_INPUTS)

        # v3 rule: full enumeration needs no seed; capped sampling does.
        assert compile_doc(doc_with(None, cap=None)).ok
        bad = compile_doc(doc_with(None, cap=3))
        assert not bad.ok
        assert "invalid_value" in reasons(bad.errors)
        assert compile_doc(doc_with(7, cap=3)).ok


class TestNativeModeProfileCombinations:
    def test_neb_requires_ensemble_profile(self) -> None:
        # R2.3b/e (G18): NEB is the retained ensemble consumer; a
        # non-ensemble profile still fails the combination rule.
        doc = v4_doc(
            [
                calc_step(
                    "s_neb",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP NEB", "neb": {"n_images": 5}},
                    profile="standard",
                    checks=["normal_termination"],
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "incompatible_capability_combination" in reasons(result.errors)

    def test_multiple_native_modes_rejected(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_mixed",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={
                        "keyword": "B3LYP Opt IRC",
                        "irc": {"direction": "both"},
                        "goat": {"MaxIter": 5},
                    },
                    profile="ensemble",
                    checks=["normal_termination"],
                    seed=5,
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        # R2.3b/e: retired ``irc``/``goat`` keys are unknown native keys.
        # The adapter-owned native-definition requirement is still the
        # single report for this defect (P3 dedup); the outcome is unchanged.
        assert "invalid_value" in reasons(result.errors)
        assert any("unknown native keys" in item.message for item in result.errors)

    def test_non_mapping_mode_section_rejected(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_neb",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt", "neb": True},
                    profile="ensemble",
                    checks=["normal_termination"],
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "invalid_value" in reasons(result.errors)
        # R2.3d (G18): NEB is retired and `neb` left the native vocabulary,
        # so a stale NEB section fails closed as an unknown key.
        assert any(
            "unknown native keys" in item.message and "neb" in item.message
            for item in result.errors
        )


class TestCustomRegistryResolution:
    def test_custom_registry_descriptors_resolve(self) -> None:
        registry = build_default_registry()
        assert registry.resolve_check("bond_drift").name == "bond_drift"
        assert registry.resolve_program("orca").program_name.value == "orca"

    def test_unknown_executor_still_rejected_with_custom_registry(self) -> None:
        registry: ExecutionRegistry = build_default_registry()
        doc = v4_doc(
            [
                {
                    "id": "s_bad",
                    "executor": "magic",
                    "bindings": {},
                }
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc, registry=registry)
        assert not result.ok
        assert "unknown_executor_capability" in reasons(result.errors)
