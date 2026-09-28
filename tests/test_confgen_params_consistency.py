#!/usr/bin/env python3

"""Consistency tests for the shared ConfGen parameter resolver (P0-C).

These lock the invariant that ``config-show``, workflow fingerprinting, and
validation interpret confgen parameters identically.  The execution-adapter
half of this file was removed with the V2/V3 workflow runtime in the
post-closure Architecture Diet.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from confflow.config.canonical.fingerprint import workflow_fingerprint
from confflow.config.canonical.types import GlobalOptions
from confflow.core.exceptions import ConfigurationError
from confflow.shared.confgen_params import resolve_confgen_params
from confflow.workflow.validation import validate_inputs_compatible


def _resolve(**params):
    return resolve_confgen_params(params, default_workers=4)


def _fingerprint(confgen_params):
    plan = SimpleNamespace(
        typed_global=GlobalOptions.from_mapping({}),
        steps=[{"name": "gen", "type": "confgen", "params": confgen_params}],
        predecessors={},
        execution_order=["gen"],
        terminal_steps=["gen"],
        step_dirnames=["step_01_gen"],
    )
    return workflow_fingerprint(plan)


def _write_xyz(path):
    path.write_text("1\nframe\nH 0 0 0\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Defaults & canonical values
# --------------------------------------------------------------------------


def test_defaults_are_canonical():
    resolved = _resolve()
    assert resolved["angle_step"] == 120
    assert resolved["bond_threshold"] == 1.15
    assert resolved["clash_threshold"] == 0.65
    assert resolved["rotate_side"] == "left"
    assert resolved["workers"] == 4


def test_clash_threshold_is_honored():
    assert _resolve(clash_threshold=0.50)["clash_threshold"] == 0.50


# --------------------------------------------------------------------------
# Alias handling
# --------------------------------------------------------------------------


def test_bond_threshold_alias_equivalence():
    assert _resolve(bond_threshold=1.2)["bond_threshold"] == 1.2
    assert _resolve(bond_multiplier=1.2)["bond_threshold"] == 1.2


def test_bond_threshold_aliases_same_value_accepted():
    assert _resolve(bond_threshold=1.2, bond_multiplier=1.2)["bond_threshold"] == 1.2


def test_bond_threshold_aliases_conflict_fails_fast():
    with pytest.raises(ConfigurationError):
        _resolve(bond_threshold=1.20, bond_multiplier=1.15)


def test_alias_representation_equivalence_is_accepted():
    # Same canonical value written differently must not be treated as a conflict.
    assert _resolve(bond_threshold="1.2", bond_multiplier=1.2)["bond_threshold"] == 1.2
    assert _resolve(workers="2", max_workers=2)["workers"] == 2
    assert _resolve(chains="1-2-3", chain=["1-2-3"])["chains"] == ["1-2-3"]


def test_alias_representation_equivalence_for_steps_and_angles():
    assert _resolve(chain_steps="180", steps=["180"])["chain_steps"] == ["180"]
    assert _resolve(chain_angles="60", angles=["60"])["chain_angles"] == ["60"]


def test_chain_aliases_equivalence():
    assert _resolve(chains=["1-2-3"])["chains"] == ["1-2-3"]
    assert _resolve(chain=["1-2-3"])["chains"] == ["1-2-3"]
    assert _resolve(chains="1-2-3")["chains"] == ["1-2-3"]
    with pytest.raises(ConfigurationError):
        _resolve(chains=["1-2"], chain=["1-2-3"])


def test_chain_steps_aliases_equivalence():
    assert _resolve(chain_steps=["180,180"])["chain_steps"] == ["180,180"]
    assert _resolve(steps=["180,180"])["chain_steps"] == ["180,180"]
    with pytest.raises(ConfigurationError):
        _resolve(chain_steps=["180"], steps=["90"])


def test_chain_angles_aliases_equivalence():
    assert _resolve(chain_angles=["60"])["chain_angles"] == ["60"]
    assert _resolve(angles=["60"])["chain_angles"] == ["60"]
    with pytest.raises(ConfigurationError):
        _resolve(chain_angles=["60"], angles=["90"])


def test_workers_aliases_equivalence():
    assert _resolve(workers=2)["workers"] == 2
    assert _resolve(max_workers=2)["workers"] == 2
    assert _resolve(max_parallel_jobs=2)["workers"] == 2
    with pytest.raises(ConfigurationError):
        _resolve(workers=2, max_workers=3)


def test_workers_must_be_positive():
    with pytest.raises(ConfigurationError):
        _resolve(workers=0)


def test_force_rotate_is_rejected():
    with pytest.raises(ConfigurationError):
        _resolve(force_rotate=[[1, 2]])


def test_empty_force_rotate_normalized_to_none():
    assert _resolve()["force_rotate"] is None
    assert _resolve(force_rotate=[])["force_rotate"] is None


# --------------------------------------------------------------------------
# Validation adapter
# --------------------------------------------------------------------------


def test_validation_uses_shared_resolver_conflict_detection():
    with pytest.raises(ConfigurationError):
        validate_inputs_compatible(
            ["a.xyz", "b.xyz"],
            {"bond_threshold": 1.20, "bond_multiplier": 1.15},
        )


def test_validation_accepts_chain_alias(tmp_path):
    first = _write_xyz(tmp_path / "a.xyz")
    second = _write_xyz(tmp_path / "b.xyz")
    validate_inputs_compatible([str(first), str(second)], {"chain": ["1"]})


# --------------------------------------------------------------------------
# Fingerprint adapter
# --------------------------------------------------------------------------


def test_fingerprint_changes_when_clash_threshold_changes():
    baseline = _fingerprint({})
    changed = _fingerprint({"clash_threshold": 0.50})
    assert baseline != changed


def test_fingerprint_ignores_alias_spelling():
    assert _fingerprint({"bond_multiplier": 1.2}) == _fingerprint({"bond_threshold": 1.2})
    assert _fingerprint({"chain": ["1-2-3"]}) == _fingerprint({"chains": ["1-2-3"]})
    assert _fingerprint({"steps": ["180"]}) == _fingerprint({"chain_steps": ["180"]})
    assert _fingerprint({"angles": ["60"]}) == _fingerprint({"chain_angles": ["60"]})
    assert _fingerprint({"max_workers": 2}) == _fingerprint({"workers": 2})


def test_fingerprint_rejects_conflicting_aliases():
    with pytest.raises(ConfigurationError):
        _fingerprint({"bond_threshold": 1.20, "bond_multiplier": 1.15})
