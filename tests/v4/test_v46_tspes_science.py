#!/usr/bin/env python3

"""TSPES scientific-energy contract: asymmetric anti-false-green tests.

Frozen canonical semantics (Worker A audit F1+F2):

- ``energy`` is electronic energy only, ``gibbs_correction`` the thermal
  correction only, ``gibbs_energy`` the full Gibbs energy only.  A kind
  never changes meaning with context.
- The TSPES composite is ``G_high = E_high(SP) + correction(freq)``:
  the electronic leg comes only from the single-point steps, the
  correction leg only from the frequency steps, pinned by explicit
  ``(source step, subject, kind)`` ResultRef identity.  Subject/kind
  first-match and pool order never decide.

Every number below is asymmetric on purpose: the high-level single-point
energies differ from the low-level frequency energies AND from the
low-level Gibbs energies, so dropping a leg, substituting a theory
level, or double counting Gibbs content each yields a different,
wrong barrier:

- TS: SP E=-70.0; freq E=-60.0, corr=+0.10, G=-59.90
- endpoint: SP E=-80.0; freq E=-65.0, corr=+0.20, G=-64.80
- composite TS G=-69.90, endpoint G=-79.80
- barrier = G_TS - G_endpoint = -69.90 - (-79.80) = 9.90 (TS above
  endpoint, positive by the ``barrier_*_endpoint = G_TS - G_endpoint``
  definition).

Wrong-leg outcomes the tests discriminate against: freq-only composite
4.90, Gibbs-as-electronic double count 4.80, SP-only electronic delta
10.00 as a *barrier* (it is the electronic delta, not the barrier).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from confflow.analysis.compute import policy_from_native
from confflow.analysis.reaction import (
    KIND_BARRIER_FORWARD_ENDPOINT,
    KIND_BARRIER_REVERSE_ENDPOINT,
    KIND_ENDPOINT_ENERGY_DELTA,
    KIND_ENDPOINT_GIBBS_DELTA,
    KIND_REACTION_PROFILE,
    ReactionNodeGroup,
    assemble_reaction_result,
)
from confflow.analysis.thermochemistry import (
    AnalysisMathError,
    EnergyModel,
    resolve_node_gibbs,
    select_result,
)
from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import (
    FrozenDict,
    ResourceRequest,
    ResultSet,
    ScientificResult,
    StructureSet,
    Unit,
)
from confflow.domain.result import make_result_id
from confflow.execution import GeometryOutput, NativeResult, ProgramName, ResolvedCalculationInputs
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.profiles import ProfileContext
from confflow.producer import get_recipe_v4
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._builders import structure

# ---------------------------------------------------------------------------
# Asymmetric fixtures (hand-computed expectations above).
# ---------------------------------------------------------------------------

TS_SP_E = -70.0
TS_FREQ_E = -60.0
TS_CORR = 0.10
TS_GIBBS = -59.90

END_SP_E = -80.0
END_FREQ_E = -65.0
END_CORR = 0.20
END_GIBBS = -64.80

COMPOSITE_TS_G = TS_SP_E + TS_CORR  # -69.90
COMPOSITE_END_G = END_SP_E + END_CORR  # -79.80
EXPECTED_BARRIER = COMPOSITE_TS_G - COMPOSITE_END_G  # 9.90

TS_ID = "ts-asym"
FORWARD_ID = "fwd-asym"
REVERSE_ID = "rev-asym"
GROUP_KEY = "rxn-asym"

SCOPED_COMPOSITE = EnergyModel(
    mode="composite",
    electronic_selector="energy",
    correction_selector="gibbs_correction",
    fallback="none",
    electronic_source_steps=("endpoint_sp", "ts_sp"),
    correction_source_steps=("endpoint_freq", "ts_freq"),
)


def _producer_digest(step: str, subject: str, kind: str) -> str:
    """Return a deterministic fake producer digest for ResultRef minting."""
    return "sha256:" + hashlib.sha256(f"{step}:{subject}:{kind}".encode()).hexdigest()


def _result(
    kind: str,
    value: float,
    subject: str,
    *,
    step: str,
    unit: Unit = Unit.HARTREE,
) -> ScientificResult:
    """Build one production-stamped source result from producer *step*."""
    return ScientificResult(
        kind=kind,
        value=value,
        unit=unit,
        subject_structure_id=subject,
        source_step_id=step,
        result_id=make_result_id(
            step_id=step,
            kind=kind,
            subject_structure_id=subject,
            producer_digest=_producer_digest(step, subject, kind),
        ),
    )


def _node_pool(
    sp_e: float,
    freq_e: float,
    corr: float,
    gibbs: float,
    subject: str,
    *,
    sp_step: str,
    freq_step: str,
) -> ResultSet:
    """Build one node's full asymmetric pool: SP leg plus frequency legs."""
    return ResultSet.of(
        _result("energy", sp_e, subject, step=sp_step),
        _result("energy", freq_e, subject, step=freq_step),
        _result("gibbs_correction", corr, subject, step=freq_step),
        _result("gibbs_energy", gibbs, subject, step=freq_step),
    )


def _ts_pool() -> ResultSet:
    """Return the asymmetric TS pool (SP leg from ``ts_sp``)."""
    return _node_pool(
        TS_SP_E, TS_FREQ_E, TS_CORR, TS_GIBBS, TS_ID, sp_step="ts_sp", freq_step="ts_freq"
    )


def _endpoint_pool(subject: str, *, sp_step: str = "endpoint_sp") -> ResultSet:
    """Return the asymmetric endpoint pool (SP leg from ``endpoint_sp``)."""
    return _node_pool(
        END_SP_E,
        END_FREQ_E,
        END_CORR,
        END_GIBBS,
        subject,
        sp_step=sp_step,
        freq_step="endpoint_freq",
    )


def _group() -> ReactionNodeGroup:
    """Build the single asymmetric reaction group."""
    return ReactionNodeGroup(
        group_key=GROUP_KEY,
        ts_structure_id=TS_ID,
        forward_structure_id=FORWARD_ID,
        reverse_structure_id=REVERSE_ID,
    )


def _lookup() -> dict[str, ResultSet]:
    """Build per-node asymmetric pools for TS/forward/reverse."""
    return {
        TS_ID: _ts_pool(),
        FORWARD_ID: _endpoint_pool(FORWARD_ID),
        REVERSE_ID: _endpoint_pool(REVERSE_ID),
    }


# ---------------------------------------------------------------------------
# Profile semantics: energy is electronic only (F2).
# ---------------------------------------------------------------------------


def _profile_inputs() -> ResolvedCalculationInputs:
    """Build minimal resolved inputs over the water fixture."""
    return ResolvedCalculationInputs(
        structure=structure("s0"),
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=ResourceRequest.from_values(cores_per_item=1, memory_per_item="1GB"),
        native=FrozenDict({"keyword": "B3LYP Freq"}),
        checkpoints=(),
        step_id="s_freq",
        work_item_id="wi:s_freq:item0",
        logical_key="s_freq:item0",
    )


def _apply_profile(energies: dict[str, float]) -> Any:
    """Apply the standard profile to parser energies."""
    profile = PROFILES["standard"]
    return profile.apply(
        ProfileContext(
            work_item_id="wi:s_freq:item0",
            step_id="s_freq",
            logical_key="s_freq:item0",
            profile_name=profile.name,
            profile_version=profile.contract_version,
            native_result=NativeResult(
                program=ProgramName.ORCA,
                terminated_normally=True,
                geometry_output=GeometryOutput.NONE,
                energies_hartree=FrozenDict(dict(energies)),
                log_file_name="job.out",
            ),
            inputs=_profile_inputs(),
        )
    )


class TestStandardProfileSemantics:
    """kind=energy is electronic only, even with Gibbs content parsed."""

    def test_frequency_like_output_keeps_electronic_energy(self) -> None:
        output = _apply_profile({"electronic": TS_FREQ_E, "gibbs": TS_GIBBS})
        kinds = {item.kind: item.value for item in output.results}
        assert kinds["energy"] == pytest.approx(TS_FREQ_E, abs=1e-9)
        assert kinds["energy"] != pytest.approx(TS_GIBBS)
        assert kinds["gibbs_energy"] == pytest.approx(TS_GIBBS, abs=1e-9)
        assert kinds["gibbs_correction"] == pytest.approx(TS_CORR, abs=1e-6)

    def test_single_point_like_output_emits_electronic_only(self) -> None:
        output = _apply_profile({"electronic": TS_SP_E})
        kinds = {item.kind: item.value for item in output.results}
        assert kinds["energy"] == pytest.approx(TS_SP_E, abs=1e-9)
        assert "gibbs_energy" not in kinds
        assert "gibbs_correction" not in kinds

    def test_explicit_correction_never_folds_into_energy(self) -> None:
        output = _apply_profile({"electronic": END_FREQ_E, "gibbs_correction": END_CORR})
        kinds = {item.kind: item.value for item in output.results}
        assert kinds["energy"] == pytest.approx(END_FREQ_E, abs=1e-9)
        assert kinds["gibbs_correction"] == pytest.approx(END_CORR, abs=1e-9)
        assert "gibbs_energy" not in kinds

    def test_gibbs_only_parses_without_inventing_electronic(self) -> None:
        output = _apply_profile({"gibbs": TS_GIBBS})
        kinds = {item.kind: item.value for item in output.results}
        assert "energy" not in kinds
        assert kinds["gibbs_energy"] == pytest.approx(TS_GIBBS, abs=1e-9)


# ---------------------------------------------------------------------------
# Direct mode: consume gibbs_energy as-is.
# ---------------------------------------------------------------------------


class TestDirectGibbsMode:
    """Direct policy consumes the parsed full Gibbs energy unchanged."""

    def test_direct_resolution_consumes_gibbs(self) -> None:
        model = EnergyModel(
            mode="direct",
            electronic_selector="energy",
            correction_selector="gibbs_correction",
        )
        pool = ResultSet.of(_result("gibbs_energy", TS_GIBBS, TS_ID, step="ts_freq"))
        resolved = resolve_node_gibbs(pool, TS_ID, model)
        assert resolved.value_hartree == pytest.approx(TS_GIBBS, abs=1e-9)
        assert resolved.formula == "direct_gibbs"

    def test_direct_group_barriers_use_gibbs(self) -> None:
        model = EnergyModel(
            mode="direct",
            electronic_selector="energy",
            correction_selector="gibbs_correction",
        )
        lookup = {
            TS_ID: ResultSet.of(
                _result("gibbs_energy", TS_GIBBS, TS_ID, step="ts_freq"),
                _result("energy", TS_FREQ_E, TS_ID, step="ts_freq"),
            ),
            FORWARD_ID: ResultSet.of(
                _result("gibbs_energy", END_GIBBS, FORWARD_ID, step="endpoint_freq"),
                _result("energy", END_FREQ_E, FORWARD_ID, step="endpoint_freq"),
            ),
            REVERSE_ID: ResultSet.of(
                _result("gibbs_energy", END_GIBBS, REVERSE_ID, step="endpoint_freq"),
                _result("energy", END_FREQ_E, REVERSE_ID, step="endpoint_freq"),
            ),
        }
        analysis = assemble_reaction_result(_group(), model, lookup, analysis_step_id="s_analysis")
        assert analysis.ok, [(item.code, item.message) for item in analysis.diagnostics]
        by_kind = {result.kind: result for result in analysis.results}
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(
            TS_GIBBS - END_GIBBS, abs=1e-9
        )


# ---------------------------------------------------------------------------
# Composite mode: scoped (source step, subject, kind) selection.
# ---------------------------------------------------------------------------


class TestCompositeScopedSelection:
    """Composite G = E_high(SP step) + correction(freq step), never else."""

    def test_composite_ts_value(self) -> None:
        resolved = resolve_node_gibbs(_ts_pool(), TS_ID, SCOPED_COMPOSITE)
        assert resolved.value_hartree == pytest.approx(COMPOSITE_TS_G, abs=1e-9)
        assert resolved.value_hartree == pytest.approx(-69.90, abs=1e-9)

    def test_composite_endpoint_value(self) -> None:
        resolved = resolve_node_gibbs(_endpoint_pool(FORWARD_ID), FORWARD_ID, SCOPED_COMPOSITE)
        assert resolved.value_hartree == pytest.approx(COMPOSITE_END_G, abs=1e-9)
        assert resolved.value_hartree == pytest.approx(-79.80, abs=1e-9)

    def test_legs_cite_sp_and_correction_resultrefs(self) -> None:
        resolved = resolve_node_gibbs(_ts_pool(), TS_ID, SCOPED_COMPOSITE)
        assert resolved.electronic_source_id is not None
        assert resolved.correction_source_id is not None
        by_id = {item.result_id: item for item in _ts_pool()}
        electronic = by_id[resolved.electronic_source_id]
        correction = by_id[resolved.correction_source_id]
        assert (electronic.source_step_id, electronic.kind) == ("ts_sp", "energy")
        assert (correction.source_step_id, correction.kind) == ("ts_freq", "gibbs_correction")
        assert float(electronic.value) == pytest.approx(TS_SP_E, abs=1e-9)
        assert float(correction.value) == pytest.approx(TS_CORR, abs=1e-6)

    def test_group_barrier_is_990(self) -> None:
        analysis = assemble_reaction_result(
            _group(), SCOPED_COMPOSITE, _lookup(), analysis_step_id="s_analysis"
        )
        assert analysis.ok, [(item.code, item.message) for item in analysis.diagnostics]
        by_kind = {result.kind: result for result in analysis.results}
        assert set(by_kind) == {
            KIND_BARRIER_FORWARD_ENDPOINT,
            KIND_BARRIER_REVERSE_ENDPOINT,
            KIND_ENDPOINT_GIBBS_DELTA,
            KIND_ENDPOINT_ENERGY_DELTA,
            KIND_REACTION_PROFILE,
        }
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER, abs=1e-9
        )
        assert by_kind[KIND_BARRIER_REVERSE_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER, abs=1e-9
        )
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(9.90, abs=1e-9)
        assert by_kind[KIND_ENDPOINT_GIBBS_DELTA].value == pytest.approx(0.0, abs=1e-9)
        profile = dict(by_kind[KIND_REACTION_PROFILE].value)
        assert profile["gibbs_energy"]["ts"]["value"] == pytest.approx(-69.90, abs=1e-9)
        assert profile["gibbs_energy"]["forward"]["value"] == pytest.approx(-79.80, abs=1e-9)
        assert profile["electronic_energy"]["ts"]["value"] == pytest.approx(TS_SP_E, abs=1e-9)
        assert profile["electronic_energy"]["forward"]["value"] == pytest.approx(END_SP_E, abs=1e-9)

    def test_unscoped_pool_with_two_energies_fails_ambiguous(self) -> None:
        # Without the source-step scope the low-level frequency energy and
        # the high-level SP energy collide on (subject, kind): order must
        # not decide, so selection fails closed.  This is why the recipe
        # scopes the composite legs to their producer steps.
        unscoped = EnergyModel(
            mode="composite",
            electronic_selector="energy",
            correction_selector="gibbs_correction",
        )
        with pytest.raises(AnalysisMathError) as excinfo:
            resolve_node_gibbs(_ts_pool(), TS_ID, unscoped)
        assert excinfo.value.code == "ambiguous_selection"

    def test_scoped_selection_ignores_pool_order(self) -> None:
        for pool in (_ts_pool(), ResultSet(tuple(reversed(tuple(_ts_pool()))))):
            resolved = resolve_node_gibbs(pool, TS_ID, SCOPED_COMPOSITE)
            assert resolved.value_hartree == pytest.approx(-69.90, abs=1e-9)

    def test_missing_sp_leg_fails_closed(self) -> None:
        freq_only = ResultSet.of(
            _result("energy", TS_FREQ_E, TS_ID, step="ts_freq"),
            _result("gibbs_correction", TS_CORR, TS_ID, step="ts_freq"),
        )
        with pytest.raises(AnalysisMathError) as excinfo:
            resolve_node_gibbs(freq_only, TS_ID, SCOPED_COMPOSITE)
        assert excinfo.value.code == "energy_missing"

    def test_wrong_step_correction_fails_closed(self) -> None:
        pool = ResultSet.of(
            _result("energy", TS_SP_E, TS_ID, step="ts_sp"),
            _result("gibbs_correction", 0.99, TS_ID, step="endpoint_sp"),
        )
        with pytest.raises(AnalysisMathError) as excinfo:
            resolve_node_gibbs(pool, TS_ID, SCOPED_COMPOSITE)
        assert excinfo.value.code == "correction_missing"

    def test_select_result_source_step_scope(self) -> None:
        pool = _ts_pool()
        selected = select_result(pool, TS_ID, "energy", "energy", source_step_ids={"ts_sp"})
        assert selected is not None
        assert selected.source_step_id == "ts_sp"
        assert float(selected.value) == pytest.approx(TS_SP_E, abs=1e-9)
        assert (
            select_result(pool, TS_ID, "energy", "energy", source_step_ids={"ts_freq"}) is not None
        )
        assert (
            select_result(pool, TS_ID, "energy", "energy", source_step_ids={"no_such_step"}) is None
        )

    def test_policy_from_native_parses_scopes(self) -> None:
        policy = policy_from_native(
            {
                "energy_mode": "composite",
                "electronic_result_kind": "energy",
                "correction_result_kind": "gibbs_correction",
                "electronic_source_steps": ["ts_sp", "endpoint_sp"],
                "correction_source_steps": ["ts_freq", "endpoint_freq"],
                "energy_fallback": "none",
            }
        )
        assert policy.electronic_source_steps == ("endpoint_sp", "ts_sp")
        assert policy.correction_source_steps == ("endpoint_freq", "ts_freq")
        resolved = resolve_node_gibbs(_ts_pool(), TS_ID, policy)
        assert resolved.value_hartree == pytest.approx(-69.90, abs=1e-9)

    def test_policy_from_native_rejects_bad_scopes(self) -> None:
        with pytest.raises(ValueError):
            policy_from_native(
                {
                    "energy_mode": "composite",
                    "electronic_source_steps": "ts_sp",
                }
            )
        with pytest.raises(ValueError):
            policy_from_native(
                {
                    "energy_mode": "composite",
                    "correction_source_steps": ["ts_freq", "  "],
                }
            )


# ---------------------------------------------------------------------------
# Formal V4RunApplication TSPES run with asymmetric legs + durable manifest.
# ---------------------------------------------------------------------------

WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.760000 0.590000 0.000000
H 0.760000 -0.590000 0.000000
"""

DISPATCH_BODY = """import importlib.util
import os
import sys

FAKE_ORCA = {fake_orca_path!r}
FAKE_IRC = {fake_irc_path!r}

inp = open(sys.argv[1]).read()
cwd = os.getcwd()
if 'IRC' in inp:
    os.execv(sys.executable, [sys.executable, FAKE_IRC, *sys.argv[1:]])
spec = importlib.util.spec_from_file_location('fake_orca', FAKE_ORCA)
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
if ' SP' in inp:
    os.environ['FAKE_MODE'] = 'success_sp'
    f.ENERGY_HARTREE = -70.0 if 'ts_sp' in cwd else -80.0
elif 'Freq' in inp:
    os.environ['FAKE_MODE'] = 'success_freq_noshift'
    if 'ts_freq' in cwd:
        f.ENERGY_HARTREE = -60.0
        f.GIBBS_CORRECTION = 0.10
    else:
        f.ENERGY_HARTREE = -65.0
        f.GIBBS_CORRECTION = 0.20
elif 'OptTS' in inp:
    os.environ['FAKE_MODE'] = 'ts_candidate'
else:
    os.environ['FAKE_MODE'] = 'success_opt'
sys.exit(f.main(sys.argv))
"""


def _install_asymmetric_orca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the asymmetric dispatching ``orca`` first on PATH."""
    fakes_dir = Path(__file__).resolve().parent / "fakes"
    bin_dir = tmp_path / "bin-asym"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        + DISPATCH_BODY.format(
            fake_orca_path=str(fakes_dir / "fake_orca.py"),
            fake_irc_path=str(fakes_dir / "fake_irc.py"),
        )
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")


def _asymmetric_inputs() -> Any:
    """Build one TS run input with an explicit reaction group key."""
    (record,) = tuple(import_xyz(WATER_XYZ, source_name="ts-asym.xyz"))
    keyed = replace(record, group_key="rxn-asym", lineage_root_id="root-asym")
    return StructureSet.of(keyed)


def _asymmetric_document() -> dict[str, Any]:
    """Return the catalog TSPES document plus required global defaults."""
    document = copy.deepcopy(get_recipe_v4("tspes")["document"])
    document["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    return document


def _run_asymmetric_tspes(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    """Run one asymmetric TSPES chain through the formal application."""
    run_root = str(tmp_path / "run")
    report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=_asymmetric_document(),
            run_inputs=RunInputs(structures=FrozenDict({"structures": _asymmetric_inputs()})),
            run_root=run_root,
        )
    )
    assert report.status == "completed", [
        (step.step_id, dict(step.summary)) for step in report.step_results
    ]
    manifest = json.loads((Path(run_root) / RUN_RESULT_FILENAME).read_text())
    return report, manifest


class TestFormalTspesAsymmetric:
    """Formal TSPES run: barrier 9.90 from SP legs + freq corrections."""

    def test_formal_run_barrier_is_990(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_asymmetric_orca(tmp_path, monkeypatch)
        report, _ = _run_asymmetric_tspes(tmp_path)
        by_id = {result.step_id: result for result in report.step_results}
        analysis = by_id["reaction_profile"]
        assert analysis.status.value == "completed"
        by_kind = {record.kind: record for record in analysis.results}
        assert set(by_kind) == {
            KIND_BARRIER_FORWARD_ENDPOINT,
            KIND_BARRIER_REVERSE_ENDPOINT,
            KIND_ENDPOINT_GIBBS_DELTA,
            KIND_ENDPOINT_ENERGY_DELTA,
            KIND_REACTION_PROFILE,
        }
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(9.90, abs=1e-6)
        assert by_kind[KIND_BARRIER_REVERSE_ENDPOINT].value == pytest.approx(9.90, abs=1e-6)
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER, abs=1e-6
        )
        profile = dict(by_kind[KIND_REACTION_PROFILE].value)
        assert profile["group_key"] == GROUP_KEY
        assert profile["gibbs_energy"]["ts"]["value"] == pytest.approx(-69.90, abs=1e-6)
        assert profile["gibbs_energy"]["forward"]["value"] == pytest.approx(-79.80, abs=1e-6)
        assert profile["gibbs_energy"]["reverse"]["value"] == pytest.approx(-79.80, abs=1e-6)
        assert profile["electronic_energy"]["ts"]["value"] == pytest.approx(-70.0, abs=1e-9)
        assert profile["electronic_energy"]["forward"]["value"] == pytest.approx(-80.0, abs=1e-9)
        # The cited sources prove the composite legs: high-level SP
        # electronic energies plus low-level frequency corrections.
        assert profile["source_result_ids"] == sorted(profile["source_result_ids"])
        cited = {str(item) for item in profile["source_result_ids"]}
        step_kinds: dict[str, tuple[str | None, str]] = {}
        for step_result in report.step_results:
            for record in step_result.results:
                if record.result_id in cited:
                    step_kinds[record.result_id] = (
                        record.source_step_id,
                        record.kind,
                    )
        assert set(cited) == set(step_kinds)
        legs = sorted(step_kinds.values())
        assert legs == [
            ("endpoint_freq", "gibbs_correction"),
            ("endpoint_freq", "gibbs_correction"),
            ("endpoint_sp", "energy"),
            ("endpoint_sp", "energy"),
            ("ts_freq", "gibbs_correction"),
            ("ts_sp", "energy"),
        ]

    def test_durable_manifest_barrier_is_990(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_asymmetric_orca(tmp_path, monkeypatch)
        _, manifest = _run_asymmetric_tspes(tmp_path)
        groups = [entry for entry in manifest["analyses"] if "group_key" in entry]
        assert [entry["group_key"] for entry in groups] == [GROUP_KEY]
        (entry,) = groups
        assert entry["capability"] == "reaction_profile"
        assert entry["step_id"] == "reaction_profile"
        assert set(entry["barriers"]) == {"forward_endpoint", "reverse_endpoint"}
        for barrier in entry["barriers"].values():
            assert barrier["value"] == pytest.approx(9.90, abs=1e-6)
            assert barrier["unit"] == "hartree"
        published = {item["result_id"]: item for item in manifest.get("results", [])}
        assert published
        assert set(entry["source_result_ids"]) <= set(published)
        cited_kinds = sorted(
            (published[result_id]["source_step_id"], published[result_id]["kind"])
            for result_id in entry["source_result_ids"]
        )
        assert cited_kinds == [
            ("endpoint_freq", "gibbs_correction"),
            ("endpoint_freq", "gibbs_correction"),
            ("endpoint_sp", "energy"),
            ("endpoint_sp", "energy"),
            ("ts_freq", "gibbs_correction"),
            ("ts_sp", "energy"),
        ]
