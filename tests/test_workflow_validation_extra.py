"""Additional workflow input validation tests."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from confflow.workflow.validation import validate_inputs_compatible


def _write_xyz(path, atoms: list[str], *, comment: str = "mol") -> None:
    lines = [str(len(atoms)), comment]
    for idx, atom in enumerate(atoms):
        lines.append(f"{atom} {idx}.0 0.0 0.0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_validate_inputs_compatible_rejects_no_inputs() -> None:
    with pytest.raises(ValueError, match="no input files provided"):
        validate_inputs_compatible([])


def test_validate_inputs_compatible_accepts_matching_inputs(tmp_path) -> None:
    first = tmp_path / "a.xyz"
    second = tmp_path / "b.xyz"
    _write_xyz(first, ["C", "H", "H"])
    _write_xyz(second, ["C", "H", "H"])

    validate_inputs_compatible([str(first), str(second)])


def test_validate_inputs_compatible_rejects_atom_count_mismatch(tmp_path) -> None:
    first = tmp_path / "a.xyz"
    second = tmp_path / "b.xyz"
    _write_xyz(first, ["C", "H", "H"])
    _write_xyz(second, ["C", "H"])

    with pytest.raises(ValueError, match="atom count mismatch"):
        validate_inputs_compatible([str(first), str(second)])


def test_validate_inputs_compatible_rejects_order_without_chain_mapping(tmp_path) -> None:
    first = tmp_path / "a.xyz"
    second = tmp_path / "b.xyz"
    _write_xyz(first, ["C", "O", "H"])
    _write_xyz(second, ["O", "C", "H"])

    with pytest.raises(ValueError, match="element order mismatch"):
        validate_inputs_compatible([str(first), str(second)])


def test_validate_inputs_compatible_allows_reorder_with_chain_mapping(tmp_path) -> None:
    first = tmp_path / "a.xyz"
    second = tmp_path / "b.xyz"
    _write_xyz(first, ["C", "O", "H"])
    _write_xyz(second, ["O", "C", "H"])

    validate_inputs_compatible([str(first), str(second)], confgen_params={"chains": ["1-2"]})


def test_validate_inputs_compatible_rejects_composition_mismatch_with_chain_mapping(
    tmp_path,
) -> None:
    first = tmp_path / "a.xyz"
    second = tmp_path / "b.xyz"
    _write_xyz(first, ["C", "O", "H"])
    _write_xyz(second, ["N", "C", "H"])

    with pytest.raises(ValueError, match="element composition mismatch"):
        validate_inputs_compatible([str(first), str(second)], confgen_params={"chain": "1-2"})


def test_validate_inputs_compatible_rejects_multiframe_input(tmp_path) -> None:
    multi = tmp_path / "multi.xyz"
    multi.write_text(
        "1\none\nH 0 0 0\n1\ntwo\nH 0 0 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="single-frame XYZ"):
        validate_inputs_compatible([str(multi), str(multi)])


def test_validate_inputs_compatible_force_consistency_warns(tmp_path, caplog) -> None:
    first = tmp_path / "a.xyz"
    second = tmp_path / "b.xyz"
    _write_xyz(first, ["C"])
    _write_xyz(second, ["H"])

    validate_inputs_compatible([str(first), str(second)], force_consistency=True)

    assert "force_consistency=true" in caplog.text


def test_validate_inputs_compatible_reports_invalid_chain_reference(tmp_path) -> None:
    xyz = tmp_path / "mol.xyz"
    _write_xyz(xyz, ["C", "H"])
    fake_validator = Mock()
    fake_validator.validate_mol.return_value = [{"raw_chain": "1-9", "error": "out of range"}]

    with (
        patch("confflow.workflow.validation.ChainValidator", return_value=fake_validator),
        patch("confflow.workflow.validation.load_mol_from_xyz", return_value=object()),
        pytest.raises(ValueError, match="Flexible chains are invalid"),
    ):
        validate_inputs_compatible(
            [str(xyz)],
            confgen_params={"chains": ["1-9"], "validate_chain_bonds": True},
        )


def test_validate_inputs_compatible_reports_chain_loader_failure(tmp_path) -> None:
    xyz = tmp_path / "mol.xyz"
    _write_xyz(xyz, ["C", "H"])

    with (
        patch("confflow.workflow.validation.load_mol_from_xyz", side_effect=RuntimeError("boom")),
        pytest.raises(ValueError, match="failed to validate flexible chains: boom"),
    ):
        validate_inputs_compatible(
            [str(xyz)],
            confgen_params={"chains": ["1-2"], "validate_chain_bonds": True},
        )
