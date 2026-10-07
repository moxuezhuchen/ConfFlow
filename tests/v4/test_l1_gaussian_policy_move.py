#!/usr/bin/env python3

"""L1-G1a pure Gaussian route mechanical move (rehearsal).

Asserts the ``producer/checkpoints -> programs/gaussian/checkpoint_policy``
move keeps old ``producer.checkpoints`` private paths observable: same
objects (``is``), unchanged signatures/bodies (AST-exact, verified here via
``inspect`` + fixed vectors), old ``__all__``/version/modes intact, private
``__module__`` now the definition site (policy), no science table copy, and
the fixed readfc/conflict/unknown-option/SP-edge corpus behaves
identically (return or exception type/message).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

_TRIO = ("_add_opt_option", "_strip_managed_items")
_CONSTANTS = (
    "_QST_TOKEN_RE",
    "_IRC_MANAGED_RE",
    "_OPT_PAREN_RE",
    "_OPT_ASSIGN_RE",
    "_OPT_BARE_RE",
    "_FREQ_TOKEN_RE",
    "_SP_MANAGED_RE",
    "_READFC_CONFLICTS",
    "_LINK0_CHECKPOINT_RE",
)
_POLICY_MODULE = "confflow.programs.gaussian.checkpoint_policy"
_PRODUCER_MODULE = "confflow.producer.checkpoints"


def test_reexport_same_objects() -> None:
    import confflow.producer.checkpoints as producer
    import confflow.programs.gaussian.checkpoint_policy as policy

    for name in (*_TRIO, *_CONSTANTS):
        assert getattr(producer, name) is getattr(policy, name), name
    # Light refusal is homomorphic, not the same object: each module owns one.
    assert producer._refuse is not policy._refuse
    # Authority aliases bind the same module objects (bodies unchanged).
    import confflow.programs.gaussian.rendering as gaussian_rendering

    assert policy._gaussian_rendering is gaussian_rendering


def test_signatures_unchanged_no_wrappers() -> None:
    import confflow.producer.checkpoints as producer
    import confflow.programs.gaussian.checkpoint_policy as policy

    for name in _TRIO:
        assert str(inspect.signature(getattr(producer, name))) == str(
            inspect.signature(getattr(policy, name))
        ), name
        assert getattr(producer, name).__name__ == name
    # No wrapper: producer attribute is the definition object itself.
    assert producer._add_opt_option.__qualname__ == "_add_opt_option"


def test_module_public_surface_unchanged() -> None:
    import confflow.producer.checkpoints as producer

    assert producer.__all__ == [
        "CHECKPOINT_REUSE_VERSION",
        "REUSE_MODES",
        "wire_checkpoint_reuse",
    ]
    assert producer.CHECKPOINT_REUSE_VERSION == "confflow.producer.checkpoints.v1"
    assert producer.REUSE_MODES == ("checkpoint", "readfc")
    # Private moved functions record the definition site; public surface does not move.
    import confflow.programs.gaussian.checkpoint_policy as policy

    for name in _TRIO:
        assert getattr(policy, name).__module__ == _POLICY_MODULE, name
        assert getattr(producer, name).__module__ == _POLICY_MODULE, name


def test_policy_imports_contain_no_producer_workflow_science() -> None:
    # G1b stage assertion (migrated from G1a mechanical-only stage):
    # producer/workflow/science stay forbidden; G1b necessarily allows the
    # ProgramName Enum identity (ROOT priority), while ExecutionRegistry and
    # producer callbacks stay forbidden. Identity (is not) is evidenced, the
    # .value string variant is forbidden.
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "confflow/programs/gaussian/checkpoint_policy.py").read_text())
    mods = [
        ("." * node.level + (node.module or ""))
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]
    assert not any("producer" in mod for mod in mods), mods
    assert not any("workflow" in mod for mod in mods), mods
    assert not any("science" in mod for mod in mods), mods
    # G1b: ProgramName Enum identity is required; registry/callbacks are not.
    # (docstring/prose exempt by construction: only Name/Call/Compare nodes count).
    code_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "ProgramName" in code_names, sorted(code_names)
    assert "ExecutionRegistry" not in code_names
    def_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "require_gaussian_program" in def_names
    # No producer-callback shape: no Callable/lambda parameters in stages.
    assert "Callable" not in code_names
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            raise AssertionError("policy stages must not take lambda/Callable")
    # Identity evidence: `is not ProgramName.GAUSSIAN` present; the
    # `.value` string variant for ProgramName dispatch is absent.
    has_identity = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, _comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.IsNot, ast.Is)):
                    text = ast.unparse(node)
                    if "ProgramName.GAUSSIAN" in text and "is not" in text:
                        has_identity = True
        if isinstance(node, ast.Attribute) and node.attr == "value":
            base = node.value
            if (
                isinstance(base, ast.Attribute)
                and base.attr == "GAUSSIAN"
                and isinstance(base.value, ast.Name)
                and base.value.id == "ProgramName"
            ):
                raise AssertionError("ProgramName must use identity, not .value")
    assert has_identity, "missing `is not ProgramName.GAUSSIAN` identity"
    # Runtime identity (not value equality): fake same-value object still refuses.
    import confflow.programs.gaussian.checkpoint_policy as _pol
    from confflow.domain.errors import DomainError as _DErr
    from confflow.execution.native import ProgramName as _PN

    _pol.require_gaussian_program(step_id="s", program="g16", program_name=_PN.GAUSSIAN)

    class _FakeProgram:
        value = "gaussian"

    try:
        _pol.require_gaussian_program(step_id="s", program="g16", program_name=_FakeProgram())  # type: ignore[arg-type]
    except _DErr:
        pass
    else:
        raise AssertionError("fake same-value program must still refuse (identity)")


def test_producer_keeps_g1b_gaussian_branches() -> None:
    # G1b delegation semantics (node ID retained; history ID kept for L3 reorg):
    # G1a asserted producer kept Gaussian branches; G1b inverts it by design --
    # producer AST must have zero Gaussian conditions/authority calls, with
    # real delegation evidence to each policy stage (consistent with g1_* gates).
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "confflow/producer/checkpoints.py").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        if isinstance(node, ast.Compare):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    assert "ProgramName" not in names, sorted(names)
    assert "_QST_TOKEN_RE" not in names, sorted(names)
    assert "_LINK0_CHECKPOINT_RE" not in names, sorted(names)
    assert "_OPT_PAREN_RE" not in names
    assert "_READFC_CONFLICTS" not in names
    # Zero direct Gaussian authority calls (G-PROD-1).
    bad_calls = {
        "resolve_write_chk",
        "coerce_section_lines",
        "normalize_gaussian_keyword",
        "parse_irc_route",
        "unsupported_method_finding",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            call_name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else "")
            )
            assert call_name not in bad_calls, (node.lineno, call_name)
    # Real delegation evidence to every policy stage.
    call_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                call_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                call_names.add(func.attr)
    for stage in (
        "require_gaussian_program",
        "require_standard_adapter",
        "require_no_qst",
        "require_standard_checkpoint_role",
        "require_artifact_checkpoint_role",
        "ensure_source_write_chk",
        "check_target_link0_core",
        "check_unsupported_method",
        "check_route_cores",
        "check_native_payload_cores",
    ):
        assert stage in call_names, stage


def test_readfc_fixed_vectors() -> None:
    import confflow.domain.errors as errors
    import confflow.producer.checkpoints as producer

    assert (
        producer._add_opt_option("B3LYP/6-31G* opt(tight)", "ReadFC", step_id="s1")
        == "B3LYP/6-31G* opt(tight,ReadFC)"
    )
    assert (
        producer._add_opt_option("B3LYP opt=tight", "ReadFC", step_id="s1")
        == "B3LYP opt=(tight,ReadFC)"
    )
    assert producer._add_opt_option("B3LYP opt", "ReadFC", step_id="s1") == "B3LYP opt=ReadFC"
    keyword = "B3LYP/6-31G* opt(ReadFC,tight)"
    assert producer._add_opt_option(keyword, "ReadFC", step_id="s1") == keyword
    for bad in ("B3LYP/6-31G* opt(CalcFC)", "B3LYP/6-31G* opt(CalcAll)"):
        try:
            producer._add_opt_option(bad, "ReadFC", step_id="s1")
        except errors.InvalidBindingError as exc:
            assert "opposed" in str(exc)
        else:
            raise AssertionError(bad)
    try:
        producer._add_opt_option("B3LYP/6-31G*", "ReadFC", step_id="s1")
    except errors.InvalidBindingError as exc:
        assert "no Opt route item" in str(exc)
    else:
        raise AssertionError("expected no-Opt refusal")


def test_strip_sp_edge_vectors() -> None:
    import confflow.producer.checkpoints as producer

    assert producer._strip_managed_items("B3LYP/6-31G* Opt(tight) Freq") == "b3lyp/6-31g*"
    assert producer._strip_managed_items("B3LYP SP") == "b3lyp"
    # Letters SP inside functional/basis stay untouched.
    assert producer._strip_managed_items("B3LYP/SP") == "b3lyp/sp"
    assert producer._strip_managed_items("B3LYP CSP") == "b3lyp csp"
    assert producer._strip_managed_items("B3LYP IRC(MaxPoints=20) Opt Freq SP") == "b3lyp"
