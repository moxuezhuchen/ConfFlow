#!/usr/bin/env python3

"""Focused tests for the Phase 5 machine-capacity resolver.

Covers resolution arithmetic (CPU- vs memory-bound), oversized refusal,
explicit overrides, ``on_failure`` preservation, strict field/bool/
non-finite validation, the coordinated operational schema (per-program
executable mapping, ``binding_id``, ``remote``/``remote_target``/``target``
canonicalization), and the digest contract: operational changes are
digest-inert while request changes move scientific digests.
"""

from __future__ import annotations

import pytest

from confflow.domain.errors import InvalidResourceError
from confflow.producer.machine import MACHINE_RESOLUTION_VERSION, resolve_machine_resources
from tests.v4._builders import calc_step, compile_doc, v4_doc

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _profile(**overrides: object) -> dict:
    base: dict = {"name": "node-a", "total_cores": 16, "total_memory": "32GB"}
    base.update(overrides)
    return base


def _request(**overrides: object) -> dict:
    base: dict = {"cores_per_item": 4, "memory_per_item": "8GB"}
    base.update(overrides)
    return base


class TestResolutionArithmetic:
    """Slot limits are the minimum of CPU and memory floors."""

    def test_cpu_bound_width(self) -> None:
        out = resolve_machine_resources(_profile(), _request())
        assert out["scheduler"] == {"max_parallel_items": 4}
        assert out["resources"] == {"cores_per_item": 4, "memory_per_item": "8GB"}
        assert out["provenance"]["cpu_slots"] == 4
        assert out["provenance"]["mem_slots"] == 4

    def test_memory_bound_width(self) -> None:
        out = resolve_machine_resources(
            _profile(total_cores=64, total_memory="16GB"),
            _request(cores_per_item=4, memory_per_item="8GB"),
        )
        assert out["scheduler"] == {"max_parallel_items": 2}
        assert out["provenance"]["cpu_slots"] == 16
        assert out["provenance"]["mem_slots"] == 2

    def test_int_memory_wire(self) -> None:
        out = resolve_machine_resources(
            _profile(total_cores=8, total_memory=8 * 1024**3),
            _request(cores_per_item=2, memory_per_item=2 * 1024**3),
        )
        assert out["resources"] == {"cores_per_item": 2, "memory_per_item": 2 * 1024**3}
        assert out["scheduler"] == {"max_parallel_items": 4}

    def test_oversized_cores_fail(self) -> None:
        with pytest.raises(InvalidResourceError, match="exceeds"):
            resolve_machine_resources(_profile(total_cores=4), _request(cores_per_item=8))

    def test_oversized_memory_fail(self) -> None:
        with pytest.raises(InvalidResourceError, match="exceeds"):
            resolve_machine_resources(_profile(total_memory="4GB"), _request(memory_per_item="8GB"))

    def test_exact_fit_is_allowed(self) -> None:
        out = resolve_machine_resources(_profile(total_cores=4, total_memory="8GB"), _request())
        assert out["scheduler"] == {"max_parallel_items": 1}


class TestExplicitOverrides:
    """Scheduler overrides must be positive and within capacity."""

    def test_explicit_width_below_limit(self) -> None:
        out = resolve_machine_resources(_profile(), _request(), {"max_parallel_items": 2})
        assert out["scheduler"] == {"max_parallel_items": 2}
        assert out["provenance"]["explicit_override"] is True

    def test_explicit_width_above_limit_fails(self) -> None:
        with pytest.raises(InvalidResourceError, match="exceeds the capacity limit"):
            resolve_machine_resources(_profile(), _request(), {"max_parallel_items": 5})

    def test_explicit_width_must_be_positive(self) -> None:
        with pytest.raises(InvalidResourceError):
            resolve_machine_resources(_profile(), _request(), {"max_parallel_items": 0})

    def test_on_failure_preserved(self) -> None:
        out = resolve_machine_resources(_profile(), _request(), {"on_failure": "fail_fast"})
        assert out["scheduler"] == {"max_parallel_items": 4, "on_failure": "fail_fast"}
        assert out["provenance"]["on_failure"] == "fail_fast"

    def test_on_failure_rejects_unknown_values(self) -> None:
        with pytest.raises(InvalidResourceError, match="on_failure"):
            resolve_machine_resources(_profile(), _request(), {"on_failure": "retry"})


class TestStrictValidation:
    """Unknown fields, booleans, non-finite numbers, and gaps fail closed."""

    def test_unknown_profile_field_fails(self) -> None:
        with pytest.raises(InvalidResourceError, match="unknown field"):
            resolve_machine_resources(_profile(workers=4), _request())

    def test_unknown_resources_field_fails(self) -> None:
        with pytest.raises(InvalidResourceError, match="unknown field"):
            resolve_machine_resources(_profile(), _request(gpus_per_item=1))

    def test_unknown_scheduler_field_fails(self) -> None:
        with pytest.raises(InvalidResourceError, match="unknown field"):
            resolve_machine_resources(_profile(), _request(), {"priority": "high"})

    def test_bool_cores_rejected(self) -> None:
        with pytest.raises(InvalidResourceError, match="boolean"):
            resolve_machine_resources(_profile(total_cores=True), _request())

    def test_bool_memory_rejected(self) -> None:
        with pytest.raises(InvalidResourceError, match="boolean"):
            resolve_machine_resources(_profile(), _request(memory_per_item=True))

    def test_nonfinite_memory_rejected(self) -> None:
        with pytest.raises(InvalidResourceError, match="finite"):
            resolve_machine_resources(_profile(), _request(memory_per_item=float("inf")))

    def test_nonfinite_capacity_rejected(self) -> None:
        with pytest.raises(InvalidResourceError, match="finite"):
            resolve_machine_resources(_profile(total_memory=float("nan")), _request())

    def test_zero_capacity_rejected(self) -> None:
        with pytest.raises(InvalidResourceError):
            resolve_machine_resources(_profile(total_cores=0), _request())

    def test_missing_request_dimension_never_defaulted(self) -> None:
        with pytest.raises(InvalidResourceError, match="never defaulted"):
            resolve_machine_resources(_profile(), {"cores_per_item": 2})
        with pytest.raises(InvalidResourceError, match="never defaulted"):
            resolve_machine_resources(_profile(), {"memory_per_item": "2GB"})

    def test_missing_capacity_declaration_fails(self) -> None:
        profile = _profile()
        del profile["total_cores"]
        with pytest.raises(InvalidResourceError, match="total_cores"):
            resolve_machine_resources(profile, _request())

    def test_non_mapping_inputs_fail(self) -> None:
        with pytest.raises(InvalidResourceError):
            resolve_machine_resources("node-a", _request())  # type: ignore[arg-type]


class TestOperationalSchema:
    """Coordinated authoring/UI operational fields stay provenance-only."""

    def test_executable_string_recorded_in_provenance(self) -> None:
        out = resolve_machine_resources(_profile(executable="/opt/g16/g16"), _request())
        assert out["provenance"]["operational"]["executable"] == "/opt/g16/g16"
        assert "executable" not in out["resources"]
        assert "executable" not in out["scheduler"]

    def test_executable_per_program_mapping(self) -> None:
        mapping = {"gaussian": "/opt/g16/g16", "orca": "/opt/orca/orca"}
        out = resolve_machine_resources(_profile(executable=mapping), _request())
        assert out["provenance"]["operational"]["executable"] == mapping

    def test_executable_mapping_rejects_bad_entries(self) -> None:
        with pytest.raises(InvalidResourceError, match="executable"):
            resolve_machine_resources(_profile(executable={"gaussian": ""}), _request())
        with pytest.raises(InvalidResourceError, match="executable"):
            resolve_machine_resources(_profile(executable={}), _request())
        with pytest.raises(InvalidResourceError, match="executable"):
            resolve_machine_resources(_profile(executable=123), _request())

    def test_binding_id_recorded_in_provenance(self) -> None:
        out = resolve_machine_resources(_profile(binding_id="cluster-a"), _request())
        assert out["provenance"]["operational"]["binding_id"] == "cluster-a"

    def test_target_aliases_canonicalize(self) -> None:
        out = resolve_machine_resources(_profile(remote="node-7"), _request())
        assert out["provenance"]["operational"]["target"] == "node-7"
        assert "remote" not in out["provenance"]["operational"]
        out = resolve_machine_resources(_profile(remote_target="node-9"), _request())
        assert out["provenance"]["operational"]["target"] == "node-9"
        assert "remote_target" not in out["provenance"]["operational"]

    def test_target_alias_conflict_rejected(self) -> None:
        with pytest.raises(InvalidResourceError, match="conflict"):
            resolve_machine_resources(_profile(target="node-1", remote="node-2"), _request())
        with pytest.raises(InvalidResourceError, match="conflict"):
            resolve_machine_resources(_profile(target="node-1", remote_target="node-3"), _request())

    def test_agreeing_aliases_canonicalize(self) -> None:
        out = resolve_machine_resources(_profile(target="node-1", remote="node-1"), _request())
        assert out["provenance"]["operational"]["target"] == "node-1"

    def test_env_sandbox_and_limits_validated(self) -> None:
        out = resolve_machine_resources(
            _profile(
                env={"GAUSS_SCRDIR": "/scratch"},
                sandbox="/scratch/sandbox",
                allowed_executables=["/opt/g16/g16"],
                walltime_seconds=7200,
            ),
            _request(),
        )
        operational = out["provenance"]["operational"]
        assert operational["env"] == {"GAUSS_SCRDIR": "/scratch"}
        assert operational["sandbox"] == "/scratch/sandbox"
        assert operational["walltime_seconds"] == 7200
        with pytest.raises(InvalidResourceError, match="env"):
            resolve_machine_resources(_profile(env={"K": 1}), _request())
        with pytest.raises(InvalidResourceError, match="walltime"):
            resolve_machine_resources(_profile(walltime_seconds=0), _request())

    def test_provenance_version_stamp(self) -> None:
        out = resolve_machine_resources(_profile(), _request())
        assert out["provenance"]["version"] == MACHINE_RESOLUTION_VERSION
        assert set(out) == {"resources", "scheduler", "provenance"}


class TestDigestContract:
    """Operational resolution is digest-inert; requests move digests."""

    def _doc_with(self, **step_kwargs: object) -> dict:
        step = calc_step(
            "s_opt",
            bindings={"structure": {"source": {"run": "structures"}}},
            **step_kwargs,  # type: ignore[arg-type]
        )
        return v4_doc([step], inputs=STRUCTURE_INPUTS)

    def _digests(self, document: dict) -> tuple[str, str]:
        compiled = compile_doc(document)
        assert compiled.ok, [(d.code, d.message) for d in compiled.diagnostics]
        plan = compiled.plan
        assert plan is not None
        return plan.definition_digest, plan.steps[0].step_semantic_digest

    def test_operational_profile_does_not_move_digests(self) -> None:
        wire = resolve_machine_resources(_profile(), _request())
        plain = self._doc_with(resources=wire["resources"], scheduler=wire["scheduler"])
        bound = self._doc_with(
            resources=wire["resources"],
            scheduler=wire["scheduler"],
            execution={
                "binding_id": "cluster-a",
                "executable": "/opt/g16/g16",
                "env": {"GAUSS_SCRDIR": "/scratch"},
                "target": "node-7",
            },
        )
        assert self._digests(plain) == self._digests(bound)

    def test_scheduler_width_does_not_move_digests(self) -> None:
        narrow = resolve_machine_resources(_profile(), _request(), {"max_parallel_items": 1})
        wide = resolve_machine_resources(_profile(), _request(), {"max_parallel_items": 4})
        assert narrow["scheduler"] != wide["scheduler"]
        assert self._digests(
            self._doc_with(resources=narrow["resources"], scheduler=narrow["scheduler"])
        ) == self._digests(self._doc_with(resources=wide["resources"], scheduler=wide["scheduler"]))

    def test_request_change_moves_digests(self) -> None:
        first = resolve_machine_resources(_profile(), _request(cores_per_item=2))
        second = resolve_machine_resources(_profile(), _request(cores_per_item=4))
        assert self._digests(self._doc_with(resources=first["resources"])) != (
            self._digests(self._doc_with(resources=second["resources"]))
        )

    def test_memory_change_moves_digests(self) -> None:
        first = resolve_machine_resources(_profile(), _request(memory_per_item="8GB"))
        second = resolve_machine_resources(_profile(), _request(memory_per_item="16GB"))
        assert self._digests(self._doc_with(resources=first["resources"])) != (
            self._digests(self._doc_with(resources=second["resources"]))
        )

    def test_resolved_wire_compiles_against_strict_schema(self) -> None:
        wire = resolve_machine_resources(_profile(), _request(), {"on_failure": "fail_fast"})
        compiled = compile_doc(
            v4_doc(
                [calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}})],
                inputs=STRUCTURE_INPUTS,
                global_config={
                    "resources": wire["resources"],
                    "scheduler": wire["scheduler"],
                },
            )
        )
        assert compiled.ok, [(d.code, d.message) for d in compiled.diagnostics]
