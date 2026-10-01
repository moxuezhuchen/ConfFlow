#!/usr/bin/env python3

"""Second-review regressions for tasks A-F (seed identity, machine, checkpoints, cards)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.workflow.v4.compiler import compile_workflow

GLOBALS = {"charge": 0, "multiplicity": 1}

TORSIONS = [
    {
        "id": "t1",
        "bond": [1, 2],
        "model": "relative_rotation_grid",
        "angles": [0, 120, 240],
        "treatment": "enumerate",
    }
]


def _capped_intent(**top: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "globals": dict(GLOBALS),
        "steps": [
            {
                "card": "confgen@v1",
                "native": {
                    "schema_version": 3,
                    "torsions": copy.deepcopy(TORSIONS),
                    "sampling": {"cap": 50},
                },
            }
        ],
    }
    document.update(top)
    return document


def _seed(intent: dict[str, Any], **kwargs: Any) -> int:
    return compile_intent(intent, **kwargs)["steps"][0]["confgen"]["seed"]


class TestSeedInputSemantics:
    """Task A: provenance/display inert, edges canonical, order sorted."""

    def test_topology_provenance_inert(self) -> None:
        base = _capped_intent()
        prov_a = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "grouping": "each_entity",
                    "topology": {"add": [[1, 2]], "delete": [], "provenance": "label-a"},
                }
            }
        )
        prov_b = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "grouping": "each_entity",
                    "topology": {"add": [[1, 2]], "delete": [], "provenance": "label-b"},
                }
            }
        )
        assert _seed(prov_a) == _seed(prov_b)
        assert _seed(base) != _seed(prov_a)

    def test_topology_edge_order_canonical(self) -> None:
        forward = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "topology": {"add": [[1, 2]], "delete": []},
                }
            }
        )
        reversed_edges = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "topology": {"add": [[2, 1]], "delete": []},
                }
            }
        )
        assert _seed(forward) == _seed(reversed_edges)

    def test_topology_edge_change_moves_seed(self) -> None:
        one = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "topology": {"add": [[1, 2]], "delete": []},
                }
            }
        )
        two = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "topology": {"add": [[1, 3]], "delete": []},
                }
            }
        )
        assert _seed(one) != _seed(two)

    def test_input_display_and_defaults_canonical(self) -> None:
        plain = _capped_intent()
        described = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "grouping": "each_entity",
                    "description": "display only",
                }
            }
        )
        explicit_defaults = _capped_intent(
            inputs={
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "pairing": None,
                    "grouping": "each_entity",
                }
            }
        )
        assert _seed(plain) == _seed(described) == _seed(explicit_defaults)

    def test_explicit_step_permutation_same_seed(self) -> None:
        from confflow.producer.seeds import derive_seed

        # Two-step chain: permute the wire list but keep explicit bindings.
        two = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "steps": [
                    {
                        "id": "aaa",
                        "card": "confgen@v1",
                        "native": {
                            "schema_version": 3,
                            "torsions": copy.deepcopy(TORSIONS),
                            "sampling": {"cap": 50},
                        },
                        "bindings": {"structure": {"source": {"run": "structures"}}},
                    },
                    {
                        "id": "zzz",
                        "card": "opt@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ Opt"},
                        "bindings": {
                            "structure": {"source": {"step": "aaa", "port": "structures"}}
                        },
                    },
                ],
            }
        )
        forward = [str(step["id"]) for step in two["steps"]]
        assert forward == ["aaa", "zzz"]
        swapped = copy.deepcopy(two)
        swapped["steps"] = [swapped["steps"][1], swapped["steps"][0]]
        assert derive_seed("aaa", two) == derive_seed("aaa", swapped)
        assert derive_seed("zzz", two) == derive_seed("zzz", swapped)

    def test_enabled_and_completion_move_seed(self) -> None:
        from confflow.producer.seeds import derive_seed

        wire = compile_intent(_capped_intent())
        assert wire["steps"][0].get("enabled", True) is True
        disabled = copy.deepcopy(wire)
        disabled["steps"][0]["enabled"] = False
        assert derive_seed("confgen_1", wire) != derive_seed("confgen_1", disabled)
        completed = copy.deepcopy(wire)
        completed["steps"][0]["completion"] = {"mode": "allow_partial"}
        assert derive_seed("confgen_1", wire) != derive_seed("confgen_1", completed)


class TestPartialMachineResources:
    """Task B: partial requests fill schema defaults before the helper."""

    PROFILE = {"name": "node-b", "total_cores": 16, "total_memory": "32GB"}

    def _intent_with(self, resources: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "steps": [
                {
                    "card": "opt@v1",
                    "program": "orca",
                    "native": {"keyword": "B3LYP D3BJ Opt"},
                    "resources": dict(resources),
                }
            ],
        }

    def test_partial_cores_matches_no_profile_digest_and_seed(self) -> None:
        partial = self._intent_with({"cores_per_item": 2})
        full = self._intent_with({"cores_per_item": 2, "memory_per_item": "1GiB"})
        with_profile = compile_intent(partial, machine_profile=self.PROFILE)
        without_profile = compile_intent(full)
        assert (
            compile_workflow(with_profile).plan.definition_digest
            == compile_workflow(without_profile).plan.definition_digest
        )
        assert with_profile["steps"][0]["scheduler"] == {"max_parallel_items": 8}

    def test_partial_memory_matches_no_profile_digest(self) -> None:
        partial = self._intent_with({"memory_per_item": "2GiB"})
        full = self._intent_with({"cores_per_item": 1, "memory_per_item": "2GiB"})
        with_profile = compile_intent(partial, machine_profile=self.PROFILE)
        without_profile = compile_intent(full)
        assert (
            compile_workflow(with_profile).plan.definition_digest
            == compile_workflow(without_profile).plan.definition_digest
        )
        assert with_profile["steps"][0]["scheduler"] == {"max_parallel_items": 16}

    def test_partial_capped_confgen_seed_stable_with_profile(self) -> None:
        def build(resources: Any) -> dict[str, Any]:
            return {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "steps": [
                    {
                        "card": "confgen@v1",
                        "native": {
                            "schema_version": 3,
                            "torsions": copy.deepcopy(TORSIONS),
                            "sampling": {"cap": 50},
                        },
                        "resources": resources,
                    }
                ],
            }

        explicit = build({"cores_per_item": 2, "memory_per_item": "1GiB"})
        partial = build({"cores_per_item": 2})
        assert (
            compile_intent(explicit)["steps"][0]["confgen"]["seed"]
            == compile_intent(partial)["steps"][0]["confgen"]["seed"]
        )
        assert (
            compile_intent(explicit, machine_profile=self.PROFILE)["steps"][0]["confgen"]["seed"]
            == compile_intent(partial, machine_profile=self.PROFILE)["steps"][0]["confgen"]["seed"]
        )


class TestMultiCheckpointWithStochastic:
    """Task C: provisional seeds let multi-edge wiring compile."""

    def test_two_checkpoint_edges_plus_capped_confgen(self) -> None:
        intent = {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "steps": [
                {
                    "id": "s_freq",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G* freq"},
                },
                {
                    "id": "s_opt",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G* opt"},
                    "reuse_checkpoint": {"step": "s_freq", "mode": "checkpoint"},
                },
                {
                    "id": "s_opt2",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G* opt(tight)"},
                    "reuse_checkpoint": {"step": "s_opt", "mode": "readfc"},
                },
                {
                    "id": "capped",
                    "card": "confgen@v1",
                    "from": "run:structures",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 10},
                    },
                },
            ],
        }
        document = compile_intent(intent)
        by_id = {step["id"]: step for step in document["steps"]}
        assert by_id["s_opt"]["bindings"]["checkpoint"]["cardinality"] == "one"
        assert by_id["s_opt2"]["bindings"]["checkpoint"]["cardinality"] == "one"
        assert "ReadFC" in by_id["s_opt2"]["calculation"]["native"]["keyword"]
        assert isinstance(by_id["capped"]["confgen"]["seed"], int)
        assert (
            by_id["capped"]["annotations"]["producer_resolution"]["seed_scope"] == "native_sampling"
        )
        assert by_id["capped"]["annotations"]["producer_resolution"]["seed_source"] == "derived"
        assert by_id["capped"]["annotations"]["producer_resolution"][
            "seed_identity_digest"
        ].startswith("sha256:")
        assert compile_workflow(document).ok

    def test_explicit_seed_preserved_through_checkpoints(self) -> None:
        intent = {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "steps": [
                {
                    "id": "s_freq",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G* freq"},
                },
                {
                    "id": "s_opt",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G* opt"},
                    "reuse_checkpoint": {"step": "s_freq", "mode": "checkpoint"},
                },
                {
                    "id": "capped",
                    "card": "confgen@v1",
                    "from": "run:structures",
                    "seed": 12345,
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 10},
                    },
                },
            ],
        }
        document = compile_intent(intent)
        by_id = {step["id"]: step for step in document["steps"]}
        assert by_id["capped"]["confgen"]["seed"] == 12345
        assert by_id["capped"]["annotations"]["producer_resolution"]["seed_source"] == "explicit"


class TestReusableCards:
    """Task D: named cards expand mechanically with step override winning."""

    def test_named_card_reuse(self) -> None:
        intent = {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "cards": {
                "low_opt": {
                    "card": "opt@v1",
                    "program": "orca",
                    "native": {"keyword": "B3LYP D3BJ Opt"},
                    "resources": {"cores_per_item": 2},
                }
            },
            "steps": [
                {"card": "low_opt"},
                {"card": "low_opt", "native": {"keyword": "B3LYP D3BJ Opt Tight"}},
            ],
        }
        document = compile_intent(intent)
        assert [step["id"] for step in document["steps"]] == ["opt_1", "opt_2"]
        assert document["steps"][0]["calculation"]["native"] == {"keyword": "B3LYP D3BJ Opt"}
        assert document["steps"][1]["calculation"]["native"] == {"keyword": "B3LYP D3BJ Opt Tight"}
        assert (
            document["steps"][0]["resources"]
            == {
                "cores_per_item": 2,
                "memory_per_item": "1GiB",
            }
            or document["steps"][0]["resources"].get("cores_per_item") == 2
        )
        assert compile_workflow(document).ok

    def test_unknown_card_ref_rejected(self) -> None:
        with pytest.raises(IntentCompilationError, match="unknown card"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "steps": [{"card": "missing_card"}],
                }
            )

    def test_card_chain_cycle_rejected(self) -> None:
        with pytest.raises(IntentCompilationError, match="cycle"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "cards": {
                        "aaa": {"card": "bbb", "program": "orca"},
                        "bbb": {"card": "aaa", "program": "orca"},
                    },
                    "steps": [{"card": "aaa", "native": {"keyword": "X Opt"}}],
                }
            )

    def test_bad_card_type_version_rejected(self) -> None:
        with pytest.raises(IntentCompilationError):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "cards": {"bad": {"card": "banana@v1", "program": "orca"}},
                    "steps": [{"card": "bad", "native": {"keyword": "X Opt"}}],
                }
            )


class TestRoleCards:
    """Task E: recipe roles map onto reusable cards without keyword edits."""

    def _cards(self) -> dict[str, Any]:
        return {
            "low_ts": {
                "card": "ts@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D3 OptTS"},
            },
            "low_tsfreq": {
                "card": "ts_freq@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D3 Freq"},
            },
            "low_opt": {
                "card": "opt@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D3 Opt"},
            },
            "low_freq": {
                "card": "freq@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D3 Freq"},
            },
            "low_irc": {
                "card": "irc@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D3 IRC"},
            },
            "high_sp": {
                "card": "sp@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D3 SP"},
            },
        }

    def test_role_cards_cover_tspes(self) -> None:
        intent = {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "tspes",
            "cards": self._cards(),
            "role_cards": {
                "ts": "low_ts",
                "freq": "low_freq",
                "ts_freq": "low_tsfreq",
                "opt": "low_opt",
                "irc": "low_irc",
                "sp": "high_sp",
            },
            "steps": [],
        }
        # step-id key wins for ts_freq; role key covers the rest.
        document = compile_intent(intent)
        assert len(document["steps"]) == 8
        by_id = {step["id"]: step for step in document["steps"]}
        assert by_id["ts"]["calculation"]["native"] == {"keyword": "wB97X-D3 OptTS"}
        assert by_id["ts_sp"]["calculation"]["native"] == {"keyword": "wB97X-D3 SP"}
        assert by_id["endpoint_sp"]["calculation"]["native"] == {"keyword": "wB97X-D3 SP"}
        assert "wB97X-D3" in by_id["irc"]["calculation"]["native"]["keyword"]
        assert compile_workflow(document).ok

    def test_role_cards_require_full_coverage(self) -> None:
        with pytest.raises(IntentCompilationError, match="missing"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "recipe": "tspes",
                    "cards": self._cards(),
                    "role_cards": {"ts": "low_ts"},
                    "steps": [],
                }
            )

    def test_ts_freq_role_with_plain_freq_refused(self) -> None:
        with pytest.raises(IntentCompilationError, match="ts_freq"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "recipe": "tspes",
                    "cards": self._cards(),
                    "role_cards": {
                        "ts": "low_ts",
                        "freq": "low_freq",
                        "opt": "low_opt",
                        "irc": "low_irc",
                        "sp": "high_sp",
                    },
                    "steps": [],
                }
            )

    def test_family_card_native_by_role(self) -> None:
        intent = {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "tspes",
            "cards": {
                "low_family": {
                    "card": "opt@v1",
                    "program": "orca",
                    "native_by_role": {
                        "ts": {"keyword": "wB97X-D3 OptTS"},
                        "freq": {"keyword": "wB97X-D3 Freq"},
                        "opt": {"keyword": "wB97X-D3 Opt"},
                        "irc": {"keyword": "wB97X-D3 IRC"},
                        "sp": {"keyword": "wB97X-D3 SP"},
                    },
                },
                "low_tsfreq": {
                    "card": "ts_freq@v1",
                    "program": "orca",
                    "native": {"keyword": "wB97X-D3 Freq"},
                },
            },
            "role_cards": {
                "ts": "low_family",
                "freq": "low_family",
                "ts_freq": "low_tsfreq",
                "opt": "low_family",
                "irc": "low_family",
                "sp": "low_family",
            },
            "steps": [],
        }
        document = compile_intent(intent)
        by_id = {step["id"]: step for step in document["steps"]}
        assert by_id["ts"]["calculation"]["native"] == {"keyword": "wB97X-D3 OptTS"}
        assert by_id["endpoint_opt"]["calculation"]["native"] == {"keyword": "wB97X-D3 Opt"}
        assert compile_workflow(document).ok


class TestMinimalTwoCardTspes:
    """Review acceptance: two user cards drive all 8 TSPES nodes verbatim."""

    def _two_cards(self) -> dict[str, Any]:
        return {
            "low": {
                "card": "opt@v1",
                "program": "orca",
                "native_by_role": {
                    "ts": {"keyword": "r2SCAN-3c OptTS"},
                    "ts_freq": {"keyword": "r2SCAN-3c Freq"},
                    "freq": {"keyword": "r2SCAN-3c Freq"},
                    "opt": {"keyword": "r2SCAN-3c Opt"},
                    "irc": {
                        "keyword": "r2SCAN-3c IRC",
                        "irc": {"direction": "both"},
                    },
                },
            },
            "high": {
                "card": "sp@v1",
                "program": "orca",
                "native": {"keyword": "wB97X-D4 def2-TZVP SP"},
            },
        }

    def _role_cards(self) -> dict[str, str]:
        return {"ts": "low", "freq": "low", "opt": "low", "irc": "low", "sp": "high"}

    def test_two_cards_cover_all_8_nodes(self) -> None:
        document = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1},
                "recipe": "tspes",
                "cards": self._two_cards(),
                "role_cards": self._role_cards(),
                "steps": [],
            }
        )
        assert len(document["steps"]) == 8
        by_id = {step["id"]: step for step in document["steps"]}
        # High SP card shared twice, verbatim.
        assert by_id["ts_sp"]["calculation"]["native"] == {"keyword": "wB97X-D4 def2-TZVP SP"}
        assert by_id["endpoint_sp"]["calculation"]["native"] == {"keyword": "wB97X-D4 def2-TZVP SP"}
        # Low family variants selected per stage, verbatim, no demo leakage.
        assert by_id["ts"]["calculation"]["native"] == {"keyword": "r2SCAN-3c OptTS"}
        assert by_id["ts_freq"]["calculation"]["native"] == {"keyword": "r2SCAN-3c Freq"}
        assert by_id["endpoint_freq"]["calculation"]["native"] == {"keyword": "r2SCAN-3c Freq"}
        assert by_id["endpoint_opt"]["calculation"]["native"] == {"keyword": "r2SCAN-3c Opt"}
        assert by_id["irc"]["calculation"]["native"]["keyword"] == "r2SCAN-3c IRC"
        assert by_id["irc"]["calculation"]["native"]["irc"] == {"direction": "both"}
        for step in document["steps"]:
            for keyword in ("B3LYP", "D3BJ"):
                assert keyword not in str(step.get("calculation", {}).get("native", {}))
        # Purpose checks follow the stage, not the family base card.
        assert by_id["ts"]["calculation"]["checks"] == [
            "normal_termination",
            "geometry_required",
        ]
        ts_freq_calc = by_id["ts_freq"]["calculation"]
        assert "imaginary_frequency_count" in ts_freq_calc["checks"]
        assert ts_freq_calc["check_params"] == {"imaginary_frequency_count": {"expected": 1}}
        endpoint_freq_calc = by_id["endpoint_freq"]["calculation"]
        assert endpoint_freq_calc["checks"] == [
            "normal_termination",
            "frequencies_required",
        ]
        assert by_id["irc"]["calculation"]["result_profile"] == "path_endpoints"
        assert compile_workflow(document).ok

    def test_recipe_cards_normal_mode_matches_role_cards(self) -> None:
        via_roles = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1},
                "recipe": "tspes",
                "cards": self._two_cards(),
                "role_cards": self._role_cards(),
                "steps": [],
            }
        )
        via_recipe_cards = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1},
                "recipe": "tspes",
                "cards": self._two_cards(),
                "recipe_cards": {"low_level": "low", "single_point": "high"},
                "steps": [],
            }
        )
        assert [step["id"] for step in via_recipe_cards["steps"]] == [
            step["id"] for step in via_roles["steps"]
        ]
        for step in via_recipe_cards["steps"]:
            if step.get("calculation") is not None:
                other = next(item for item in via_roles["steps"] if item["id"] == step["id"])
                assert step["calculation"] == other["calculation"]
        assert compile_workflow(via_recipe_cards).ok

    def test_recipe_cards_require_globals(self) -> None:
        with pytest.raises(IntentCompilationError, match="explicit 'globals'"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "recipe": "tspes",
                    "cards": self._two_cards(),
                    "recipe_cards": {"low_level": "low", "single_point": "high"},
                    "steps": [],
                }
            )

    def test_missing_family_variant_fails(self) -> None:
        cards = self._two_cards()
        del cards["low"]["native_by_role"]["ts_freq"]
        with pytest.raises(IntentCompilationError, match="ts_freq"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": {"charge": 0, "multiplicity": 1},
                    "recipe": "tspes",
                    "cards": cards,
                    "role_cards": self._role_cards(),
                    "steps": [],
                }
            )

    def test_named_card_provenance_preserved(self) -> None:
        document = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1},
                "recipe": "tspes",
                "cards": self._two_cards(),
                "recipe_cards": {"low_level": "low", "single_point": "high"},
                "steps": [],
            }
        )
        by_id = {step["id"]: step for step in document["steps"]}
        ts_resolution = by_id["ts"]["annotations"]["producer_resolution"]
        assert ts_resolution["named_card"] == "low"
        assert ts_resolution["role_card"] == "low"
        assert ts_resolution["family_variant"] == "ts"
        assert ts_resolution["purpose"] == "ts"
        assert ts_resolution["recipe_assignment"] is True
        ts_freq_resolution = by_id["ts_freq"]["annotations"]["producer_resolution"]
        assert ts_freq_resolution["family_variant"] == "ts_freq"
        assert ts_freq_resolution["purpose"] == "ts_freq"
        sp_resolution = by_id["ts_sp"]["annotations"]["producer_resolution"]
        assert sp_resolution["named_card"] == "high"
        assert sp_resolution["purpose"] == "sp"


class TestFinalUxAudits:
    """Explicit freeze clear and explicit-seed audit on non-stochastic steps."""

    def test_globals_freeze_empty_list_allowed(self) -> None:
        document = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1, "freeze": []},
                "steps": [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ Opt"},
                    }
                ],
            }
        )
        assert document["global"]["scientific_defaults"]["freeze"] == []
        assert compile_workflow(document).ok

    def test_explicit_seed_on_uncapped_confgen_audited(self) -> None:
        document = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "steps": [
                    {
                        "card": "confgen@v1",
                        "seed": 999,
                        "native": {
                            "schema_version": 3,
                            "torsions": copy.deepcopy(TORSIONS),
                        },
                    }
                ],
            }
        )
        block = document["steps"][0]["confgen"]
        assert block["seed"] == 999
        resolution = document["steps"][0]["annotations"]["producer_resolution"]
        assert resolution["seed_source"] == "explicit"
        assert resolution["seed_scope"] == "native_sampling"
        assert compile_workflow(document).ok
