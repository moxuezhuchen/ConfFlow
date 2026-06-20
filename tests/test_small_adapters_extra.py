"""Tests for small compatibility adapters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from confflow.blocks.refine.result import RefineResult
from confflow.calc.components.parser import parse_output
from confflow.calc.postprocess import run_refine_postprocess
from confflow.calc.psutil_compat import maybe_import_psutil, psutil_exception_types
from confflow.core.chem_validation import (
    ChainValidator,
    load_mol_from_xyz,
    validate_chain_definitions,
)


def test_parse_output_missing_and_invalid_policy(tmp_path) -> None:
    assert parse_output(str(tmp_path / "missing.log"), {}, 1) == {}
    log = tmp_path / "job.log"
    log.write_text("content", encoding="utf-8")
    assert parse_output(str(log), {}, 999) == {}


def test_parse_output_delegates_to_policy(tmp_path) -> None:
    log = tmp_path / "job.log"
    log.write_text("content", encoding="utf-8")
    policy = Mock()
    policy.parse_output.return_value = {"energy": -1.0}

    with patch("confflow.calc.components.parser.get_policy", return_value=policy):
        assert parse_output(str(log), {"itask": "sp"}, 1, is_sp_task=True) == {"energy": -1.0}

    policy.parse_output.assert_called_once_with(str(log), {"itask": "sp"}, is_sp_task=True)


def test_run_refine_postprocess_returns_refine_result(tmp_path) -> None:
    expected = RefineResult(produced_output=True, output_path="out.xyz", kept_count=2)

    with patch("confflow.calc.postprocess.refine.process_xyz", return_value=expected) as process:
        result = run_refine_postprocess(
            input_file="in.xyz",
            output_file="out.xyz",
            threshold=0.2,
            ewin=5.0,
            energy_tolerance=0.1,
            workers=2,
            noH=True,
            dedup_only=True,
            keep_all_topos=True,
            imag=0,
            max_conformers=3,
        )

    assert result is expected
    options = process.call_args.args[0]
    assert options.input_file == "in.xyz"
    assert options.output == "out.xyz"
    assert options.max_conformers == 3


def test_run_refine_postprocess_wraps_legacy_return(tmp_path) -> None:
    output = tmp_path / "out.xyz"
    output.write_text("0\n\n", encoding="utf-8")

    with patch("confflow.calc.postprocess.refine.process_xyz", return_value=None):
        result = run_refine_postprocess(
            input_file="in.xyz",
            output_file=str(output),
            threshold=0.2,
            ewin=None,
            energy_tolerance=0.1,
            workers=1,
        )

    assert result.produced_output is True
    assert result.output_path == str(output)
    assert result.reason == "legacy_refine_return"


def test_psutil_exception_types_includes_valid_psutil_error() -> None:
    class FakePsutilError(Exception):
        pass

    types = psutil_exception_types(SimpleNamespace(Error=FakePsutilError))
    assert types[0] is FakePsutilError
    assert RuntimeError in types
    assert psutil_exception_types(SimpleNamespace(Error=object)) == (
        AttributeError,
        OSError,
        RuntimeError,
    )


def test_maybe_import_psutil_returns_none_when_unavailable() -> None:
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        assert maybe_import_psutil() is None


def test_validate_chain_definitions_returns_invalid_messages() -> None:
    validator = Mock()
    validator.validate_mol.return_value = [
        {"valid": True, "raw_chain": "1-2"},
        {"valid": False, "raw_chain": "2-3", "error": "not bonded"},
    ]

    with (
        patch("confflow.core.chem_validation.ChainValidator", return_value=validator),
        patch("confflow.core.chem_validation.load_mol_from_xyz", return_value=object()) as load_mol,
    ):
        messages = validate_chain_definitions(
            input_file="mol.xyz",
            chains=["1-2", "2-3"],
            bond_threshold=1.2,
        )

    load_mol.assert_called_once_with("mol.xyz", 1.2)
    validator.validate_mol.assert_called_once()
    assert messages == ["2-3: not bonded"]


def test_chem_validation_wrappers_delegate_to_confgen_modules() -> None:
    fake_mol = object()
    fake_validator = object()

    with (
        patch("confflow.blocks.confgen.generator.load_mol_from_xyz", return_value=fake_mol) as load,
        patch(
            "confflow.blocks.confgen.validator.ChainValidator",
            return_value=fake_validator,
        ) as validator_cls,
    ):
        assert load_mol_from_xyz("mol.xyz", 1.1) is fake_mol
        assert ChainValidator(["1-2"]) is fake_validator

    load.assert_called_once_with("mol.xyz", 1.1)
    validator_cls.assert_called_once_with(["1-2"])
