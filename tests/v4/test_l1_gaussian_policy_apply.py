#!/usr/bin/env python3
"""L1-G1b staged Gaussian policy apply (rehearsal, new-file only).

Covers DESIGN-v2 §2 delegation with ROOT priority (Enum identity, no value
strings, adapter raw value), §1 order pairs, §5 AST gates, and per-rule
real-violation + prose-positive evidence. Raw scope is not relaxed.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from confflow.domain.errors import DomainError
from confflow.execution.native import ProgramName


def _policy():
    import confflow.programs.gaussian.checkpoint_policy as p

    return p


def test_program_identity_not_value() -> None:
    p = _policy()
    p.require_gaussian_program(step_id="s", program="g16", program_name=ProgramName.GAUSSIAN)
    with pytest.raises(DomainError, match="ORCA declares no checkpoint"):
        p.require_gaussian_program(step_id="s", program="orca", program_name=ProgramName.ORCA)
    # Fake same-value object must still refuse (identity, not .value/==).

    class _Fake:
        value = "gaussian"

    with pytest.raises(DomainError, match="ORCA declares no checkpoint"):
        p.require_gaussian_program(step_id="s", program="g16", program_name=_Fake())  # type: ignore[arg-type]


def test_adapter_raw_value() -> None:
    p = _policy()
    p.require_standard_adapter(step_id="s", execution_adapter="standard")
    with pytest.raises(DomainError, match="'standard' adapter"):
        p.require_standard_adapter(step_id="s", execution_adapter="named_structures")
    # Raw scope: None (non-string) refuses with the same branch, no coercion.
    with pytest.raises(DomainError, match="'standard' adapter"):
        p.require_standard_adapter(step_id="s", execution_adapter=None)


def test_qst_and_roles() -> None:
    p = _policy()
    p.require_no_qst(step_id="s", keyword="B3LYP/6-31G* opt")
    with pytest.raises(DomainError, match="QST route item"):
        p.require_no_qst(step_id="s", keyword="B3LYP/6-31G* QST2 opt")
    p.require_standard_checkpoint_role(port_present=True, has_checkpoint_role=True)
    with pytest.raises(DomainError, match="no 'checkpoint' role"):
        p.require_standard_checkpoint_role(port_present=True, has_checkpoint_role=False)
    with pytest.raises(DomainError, match="no 'checkpoint' role"):
        p.require_standard_checkpoint_role(port_present=False, has_checkpoint_role=False)
    p.require_artifact_checkpoint_role(port_present=True, has_checkpoint_role=True)
    with pytest.raises(DomainError, match="no 'checkpoint' role"):
        p.require_artifact_checkpoint_role(port_present=True, has_checkpoint_role=False)


def test_write_chk_and_link0() -> None:
    p = _policy()
    p.ensure_source_write_chk(step_id="s", native={"keyword": "x"})
    p.ensure_source_write_chk(step_id="s", native={"keyword": "x", "write_chk": True})
    with pytest.raises(DomainError, match="disables native 'write_chk'"):
        p.ensure_source_write_chk(step_id="s", native={"keyword": "x", "write_chk": False})
    p.check_target_link0_core(step_id="t", link0_value=None)
    p.check_target_link0_core(step_id="t", link0_value="%NProcShared=4\n%Mem=2GB")
    with pytest.raises(DomainError, match="manages checkpoint paths"):
        p.check_target_link0_core(step_id="t", link0_value="%Chk=job.chk")
    with pytest.raises(DomainError, match="manages checkpoint paths"):
        p.check_target_link0_core(step_id="t", link0_value=["%OldChk=old.chk"])
    with pytest.raises(ValueError):
        p.check_target_link0_core(step_id="t", link0_value={"bad": "type"})


def test_method_stages() -> None:
    p = _policy()
    p.check_unsupported_method(target_id="t", target_keyword="B3LYP/6-31G* opt")
    with pytest.raises(DomainError, match="whose final energy"):
        p.check_unsupported_method(target_id="t", target_keyword="MP2/6-31G* opt")
    p.check_route_cores(
        source_id="s",
        target_id="t",
        source_keyword="B3LYP/6-31G* freq",
        target_keyword="B3LYP/6-31G* opt",
        allow_method_change=False,
    )
    with pytest.raises(DomainError, match="differing method"):
        p.check_route_cores(
            source_id="s",
            target_id="t",
            source_keyword="B3LYP/6-31G* freq",
            target_keyword="M06-2X/def2-TZVP opt",
            allow_method_change=False,
        )
    mk = frozenset({"keyword", "write_chk", "link0"})
    p.check_native_payload_cores(
        source_id="s",
        target_id="t",
        source_native={"keyword": "a", "basis": "x"},
        target_native={"keyword": "b", "basis": "x"},
        allow_method_change=False,
        managed_keys=mk,
    )
    with pytest.raises(DomainError, match="native scientific payload"):
        p.check_native_payload_cores(
            source_id="s",
            target_id="t",
            source_native={"keyword": "a", "extra_sections": "gen"},
            target_native={"keyword": "b"},
            allow_method_change=False,
            managed_keys=mk,
        )
    # Override path stays open.
    p.check_route_cores(
        source_id="s",
        target_id="t",
        source_keyword="B3LYP/6-31G* freq",
        target_keyword="M06-2X/def2-TZVP opt",
        allow_method_change=True,
    )
    assert p.native_scientific_core({"keyword": "a", "basis": "x"}, managed_keys=mk) == {
        "basis": "x"
    }


def test_wrappers_keep_signatures_and_wire() -> None:
    import confflow.producer.checkpoints as c

    assert list(inspect.signature(c._check_gaussian_sides).parameters) == [
        "registry",
        "source",
        "target",
    ]
    assert list(inspect.signature(c._check_write_chk).parameters) == ["source"]
    assert list(inspect.signature(c._check_target_link0).parameters) == ["target"]
    assert "allow_method_change" in str(inspect.signature(c._check_method_compatibility))
    assert list(inspect.signature(c._native_scientific_core).parameters) == ["native"]
    # New policy stages have different signatures: must not claim `is`.
    assert c._check_gaussian_sides.__module__ == "confflow.producer.checkpoints"
    assert c.CHECKPOINT_REUSE_VERSION == "confflow.producer.checkpoints.v1"
    assert c.REUSE_MODES == ("checkpoint", "readfc")
    assert c.__all__ == ["CHECKPOINT_REUSE_VERSION", "REUSE_MODES", "wire_checkpoint_reuse"]


def test_order_pairs_match_old() -> None:
    from confflow.execution.registry import RegistryLookupError, default_registry
    from confflow.producer import checkpoints as C
    from tests.v4._builders import calc_step, v4_doc

    real = default_registry()
    SI = {"structures": {"kind": "structure", "cardinality": "many"}}

    def doc(sk="B3LYP/6-31G* freq", tk="B3LYP/6-31G* opt"):
        return v4_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": sk},
                ),
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_freq", "port": "structures"}}},
                    native={"keyword": tk},
                ),
            ],
            inputs=SI,
        )

    # (a) source-ORCA + target-unknown => ORCA (S-loop source precedes target).
    d = doc()
    d["steps"][0]["calculation"]["program"] = "orca"
    d["steps"][1]["calculation"]["program"] = "definitely-unknown-program-xyz"
    with pytest.raises(DomainError, match="ORCA declares no"):
        C.wire_checkpoint_reuse(d, "s_opt", "s_freq")

    # (d) roles missing precedes unresolvable executor.
    class _BarePort:
        roles = ()

    class _BareAdapter:
        def input_port(self, name):
            return _BarePort()

    class _Reg:
        def resolve_program(self, n):
            return real.resolve_program(n)

        def resolve_adapter(self, n):
            return _BareAdapter()

        def resolve_executor(self, c):
            raise RegistryLookupError("no such executor")

        def resolve_profile(self, n):
            return real.resolve_profile(n)

    with pytest.raises(DomainError, match="no 'checkpoint' role"):
        C.wire_checkpoint_reuse(doc(), "s_opt", "s_freq", registry=_Reg())  # type: ignore[arg-type]

    # Method M1 precedes native-shape gate (direct compat call).
    d2 = doc("MP2/6-31G* freq", "MP2/6-31G* opt")
    d2["steps"][0]["calculation"]["native"] = "not-a-mapping"
    with pytest.raises(DomainError, match="whose final energy"):
        C._check_method_compatibility(
            d2,
            d2["steps"][0],
            d2["steps"][1],
            "MP2/6-31G* freq",
            "MP2/6-31G* opt",
            allow_method_change=False,
            registry=real,
        )


def test_g1_gates_clean_and_prose_silent(tmp_path: Path) -> None:
    from tools.architecture_policy import g1_violations

    root = Path(__file__).resolve().parents[2]
    assert g1_violations(root) == []
    # Real violation fires.
    (tmp_path / "confflow/producer").mkdir(parents=True)
    (tmp_path / "confflow/programs/gaussian").mkdir(parents=True)
    (tmp_path / "confflow/producer/__init__.py").write_text("")
    (tmp_path / "confflow/programs/__init__.py").write_text("")
    (tmp_path / "confflow/programs/gaussian/__init__.py").write_text("")
    (tmp_path / "confflow/producer/checkpoints.py").write_text(
        "x = ProgramName\nif ProgramName:\n    y = 1\n"
    )
    (tmp_path / "confflow/programs/gaussian/checkpoint_policy.py").write_text("x = 1\n")
    from tools.architecture_policy import g1_prod_condition_violations

    assert g1_prod_condition_violations(tmp_path) != []
    # Prose positive stays silent.
    (tmp_path / "confflow/producer/checkpoints.py").write_text(
        '"""ProgramName _QST_TOKEN_RE"""\n# ProgramName\nx = 1\n'
    )
    assert g1_prod_condition_violations(tmp_path) == []
    assert g1_violations(root) == []


def test_native_core_duck_matches_old_helper() -> None:
    # Real compat counterexample (root duck probe): old helper path with a
    # Duck (.items only) must succeed via the producer helper itself, not by
    # converting the Duck to a dict around it.
    import confflow.producer.checkpoints as prod

    class _Duck:
        def items(self):  # noqa: D102
            return [("keyword", "opt"), ("basis", "x")]

    assert prod._native_scientific_core(_Duck()) == {"basis": "x"}  # type: ignore[arg-type]
    import confflow.programs.gaussian.checkpoint_policy as pol

    assert pol.native_scientific_core(_Duck(), managed_keys=frozenset({"keyword", "write_chk", "link0"})) == {"basis": "x"}  # type: ignore[arg-type]


def test_native_core_plain_object_same_attribute_error() -> None:
    # Bad-type对照: plain object without .items raises the same
    # AttributeError class and text through the producer helper path.
    import confflow.producer.checkpoints as prod

    with __import__("pytest").raises(AttributeError, match="has no attribute 'items'"):
        prod._native_scientific_core(object())  # type: ignore[arg-type]
