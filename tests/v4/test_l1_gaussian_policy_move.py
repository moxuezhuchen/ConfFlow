#!/usr/bin/env python3

"""L1-G1a pure Gaussian route mechanical move (rehearsal).

Asserts the ``producer/checkpoints -> programs/gaussian/checkpoint_policy``
move keeps old ``producer.checkpoints`` private paths observable: same
objects (``is``), unchanged signatures/bodies (AST-exact, verified here via
``inspect`` + fixed vectors), old ``__all__``/version/modes intact, private
``__module__`` now the definition site (policy), no science table copy, and
the fixed readfc/rcfc/conflict/unknown-option/SP-edge corpus behaves
identically (return or exception type/message).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

_TRIO = ("_add_opt_option", "_add_irc_rcfc", "_strip_managed_items")
_CONSTANTS = (
    "_QST_TOKEN_RE",
    "_IRC_MANAGED_RE",
    "_IRC_ITEM_RE",
    "_OPT_PAREN_RE",
    "_OPT_ASSIGN_RE",
    "_OPT_BARE_RE",
    "_FREQ_TOKEN_RE",
    "_SP_MANAGED_RE",
    "_READFC_CONFLICTS",
    "_RCFC_CONFLICTS",
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
    import confflow.programs.gaussian.path as gaussian_path
    import confflow.programs.gaussian.rendering as gaussian_rendering

    assert policy._irc_path is gaussian_path
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
    assert producer.REUSE_MODES == ("checkpoint", "readfc", "rcfc")
    # Private moved functions record the definition site; public surface does not move.
    import confflow.programs.gaussian.checkpoint_policy as policy

    for name in _TRIO:
        assert getattr(policy, name).__module__ == _POLICY_MODULE, name
        assert getattr(producer, name).__module__ == _POLICY_MODULE, name


def test_policy_imports_contain_no_producer_workflow_science() -> None:
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
    # No ProgramName/registry/stage knowledge in the G1a policy code
    # (docstring/prose exempt by construction: only Name/Call/Compare nodes count).
    code_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "ProgramName" not in code_names, sorted(code_names)
    assert "ExecutionRegistry" not in code_names
    assert "require_gaussian_program" not in code_names


def test_producer_keeps_g1b_gaussian_branches() -> None:
    # G1a must not fake-zero producer Gaussian dispatch (belongs to G1b).
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
    assert "ProgramName" in names
    assert "_QST_TOKEN_RE" in names
    assert "_LINK0_CHECKPOINT_RE" in names


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


def test_rcfc_fixed_vectors() -> None:
    import confflow.domain.errors as errors
    import confflow.producer.checkpoints as producer

    assert (
        producer._add_irc_rcfc("B3LYP/6-31G* IRC(MaxPoints=20)", step_id="t1")
        == "B3LYP/6-31G* IRC(MaxPoints=20,RCFC)"
    )
    assert producer._add_irc_rcfc("B3LYP/6-31G* IRC", step_id="t1") == "B3LYP/6-31G* IRC(RCFC)"
    keyword = "B3LYP/6-31G* IRC(RCFC,MaxPoints=20)"
    assert producer._add_irc_rcfc(keyword, step_id="t1") == keyword
    try:
        producer._add_irc_rcfc("B3LYP/6-31G* IRC(Forward)", step_id="t1")
    except errors.InvalidBindingError as exc:
        assert "explicit IRC direction" in str(exc)
    else:
        raise AssertionError("expected direction refusal")
    try:
        producer._add_irc_rcfc("B3LYP/6-31G* IRC(Bogus)", step_id="t1")
    except errors.InvalidBindingError as exc:
        assert "invalid IRC route" in str(exc)
    else:
        raise AssertionError("expected unknown-option refusal")
    try:
        producer._add_irc_rcfc("B3LYP/6-31G* opt", step_id="t1")
    except errors.InvalidBindingError as exc:
        assert "invalid IRC route" in str(exc)
    else:
        raise AssertionError("expected missing-IRC refusal")


def test_strip_sp_edge_vectors() -> None:
    import confflow.producer.checkpoints as producer

    assert producer._strip_managed_items("B3LYP/6-31G* Opt(tight) Freq") == "b3lyp/6-31g*"
    assert producer._strip_managed_items("B3LYP SP") == "b3lyp"
    # Letters SP inside functional/basis stay untouched.
    assert producer._strip_managed_items("B3LYP/SP") == "b3lyp/sp"
    assert producer._strip_managed_items("B3LYP CSP") == "b3lyp csp"
    assert producer._strip_managed_items("B3LYP IRC(MaxPoints=20) Opt Freq SP") == "b3lyp"
