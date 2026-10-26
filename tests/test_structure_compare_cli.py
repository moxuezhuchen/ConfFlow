"""Tests for ``confflow compare`` / ``confflow dedupe`` (application CLI layer)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolTransforms

from confflow.application import structure_compare as sc
from confflow.application.structure_compare import (
    StructureCompareError,
    _split_selector,
    main,
    parse_energy,
    parse_xyz_frames,
)
from confflow.cli import main as cli_main


def _embedded_tbu_benzene():
    """Return (mol, elements, coords) for tert-butylbenzene with hydrogens."""
    mol = Chem.AddHs(Chem.MolFromSmiles("CC(C)(C)c1ccccc1"))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    AllChem.MMFFOptimizeMolecule(mol)
    elements = [atom.GetSymbol() for atom in mol.GetAtoms()]
    coords = np.array(mol.GetConformer().GetPositions())
    return mol, elements, coords


def _quaternary(mol):
    """Return the tBu central carbon: four heavy neighbours and no hydrogens."""
    return next(
        atom
        for atom in mol.GetAtoms()
        if atom.GetSymbol() == "C"
        and atom.GetDegree() == 4
        and all(n.GetSymbol() != "H" for n in atom.GetNeighbors())
    )


def _methyl_groups(mol) -> list[list[int]]:
    """Methyl carbon followed by its three hydrogens, for each tBu methyl."""
    quaternary = _quaternary(mol)
    groups = []
    for neighbour in quaternary.GetNeighbors():
        hydrogens = [n.GetIdx() for n in neighbour.GetNeighbors() if n.GetSymbol() == "H"]
        if len(hydrogens) == 3:
            groups.append([neighbour.GetIdx(), *hydrogens])
    return groups


def _swap_methyls_coords(mol, coords: np.ndarray) -> np.ndarray:
    """Relabel two tBu methyl groups: same geometry, permuted atom identities."""
    groups = _methyl_groups(mol)
    perm = list(range(len(coords)))
    for a, b in zip(groups[0], groups[1], strict=True):
        perm[a], perm[b] = b, a
    return coords[perm]


def _rotated_phenyl_coords(mol, coords: np.ndarray) -> np.ndarray:
    """Rotate the phenyl ring 90 degrees about the tBu-phenyl bond (a different conformer)."""
    quaternary = _quaternary(mol)
    ipso = next(n for n in quaternary.GetNeighbors() if n.GetIsAromatic())
    ortho = next(n for n in ipso.GetNeighbors() if n.GetIsAromatic())
    methyl = _methyl_groups(mol)[0][0]
    conf = Chem.Conformer(mol.GetConformer())
    rdMolTransforms.SetDihedralDeg(
        conf, methyl, quaternary.GetIdx(), ipso.GetIdx(), ortho.GetIdx(), 90.0
    )
    return np.array(conf.GetPositions())


def _frame_text(elements, coords, comment: str = "") -> str:
    lines = [str(len(elements)), comment]
    for element, (x, y, z) in zip(elements, coords.tolist(), strict=True):
        lines.append(f"{element} {x:.8f} {y:.8f} {z:.8f}")
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def molecule():
    mol, elements, coords = _embedded_tbu_benzene()
    return {
        "mol": mol,
        "elements": elements,
        "coords": coords,
        "permuted": _swap_methyls_coords(mol, coords),
        "rotated": _rotated_phenyl_coords(mol, coords),
    }


def _write(tmp_path: Path, name: str, frames: list[str]) -> str:
    path = tmp_path / name
    path.write_text("\n".join(frames), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------- parser


def test_parse_multi_frame_with_blank_lines_between_frames():
    text = "2\nfirst\nH 0 0 0\nH 0 0 0.74\n\n\n2\nsecond energy_eh=-1.5\nH 0 0 0\nH 0 0 1.0\n\n"
    frames = parse_xyz_frames(text, source="t.xyz")
    assert len(frames) == 2
    assert frames[0].elements == ("H", "H")
    assert frames[0].comment == "first"
    assert frames[1].comment == "second energy_eh=-1.5"
    assert frames[1].coords.shape == (2, 3)
    assert frames[1].coords[1, 2] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "text, message",
    [
        ("3\nc\nH 0 0 0\nH 0 0 1\n", "truncated frame, expected 3 atoms, found 2"),
        ("2\n", "missing comment line"),
        ("2\nc\nH 0 0 0\n\nH 0 0 1\n", "truncated frame, expected 2 atoms, found 1"),
        ("2\nc\nH 0 0 0\nH 0 x 1\n", "non-numeric coordinate"),
        ("2\nc\nH 0 0\nH 0 0 1\n", "expected an element and three coordinates"),
        ("two\nc\nH 0 0 0\n", "expected an atom count"),
        ("0\nc\n", "atom count must be >= 1"),
        ("\n\n", "no XYZ frames found"),
    ],
)
def test_parse_rejects_malformed_frames(text, message):
    with pytest.raises(StructureCompareError, match=message):
        parse_xyz_frames(text, source="bad.xyz")


def test_parse_error_names_source_and_line():
    with pytest.raises(StructureCompareError, match=r"bad.xyz: line 4: non-numeric"):
        parse_xyz_frames("2\nc\nH 0 0 0\nH 0 q 1\n", source="bad.xyz")


# ---------------------------------------------------------------- energy


@pytest.mark.parametrize(
    "comment, expected",
    [
        ("energy_eh=-76.123456", -76.123456),
        ("energy_eh=-1e-3 other", -0.001),
        ("SCF Done E -76.5000 Eh", -76.5),
        ("energy_eh=abc", None),
        ("energy_eh=abc E -2.25", -2.25),
        ("no energy here", None),
    ],
)
def test_parse_energy_formats(comment, expected):
    result = parse_energy(comment)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_split_selector_variants():
    assert _split_selector("a.xyz") == ("a.xyz", 0)
    assert _split_selector("a.xyz@3") == ("a.xyz", 3)
    assert _split_selector("dir@x/a.xyz") == ("dir@x/a.xyz", 0)
    assert _split_selector("@2") == ("@2", 0)


# ---------------------------------------------------------------- compare


def test_compare_symmetry_equivalent_is_same(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    b = _write(tmp_path, "b.xyz", [_frame_text(molecule["elements"], molecule["permuted"])])
    assert main(["compare", a, b]) == 0
    out = capsys.readouterr().out
    assert "verdict: same" in out
    assert "dE: n/a kcal/mol" in out


def test_compare_reports_plain_rmsd_large_and_symmetric_small(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    b = _write(tmp_path, "b.xyz", [_frame_text(molecule["elements"], molecule["permuted"])])
    assert main(["compare", a, b, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["symmetry_rmsd"] < 0.05
    assert report["heavy_atom_rmsd"] > 0.5
    assert report["verdict"] == "same"
    assert set(report) == {
        "a",
        "b",
        "symmetry_rmsd",
        "heavy_atom_rmsd",
        "delta_kcal",
        "rmsd_threshold",
        "e_tol",
        "verdict",
    }


def test_compare_rotated_conformer_is_different(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    b = _write(tmp_path, "b.xyz", [_frame_text(molecule["elements"], molecule["rotated"])])
    assert main(["compare", a, b, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["symmetry_rmsd"] > 0.2
    assert report["verdict"] == "different"


def test_compare_energy_decides_when_geometry_matches(tmp_path, molecule, capsys):
    elements, coords, perm = molecule["elements"], molecule["coords"], molecule["permuted"]
    a = _write(tmp_path, "a.xyz", [_frame_text(elements, coords, "energy_eh=-100.0")])
    close = _write(tmp_path, "close.xyz", [_frame_text(elements, perm, "E -100.0001")])
    far = _write(tmp_path, "far.xyz", [_frame_text(elements, perm, "energy_eh=-100.01")])

    assert main(["compare", a, close, "--json"]) == 0
    near_report = json.loads(capsys.readouterr().out)
    assert near_report["verdict"] == "same"
    assert near_report["delta_kcal"] == pytest.approx(-0.0001 * 627.5094740631, abs=1e-6)

    assert main(["compare", a, far, "--json"]) == 0
    far_report = json.loads(capsys.readouterr().out)
    assert far_report["verdict"] == "different"
    assert far_report["delta_kcal"] == pytest.approx(-0.01 * 627.5094740631, abs=1e-4)

    assert main(["compare", a, far, "--no-energy", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "same"


def test_compare_text_prints_delta_when_both_energies_known(tmp_path, molecule, capsys):
    elements, coords = molecule["elements"], molecule["coords"]
    a = _write(tmp_path, "a.xyz", [_frame_text(elements, coords, "energy_eh=-100.0")])
    b = _write(tmp_path, "b.xyz", [_frame_text(elements, coords, "energy_eh=-100.0005")])
    assert main(["compare", a, b]) == 0
    out = capsys.readouterr().out
    assert "dE: -0.31 kcal/mol" in out
    assert "verdict: same" in out


def test_compare_frame_selector_and_same_file(tmp_path, molecule, capsys):
    elements, coords, perm = molecule["elements"], molecule["coords"], molecule["permuted"]
    path = _write(
        tmp_path,
        "multi.xyz",
        [_frame_text(elements, coords), _frame_text(elements, perm)],
    )
    assert main(["compare", f"{path}@0", f"{path}@1", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["a"] == {"file": path, "frame": 0}
    assert report["b"] == {"file": path, "frame": 1}
    assert report["verdict"] == "same"


def test_compare_options_are_accepted(tmp_path, molecule, capsys):
    elements, coords = molecule["elements"], molecule["coords"]
    a = _write(tmp_path, "a.xyz", [_frame_text(elements, coords)])
    b = _write(tmp_path, "b.xyz", [_frame_text(elements, molecule["permuted"])])
    argv = [
        "compare",
        a,
        b,
        "--no-mirror",
        "--bond-scale",
        "1.2",
        "--metal",
        "1",
        "--pair",
        "1",
        "2",
        "--e-tol",
        "0.3",
        "--rmsd",
        "0.3",
        "--json",
    ]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["rmsd_threshold"] == 0.3


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--rmsd", "0"], "--rmsd must be positive"),
        (["--bond-scale", "0"], "--bond-scale must be positive"),
        (["--e-tol", "-1"], "--e-tol must be non-negative"),
        (["--metal", "0"], "--metal atom 0 out of range"),
        (["--metal", "99"], "--metal atom 99 out of range"),
        (["--pair", "1", "99"], "--pair atom 99 out of range"),
        (["--pair", "2", "2"], "atoms must differ"),
    ],
)
def test_compare_invalid_options_exit_2(tmp_path, molecule, capsys, extra, message):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    assert main(["compare", a, a, *extra]) == 2
    assert message in capsys.readouterr().err


def test_compare_frame_index_out_of_range_exit_2(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    assert main(["compare", f"{a}@5", a]) == 2
    assert "frame index 5 out of range" in capsys.readouterr().err


def test_compare_element_mismatch_exit_2(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    other = _write(tmp_path, "other.xyz", ["2\nc\nH 0 0 0\nH 0 0 1\n"])
    assert main(["compare", a, other]) == 2
    assert "element sequence" in capsys.readouterr().err


def test_compare_missing_file_exit_2(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    assert main(["compare", a, str(tmp_path / "nope.xyz")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_main_requires_known_command(capsys):
    assert main([]) == 2
    assert main(["bogus"]) == 2
    assert "expected 'compare' or 'dedupe'" in capsys.readouterr().err


def test_cli_dispatches_compare_and_dedupe(tmp_path, molecule, capsys):
    a = _write(tmp_path, "a.xyz", [_frame_text(molecule["elements"], molecule["coords"])])
    assert cli_main(["compare", a, a]) == 0
    assert "verdict: same" in capsys.readouterr().out
    assert cli_main(["dedupe", a]) == 0
    assert "1 frames -> 1 unique" in capsys.readouterr().out


# ---------------------------------------------------------------- dedupe


def _dedupe_inputs(tmp_path, molecule, *, with_energy: bool):
    elements, coords = molecule["elements"], molecule["coords"]
    if with_energy:
        e_a, e_perm, e_rot = "energy_eh=-100.0", "energy_eh=-100.0000002", "energy_eh=-99.0"
    else:
        e_a = e_perm = e_rot = ""
    one = _write(
        tmp_path,
        "one.xyz",
        [_frame_text(elements, coords, e_a), _frame_text(elements, molecule["permuted"], e_perm)],
    )
    two = _write(
        tmp_path,
        "two.xyz",
        [_frame_text(elements, molecule["rotated"], e_rot), _frame_text(elements, coords, e_a)],
    )
    return one, two


def test_dedupe_text_report_and_membership(tmp_path, molecule, capsys):
    one, two = _dedupe_inputs(tmp_path, molecule, with_energy=False)
    assert main(["dedupe", one, two]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "4 frames -> 2 unique"
    assert lines[1] == f"{one}@0: unique"
    assert lines[2].startswith(f"{one}@1: duplicate of {one}@0 (rmsd ")
    assert "dE" not in lines[2]
    assert lines[3] == f"{two}@0: unique"
    assert lines[4].startswith(f"{two}@1: duplicate of {one}@0 (rmsd ")


def test_dedupe_output_file_members_and_energy_order(tmp_path, molecule, capsys):
    one, two = _dedupe_inputs(tmp_path, molecule, with_energy=True)
    out_path = str(tmp_path / "unique.xyz")
    assert main(["dedupe", one, two, "-o", out_path]) == 0
    text = capsys.readouterr().out
    assert "4 frames -> 2 unique" in text
    assert "dE" in text
    frames = parse_xyz_frames(Path(out_path).read_text(encoding="utf-8"), source=out_path)
    assert len(frames) == 2
    # The -100 group keeps its lowest member (-100.0000002, one@1) and is written first;
    # the rotated frame (-99.0) is a singleton written after it.
    assert frames[0].comment == "energy_eh=-100.0000002 members=3"
    assert frames[1].comment == "energy_eh=-99.0 members=1"
    assert frames[0].elements == tuple(molecule["elements"])
    assert np.allclose(frames[0].coords, molecule["permuted"], atol=1e-6)


def test_dedupe_output_without_comment_and_input_order(tmp_path, molecule, capsys):
    elements = molecule["elements"]
    path = _write(
        tmp_path,
        "plain.xyz",
        [_frame_text(elements, molecule["rotated"]), _frame_text(elements, molecule["permuted"])],
    )
    out_path = str(tmp_path / "out.xyz")
    assert main(["dedupe", path, "-o", out_path]) == 0
    frames = parse_xyz_frames(Path(out_path).read_text(encoding="utf-8"), source=out_path)
    # Energies unknown: kept frames stay in input order; empty comments get members only.
    assert [frame.comment for frame in frames] == ["members=1", "members=1"]
    assert np.allclose(frames[0].coords, molecule["rotated"], atol=1e-6)
    assert np.allclose(frames[1].coords, molecule["permuted"], atol=1e-6)


def test_dedupe_energy_duplicate_reports_delta(tmp_path, molecule, capsys):
    elements, coords, perm = molecule["elements"], molecule["coords"], molecule["permuted"]
    path = _write(
        tmp_path,
        "dup.xyz",
        [
            _frame_text(elements, coords, "energy_eh=-100.0"),
            _frame_text(elements, perm, "energy_eh=-99.9999999"),
        ],
    )
    assert main(["dedupe", path, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["unique"] == 1
    dup = report["frames"][1]
    assert dup["representative_frame"] == 0
    assert dup["rmsd"] < 0.05
    assert dup["delta_kcal"] == pytest.approx(0.0, abs=1e-2)


def test_dedupe_energy_gate_and_no_energy(tmp_path, molecule, capsys):
    elements, coords, perm = molecule["elements"], molecule["coords"], molecule["permuted"]
    path = _write(
        tmp_path,
        "gate.xyz",
        [
            _frame_text(elements, coords, "energy_eh=-100.0"),
            _frame_text(elements, perm, "energy_eh=-99.99"),
        ],
    )
    assert main(["dedupe", path, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["unique"] == 2
    assert report["output"] is None
    assert main(["dedupe", path, "--no-energy", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unique"] == 1


def test_dedupe_json_report_keys(tmp_path, molecule, capsys):
    one, two = _dedupe_inputs(tmp_path, molecule, with_energy=False)
    out_path = str(tmp_path / "rep.xyz")
    assert main(["dedupe", one, two, "--json", "-o", out_path]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 4
    assert report["unique"] == 2
    assert report["output"] == out_path
    assert len(report["frames"]) == 4
    assert set(report["frames"][0]) == {
        "file",
        "frame",
        "representative_file",
        "representative_frame",
        "rmsd",
        "delta_kcal",
    }
    assert report["frames"][0]["rmsd"] is None
    assert report["frames"][0]["delta_kcal"] is None


def test_dedupe_rejects_element_mismatch_across_files(tmp_path, molecule, capsys):
    one, _ = _dedupe_inputs(tmp_path, molecule, with_energy=False)
    other = _write(tmp_path, "other.xyz", ["2\nc\nH 0 0 0\nH 0 0 1\n"])
    assert main(["dedupe", one, other]) == 2
    assert "element sequence" in capsys.readouterr().err


def test_dedupe_output_write_failure_exit_2(tmp_path, molecule, capsys):
    one, _ = _dedupe_inputs(tmp_path, molecule, with_energy=False)
    bad = str(tmp_path / "missing_dir" / "out.xyz")
    assert main(["dedupe", one, "-o", bad]) == 2
    assert "cannot write" in capsys.readouterr().err


def test_dedupe_invalid_index_exit_2(tmp_path, molecule, capsys):
    one, _ = _dedupe_inputs(tmp_path, molecule, with_energy=False)
    assert main(["dedupe", one, "--pair", "1", "50"]) == 2
    assert "out of range" in capsys.readouterr().err


def test_dedupe_options_are_accepted(tmp_path, molecule, capsys):
    one, _ = _dedupe_inputs(tmp_path, molecule, with_energy=False)
    assert main(["dedupe", one, "--rmsd", "0.2", "--no-mirror", "--metal", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 2


def test_module_exposes_main_and_constants():
    assert sc.EXIT_USAGE == 2
    assert callable(sc.main)
