#!/usr/bin/env python3

"""E1 rehearsal: monomer_conformers (13th recipe) catalog + wiring + protocol E2E.

Scope: catalog identity (order 130, 13 entries, old-12 bytes unchanged,
14 cards unchanged), compile->plan wiring (preopt product -> confgen ->
dedup), template protocol under a fake external ORCA adapter with the real
ConfGen/dedup engines, legal user-replacement scope, and read-only JD
contract consumption. No real ORCA/xtb is invoked; no GFN energy claim is
made (fake only proves protocol + product flow).
"""

from __future__ import annotations

import copy
import glob
import json
import os
from pathlib import Path
from typing import Any

import pytest

from confflow.config.contract_schemas import RECIPE_CATALOG_SCHEMA
from confflow.domain import FrozenDict
from confflow.domain.canonical import canonical_json_bytes, canonical_sha256
from confflow.execution.process import NativeProcessSupervisor
from confflow.producer.cards import CARD_TYPES
from confflow.producer.recipes import (
    RECIPE_IDS_V4,
    build_recipe_catalog_v4,
    get_recipe_v4,
)
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.document import SCHEMA_ID
from confflow.workflow.v4.parser import parse_workflow_document

# R2.2 声明重钉：目录剩 7 项（irc/qst2/qst3/neb/goat/tspes 退役）；
# 未动的前 6 个 recipe 字节不变（自身内容未改，只退役邻居）。
OLD6_SHA = "8b62e352d4743202137423104b31895666d0ef9baab6f264f34a5a874be3190b"
OLD6_IDS = (
    "optimize",
    "single_point",
    "frequency",
    "opt_freq",
    "transition_state",
    "confgen_torsion",
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"

# 9-heavy-atom propyl-cyclohexane-like seed matching the recipe example
# (isolated 6-ring 1-6 + acyclic tail 7-9; torsion 7-8 keeps the 6-7-8-9
# dihedral frame measurable). Coordinates mirror the frozen chair_A_6
# template plus a radial 1.54 A tail.
PROPYL_XYZ = """9
propyl-cyclohexane example seed (USER MUST CONFIRM/REPLACE axes)
C 1.451926 -0.000000 0.256667
C 0.725963 1.257405 -0.256667
C -0.725963 1.257405 0.256667
C -1.451926 0.000000 -0.256667
C -0.725963 -1.257405 0.256667
C 0.725963 -1.257405 -0.256667
C 2.968413 -0.000000 0.524746
C 4.484900 -0.000000 0.792826
C 4.984900 1.400000 1.092826
"""


def _recipe() -> dict[str, Any]:
    return get_recipe_v4("monomer_conformers")


class TestCatalogIdentity:
    def test_monomer_second_last_in_frozen_order(self) -> None:
        # N4 声明新增：ensemble_refine appended last；monomer 让出末位。
        assert RECIPE_IDS_V4[-1] == "ensemble_refine"
        assert RECIPE_IDS_V4[-2] == "monomer_conformers"
        assert list(RECIPE_IDS_V4) == list(OLD6_IDS) + ["monomer_conformers", "ensemble_refine"]
        catalog = build_recipe_catalog_v4()
        assert [r["id"] for r in catalog["recipes"]] == list(RECIPE_IDS_V4)
        assert catalog["schema"] == RECIPE_CATALOG_SCHEMA
        assert catalog["workflow_schema_version"] == SCHEMA_ID

    def test_order_130_and_category(self) -> None:
        recipe = _recipe()
        assert recipe["order"] == 130
        assert recipe["category"] == "Conformers"
        assert recipe["label"] == "Monomer Conformers (XTB2 preopt)"

    def test_required_is_editor_hint_subset_of_exposed(self) -> None:
        recipe = _recipe()
        assert set(recipe["required_fields"]) <= set(recipe["exposed_fields"])
        assert set(recipe["required_fields"]) == {
            "calc.program",
            "calc.native",
            "confgen.v3.rings",
            "confgen.v3.torsions",
        }

    def test_old6_bytes_unchanged(self) -> None:
        catalog = build_recipe_catalog_v4()
        old_only = {
            "schema": catalog["schema"],
            "workflow_schema_version": catalog["workflow_schema_version"],
            "label": catalog["label"],
            "recipes": [r for r in catalog["recipes"] if r["id"] in OLD6_IDS],
        }
        assert [r["id"] for r in old_only["recipes"]] == list(OLD6_IDS)
        assert canonical_sha256(old_only) == OLD6_SHA

    def test_9_cards_unchanged(self) -> None:
        assert len(CARD_TYPES) == 9
        assert tuple(sorted(CARD_TYPES)) == (
            "confgen",
            "deduplicate",
            "freq",
            "opt",
            "opt_freq",
            "refine",
            "sp",
            "ts",
            "ts_freq",
        )


class TestCompilePlan:
    def test_compiles_to_three_planned_steps(self) -> None:
        recipe = _recipe()
        parsed = parse_workflow_document(recipe["document"])
        assert parsed.definition is not None
        compiled = compile_workflow(parsed)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        assert compiled.plan is not None
        assert [s.step_id for s in compiled.plan.steps] == ["preopt", "confgen", "dedup"]

    def test_product_wiring_ports(self) -> None:
        recipe = _recipe()
        by_id = {s["id"]: s for s in recipe["document"]["steps"]}
        assert by_id["preopt"]["bindings"] == {"structure": {"source": {"run": "structures"}}}
        assert by_id["confgen"]["bindings"] == {
            "structure": {"source": {"step": "preopt", "port": "structures"}}
        }
        assert by_id["dedup"]["bindings"] == {
            "structure": {"source": {"step": "confgen", "port": "structures"}}
        }
        assert by_id["preopt"]["calculation"]["native"] == {"keyword": "XTB2 Opt"}
        assert by_id["preopt"]["calculation"]["program"] == "orca"
        assert by_id["dedup"]["transform"] == {"kind": "deduplicate", "native": {}}
        compiled = compile_workflow(recipe["document"])
        assert compiled.ok
        edges = {
            (e.target_step_id, e.target_port.name): (
                e.source.step_id if hasattr(e.source, "step_id") else None,
                e.source.port if hasattr(e.source, "port") else None,
            )
            for e in compiled.plan.graph.edges
        }
        # Real binding-graph edges (graph.py/plan.py authority); top-level
        # run input must never feed confgen directly.
        assert edges[("confgen", "structure")][0] == "preopt"
        assert edges[("dedup", "structure")][0] == "confgen"

    def test_required_fields_are_not_a_compiler_gate(self) -> None:
        # ROOT-REVIEW correction: required_fields is an editor prompt. An
        # empty-axes copy still compiles (identity scope) and must never be
        # presented as a success signal; the E2E below always uses real axes.
        empty = copy.deepcopy(_recipe()["document"])
        empty["steps"][1]["confgen"] = {"schema_version": 3}
        compiled = compile_workflow(empty)
        assert compiled.ok
        assert _recipe()["required_fields"] != []


class TestUserReplacementScope:
    def test_replace_axes_via_wire_copy_recompiles(self) -> None:
        # Legal scope: copy the wire document, replace both axes, recompile
        # through the real parser/compiler. No dotted-path override API is
        # invented; unknown scopes fail closed below.
        doc = copy.deepcopy(_recipe()["document"])
        doc["steps"][1]["confgen"]["rings"] = [
            {"id": "r9", "atoms": [1, 2, 3, 4, 5, 6], "forms": ["C_0"], "treatment": "enumerate"}
        ]
        doc["steps"][1]["confgen"]["torsions"] = [
            {
                "id": "t9",
                "atoms": [6, 7, 8, 9],
                "model": "absolute_dihedral_grid",
                "angles": [0, 180],
                "treatment": "enumerate",
            }
        ]
        compiled = compile_workflow(doc)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]

    def test_illegal_axes_fail_closed(self) -> None:
        from pydantic import ValidationError

        from confflow.workflow.v4.confgen_schema import ConfgenModelV3

        # Ring template/size mismatch.
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "rings": [{"id": "r1", "atoms": [1, 2, 3, 4], "templates": ["chair_A_6"]}],
                }
            )
        # Torsion periodic duplicate.
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "t1",
                            "bond": [7, 8],
                            "model": "relative_rotation_grid",
                            "angles": [0, 360],
                            "treatment": "enumerate",
                        }
                    ],
                }
            )
        # Unknown field is forbidden.
        bad = copy.deepcopy(_recipe()["document"])
        bad["steps"][1]["confgen"]["no_such_field"] = 1
        assert not compile_workflow(bad).ok
        # sampling.cap without seed is refused.
        bad2 = copy.deepcopy(_recipe()["document"])
        bad2["steps"][1]["confgen"]["sampling"] = {"cap": 2}
        assert not compile_workflow(bad2).ok

    def test_intent_recipe_lane_requires_preopt_science(self) -> None:
        from confflow.producer.intent import INTENT_SCHEMA, compile_intent

        with pytest.raises(Exception, match="missing"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": {"charge": 0, "multiplicity": 1},
                    "recipe": "monomer_conformers",
                    "steps": [],
                }
            )
        wire = compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": {"charge": 0, "multiplicity": 1},
                "recipe": "monomer_conformers",
                "steps": [
                    {
                        "id": "preopt",
                        "card": "opt@v1",
                        "program": "orca",
                        "native": {"keyword": "XTB2 Opt"},
                    }
                ],
            }
        )
        by_id = {s["id"]: s for s in wire["steps"]}
        assert by_id["preopt"]["calculation"]["native"] == {"keyword": "XTB2 Opt"}
        # ConfGen base passes through with the example axes intact.
        assert by_id["confgen"]["confgen"]["rings"][0]["id"] == "r1"


def _install_fake_orca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str = "success_opt"
) -> Path:
    bin_dir = tmp_path / "bin-orca"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    count = tmp_path / "orca.count"
    count.write_text("")
    wrapper.write_text(
        "#!/bin/sh\n"
        'base=$(basename "$1")\n'
        f'printf \'%s\\n\' "$base" >> "{count}"\n'
        f'exec python3 "{FAKE_ORCA}" "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_MODE", mode)
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & 0o022)
    return count


class TestFakeProtocolE2E:
    def test_compile_plan_fake_orca_then_real_engines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fake external ORCA (protocol only) + real confgen/dedup engines."""
        from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
        from confflow.workflow.v4.assembly import RunInputs

        count = _install_fake_orca(tmp_path, monkeypatch)
        document = copy.deepcopy(_recipe()["document"])
        compiled = compile_workflow(document)
        assert compiled.ok and compiled.plan is not None
        structures = import_xyz(PROPYL_XYZ, source_name="propyl.xyz")
        assert len(structures) == 1
        orig_x = tuple(structures)[0].coordinates[0][0]
        run_root = str(tmp_path / "run")
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=json.loads(canonical_json_bytes(document).decode("utf-8")),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=run_root,
            )
        )
        assert report.status == "completed", [
            (s.step_id, getattr(s, "status", None), [str(d) for d in getattr(s, "diagnostics", [])])
            for s in report.step_results
        ]
        by_id = {s.step_id: s for s in report.step_results}
        assert str(by_id["preopt"].status) == "StepStatus.COMPLETED"
        assert str(by_id["confgen"].status) == "StepStatus.COMPLETED"
        assert str(by_id["dedup"].status) == "StepStatus.COMPLETED"
        # Only the preopt calculation hits the external adapter.
        assert len([line for line in count.read_text().splitlines() if line.strip()]) == 1
        # Template protocol preserved verbatim (offline rendering proof, not physics).
        inp = glob.glob(str(tmp_path / "run/steps/preopt/**/*.inp"), recursive=True)[0]
        text = Path(inp).read_text()
        assert text.splitlines()[0].strip() == "! XTB2 Opt"
        assert "%pal" in text and "%maxcore" in text and "* xyz" in text
        # Fake output differs from input (source identification) without any
        # GFN energy claim.
        produced = glob.glob(str(tmp_path / "run/steps/preopt/**/*.xyz"), recursive=True)[0]
        lines = Path(produced).read_text().splitlines()
        first_x = float(lines[2].split()[1])
        assert abs(first_x - (orig_x + 0.01)) < 1e-6
        # ConfGen consumed the preopt product (driving id names preopt), and
        # published the full 3-member grid; dedup kept all 3.
        ensemble = glob.glob(
            str(tmp_path / "run/steps/confgen/**/ensemble_report.json"), recursive=True
        )[0]
        data = json.loads(Path(ensemble).read_text())
        assert str(data["driving_id"]).startswith("preopt:")
        assert data["counts"]["published"] == 3


WATER_PAIR_XYZ = """3
water a
O 0.0 0.0 0.0
H 0.76 0.59 0.0
H 0.76 -0.59 0.0
3
water b
O 0.5 0.5 0.5
H 1.26 1.09 0.5
H 1.26 -0.09 0.5
"""


def _freq_filter_document() -> dict[str, Any]:
    return {
        "schema": SCHEMA_ID,
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "freq",
                "executor": "calculation",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "calculation": {
                    "program": "orca",
                    "role": "freq",
                    "execution_adapter": "standard",
                    "result_profile": "standard",
                    "native": {"keyword": "B3LYP D3BJ Freq"},
                    "checks": ["normal_termination", "frequencies_required"],
                    "recovery": {"profile": "none"},
                },
            },
            {
                "id": "sel",
                "executor": "structure_transform",
                "bindings": {
                    "structure": {"source": {"step": "freq", "port": "structures"}},
                    "results": {"source": {"step": "freq", "port": "results"}},
                },
                "transform": {
                    "kind": "filter",
                    "native": {"max_imaginary_count": 0, "lowest_n": 1},
                },
            },
        ],
    }


class TestFilterEnergyEndToEnd:
    def test_compile_plan_fake_freq_then_real_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real compile->plan->fake-ORCA freq->filter energy selection flow."""
        from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
        from confflow.workflow.v4.assembly import RunInputs

        count = _install_fake_orca(tmp_path, monkeypatch, "success_freq")
        document = _freq_filter_document()
        compiled = compile_workflow(document)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        assert compiled.plan is not None
        edges = {
            (e.target_step_id, e.target_port.name): e.source_step_id
            for e in compiled.plan.graph.edges
        }
        assert edges[("sel", "structure")] == "freq"
        assert edges[("sel", "results")] == "freq"
        structures = import_xyz(WATER_PAIR_XYZ, source_name="pair.xyz")
        assert len(structures) == 2
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=json.loads(canonical_json_bytes(document).decode("utf-8")),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=str(tmp_path / "run"),
            )
        )
        assert report.status == "completed", [
            (s.step_id, getattr(s, "status", None), [str(d) for d in getattr(s, "diagnostics", [])])
            for s in report.step_results
        ]
        by_id = {s.step_id: s for s in report.step_results}
        assert str(by_id["freq"].status) == "StepStatus.COMPLETED"
        assert str(by_id["sel"].status) == "StepStatus.COMPLETED"
        # Both waters ran freq; identical fake energies tie, so lowest_n keeps
        # the first in id order and the imaginary gate passes real modes.
        assert len(by_id["freq"].structures) == 2
        assert len(by_id["sel"].structures) == 1
        assert len([line for line in count.read_text().splitlines() if line.strip()]) == 2


class TestJDConsumption:
    def test_jd_parser_consumes_8_catalog_readonly(self) -> None:
        jd_src = os.environ.get("JOBDESK_V2_SRC", "/opt/jobdesk-v2-v4/src")
        if not Path(jd_src).is_dir():
            pytest.skip("JobDesk checkout absent")
        os.environ["JOBDESK_V2_ALLOW_ANY_SHA"] = "1"
        sys_path_added = False
        import sys

        if jd_src not in sys.path:
            sys.path.insert(0, jd_src)
            sys_path_added = True
        try:
            from jobdesk_v2.application.editor.contract.v4 import parse_v4_contract_bytes

            from confflow.producer.contract import generate_contract_bytes

            payload = generate_contract_bytes(
                producer_version="e1-rehearsal", producer_commit="proto", producer_dirty=True
            )
            verified = parse_v4_contract_bytes(payload)
            assert verified.is_v4_capable
            # N4 声明：目录 7→8（ensemble_refine appended last）。
            assert len(verified.recipe_ids) == 8
            assert "monomer_conformers" in verified.recipe_ids
            assert "ensemble_refine" in verified.recipe_ids
        finally:
            if sys_path_added:
                sys.path.remove(jd_src)


def _ensemble_recipe() -> dict[str, Any]:
    return get_recipe_v4("ensemble_refine")


class TestEnsembleRefineCatalog:
    def test_is_8th_in_frozen_order(self) -> None:
        assert RECIPE_IDS_V4[-1] == "ensemble_refine"
        assert list(RECIPE_IDS_V4).index("ensemble_refine") == len(RECIPE_IDS_V4) - 1
        catalog = build_recipe_catalog_v4()
        assert [r["id"] for r in catalog["recipes"]] == list(RECIPE_IDS_V4)

    def test_order_140_and_filter_fields_editable(self) -> None:
        recipe = _ensemble_recipe()
        assert recipe["order"] == 140
        assert recipe["category"] == "Conformers"
        assert set(recipe["required_fields"]) <= set(recipe["exposed_fields"])
        assert "filter.energy_window_kcal" in recipe["exposed_fields"]
        assert "filter.lowest_n" in recipe["exposed_fields"]
        assert set(recipe["required_fields"]) >= {
            "calc.program",
            "calc.native",
            "filter.energy_window_kcal",
            "filter.lowest_n",
        }

    def test_description_carries_n2_and_md_notes(self) -> None:
        text = _ensemble_recipe()["description"]
        assert "N2" in text and "deduplicate" in text and "opt" in text
        assert "thin" in text and "no frame-count" in text


class TestEnsembleRefineCompilePlan:
    def test_compiles_to_five_planned_steps(self) -> None:
        recipe = _ensemble_recipe()
        compiled = compile_workflow(recipe["document"])
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        assert compiled.plan is not None
        assert [s.step_id for s in compiled.plan.steps] == [
            "dedup",
            "opt",
            "refine",
            "freq",
            "select",
        ]

    def test_filter_results_explicitly_bound_to_freq(self) -> None:
        recipe = _ensemble_recipe()
        by_id = {s["id"]: s for s in recipe["document"]["steps"]}
        assert by_id["select"]["bindings"] == {
            "structure": {"source": {"step": "freq", "port": "structures"}},
            "results": {"source": {"step": "freq", "port": "results"}},
        }
        native = by_id["select"]["transform"]["native"]
        assert native["energy_window_kcal"] == 5.0
        assert native["lowest_n"] == 10
        compiled = compile_workflow(recipe["document"])
        assert compiled.ok
        edges = {
            (e.target_step_id, e.target_port.name): e.source_step_id
            for e in compiled.plan.graph.edges
        }
        assert edges[("select", "structure")] == "freq"
        assert edges[("select", "results")] == "freq"

    def test_refine_between_opt_and_freq_not_after(self) -> None:
        # N3 freezes filter structure+results to the same calculation step,
        # so refine cannot sit between freq and filter; it collapses
        # near-duplicate opt minima before the freq leg instead.
        recipe = _ensemble_recipe()
        by_id = {s["id"]: s for s in recipe["document"]["steps"]}
        assert by_id["refine"]["bindings"]["structure"]["source"]["step"] == "opt"
        assert by_id["freq"]["bindings"]["structure"]["source"]["step"] == "refine"


class TestEnsembleRefineFakeEndToEnd:
    def test_window_admits_then_lowest_n_selects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real recipe pipeline under fake ORCA: window passes, lowest_n keeps 1."""
        from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
        from confflow.workflow.v4.assembly import RunInputs

        count = _install_fake_orca(tmp_path, monkeypatch, "success_freq")
        document = copy.deepcopy(_ensemble_recipe()["document"])
        for step in document["steps"]:
            if step["id"] == "select":
                step["transform"]["native"]["lowest_n"] = 1
        compiled = compile_workflow(document)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        structures = import_xyz(WATER_PAIR_XYZ, source_name="pair.xyz")
        assert len(structures) == 2
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=json.loads(canonical_json_bytes(document).decode("utf-8")),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=str(tmp_path / "run"),
            )
        )
        assert report.status == "completed", [
            (s.step_id, getattr(s, "status", None), [str(d) for d in getattr(s, "diagnostics", [])])
            for s in report.step_results
        ]
        by_id = {s.step_id: s for s in report.step_results}
        for step_id in ("dedup", "opt", "refine", "freq", "select"):
            assert str(by_id[step_id].status) == "StepStatus.COMPLETED", step_id
        # Identical fake energies sit inside the 5 kcal window, so both reach
        # the lowest_n stage, which keeps the first in id order.
        assert len(by_id["freq"].structures) == 2
        assert len(by_id["select"].structures) == 1
        # Both frames ran opt and freq through the external adapter.
        assert len([line for line in count.read_text().splitlines() if line.strip()]) == 4
