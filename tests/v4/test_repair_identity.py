#!/usr/bin/env python3

"""Wave-1 repair tests for worker B: identity / ResultRef / Binding / mapping.

Covers the freeze-mandated B ownership only; no importer/application/batch
coverage (D owns those) and no registry/profile edits (A/C own those):

- independent result identity (ResultRef/result_id, value vs entity);
- provenance-aware digests (entity/group/role/lineage + ResultRef/provenance);
- status/cardinality gating with per-step scoped diagnostics;
- IDS result filtering per requested id (MANY may select several distinct ids);
"""

from __future__ import annotations

from confflow.domain import (
    FrozenDict,
    ResultSet,
    ScientificResult,
    StructureSet,
    Unit,
    make_result_id,
)
from confflow.domain.errors import InvalidResultError


def _energy(
    value: float,
    *,
    subject: str | None = None,
    step: str | None = None,
    item: str | None = None,
    result_id: str | None = None,
    program: str | None = None,
) -> ScientificResult:
    from confflow.domain import Provenance

    provenance = Provenance(program=program) if program is not None else None
    return ScientificResult(
        kind="energy",
        value=value,
        unit=Unit.HARTREE,
        subject_structure_id=subject,
        source_step_id=step,
        source_work_item_id=item,
        provenance=provenance,
        result_id=result_id,
    )


class TestIndependentResultIdentity:
    DIGEST_A = "sha256:" + "a1" * 32
    DIGEST_B = "sha256:" + "b2" * 32

    def test_same_value_different_producers_have_different_identity(self) -> None:
        first = _energy(
            1.0,
            subject="s0",
            step="s_opt",
            result_id=make_result_id(
                step_id="s_opt",
                work_item_id="wi:s_opt:s0",
                kind="energy",
                subject_structure_id="s0",
                discriminator="low",
                producer_digest=self.DIGEST_A,
            ),
        )
        second = _energy(
            1.0,
            subject="s0",
            step="s_sp",
            result_id=make_result_id(
                step_id="s_sp",
                work_item_id="wi:s_sp:s0",
                kind="energy",
                subject_structure_id="s0",
                discriminator="high",
                producer_digest=self.DIGEST_B,
            ),
        )
        assert first.value_digest == second.value_digest
        assert first.identity_digest != second.identity_digest
        assert first.digest_payload() != second.digest_payload()
        assert first.ref is not None and second.ref is not None
        assert first.ref != second.ref

    def test_producer_digest_binds_identity_to_scientific_generation(self) -> None:
        """Same method/basis with changed science mints new refs (no aliasing)."""
        base = dict(
            step_id="s_opt",
            work_item_id="wi:s_opt:s0",
            kind="energy",
            subject_structure_id="s0",
            method="B3LYP",
            basis="6-31G*",
        )
        first = make_result_id(producer_digest=self.DIGEST_A, **base)
        retried = make_result_id(producer_digest=self.DIGEST_A, **base)
        changed_science = make_result_id(producer_digest=self.DIGEST_B, **base)
        assert retried == first  # retry of the same semantic item retains ref
        assert changed_science != first  # changed solvent/native yields new ref

    def test_producer_digest_is_required_and_digest_shaped(self) -> None:
        from confflow.domain import InvalidResultError as _IRE

        for bad in (None, "", "not-a-digest", "sha256:" + "zz" * 32):
            try:
                make_result_id(step_id="s_opt", kind="energy", producer_digest=bad)
            except _IRE:
                pass
            else:  # pragma: no cover
                raise AssertionError(f"bad producer_digest {bad!r} must fail")

    def test_legacy_result_without_id_has_no_production_identity(self) -> None:
        legacy = _energy(1.0, subject="s0", step="s_opt")
        assert legacy.result_id is None
        assert legacy.ref is None
        assert legacy.identity_digest is None
        assert legacy.is_production is False

    def test_select_ids_resolves_each_requested_id_independently(self) -> None:
        first = _energy(1.0, result_id="res-a")
        second = _energy(2.0, result_id="res-b")
        results = ResultSet.of(first, second)
        selected, missing, ambiguous = results.select_ids(("res-a", "res-b"))
        assert missing == () and ambiguous == ()
        assert selected.results == (first, second)

    def test_select_ids_missing_and_duplicate_same_id_fail(self) -> None:
        only = _energy(1.0, result_id="res-a")
        results = ResultSet.of(only)
        selected, missing, ambiguous = results.select_ids(("res-a", "res-z"))
        assert selected.results == (only,)
        assert missing == ("res-z",)
        assert ambiguous == ()
        dup = ResultSet.of(only, _energy(9.0, result_id="res-a"))
        _, _, ambiguous = dup.select_ids(("res-a",))
        assert ambiguous == ("res-a",)

    def test_select_unique_rejects_zero_and_ambiguous(self) -> None:
        first = _energy(1.0, subject="s1")
        second = _energy(2.0, subject="s1")
        results = ResultSet.of(first, second)
        try:
            results.select_unique("energy", subject_structure_id="s1")
        except InvalidResultError:
            pass
        else:  # pragma: no cover
            raise AssertionError("ambiguous selection must fail")
        unique = ResultSet.of(first).select_unique("energy", subject_structure_id="s1")
        assert unique is first
        try:
            results.select_unique("energy", subject_structure_id="missing")
        except InvalidResultError:
            pass
        else:  # pragma: no cover
            raise AssertionError("empty selection must fail")

    def test_production_gate_rejects_legacy_and_detects_duplicate_ids(self) -> None:
        from confflow.domain import find_duplicate_result_ids, require_production_ids

        legacy = ResultSet.of(_energy(1.0, subject="s0", step="s_opt"))
        try:
            require_production_ids(legacy)
        except InvalidResultError:
            pass
        else:  # pragma: no cover
            raise AssertionError("legacy record without result_id must fail production gate")
        stamped = ResultSet.of(
            _energy(1.0, subject="s0", step="s_opt", result_id="res-a"),
            _energy(1.0, subject="s0", step="s_opt", result_id="res-b"),
        )
        require_production_ids(stamped)  # distinct siblings pass
        assert find_duplicate_result_ids(stamped) == ()
        assert find_duplicate_result_ids(
            ResultSet.of(*stamped, _energy(2.0, result_id="res-a"))
        ) == ("res-a",)
        # Same value across producers mints distinct ids: never one entity.
        digest = "sha256:" + "c3" * 32
        assert make_result_id(
            step_id="s_opt",
            kind="energy",
            subject_structure_id="s0",
            producer_digest=digest,
        ) != make_result_id(
            step_id="s_sp", kind="energy", subject_structure_id="s0", producer_digest=digest
        )


class TestProvenanceAwareDigests:
    def test_structure_reuse_payload_pins_semantic_axes(self) -> None:
        from tests.v4._builders import structure

        base = structure("s0")
        assert base.reuse_payload()["entity_id"] == "s0"
        assert base.reuse_payload()["group_key"] is None
        moved_group = structure("s0", group_key="g2")
        assert base.reuse_payload() != moved_group.reuse_payload()
        moved_role = structure("s0", role="product")
        assert base.reuse_payload() != moved_role.reuse_payload()
        other_entity = structure("s9")
        assert base.reuse_payload() != other_entity.reuse_payload()

    def test_conflicting_payloads_for_one_id_are_detected(self) -> None:
        from confflow.domain import check_structure_id_conflicts
        from tests.v4._builders import structure

        first = structure("s0")
        same = structure("s0")
        assert check_structure_id_conflicts([first, same]) == []
        conflict = structure("s0", group_key="g2")
        hits = check_structure_id_conflicts([first, conflict])
        assert len(hits) == 1 and hits[0][0] == "s0"

    def test_result_provenance_participates_in_digest(self) -> None:
        plain = _energy(1.0, subject="s0", step="s_opt", result_id="res-a")
        proved = _energy(
            1.0,
            subject="s0",
            step="s_opt",
            result_id="res-a",
            program="gaussian",
        )
        assert plain.value_digest == proved.value_digest
        assert plain.digest_payload() != proved.digest_payload()

    def test_work_item_digest_kind_is_v2(self) -> None:
        from confflow.domain import WORK_ITEM_DIGEST_KIND

        assert WORK_ITEM_DIGEST_KIND == "confflow.work_item.v2"


class TestAssemblyGating:
    """Assembly-level gating runs through the compile path (needs a healthy tree)."""

    def _compile(self, document: dict, registry: object = None):  # type: ignore[no-untyped-def]
        from tests.v4._builders import compile_doc

        result = compile_doc(document, registry=registry)
        assert result.ok, [(d.code, d.details.get("reason")) for d in result.errors]
        return result.plan

    def test_failed_producer_blocks_consumer_but_keeps_healthy_step(self) -> None:
        from confflow.domain import StepStatus
        from confflow.workflow.v4 import (
            MaterializedOutputs,
            StepOutputs,
            assemble_work_items,
        )
        from tests.v4._builders import calc_step, run_inputs, structure_set, v4_doc

        doc = v4_doc(
            [
                calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step(
                    "s_sp",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                ),
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        plan = self._compile(doc)
        structures = structure_set("s0")
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "s_opt": StepOutputs(
                            step_id="s_opt",
                            structures=structures,
                            status=StepStatus.FAILED,
                        )
                    }
                )
            ),
        )
        assert not assembly.ok
        # Healthy producer step retains items; errored consumer yields none.
        assert [i.step_id for i in assembly.items] == ["s_opt"]
        assert any(i.step_id == "s_sp" for i in assembly.errors)

    def test_unknown_producer_status_is_rejected_fail_closed(self) -> None:
        """A producer without explicit status never reads as completed."""
        from confflow.workflow.v4 import (
            MaterializedOutputs,
            StepOutputs,
            assemble_work_items,
        )
        from tests.v4._builders import calc_step, run_inputs, structure_set, v4_doc

        doc = v4_doc(
            [
                calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step(
                    "s_sp",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                ),
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        plan = self._compile(doc)
        structures = structure_set("s0")
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        # Legacy marker: deserializes, but cannot bind silently.
                        "s_opt": StepOutputs(step_id="s_opt", structures=structures)
                    }
                )
            ),
        )
        assert not assembly.ok
        assert [i.step_id for i in assembly.items] == ["s_opt"]
        consumer_errors = [d for d in assembly.errors if d.step_id == "s_sp"]
        assert len(consumer_errors) == 1
        assert consumer_errors[0].details.get("producer_status") == "unknown"

    def test_cancelled_producer_blocks_consumer(self) -> None:
        from confflow.domain import StepStatus
        from confflow.workflow.v4 import (
            MaterializedOutputs,
            StepOutputs,
            assemble_work_items,
        )
        from tests.v4._builders import calc_step, run_inputs, structure_set, v4_doc

        doc = v4_doc(
            [
                calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step(
                    "s_sp",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                ),
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        plan = self._compile(doc)
        structures = structure_set("s0")
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "s_opt": StepOutputs(
                            step_id="s_opt",
                            structures=structures,
                            status=StepStatus.CANCELLED,
                        )
                    }
                )
            ),
        )
        assert not assembly.ok
        assert [i.step_id for i in assembly.items] == ["s_opt"]
        assert any(d.details.get("producer_status") == "cancelled" for d in assembly.errors)

    def test_partial_producer_needs_accept_subset(self) -> None:
        from tests.v4._builders import calc_step, v4_doc

        producer_completion = {"mode": "allow_partial", "partial_output": "allow"}
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    completion=producer_completion,
                ),
                calc_step(
                    "s_sp",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                ),
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        from tests.v4._builders import compile_doc

        result = compile_doc(doc)
        assert not result.ok
        assert "partial_consumption_undefined" in [
            str(d.details.get("reason")) for d in result.diagnostics
        ]

    def test_partial_producer_with_accept_subset_assembles(self) -> None:
        from confflow.domain import StepStatus
        from confflow.workflow.v4 import (
            MaterializedOutputs,
            StepOutputs,
            assemble_work_items,
        )
        from tests.v4._builders import calc_step, run_inputs, structure_set, v4_doc

        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    completion={"mode": "allow_partial", "partial_output": "allow"},
                ),
                calc_step(
                    "s_sp",
                    bindings={
                        "structure": {
                            "source": {"step": "s_opt", "port": "structures"},
                            "partial_consumption": "accept_subset",
                        }
                    },
                ),
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        plan = self._compile(doc)
        structures = structure_set("s0")
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "s_opt": StepOutputs(
                            step_id="s_opt",
                            structures=structures,
                            status=StepStatus.PARTIAL,
                        )
                    }
                )
            ),
        )
        assert assembly.ok, [(d.code, d.details.get("reason")) for d in assembly.errors]
        assert [i.logical_key for i in assembly.for_step("s_sp")] == ["s_sp:s0"]

    # R2.2 (G18): retired with the analysis executor: no retained
    # executor consumes result-kind bindings, so the result-id
    # selector assembly is unreachable until analysis returns.

    def test_group_key_change_moves_item_digest(self) -> None:
        from confflow.workflow.v4 import assemble_work_items
        from tests.v4._builders import calc_step, run_inputs, v4_doc
        from tests.v4._builders import structure as build_structure

        # R2.2: vehicle is the retained standard adapter (named_structures
        # retired); the group-key-moves-digest property is unchanged.
        doc = v4_doc(
            [
                calc_step(
                    "s_ts",
                    bindings={"structure": {"source": {"run": "structures"}}},
                )
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        plan = self._compile(doc)
        first = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": StructureSet.of(build_structure("R1", group_key="g1"))}
            ),
        )
        second = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": StructureSet.of(build_structure("R1", group_key="g2"))}
            ),
        )
        assert first.ok and second.ok
        assert first.items[0].semantic_digest != second.items[0].semantic_digest
        # Standard per-structure pairing keys items by structure id, so the
        # logical key stays while the digest moves with the group identity.
        assert first.items[0].logical_key == second.items[0].logical_key
