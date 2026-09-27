#!/usr/bin/env python3

"""Wave-1 repair tests (worker A): unified capability registry resolution.

Every capability the compiler, planner, and producer contract advertise
must resolve through :class:`ExecutionRegistry` to a descriptor backed by
a real implementation.  Unknown programs and unimplemented or incompatible
combinations fail closed at compile time with structured diagnostics.
"""

from __future__ import annotations

import sys
from typing import Any

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


def _goat_doc(**overrides: Any) -> dict[str, Any]:
    native: dict[str, Any] = {"keyword": "B3LYP D3BJ GOAT", "goat": {"MaxIter": 50}}
    params: dict[str, Any] = {
        "program": "orca",
        "bindings": {"structure": {"source": {"run": "structures"}}},
        "native": native,
        "profile": "ensemble",
        "checks": ["normal_termination"],
    }
    params.update(overrides)
    return v4_doc([calc_step("s_goat", **params)], inputs=STRUCTURE_INPUTS)


class TestUnifiedResolution:
    def test_resolve_executor_accepts_enum_and_string(self) -> None:
        registry = default_registry()
        from confflow.execution.contracts import ExecutorCapability

        by_enum = registry.resolve_executor(ExecutorCapability.CALCULATION)
        by_string = registry.resolve_executor("calculation")
        assert by_enum.capability.value == "calculation"
        assert by_string.contract_version == by_enum.contract_version
        for capability in ("calculation", "confgen", "analysis", "structure_transform"):
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
        assert registry.resolve_profile("path_endpoints").name == "path_endpoints"
        assert registry.resolve_profile("ensemble").name == "ensemble"
        assert registry.resolve_check("normal_termination").name == "normal_termination"
        assert registry.resolve_recovery("none").name == "none"
        assert registry.resolve_recovery("ts_rescue_scan").name == "ts_rescue_scan"
        assert registry.resolve_adapter("standard").name == "standard"
        assert registry.resolve_adapter("named_structures").name == "named_structures"
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

    def test_executor_implementations_resolve_from_same_entry(self) -> None:
        registry = default_registry()
        from confflow.analysis.item_adapter import AnalysisItemAdapter
        from confflow.execution.confgen_executor import ConfgenExecutor
        from confflow.execution.transform_executor import TransformExecutor
        from confflow.execution.work_item_executor import WorkItemExecutor

        assert registry.executor_implementation("calculation") is WorkItemExecutor
        assert registry.executor_implementation("confgen") is ConfgenExecutor
        assert registry.executor_implementation("structure_transform") is TransformExecutor
        # Final contract (freeze §4.2/F): analysis dispatches via the
        # in-package work-item adapter (same claim/commit/publish path),
        # not the whole-set core directly.
        assert registry.executor_implementation("analysis") is AnalysisItemAdapter
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
        envelope = build_configuration_contract_v4(producer_version="test")
        assert [entry["name"] for entry in envelope["result_profiles"]] == [
            "ensemble",
            "path_endpoints",
            "standard",
        ]
        assert [entry["name"] for entry in envelope["execution_adapters"]] == [
            "named_structures",
            "standard",
        ]
        assert [entry["program"] for entry in envelope["programs"]] == ["gaussian", "orca"]
        assert [entry["capability"] for entry in envelope["executors"]] == [
            "analysis",
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


class TestGoatSeedValidation:
    def test_goat_without_seed_rejected(self) -> None:
        result = compile_doc(_goat_doc())
        assert not result.ok
        assert "seed_required" in reasons(result.errors)

    def test_goat_with_seed_compiles(self) -> None:
        assert compile_doc(_goat_doc(seed=11)).ok

    def test_goat_native_seed_conflict_rejected(self) -> None:
        # Any user-supplied native RANDOMSEED is a second seed authority
        # and fails closed — set the step seed instead (the adapter
        # renders the deterministic boolean flag itself).
        doc = _goat_doc(seed=11)
        doc["steps"][0]["calculation"]["native"]["goat"]["RANDOMSEED"] = 99
        result = compile_doc(doc)
        assert not result.ok
        assert "seed_conflict" in reasons(result.errors)

    def test_goat_matching_native_seed_compiles(self) -> None:
        # Even a matching native RANDOMSEED is a second authority: the
        # adapter renders the step seed, never user native keys.
        doc = _goat_doc(seed=11)
        doc["steps"][0]["calculation"]["native"]["goat"]["RANDOMSEED"] = 11
        assert not compile_doc(doc).ok

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
        bad = v4_doc(
            [
                confgen_step(
                    "s_conf", bindings={"structure": {"source": {"run": "structures"}}}, seed=None
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(bad)
        assert not result.ok
        assert "seed_required" in reasons(result.errors)
        good = v4_doc(
            [
                confgen_step(
                    "s_conf", bindings={"structure": {"source": {"run": "structures"}}}, seed=7
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        assert compile_doc(good).ok


class TestNativeModeProfileCombinations:
    def test_goat_requires_ensemble_profile(self) -> None:
        doc = _goat_doc(seed=11, profile="standard")
        result = compile_doc(doc)
        assert not result.ok
        assert "incompatible_capability_combination" in reasons(result.errors)

    def test_irc_requires_path_endpoints_profile(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_irc",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP IRC", "irc": {"direction": "both"}},
                    profile="standard",
                    checks=["normal_termination"],
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "incompatible_capability_combination" in reasons(result.errors)

    def test_irc_with_path_endpoints_compiles(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_irc",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP IRC", "irc": {"direction": "both"}},
                    profile="path_endpoints",
                    checks=["normal_termination"],
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        assert compile_doc(doc).ok

    def test_neb_with_ensemble_compiles(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_neb",
                    program="orca",
                    adapter="named_structures",
                    bindings={
                        "reactant": {"source": {"run": "reactants"}, "pairing": "by_group_key"},
                        "product": {"source": {"run": "products"}, "pairing": "by_group_key"},
                    },
                    native={
                        "keyword": "B3LYP D3BJ NEB",
                        "neb": {"n_images": 5},
                        "atom_mapping": {"kind": "identity"},
                    },
                    profile="ensemble",
                    checks=["normal_termination"],
                )
            ],
            inputs={
                "reactants": {"kind": "structure", "cardinality": "many"},
                "products": {"kind": "structure", "cardinality": "many"},
            },
        )
        assert compile_doc(doc).ok

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
        assert "incompatible_capability_combination" in reasons(result.errors)

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
        assert "incompatible_capability_combination" in reasons(result.errors)


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
