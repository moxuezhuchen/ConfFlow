"""R5 Structured Calculation Model tests (RFC §18).

Tests the ``confflow/config/canonical/theory.py`` module:
- TheorySpec dataclass (program/task/method/basis/dispersion/solvent)
- Program capability metadata
- Task alias normalization (opt_freq remains atomic)
- Keyword compiler for g16 and orca
- Consistency validator (structured vs raw keyword)
- Editor metadata roundtrip (deterministic)
- V3 schema digest unchanged (additive backward-compatible)
- V1 engine unaffected
"""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.config.canonical.theory import (
    PROGRAM_CAPABILITIES,
    TheorySpec,
    compile_theory_keyword,
    editor_metadata_to_theory,
    normalize_program_name,
    normalize_task_name,
    program_supports_task,
    theory_to_editor_metadata,
    validate_theory_keyword_consistency,
)


# ---------------------------------------------------------------------------
# R5-1: Structured metadata matches validation authority
# ---------------------------------------------------------------------------
class TestR5StructuredMetadataMatchesAuthority:
    def test_program_capabilities_known_programs(self) -> None:
        assert "g16" in PROGRAM_CAPABILITIES
        assert "orca" in PROGRAM_CAPABILITIES

    def test_g16_supports_required_tasks(self) -> None:
        for task in ("opt", "sp", "freq", "opt_freq", "ts"):
            assert program_supports_task("g16", task), f"g16 must support {task}"

    def test_orca_supports_required_tasks(self) -> None:
        for task in ("opt", "sp", "freq", "opt_freq", "ts"):
            assert program_supports_task("orca", task), f"orca must support {task}"

    def test_unknown_program_not_supported(self) -> None:
        assert not program_supports_task("turbomole", "opt")

    def test_unknown_task_not_supported(self) -> None:
        assert not program_supports_task("g16", "future_task")


# ---------------------------------------------------------------------------
# R5-2: g16/orca capability consistency
# ---------------------------------------------------------------------------
class TestR5ProgramCapabilityConsistency:
    def test_normalize_g16_aliases(self) -> None:
        for alias in ("g16", "gaussian", "gau", "g09", "g03", "1"):
            assert normalize_program_name(alias) == "g16", f"alias {alias!r} should map to g16"

    def test_normalize_orca_aliases(self) -> None:
        for alias in ("orca", "2"):
            assert normalize_program_name(alias) == "orca", f"alias {alias!r} should map to orca"

    def test_unknown_program_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported calc program"):
            normalize_program_name("turbomole")

    def test_g16_dispersion_models(self) -> None:
        caps = PROGRAM_CAPABILITIES["g16"]
        assert "d3bj" in caps["dispersion_models"]
        assert "d3" in caps["dispersion_models"]

    def test_orca_dispersion_models(self) -> None:
        caps = PROGRAM_CAPABILITIES["orca"]
        assert "d3bj" in caps["dispersion_models"]
        assert "d4" in caps["dispersion_models"]


# ---------------------------------------------------------------------------
# R5-3: Task aliases normalize correctly
# ---------------------------------------------------------------------------
class TestR5TaskAliasNormalization:
    def test_opt_aliases(self) -> None:
        for alias in ("opt", "optimize", "0"):
            assert normalize_task_name(alias) == "opt"

    def test_sp_aliases(self) -> None:
        for alias in ("sp", "energy", "single_point", "singlepoint", "1"):
            assert normalize_task_name(alias) == "sp"

    def test_freq_aliases(self) -> None:
        for alias in ("freq", "frequency", "2"):
            assert normalize_task_name(alias) == "freq"

    def test_opt_freq_aliases(self) -> None:
        for alias in ("opt_freq", "optfreq", "opt+freq", "opt-freq", "opt_frequency", "3"):
            assert normalize_task_name(alias) == "opt_freq", f"alias {alias!r} should be opt_freq"

    def test_ts_aliases(self) -> None:
        for alias in ("ts", "transition_state", "transitionstate", "4"):
            assert normalize_task_name(alias) == "ts"

    def test_unknown_task_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported calc task"):
            normalize_task_name("geometry_scan")


# ---------------------------------------------------------------------------
# R5-4: opt_freq remains atomic
# ---------------------------------------------------------------------------
class TestR5OptFreqAtomic:
    def test_normalize_opt_freq_is_not_split(self) -> None:
        assert normalize_task_name("opt_freq") == "opt_freq"
        assert normalize_task_name("opt+freq") == "opt_freq"
        # opt_freq is distinct from opt or freq individually
        assert normalize_task_name("opt_freq") != "opt"
        assert normalize_task_name("opt_freq") != "freq"

    def test_compile_g16_opt_freq_produces_combined_keyword(self) -> None:
        theory = TheorySpec(method="B3LYP", basis="def2-SVP")
        kw = compile_theory_keyword(theory, program="g16", task="opt_freq")
        assert "opt" in kw.lower()
        assert "freq" in kw.lower()

    def test_compile_orca_opt_freq_produces_combined_keyword(self) -> None:
        theory = TheorySpec(method="B3LYP", basis="def2-SVP")
        kw = compile_theory_keyword(theory, program="orca", task="opt_freq")
        assert "Opt" in kw
        assert "Freq" in kw

    def test_theory_spec_task_opt_freq_roundtrip(self) -> None:
        spec = TheorySpec(program="g16", task="opt_freq", method="B3LYP", basis="def2-SVP")
        assert spec.task == "opt_freq"
        d = spec.to_dict()
        spec2 = TheorySpec.from_dict(d)
        assert spec2.task == "opt_freq"


# ---------------------------------------------------------------------------
# R5-5: Raw advanced escape hatch preserved
# ---------------------------------------------------------------------------
class TestR5RawKeywordEscapeHatch:
    def test_theory_spec_without_keyword_fully_valid(self) -> None:
        """params.keyword alone is completely legal and unchanged."""
        spec = TheorySpec()
        # A bare spec (no fields) can still be used; method is required only for compile
        assert spec.program is None
        assert spec.task is None

    def test_compile_theory_requires_method(self) -> None:
        """compile_theory_keyword requires a method to produce meaningful output."""
        spec = TheorySpec(basis="def2-SVP")
        with pytest.raises(ValueError, match="method"):
            compile_theory_keyword(spec, program="g16", task="opt")

    def test_validate_consistency_empty_keyword_passes(self) -> None:
        """Empty keyword means no raw escape hatch — validation always passes."""
        spec = TheorySpec(method="B3LYP", basis="def2-SVP")
        validate_theory_keyword_consistency(spec, keyword="", program="g16", task="opt")
        validate_theory_keyword_consistency(spec, keyword="   ", program="g16", task="opt")

    def test_keyword_only_no_theory_is_fine(self) -> None:
        """A bare keyword with no theory spec needs no consistency check."""
        bare = TheorySpec()
        validate_theory_keyword_consistency(
            bare, keyword="B3LYP/def2-SVP opt", program="g16", task="opt"
        )


# ---------------------------------------------------------------------------
# R5-6: Editor metadata roundtrip deterministic
# ---------------------------------------------------------------------------
class TestR5EditorMetadataRoundtrip:
    def test_roundtrip_simple_spec(self) -> None:
        spec = TheorySpec(program="g16", task="opt", method="B3LYP", basis="def2-SVP")
        meta = theory_to_editor_metadata(spec)
        spec2 = editor_metadata_to_theory(meta)
        assert spec == spec2

    def test_roundtrip_with_dispersion_and_solvent(self) -> None:
        spec = TheorySpec(
            program="orca",
            task="sp",
            method="wB97X-D",
            basis="def2-TZVP",
            dispersion="D3BJ",
            solvent="water",
        )
        meta = theory_to_editor_metadata(spec)
        spec2 = editor_metadata_to_theory(meta)
        assert spec == spec2

    def test_roundtrip_is_deterministic(self) -> None:
        spec = TheorySpec(program="g16", task="opt_freq", method="B3LYP", basis="6-31G*")
        meta1 = theory_to_editor_metadata(spec)
        meta2 = theory_to_editor_metadata(spec)
        assert meta1 == meta2

    def test_from_dict_unknown_field_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown theory field"):
            TheorySpec.from_dict({"method": "B3LYP", "not_a_field": "x"})


# ---------------------------------------------------------------------------
# R5-7: V3 schema digest pins the R5-chartered additive `params.theory` key
# ---------------------------------------------------------------------------
class TestR5SchemaDigestAdditiveTheoryKey:
    """R5 chartered exactly one additive V3 vocabulary key (params.theory).

    These pins assert the CURRENT digests by recomputation-equality
    (digest == canonical_sha256(schema bytes)) plus v3 != v2 — never by
    hardcoding a hex string, and never claiming the schema is "unchanged".
    Any further vocabulary change (e.g. the R5 nested-theory tightening)
    intentionally moves the V3 digests; V2 stays byte-stable.
    """

    def test_v3_document_digest_matches_canonical_schema_bytes(self) -> None:
        from confflow.config.canonical.schema import (
            SchemaProfile,
            workflow_json_schema_v3,
            workflow_schema_sha256_v3,
        )
        from confflow.config.canonical.serialization import canonical_sha256

        assert workflow_schema_sha256_v3() == canonical_sha256(
            workflow_json_schema_v3(SchemaProfile.DOCUMENT)
        )

    def test_v3_fragment_digest_matches_canonical_schema_bytes(self) -> None:
        from confflow.config.canonical.schema import (
            SchemaProfile,
            workflow_fragment_schema_sha256_v3,
            workflow_json_schema_v3,
        )
        from confflow.config.canonical.serialization import canonical_sha256

        assert workflow_fragment_schema_sha256_v3() == canonical_sha256(
            workflow_json_schema_v3(SchemaProfile.FRAGMENT)
        )

    def test_v2_digest_matches_canonical_schema_bytes(self) -> None:
        from confflow.config.canonical.schema import workflow_json_schema, workflow_schema_sha256
        from confflow.config.canonical.serialization import canonical_sha256

        assert workflow_schema_sha256() == canonical_sha256(workflow_json_schema())

    def test_v3_digest_differs_from_v2(self) -> None:
        from confflow.config.canonical.schema import (
            workflow_schema_sha256,
            workflow_schema_sha256_v3,
        )

        assert workflow_schema_sha256_v3() != workflow_schema_sha256()

    def test_theory_is_the_r5_additive_calc_key(self) -> None:
        """The single R5 vocabulary addition is present in the registry+schema."""
        from confflow.config.canonical.param_fields import param_properties, v3_calc_keys
        from confflow.config.canonical.schema import SchemaProfile, workflow_json_schema_v3

        assert "theory" in v3_calc_keys()
        assert "theory" in param_properties("calc")
        calc_props = workflow_json_schema_v3(SchemaProfile.DOCUMENT)["$defs"]["calcParams"][
            "properties"
        ]
        assert "theory" in calc_props


# ---------------------------------------------------------------------------
# R5-8: V1 unaffected
# ---------------------------------------------------------------------------
class TestR5V1Unaffected:
    def test_theory_module_does_not_import_from_v3_runtime(self) -> None:
        """theory.py must be a pure utility module without workflow runtime imports."""
        import confflow.config.canonical.theory as theory_mod

        # Ensure no circular or heavy import chain
        assert hasattr(theory_mod, "TheorySpec")
        assert hasattr(theory_mod, "compile_theory_keyword")

    def test_v1_calc_params_can_still_omit_theory(self) -> None:
        """V1/V2 workflow parsing does not require params.theory."""
        from confflow.config.canonical.parser import parse_workflow_mapping

        doc = {
            "steps": [
                {"name": "calc_step", "type": "calc", "params": {"keyword": "B3LYP/def2-SVP opt"}}
            ]
        }
        result = parse_workflow_mapping(doc)
        assert result is not None
        assert "theory" not in result.steps[0].params


# ---------------------------------------------------------------------------
# Keyword compiler tests
# ---------------------------------------------------------------------------
class TestCompileTheoryKeyword:
    def test_g16_basic_opt(self) -> None:
        t = TheorySpec(method="B3LYP", basis="def2-SVP")
        kw = compile_theory_keyword(t, program="g16", task="opt")
        assert "B3LYP/def2-SVP" in kw
        assert "opt" in kw.lower()

    def test_g16_dispersion(self) -> None:
        t = TheorySpec(method="B3LYP", basis="def2-SVP", dispersion="D3BJ")
        kw = compile_theory_keyword(t, program="g16", task="opt")
        assert "empiricaldispersion=gd3bj" in kw.lower()

    def test_g16_solvent(self) -> None:
        t = TheorySpec(method="B3LYP", basis="def2-SVP", solvent="water")
        kw = compile_theory_keyword(t, program="g16", task="opt")
        assert "scrf=" in kw.lower()
        assert "water" in kw.lower()

    def test_orca_basic_sp(self) -> None:
        t = TheorySpec(method="wB97X-D", basis="def2-TZVP")
        kw = compile_theory_keyword(t, program="orca", task="sp")
        assert "wB97X-D" in kw
        assert "def2-TZVP" in kw
        assert "SP" in kw

    def test_orca_ts_task(self) -> None:
        t = TheorySpec(method="B3LYP", basis="def2-SVP")
        kw = compile_theory_keyword(t, program="orca", task="ts")
        assert "OptTS" in kw


# ---------------------------------------------------------------------------
# Consistency validator tests
# ---------------------------------------------------------------------------
class TestValidateTheoryKeywordConsistency:
    def test_consistent_method_passes(self) -> None:
        t = TheorySpec(method="B3LYP", basis="def2-SVP")
        validate_theory_keyword_consistency(t, "B3LYP/def2-SVP opt", program="g16", task="opt")

    def test_mismatched_method_raises(self) -> None:
        t = TheorySpec(method="PBE0")
        with pytest.raises(ValueError, match="method"):
            validate_theory_keyword_consistency(t, "B3LYP/def2-SVP opt", program="g16", task="opt")

    def test_mismatched_task_opt_freq_raises(self) -> None:
        t = TheorySpec(task="opt_freq")
        with pytest.raises(ValueError, match="opt_freq"):
            validate_theory_keyword_consistency(t, "B3LYP/def2-SVP opt", program="g16", task="opt")

    def test_consistent_opt_freq_passes(self) -> None:
        t = TheorySpec(task="opt_freq")
        validate_theory_keyword_consistency(
            t, "B3LYP/def2-SVP opt freq", program="g16", task="opt_freq"
        )

    def test_orca_keyword_disagrees_with_g16_theory(self) -> None:
        t = TheorySpec(program="g16")
        with pytest.raises(ValueError, match="program"):
            validate_theory_keyword_consistency(
                t, "! B3LYP def2-SVP Opt", program="orca", task="opt"
            )


# ---------------------------------------------------------------------------
# X1: Cross-section (structured metadata -> valid V3 calc config)
# ---------------------------------------------------------------------------
class TestX1StructuredMetadataToV3CalcConfig:
    def test_theory_spec_compiles_to_valid_v3_calc_step(self) -> None:
        """TheorySpec -> keyword -> V3 calc step dict passes schema validation."""
        from confflow.config.canonical.validation import validate_workflow_definition

        spec = TheorySpec(program="g16", task="opt", method="B3LYP", basis="def2-SVP")
        keyword = compile_theory_keyword(spec)
        validate_theory_keyword_consistency(spec, keyword, program="g16", task="opt")
        doc = {
            "schema": "confflow.workflow.v3",
            "steps": [
                {
                    "id": "c1",
                    "type": "calc",
                    "inputs": [],
                    "params": {
                        "iprog": "g16",
                        "itask": "opt",
                        "keyword": keyword,
                        "theory": theory_to_editor_metadata(spec),
                    },
                }
            ],
        }
        assert validate_workflow_definition(doc) == []

    def test_opt_freq_v3_step_stays_a_single_calc_node(self) -> None:
        """An opt_freq V3 calc step parses to exactly one step (never expanded)."""
        from confflow.config.canonical.v3_parser import parse_v3_document

        spec = TheorySpec(program="g16", task="opt_freq", method="B3LYP", basis="def2-SVP")
        keyword = compile_theory_keyword(spec)
        doc = {
            "schema": "confflow.workflow.v3",
            "steps": [
                {
                    "id": "c1",
                    "type": "calc",
                    "inputs": [],
                    "params": {
                        "iprog": "g16",
                        "itask": "opt_freq",
                        "keyword": keyword,
                        "theory": spec.to_dict(),
                    },
                }
            ],
        }
        definition = parse_v3_document(doc)
        assert len(definition.steps) == 1
        assert definition.steps[0].params["theory"]["task"] == "opt_freq"


# ---------------------------------------------------------------------------
# T1-T11: R5 review-closure validation wiring (§8-§19)
# ---------------------------------------------------------------------------
V3_SCHEMA = "confflow.workflow.v3"


def _v3_calc_doc(params: dict) -> dict:
    return {
        "schema": V3_SCHEMA,
        "steps": [{"id": "c1", "type": "calc", "inputs": [], "params": params}],
    }


class TestR5TheoryValidationClosureT1T11:
    """Review-closure pins for theory wiring.

    T1 consistent object passes; T2 string shorthand consistent passes;
    T3 string mismatch rejects; T4 unknown nested field schema-rejects;
    T5 invalid dispersion rejects; T6 invalid solvent model rejects;
    T7 keyword-only valid; T8 theory+canonical-keyword valid;
    T9 A-equivalence; T10 C-equivalence (fake executables — depends on the
    ``fake_qc_executables`` fixture owned by tests/conftest.py, Agent A);
    T11 opt_freq atomic.
    """

    def test_t1_consistent_object_passes(self) -> None:
        from confflow.config.canonical.validation import validate_workflow_definition

        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                "keyword": "B3LYP/def2-SVP opt",
                "theory": {
                    "program": "g16",
                    "task": "opt",
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                },
            }
        )
        assert validate_workflow_definition(doc) == []

    def test_t2_string_shorthand_consistent_passes(self) -> None:
        """§8-§9 option A: `theory: "B3LYP/def2-SVP"` is checked, never ignored."""
        from confflow.config.canonical.validation import validate_workflow_definition

        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                "keyword": "B3LYP/def2-SVP opt",
                "theory": "B3LYP/def2-SVP",
            }
        )
        assert validate_workflow_definition(doc) == []

    def test_t3_string_shorthand_mismatch_rejects(self) -> None:
        from confflow.config.canonical.validation import validate_workflow_definition

        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                "keyword": "B3LYP/def2-SVP opt",
                "theory": "PBE0/def2-SVP",
            }
        )
        diagnostics = validate_workflow_definition(doc)
        assert any(
            d.code == "workflow.v3.params_invalid" and "params.theory" in d.path
            for d in diagnostics
        )

    def test_t4_unknown_nested_field_schema_rejects(self) -> None:
        """§10: `methd:`/`basiss:` typos fail at the JSON-schema level."""
        from confflow.config.canonical.validation import validate_workflow_definition

        for bad_theory in ({"methd": "B3LYP"}, {"method": "B3LYP", "basiss": "def2-SVP"}):
            doc = _v3_calc_doc({"keyword": "B3LYP/def2-SVP opt", "theory": bad_theory})
            diagnostics = validate_workflow_definition(doc)
            assert any(d.code == "workflow.v3.schema" for d in diagnostics), bad_theory

    def test_t5_invalid_dispersion_rejects(self) -> None:
        """§18: structured dispersion outside PROGRAM_CAPABILITIES is an error."""
        from confflow.config.canonical.validation import validate_workflow_definition

        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                # Keyword carries the same unknown token so the failure is
                # purely the capability check, not the consistency check.
                "keyword": "B3LYP/def2-SVP opt empiricaldispersion=d9zz",
                "theory": {"method": "B3LYP", "basis": "def2-SVP", "dispersion": "D9ZZ"},
            }
        )
        diagnostics = validate_workflow_definition(doc)
        assert any(
            d.code == "workflow.v3.params_invalid"
            and "params.theory" in d.path
            and "dispersion" in d.message
            for d in diagnostics
        )

    def test_t6_invalid_solvent_model_rejects(self) -> None:
        """§18: structured solvent model outside PROGRAM_CAPABILITIES errors."""
        from confflow.config.canonical.validation import validate_workflow_definition

        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                "keyword": "B3LYP/def2-SVP opt scrf=(cosmo,solvent=water)",
                "theory": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    # Solvent mapping keys are exactly {model, solvent, name}.
                    "solvent": {"model": "cosmo", "solvent": "water"},
                },
            }
        )
        diagnostics = validate_workflow_definition(doc)
        assert any(
            d.code == "workflow.v3.params_invalid"
            and "params.theory" in d.path
            and "solvent" in d.message
            for d in diagnostics
        )

    def test_t7_keyword_only_valid(self) -> None:
        """Raw keyword escape hatch stays unlimited: no theory is fine."""
        from confflow.config.canonical.validation import validate_workflow_definition

        doc = _v3_calc_doc({"keyword": "B3LYP/def2-SVP opt"})
        assert validate_workflow_definition(doc) == []

    def test_t8_theory_plus_canonical_keyword_valid(self) -> None:
        from confflow.config.canonical.validation import validate_workflow_definition

        spec = TheorySpec(program="g16", task="opt", method="B3LYP", basis="def2-SVP")
        keyword = compile_theory_keyword(spec, program="g16", task="opt")
        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                "keyword": keyword,
                "theory": theory_to_editor_metadata(spec),
            }
        )
        assert validate_workflow_definition(doc) == []

    def test_t9_definition_fingerprint_a_equivalence(self) -> None:
        """§14: keyword-only vs equivalent theory+same-keyword share fingerprint A."""
        from confflow.config.canonical.fingerprint import (
            build_workflow_definition_payload_v3,
            workflow_definition_fingerprint_v3,
        )
        from confflow.config.canonical.v3_parser import parse_v3_document

        keyword = "B3LYP/def2-SVP opt"
        doc_keyword_only = _v3_calc_doc({"iprog": "g16", "itask": "opt", "keyword": keyword})
        doc_with_theory = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt",
                "keyword": keyword,
                "theory": {"program": "g16", "task": "opt", "method": "B3LYP", "basis": "def2-SVP"},
            }
        )
        definition_keyword = parse_v3_document(doc_keyword_only)
        definition_theory = parse_v3_document(doc_with_theory)
        payload_keyword = build_workflow_definition_payload_v3(definition_keyword)
        payload_theory = build_workflow_definition_payload_v3(definition_theory)
        # Theory is authoring metadata for the same keyword: it is projected
        # out of the definition payload (the resolver never carries it), so
        # the payloads — and hence fingerprint A — are identical. The
        # fingerprint algorithm itself is untouched (read-only).
        assert payload_keyword == payload_theory
        assert workflow_definition_fingerprint_v3(definition_keyword) == (
            workflow_definition_fingerprint_v3(definition_theory)
        )

    def test_t10_execution_fingerprint_c_equivalence(
        self, tmp_path: Path, fake_qc_executables: dict[str, str]
    ) -> None:
        """§14: same-keyword docs share fingerprint C under the same context."""
        import json

        from confflow.workflow.execution_context import (
            resolve_execution_context_v3,
            workflow_execution_fingerprint_v3,
        )
        from confflow.workflow.plan import build_workflow_plan

        orca_exe = fake_qc_executables["orca"]
        spec = TheorySpec(program="orca", task="sp", method="wB97X-D", basis="def2-TZVP")
        keyword = compile_theory_keyword(spec, program="orca", task="sp")
        xyz = tmp_path / "input.xyz"
        xyz.write_text("2\nseed\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
        doc_keyword_only: dict = {
            "schema": V3_SCHEMA,
            "global": {"iprog": "orca", "itask": "sp", "orca_path": orca_exe},
            "steps": [{"id": "c1", "type": "calc", "inputs": [], "params": {"keyword": keyword}}],
        }
        doc_with_theory: dict = {
            "schema": V3_SCHEMA,
            "global": {"iprog": "orca", "itask": "sp", "orca_path": orca_exe},
            "steps": [
                {
                    "id": "c1",
                    "type": "calc",
                    "inputs": [],
                    "params": {"keyword": keyword, "theory": spec.to_dict()},
                }
            ],
        }
        cfg_keyword = tmp_path / "wf_kw.json"
        cfg_theory = tmp_path / "wf_th.json"
        cfg_keyword.write_text(json.dumps(doc_keyword_only), encoding="utf-8")
        cfg_theory.write_text(json.dumps(doc_with_theory), encoding="utf-8")
        plan_keyword = build_workflow_plan([str(xyz)], str(cfg_keyword))
        plan_theory = build_workflow_plan([str(xyz)], str(cfg_theory))
        context_keyword = resolve_execution_context_v3(plan_keyword, input_files=[str(xyz)])
        context_theory = resolve_execution_context_v3(plan_theory, input_files=[str(xyz)])
        assert plan_keyword.definition_fingerprint == plan_theory.definition_fingerprint
        assert workflow_execution_fingerprint_v3(plan_keyword, context_keyword) == (
            workflow_execution_fingerprint_v3(plan_theory, context_theory)
        )

    def test_t11_opt_freq_stays_atomic(self) -> None:
        """opt_freq is one atomic calc node, never split into opt+freq."""
        from confflow.config.canonical.v3_parser import parse_v3_document
        from confflow.config.canonical.validation import validate_workflow_definition

        spec = TheorySpec(program="g16", task="opt_freq", method="B3LYP", basis="def2-SVP")
        keyword = compile_theory_keyword(spec, program="g16", task="opt_freq")
        doc = _v3_calc_doc(
            {
                "iprog": "g16",
                "itask": "opt_freq",
                "keyword": keyword,
                "theory": spec.to_dict(),
            }
        )
        assert validate_workflow_definition(doc) == []
        definition = parse_v3_document(doc)
        assert len(definition.steps) == 1
        assert definition.steps[0].params["theory"]["task"] == "opt_freq"
        assert normalize_task_name("opt_freq") == "opt_freq"


# ---------------------------------------------------------------------------
# Producer-owned compiler error paths (structured.py coverage + semantics)
# ---------------------------------------------------------------------------
class TestCompileStructuredCalcErrors:
    def test_non_mapping_theory_rejected(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        with pytest.raises(ValueError, match="TheorySpec or a mapping"):
            compile_structured_calc(42)  # type: ignore[arg-type]

    def test_missing_method_and_keyword_rejected(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        with pytest.raises(ValueError, match="theory.method"):
            compile_structured_calc({"task": "opt"})

    def test_contradicting_program_override_rejected(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        with pytest.raises(ValueError, match="program"):
            compile_structured_calc(
                {"program": "g16", "method": "B3LYP"},
                program="orca",
                keyword="! B3LYP Opt",
            )

    def test_disagreeing_keyword_rejected(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        with pytest.raises(ValueError, match="method"):
            compile_structured_calc(
                {"method": "PBE0"},
                program="g16",
                task="opt",
                keyword="B3LYP/def2-SVP opt",
            )

    def test_override_echoed_into_theory(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        params = compile_structured_calc({"method": "B3LYP"}, program="g16", task="opt")
        assert params["iprog"] == "g16"
        assert params["itask"] == "opt"
        assert params["theory"]["program"] == "g16"
        assert params["theory"]["task"] == "opt"
        assert "B3LYP" in params["keyword"]

    def test_explicit_keyword_used_verbatim(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        params = compile_structured_calc(
            {"program": "g16", "task": "opt", "method": "B3LYP"},
            keyword="B3LYP/def2-SVP opt",
        )
        assert params["keyword"] == "B3LYP/def2-SVP opt"

    def test_empty_theory_omitted(self) -> None:
        from confflow.config.canonical.structured import compile_structured_calc

        # Stated overrides are echoed into theory (single mapping, no second
        # unrelated Program field); with no override and no spec, theory is out.
        params = compile_structured_calc(None, program="g16", task="sp", keyword="HF")
        assert params == {
            "iprog": "g16",
            "itask": "sp",
            "keyword": "HF",
            "theory": {"program": "g16", "task": "sp"},
        }
        bare = compile_structured_calc(None, keyword="HF")
        assert "theory" not in bare
