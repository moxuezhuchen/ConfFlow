#!/usr/bin/env python3

"""Phase 1 input simplification: Structure TopologyPatch.

Covers the immutable domain patch (strict one-based validation, canonical
duplicates, fail-closed conflicts), the scientific/reuse payload rules
(legacy shape preserved when absent, provenance excluded, persisted graph
semantic), patch application, single-authority graph resolution, ConfGen
typed/legacy final topology plus conflicting corrections, transform
grouping/adjacency, serialization round trips across every persistence and
transport path, the compiled-document run-import propagation bridge, the
definition-digest provenance rule, and the validation charge/spin proof.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain.errors import DomainError
from confflow.domain.structure import StructureRecord, StructureSet
from confflow.domain.topology import TopologyPatch
from confflow.execution.registry import default_registry
from confflow.execution.transform_executor import TransformExecutor
from confflow.persistence import imports as import_store
from confflow.persistence import publication as publication_store
from confflow.persistence import work_items as work_item_store
from confflow.remote import staging as remote_staging
from confflow.remote import worker as remote_worker
from confflow.remote.envelope import StructureBundleEntry, bundle_entry_digest
from confflow.science import topology as topology_authority
from confflow.workflow.v4.assembly import RunInputs
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.parser import parse_workflow_document
from confflow.workflow.v4.validation import _proven_input_state


def _water() -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    atoms = ("O", "H", "H")
    coords = ((0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0))
    return atoms, coords


def _record(**kwargs: Any) -> StructureRecord:
    atoms, coords = _water()
    base: dict[str, Any] = {"id": "mol:1", "atoms": atoms, "coordinates": coords}
    base.update(kwargs)
    return StructureRecord(**base)


# ---------------------------------------------------------------------------
# Domain: TopologyPatch validation
# ---------------------------------------------------------------------------


class TestTopologyPatchValidation:
    def test_strict_integers_rejected(self) -> None:
        for bad in (True, 1.0, "2", None):
            with pytest.raises(DomainError):
                TopologyPatch(add_edges=[(1, bad)])  # type: ignore[list-item]
        with pytest.raises(DomainError):
            TopologyPatch(add_edges=[(0, 2)])
        with pytest.raises(DomainError):
            TopologyPatch(add_edges=[(2, 2)])
        with pytest.raises(DomainError):
            TopologyPatch(add_edges=[(1, 2), (2, 1)])
        with pytest.raises(DomainError):
            TopologyPatch(add_edges=[(1, 2)], delete_edges=[(2, 1)])
        with pytest.raises(DomainError):
            TopologyPatch.from_dict(
                {"add_edges": [], "delete_edges": [], "provenance": "x", "bogus": 1}
            )

    def test_canonical_order_and_provenance_semantics(self) -> None:
        patch = TopologyPatch(
            add_edges=[(3, 1), (2, 4)], delete_edges=[(5, 4)], provenance="user:intent"
        )
        assert patch.add_edges == ((1, 3), (2, 4))
        assert patch.delete_edges == ((4, 5),)
        assert not patch.is_empty
        assert TopologyPatch().is_empty
        assert patch.same_semantics(TopologyPatch.from_dict(patch.to_dict()))
        assert patch.same_semantics(
            TopologyPatch(add_edges=[(1, 3), (2, 4)], delete_edges=[(4, 5)])
        )
        assert not patch.same_semantics(TopologyPatch(add_edges=[(1, 3)]))
        payload = patch.to_payload()
        assert payload == {"add_edges": [[1, 3], [2, 4]], "delete_edges": [[4, 5]]}
        assert TopologyPatch.from_dict(None) is None

    def test_validate_for_bounds(self) -> None:
        patch = TopologyPatch(add_edges=[(1, 3)])
        patch.validate_for(3)
        with pytest.raises(DomainError):
            patch.validate_for(2)

    def test_apply_to_adjacency(self) -> None:
        patch = TopologyPatch(add_edges=[(1, 3)], delete_edges=[(1, 2)])
        out = patch.apply_to_adjacency([[1], [0, 2], [1]])
        assert out == [[2], [2], [0, 1]]
        with pytest.raises(DomainError):
            patch.apply_to_adjacency([[1], [0]])
        # Malformed input graphs fail closed instead of being repaired.
        for bad in (
            [[1.0], [0]],
            [[True], [0]],
            [[0], [0]],  # self loop
            [[1], []],  # asymmetric
            [[1, 1], [0]],  # duplicate neighbour
            [[5], []],
        ):
            with pytest.raises(DomainError):
                TopologyPatch().apply_to_adjacency(bad)


# ---------------------------------------------------------------------------
# Domain: StructureRecord payload rules
# ---------------------------------------------------------------------------


class TestStructurePayloads:
    def test_legacy_shape_preserved_when_absent(self) -> None:
        plain = _record()
        assert set(plain.scientific_payload()) == {"geometry_digest", "charge", "multiplicity"}
        assert set(plain.reuse_payload()) == {
            "entity_id",
            "geometry_digest",
            "charge",
            "multiplicity",
            "freeze",
            "group_key",
            "role",
            "parent_ids",
            "lineage_root_id",
        }
        assert "topology_patch" not in plain.to_dict()
        assert "working_topology" not in plain.to_dict()
        assert TopologyPatch().is_empty
        empty_patch = _record(topology_patch=TopologyPatch(provenance="note"))
        assert set(empty_patch.scientific_payload()) == {
            "geometry_digest",
            "charge",
            "multiplicity",
        }
        assert plain.geometry_digest == empty_patch.geometry_digest
        assert plain.has_same_content(empty_patch)

    def test_patch_and_graph_enter_payloads_without_provenance(self) -> None:
        patch = TopologyPatch(add_edges=[(1, 3)], provenance="user:intent")
        record = _record(topology_patch=patch)
        scientific = record.scientific_payload()
        assert scientific["topology"] == {"add_edges": [[1, 3]], "delete_edges": []}
        assert record.geometry_digest == _record().geometry_digest
        assert not record.has_same_content(_record())
        graph_a = ((2,), (), (0,))
        graph_b = ((), (2,), (1,))
        ra = _record(topology_patch=patch, working_topology=graph_a)
        rb = _record(topology_patch=patch, working_topology=graph_b)
        assert ra.scientific_payload() != rb.scientific_payload()
        assert ra.reuse_payload() != rb.reuse_payload()
        assert ra.reuse_payload()["working_topology"] == [[2], [], [0]]

    def test_working_topology_validation(self) -> None:
        with pytest.raises(DomainError):
            _record(working_topology=((1,), ()))
        with pytest.raises(DomainError):
            _record(working_topology=((1,), (0, 2), (1,), ()))
        with pytest.raises(DomainError):
            _record(working_topology=((1, 2), (0,), (1,)))
        record = _record(working_topology=((1,), (0, 2), (1,)))
        assert record.working_topology == ((1,), (0, 2), (1,))


# ---------------------------------------------------------------------------
# Science single authority
# ---------------------------------------------------------------------------


class TestWorkingGraphAuthority:
    def test_conflict_gate(self) -> None:
        from confflow.domain.errors import DomainError as _DomainError

        patch = TopologyPatch(delete_edges=[(1, 2)])
        with pytest.raises(_DomainError):
            topology_authority.check_spec_patch_conflict(
                {"add_bond": [[1, 2]]}, _record(topology_patch=patch), where="t"
            )
        graph_only = _record(working_topology=((1,), (0, 2), (1,)))
        for spec in ({"bonds": []}, {"bonds": [[1, 2]]}, {"del_bond": [[1, 2]]}):
            with pytest.raises(_DomainError):
                topology_authority.check_spec_patch_conflict(spec, graph_only, where="t")
        # Native empty arrays stay absent.
        topology_authority.check_spec_patch_conflict(
            {"add_bond": [], "del_bond": []}, graph_only, where="t"
        )
        topology_authority.check_spec_patch_conflict({}, _record(), where="t")

    def test_persisted_graph_wins_over_moved_geometry(self) -> None:
        graph = ((1,), (0, 2), (1,))
        record = _record(
            working_topology=graph,
            coordinates=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)),
        )
        assert topology_authority.resolve_working_adjacency(
            record, [8, 1, 1], record.coordinates
        ) == [[1], [0, 2], [1]]
        assert not topology_authority.should_persist_working_graph(_record())
        assert topology_authority.should_persist_working_graph(record)

    def test_patch_applies_on_perception(self) -> None:
        record = _record(topology_patch=TopologyPatch(delete_edges=[(1, 2)]))
        adjacency = topology_authority.resolve_working_adjacency(
            record, [8, 1, 1], record.coordinates
        )
        assert adjacency == [[2], [], [0]]


# ---------------------------------------------------------------------------
# ConfGen typed + legacy paths
# ---------------------------------------------------------------------------


def _resolved_spec() -> dict[str, Any]:
    from confflow.science.confgen.planner import normalize_spec

    return normalize_spec({"schema_version": 3, "index_base": 1})


class TestConfgenTopology:
    def test_typed_graph_applies_patch(self) -> None:
        from confflow.science.confgen.planner import build_typed_graph

        record = _record(topology_patch=TopologyPatch(delete_edges=[(1, 2)]))
        resolved = _resolved_spec()
        adjacency, graph = build_typed_graph(record, {}, resolved)
        assert [list(row) for row in adjacency] == [[2], [], [0]]
        assert graph.edge_kind(0, 1) is None

    def test_typed_graph_does_not_reapply_patch_to_persisted_graph(self) -> None:
        from confflow.science.confgen.planner import build_typed_graph

        record = _record(
            topology_patch=TopologyPatch(add_edges=[(1, 2)]),
            working_topology=((2,), (), (0,)),
        )
        adjacency, graph = build_typed_graph(record, {}, _resolved_spec())
        assert adjacency == [[2], [], [0]]
        assert graph.edge_kind(0, 1) is None

    def test_typed_graph_conflict_fails_closed(self) -> None:
        from confflow.science.confgen.planner import build_typed_graph

        record = _record(topology_patch=TopologyPatch(add_edges=[(2, 3)]))
        resolved = _resolved_spec()
        with pytest.raises(ValueError):
            build_typed_graph(record, {"add_bond": [[0, 1]]}, resolved)
        graph_only = _record(working_topology=((1,), (0, 2), (1,)))
        with pytest.raises(ValueError):
            build_typed_graph(graph_only, {"bonds": []}, resolved)

    def test_build_context_carries_patched_adjacency(self) -> None:
        from confflow.science.confgen.model import build_context

        record = _record(topology_patch=TopologyPatch(delete_edges=[(1, 2)]))
        context = build_context(record, {"schema_version": 3, "index_base": 1})
        assert [list(row) for row in context.adjacency] == [[2], [], [0]]


# ---------------------------------------------------------------------------
# Transform grouping + adjacency
# ---------------------------------------------------------------------------


class TestTransformTopology:
    def test_distinct_topologies_never_collapse(self) -> None:
        base = _record(id="a")
        patched = _record(id="b", topology_patch=TopologyPatch(delete_edges=[(1, 2)]))
        out, _notes = TransformExecutor._deduplicate([base, patched])
        assert {record.id for record in out} == {"a", "b"}
        same = _record(id="c")
        out, _notes = TransformExecutor._deduplicate([base, same])
        assert {record.id for record in out} == {"a"}

    def test_refine_uses_persisted_graph(self) -> None:
        record = _record(
            working_topology=((1,), (0, 2), (1,)),
            coordinates=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)),
        )
        assert TransformExecutor._adjacency(record, 1.2) == [[1], [0, 2], [1]]


# ---------------------------------------------------------------------------
# Serialization round trips
# ---------------------------------------------------------------------------


class TestTopologySerialization:
    def _patched(self) -> StructureRecord:
        return _record(
            topology_patch=TopologyPatch(add_edges=[(2, 3)], provenance="user:intent"),
            working_topology=((1, 2), (0,), (0,)),
        )

    def test_work_item_store_round_trip(self) -> None:
        rebuilt = work_item_store._build_structure(self._patched().to_dict())
        assert rebuilt == self._patched()
        bare = work_item_store._build_structure(_record().to_dict())
        assert bare.topology_patch is None and bare.working_topology is None

    def test_publication_round_trip(self) -> None:
        rebuilt = publication_store._structure_record(self._patched().to_dict())
        assert rebuilt == self._patched()

    def test_staging_round_trip(self) -> None:
        built = remote_staging._build_structure_set([self._patched().to_dict()], what="structures")
        assert list(built)[0] == self._patched()

    def test_worker_allowlist_and_rebuild(self) -> None:
        assert {"topology_patch", "working_topology"} <= remote_worker._STRUCTURE_FIELDS
        record = self._patched()
        payload = record.to_dict()
        entry = StructureBundleEntry(
            structure_id=record.id,
            payload=payload,
            digest=bundle_entry_digest("structure", payload),
        )
        assert remote_worker._structure_from_entry(entry) == record

    def test_import_reinstatement_preserves_patch(self, tmp_path: Any) -> None:
        fresh = StructureSet.of(
            dataclasses.replace(
                _record(id="e1"), topology_patch=TopologyPatch(delete_edges=[(1, 2)])
            ),
            _record(id="e2"),
        )
        resolved = import_store.resolve_imported_structures(
            run_root=str(tmp_path),
            input_name="structures",
            source_name="structures",
            source_text="x",
            fresh=fresh,
        )
        assert list(resolved)[0].topology_patch == TopologyPatch(delete_edges=[(1, 2)])


# ---------------------------------------------------------------------------
# Compiled-document run-import bridge
# ---------------------------------------------------------------------------


def _compile_inputs(document: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_workflow_document(document)
    assert parsed.definition is not None
    return {item.name: item for item in parsed.definition.inputs}


def _calc_doc(inputs: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "confflow.workflow.v4", "inputs": inputs, "steps": steps}


def _calc_binding_step(step_id: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": step_id,
        "executor": "calculation",
        "bindings": {"structure": {"source": source}},
        "calculation": {
            "program": "g16",
            "execution_adapter": "standard",
            "result_profile": "standard",
            "native": {"keyword": "B3LYP/6-31G* opt"},
            "checks": [],
            "recovery": {"profile": "none"},
        },
    }


class TestRunImportBridge:
    def test_declared_patch_charge_resolve_once(self, tmp_path: Any) -> None:
        from confflow.domain._immutable import FrozenDict

        inputs = {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "topology": {"add": [[2, 3]], "delete": [], "provenance": "intent"},
                "charge": 0,
                "multiplicity": 1,
            }
        }
        document = _calc_doc(inputs, [_calc_binding_step("opt", {"run": "structures"})])
        compiled = compile_workflow(document)
        assert compiled.ok, [d.message for d in compiled.diagnostics if d.is_error]
        declarations = _compile_inputs(document)
        structures = import_xyz("3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n")
        request = V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(
                structures=FrozenDict({"structures": structures}),
                artifacts=FrozenDict({}),
                results=FrozenDict({}),
            ),
            run_root=str(tmp_path),
            import_sources=FrozenDict(
                {"structures": "3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"}
            ),
        )
        resolved = V4RunApplication._resolve_run_inputs(
            run_root=str(tmp_path), request=request, declarations=declarations
        )
        records = list(resolved.structures["structures"])
        assert len(records) == 1
        assert records[0].topology_patch == TopologyPatch(add_edges=[(2, 3)], provenance="intent")
        assert records[0].charge == 0 and records[0].multiplicity == 1
        assert records[0].working_topology is not None
        assert [list(row) for row in records[0].working_topology] == [
            [1, 2],
            [0, 2],
            [0, 1],
        ]

    def test_conflicting_record_patch_fails_closed(self, tmp_path: Any) -> None:
        from confflow.domain._immutable import FrozenDict

        inputs = {"structures": {"kind": "structure", "topology": {"add": [[2, 3]]}}}
        declarations = _compile_inputs(_calc_doc(inputs, [_calc_binding_step("o", {"run": "s"})]))
        assert declarations["structures"].topology == TopologyPatch(add_edges=[(2, 3)])
        record = dataclasses.replace(_record(), topology_patch=TopologyPatch(delete_edges=[(1, 2)]))
        request = V4RunRequest(
            workflow_document=_calc_doc(inputs, [_calc_binding_step("o", {"run": "s"})]),
            run_inputs=RunInputs(
                structures=FrozenDict({"structures": StructureSet.of(record)}),
                artifacts=FrozenDict({}),
                results=FrozenDict({}),
            ),
            run_root=str(tmp_path),
        )
        with pytest.raises(DomainError):
            V4RunApplication._resolve_run_inputs(
                run_root=str(tmp_path), request=request, declarations=declarations
            )

    def test_graph_without_patch_rejects_declared_patch(self, tmp_path: Any) -> None:
        from confflow.domain._immutable import FrozenDict

        inputs = {"structures": {"kind": "structure", "topology": {"add": [[2, 3]]}}}
        document = _calc_doc(inputs, [_calc_binding_step("o", {"run": "s"})])
        declarations = _compile_inputs(document)
        record = _record(working_topology=((1,), (0, 2), (1,)))
        request = V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(
                structures=FrozenDict({"structures": StructureSet.of(record)}),
                artifacts=FrozenDict({}),
                results=FrozenDict({}),
            ),
            run_root=str(tmp_path),
        )
        with pytest.raises(DomainError):
            V4RunApplication._resolve_run_inputs(
                run_root=str(tmp_path), request=request, declarations=declarations
            )

    def test_topology_on_non_structure_input_fails(self) -> None:
        document = _calc_doc(
            {"blobs": {"kind": "artifact", "topology": {"add": [[1, 2]]}}},
            [_calc_binding_step("o", {"run": "structures"})],
        )
        assert parse_workflow_document(document).definition is None


# ---------------------------------------------------------------------------
# Definition digest: provenance excluded, edges + charge included
# ---------------------------------------------------------------------------


class TestDefinitionDigest:
    def _doc(
        self, topology: dict[str, Any] | None, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        inputs: dict[str, Any] = {
            "structures": {"kind": "structure", "charge": 0, "multiplicity": 1}
        }
        if topology is not None:
            inputs["structures"]["topology"] = topology
        if extra:
            inputs["structures"].update(extra)
        return _calc_doc(inputs, [_calc_binding_step("opt", {"run": "structures"})])

    def _digest(self, document: dict[str, Any]) -> str:
        compiled = compile_workflow(document)
        assert compiled.ok, [d.message for d in compiled.diagnostics if d.is_error]
        assert compiled.plan is not None
        return str(compiled.plan.definition_digest)

    def test_provenance_only_edit_keeps_digest(self) -> None:
        assert self._digest(self._doc({"add": [[2, 3]], "provenance": "a"})) == self._digest(
            self._doc({"add": [[2, 3]], "provenance": "b"})
        )

    def test_edge_change_moves_digest(self) -> None:
        assert self._digest(self._doc({"add": [[2, 3]]})) != self._digest(
            self._doc({"delete": [[1, 2]]})
        )
        assert self._digest(self._doc(None)) != self._digest(self._doc({"add": [[2, 3]]}))

    def test_charge_contributes(self) -> None:
        assert self._digest(self._doc(None, {"charge": 0})) != self._digest(
            self._doc(None, {"charge": 1})
        )


# ---------------------------------------------------------------------------
# Validation: charge/spin proof through declarations and producers
# ---------------------------------------------------------------------------


def _chain_doc(inputs: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "confflow.workflow.v4", "inputs": inputs, "steps": steps}


class TestChargeSpinProof:
    def test_input_state_alone_compiles_chain(self) -> None:
        inputs = {"structures": {"kind": "structure", "charge": 0, "multiplicity": 1}}
        steps = [
            _calc_binding_step("opt", {"run": "structures"}),
            _calc_binding_step("freq", {"step": "opt", "port": "structures"}),
            _calc_binding_step("sp", {"step": "freq", "port": "structures"}),
        ]
        assert compile_workflow(_chain_doc(inputs, steps)).ok

    def test_missing_field_still_fails(self) -> None:
        inputs = {"structures": {"kind": "structure", "charge": 0}}
        steps = [_calc_binding_step("opt", {"run": "structures"})]
        compiled = compile_workflow(_chain_doc(inputs, steps))
        assert not compiled.ok
        assert any(
            item.details.get("reason") == "metadata_unavailable"
            for item in compiled.diagnostics
            if item.is_error
        )

    def test_upstream_override_fills_downstream(self) -> None:
        inputs = {"structures": {"kind": "structure", "multiplicity": 1}}
        opt = _calc_binding_step("opt", {"run": "structures"})
        opt["calculation"]["overrides"] = {"charge": 0}
        steps = [
            opt,
            _calc_binding_step("freq", {"step": "opt", "port": "structures"}),
            _calc_binding_step("sp", {"step": "freq", "port": "structures"}),
        ]
        assert compile_workflow(_chain_doc(inputs, steps)).ok

    def test_heterogeneous_roots_stay_per_structure(self) -> None:
        inputs = {
            "left": {"kind": "structure", "charge": 0, "multiplicity": 1},
            "right": {"kind": "structure", "charge": 1, "multiplicity": 2},
        }
        steps = [
            _calc_binding_step("opt_a", {"run": "left"}),
            _calc_binding_step("opt_b", {"run": "right"}),
        ]
        assert compile_workflow(_chain_doc(inputs, steps)).ok

    def test_unknown_producer_and_cycle_fail_closed(self) -> None:
        ghosts = _calc_binding_step("opt", {"step": "ghost", "port": "structures"})
        parsed = parse_workflow_document(_chain_doc({"s": {"kind": "structure"}}, [ghosts]))
        assert parsed.definition is not None
        assert not _proven_input_state(
            parsed.definition.steps[0],
            "charge",
            inputs_by_name={},
            steps_by_id={item.id: item for item in parsed.definition.steps},
            run_defaults=parsed.definition.scientific_defaults,
            registry=default_registry(),
        )
        first = _calc_binding_step("a", {"step": "b", "port": "structures"})
        second = _calc_binding_step("b", {"step": "a", "port": "structures"})
        cycled = parse_workflow_document(_chain_doc({"s": {"kind": "structure"}}, [first, second]))
        assert cycled.definition is not None
        by_id = {item.id: item for item in cycled.definition.steps}
        assert not _proven_input_state(
            by_id["a"],
            "charge",
            inputs_by_name={},
            steps_by_id=by_id,
            run_defaults=cycled.definition.scientific_defaults,
            registry=default_registry(),
        )


# ---------------------------------------------------------------------------
# Profiles: patched optimization descendants keep the input graph
# ---------------------------------------------------------------------------


class TestProfileInheritance:
    def test_standard_profile_keeps_input_graph_on_moved_output(self) -> None:
        from confflow.domain.resources import ResourceRequest
        from confflow.execution.native import (
            GeometryOutput,
            NativeResult,
            ParsedGeometry,
            ProgramName,
            ResolvedCalculationInputs,
        )
        from confflow.execution.profile_standard import _output_structure
        from confflow.execution.profiles import ProfileContext

        driving = _record(topology_patch=TopologyPatch(delete_edges=[(1, 2)]))
        driving = dataclasses.replace(
            driving,
            **topology_authority.resolve_and_persist_kwargs(driving, driving.coordinates),
        )
        assert driving.working_topology == ((2,), (), (0,))
        moved = ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (0.0, 5.0, 0.0))
        native = NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=True,
            geometry_output=GeometryOutput.PRODUCED,
            final_geometry=ParsedGeometry(atoms=("O", "H", "H"), coordinates=moved),
        )
        inputs = ResolvedCalculationInputs(
            structure=driving,
            charge=0,
            multiplicity=1,
            freeze=None,
            resources=ResourceRequest(),
        )
        context = ProfileContext(
            work_item_id="wi:1",
            step_id="opt",
            logical_key="opt:g1",
            profile_name="standard",
            profile_version="confflow.contract.result_profile.standard.v1",
            native_result=native,
            inputs=inputs,
        )
        record, _semantics = _output_structure(context)
        assert tuple(record.coordinates) == tuple(moved)
        assert record.working_topology == ((2,), (), (0,))
        assert record.topology_patch == driving.topology_patch
        assert (record.charge, record.multiplicity) == (0, 1)

    def _unchanged_context(
        self, source: StructureRecord, charge: int | None, multiplicity: int | None
    ) -> Any:
        from confflow.domain.resources import ResourceRequest
        from confflow.execution.native import (
            GeometryOutput,
            NativeResult,
            ParsedGeometry,
            ProgramName,
            ResolvedCalculationInputs,
        )
        from confflow.execution.profiles import ProfileContext

        native = NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=True,
            geometry_output=GeometryOutput.PRODUCED,
            final_geometry=ParsedGeometry(
                atoms=tuple(source.atoms),
                coordinates=tuple(source.coordinates),
            ),
        )
        inputs = ResolvedCalculationInputs(
            structure=source,
            charge=charge,
            multiplicity=multiplicity,
            freeze=None,
            resources=ResourceRequest(),
        )
        return ProfileContext(
            work_item_id="wi:1",
            step_id="opt",
            logical_key="opt:g1",
            profile_name="standard",
            profile_version="confflow.contract.result_profile.standard.v1",
            native_result=native,
            inputs=inputs,
        )

    def test_unchanged_everything_retains_source(self) -> None:
        from confflow.execution.profile_standard import _output_structure
        from confflow.execution.profiles import GeometrySemantics

        source = _record(charge=0, multiplicity=1)
        record, semantics = _output_structure(self._unchanged_context(source, 0, 1))
        assert record is source
        assert semantics is GeometrySemantics.PASSTHROUGH

    def test_unchanged_geometry_explicit_override_derives(self) -> None:
        from confflow.execution.profile_standard import _output_structure
        from confflow.execution.profiles import GeometrySemantics

        source = _record(charge=0, multiplicity=1)
        record, semantics = _output_structure(self._unchanged_context(source, 1, 2))
        assert semantics is GeometrySemantics.PRODUCED
        assert record.id != source.id
        assert tuple(record.coordinates) == tuple(source.coordinates)
        assert (record.charge, record.multiplicity) == (1, 2)
        assert record.parent_ids == (source.id,)
        assert record.scientific_payload()["charge"] == 1

    def test_unchanged_geometry_new_graph_derives(self) -> None:
        from confflow.execution.profile_standard import _output_structure
        from confflow.execution.profiles import GeometrySemantics

        source = _record(
            charge=0, multiplicity=1, topology_patch=TopologyPatch(delete_edges=[(1, 2)])
        )
        assert source.working_topology is None
        record, semantics = _output_structure(self._unchanged_context(source, 0, 1))
        assert semantics is GeometrySemantics.PRODUCED
        assert record.id != source.id
        assert record.working_topology == ((2,), (), (0,))
        assert record.topology_patch == source.topology_patch
