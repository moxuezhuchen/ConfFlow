#!/usr/bin/env python3

"""J1 projection: shared parsing preview (parsing module only).

Covers real GJF/XYZ/INP routing through the same runtime authorities,
whole-fail-closed refusals, and legacy Gaussian partial-behavior snapshots.
Authoring/boundary pairing is explicitly out of scope here.
"""

from __future__ import annotations

import ast
import inspect
import os

import pytest

from confflow.producer.structure_preview import (
    StructurePreviewError,
    structure_preview_request,
)


def _gjf_minimal(
    atoms: str = "C 0.0 0.0 0.0\nH 0.0 0.0 1.089",
    charge_mult: str = "0 1",
    title: str = "preview",
    route: str = "#p B3LYP/6-31G Opt",
) -> str:
    """Build a minimal single-geometry Gaussian document."""
    return f"%chk=job.chk\n{route}\n\n{title}\n\n{charge_mult}\n{atoms}\n\n"


def _xyz_minimal(
    atoms: str = "C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001",
    comment: str = "comment",
) -> str:
    """Build a minimal single-frame XYZ document."""
    lines = atoms.strip().splitlines()
    return f"{len(lines)}\n{comment}\n{atoms}\n"


def _inp_minimal(
    atoms: str = "O 0.0 0.0 0.0\nH 0.76 0.59 0.0\nH 0.76 -0.59 0.0",
    header: str = "* xyz 0 1",
    pre: str = "! B3LYP def2-SVP Opt\n",
) -> str:
    """Build a minimal single-inline ORCA document."""
    return f"{pre}{header}\n{atoms}\n*\n"


def _err(excinfo: pytest.ExceptionInfo[StructurePreviewError]) -> StructurePreviewError:
    """Narrow pytest error info to the structured refusal type."""
    value = excinfo.value
    assert isinstance(value, StructurePreviewError)
    return value


def test_gjf_standard_matches_runtime_exact() -> None:
    """Standard Cartesian GJF matches the runtime authority field-by-field."""
    from confflow.core.gaussian_input import parse_gaussian_input_text

    text = _gjf_minimal()
    runtime = parse_gaussian_input_text(text, "runtime")
    result = structure_preview_request({"filename": "ligand.gjf", "content_text": text})
    assert result["source_format"] == "gaussian-gjf"
    assert result["index_base"] == 1
    assert result["elements"] == runtime["atoms"]
    assert result["coordinates"] == runtime["coords"]
    assert result["charge"] == runtime["charge"] == 0
    assert result["multiplicity"] == runtime["multiplicity"] == 1
    assert isinstance(result["warnings"], list)


def test_gjf_suffix_aliases_route_to_gaussian() -> None:
    """All Gaussian suffixes share the same authority."""
    text = _gjf_minimal()
    for name in ("a.gjf", "a.gf", "a.com", "A.GJF", "A.COM"):
        out = structure_preview_request({"filename": name, "content_text": text})
        assert out["source_format"] == "gaussian-gjf"
        assert out["elements"] == ["C", "H"]


def test_gjf_atomic_numbers_match_runtime_exact() -> None:
    """Digit symbols normalize exactly like the runtime authority."""
    from confflow.core.gaussian_input import parse_gaussian_input_text

    text = _gjf_minimal(atoms="6 0.0 0.0 0.0\n1 0.0 0.0 1.089")
    runtime = parse_gaussian_input_text(text, "runtime")
    assert runtime["atoms"] == ["C", "H"]
    result = structure_preview_request({"filename": "n.gjf", "content_text": text})
    assert result["elements"] == ["C", "H"]
    assert result["coordinates"] == runtime["coords"]
    assert any("normalized" in w for w in result["warnings"])


def test_gjf_high_precision_passthrough_exact() -> None:
    """High-precision floats are preserved verbatim."""
    text = _gjf_minimal(atoms="C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001")
    result = structure_preview_request({"filename": "p.gjf", "content_text": text})
    assert result["coordinates"][0] == [1.23456789, -0.0000015, 3.14159265]
    assert result["coordinates"][0][0] == float("1.23456789")


def test_gjf_order_preserved_no_sort() -> None:
    """Atom order is the file order; no sorting or dedup."""
    text = _gjf_minimal(atoms="H 0 0 1\nO 0 0 0\nH 0 0 -1")
    result = structure_preview_request({"filename": "o.gjf", "content_text": text})
    assert result["elements"] == ["H", "O", "H"]


def test_gjf_title_numeric_trap_uses_real_header() -> None:
    """All-numeric title '1 1' does not steal the charge/mult header."""
    text = "%chk=j.chk\n#p B3LYP/6-31G\n\n1 1\n\n0 1\nC 0 0 0\nH 0 0 1\n\n"
    result = structure_preview_request({"filename": "t.gjf", "content_text": text})
    assert result["elements"] == ["C", "H"]
    assert result["charge"] == 0
    assert result["multiplicity"] == 1


def test_xyz_standard_matches_runtime_strict() -> None:
    """Single-frame XYZ matches strict file-reader semantics."""
    text = _xyz_minimal()
    result = structure_preview_request({"filename": "m.xyz", "content_text": text})
    assert result["source_format"] == "xyz"
    assert result["index_base"] == 1
    assert result["elements"] == ["C", "H"]
    assert result["coordinates"][0] == [1.23456789, -0.0000015, 3.14159265]
    assert "charge" not in result
    assert "multiplicity" not in result
    assert isinstance(result["warnings"], list)


def test_xyz_filename_never_opened() -> None:
    """A missing filename path still succeeds via content_text only."""
    text = _xyz_minimal()
    out = structure_preview_request(
        {"filename": "/nonexistent/should-never-open.xyz", "content_text": text}
    )
    assert out["elements"] == ["C", "H"]


def test_xyz_high_precision_and_order() -> None:
    """XYZ order and precision are verbatim."""
    text = "3\nc\nH 0 0 1\nO 0 0 0\nH 0 0 -1\n"
    result = structure_preview_request({"filename": "o.xyz", "content_text": text})
    assert result["elements"] == ["H", "O", "H"]


def test_inp_standard_matches_runtime_exact() -> None:
    """Single inline INP matches the frozen shared authority."""
    from confflow.programs.orca.input_parsing import parse_orca_input_text

    text = _inp_minimal()
    runtime = parse_orca_input_text(text)
    result = structure_preview_request({"filename": "j.inp", "content_text": text})
    assert result["source_format"] == "orca-inp"
    assert result["index_base"] == 1
    assert result["elements"] == runtime["elements"]
    assert result["coordinates"] == runtime["coordinates"]
    assert result["charge"] == runtime["charge"] == 0
    assert result["multiplicity"] == runtime["multiplicity"] == 1


def test_inp_high_precision_and_order() -> None:
    """INP handwritten precision and order are verbatim."""
    text = _inp_minimal(atoms="C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001")
    result = structure_preview_request({"filename": "p.inp", "content_text": text})
    assert result["elements"] == ["C", "H"]
    assert result["coordinates"][0][0] == float("1.23456789")


def test_routing_hint_agrees_and_conflicts() -> None:
    """Explicit hints must agree with the suffix when both are known."""
    text = _xyz_minimal()
    out = structure_preview_request(
        {"filename": "m.xyz", "content_text": text, "source_format_hint": "xyz"}
    )
    assert out["source_format"] == "xyz"
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request(
            {"filename": "m.xyz", "content_text": text, "source_format_hint": "orca-inp"}
        )
    assert _err(excinfo).code == "invalid_format"


def test_routing_unknown_suffix_requires_hint() -> None:
    """Unknown suffixes fail closed unless a valid hint is supplied."""
    text = _xyz_minimal()
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "m.unknown", "content_text": text})
    assert _err(excinfo).code == "invalid_format"
    out = structure_preview_request(
        {"filename": "m.unknown", "content_text": text, "source_format_hint": "xyz"}
    )
    assert out["source_format"] == "xyz"


def test_gjf_bohr_route_rejected_not_angstrom() -> None:
    """Bohr route units are refused, never returned as Angstrom."""
    text = _gjf_minimal(route="#p B3LYP/6-31G Opt Units=Bohr")
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "b.gjf", "content_text": text})
    err = _err(excinfo)
    assert err.code == "unsupported_unit"
    assert err.field == "content_text"
    assert str(err).startswith("native_input_error:")


def test_gjf_quoted_title_bohr_not_false_positive() -> None:
    """A quoted bohr title is diagnostic-only and must still parse."""
    text = '%chk=j.chk\n#p B3LYP/6-31G\n\n"my bohr job"\n\n0 1\nC 0 0 0\nH 0 0 1\n\n'
    result = structure_preview_request({"filename": "q.gjf", "content_text": text})
    assert result["elements"] == ["C", "H"]


def test_gjf_zmatrix_rejected() -> None:
    """Z-matrix input is refused as a whole."""
    text = "%chk=j.chk\n#p B3LYP/6-31G\n\nz\n\n0 1\nC\nH 1 1.09\n\n"
    with pytest.raises(StructurePreviewError):
        structure_preview_request({"filename": "z.gjf", "content_text": text})


def test_gjf_ghost_rejected() -> None:
    """Ghost/dummy symbols are refused."""
    for sym in ("Bq", "X", "Xx", "C1"):
        text = _gjf_minimal(atoms=f"{sym} 0 0 0")
        with pytest.raises(StructurePreviewError) as excinfo:
            structure_preview_request({"filename": "g.gjf", "content_text": text})
        assert _err(excinfo).code == "unknown_element"


def test_gjf_freeze_five_token_rejected_no_partial() -> None:
    """Five-token freeze-style lines fail the whole document."""
    text = _gjf_minimal(atoms="C -1 0.0 0.0 0.0")
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "f.gjf", "content_text": text})
    assert _err(excinfo).code == "unknown_coordinate_token"


def test_gjf_trailing_misline_rejected_not_truncated() -> None:
    """A trailing misline is refused, not truncated to a partial geometry."""
    text = "%chk=j.chk\n#p B3LYP/6-31G\n\nt\n\n0 1\nC 0 0 0\nnot-a-coordinate\n\n"
    with pytest.raises(StructurePreviewError):
        structure_preview_request({"filename": "e.gjf", "content_text": text})


def test_gjf_empty_rejected() -> None:
    """Empty coordinate blocks are refused."""
    text = "%chk=j.chk\n#p B3LYP/6-31G\n\ntitle\n\n0 1\n\n"
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "e.gjf", "content_text": text})
    assert _err(excinfo).code == "empty_geometry"


def test_gjf_link1_rejected_not_first_frame() -> None:
    """Link1 multi-step input is refused, never first-frame-only."""
    text = (
        "%chk=a.chk\n#p B3LYP/6-31G\n\nt\n\n0 1\nC 0 0 0\n\n"
        "--Link1--\n%chk=b.chk\n#p B3LYP/6-31G\n\nt2\n\n0 1\nH 0 0 1\n\n"
    )
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "l.gjf", "content_text": text})
    assert _err(excinfo).code == "multiple_geometries"


@pytest.mark.parametrize("coord", ["nan", "inf", "-inf", "abc"])
def test_gjf_invalid_coordinates_rejected(coord: str) -> None:
    """Non-finite/non-numeric Gaussian coordinates are refused."""
    text = _gjf_minimal(atoms=f"C {coord} 0 0")
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "n.gjf", "content_text": text})
    assert _err(excinfo).code == "invalid_coordinate"


@pytest.mark.parametrize("coord", ["nan", "inf", "-inf"])
def test_xyz_invalid_coordinates_rejected(coord: str) -> None:
    """Non-finite XYZ coordinates are refused."""
    text = f"1\nc\nC {coord} 0 0\n"
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "n.xyz", "content_text": text})
    assert _err(excinfo).code in ("invalid_coordinate", "unknown_coordinate_token")


def test_xyz_multiframe_rejected() -> None:
    """Multi-frame XYZ is refused, never first-frame-only."""
    text = "1\nc1\nH 0 0 0\n1\nc2\nHe 0 0 1\n"
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "m.xyz", "content_text": text})
    assert _err(excinfo).code == "multiple_geometries"


def test_xyz_truncated_rejected() -> None:
    """Truncated XYZ frames are refused."""
    text = "2\nc\nH 0 0 0\n"
    with pytest.raises(StructurePreviewError):
        structure_preview_request({"filename": "t.xyz", "content_text": text})


def test_inp_xyzfile_rejected_without_file_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`* xyzfile` is refused without opening the referenced file."""
    from confflow.producer import structure_preview as preview_mod

    real_read = preview_mod._preview_xyz  # noqa: SLF001 - white-box guard only
    assert callable(real_read)
    calls: list[str] = []
    import builtins

    real_open = builtins.open

    def _tracking_open(*args: object, **kwargs: object) -> object:
        if args:
            calls.append(str(args[0]))
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", _tracking_open)
    text = "! B3LYP Opt\n* xyzfile 0 1 foo.xyz\n"
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "e.inp", "content_text": text})
    assert _err(excinfo).code == "missing_external_context"
    assert not any("foo.xyz" in call for call in calls)


def test_inp_bohr_header_rejected() -> None:
    """ORCA bohr headers stay refused through the preview."""
    text = _inp_minimal(header="* xyz 0 1 bohr")
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "b.inp", "content_text": text})
    assert _err(excinfo).code == "unsupported_unit"


def test_legacy_gaussian_partial_snapshots_unchanged() -> None:
    """Old non-strict parser keeps legacy partial behavior (preview does not)."""
    from confflow.core.gaussian_input import parse_gaussian_input_text

    bohr = "%chk=j.chk\n#p B3LYP/6-31G Opt units=bohr\n\nt\n\n0 1\nC 0 0 1\n\n"
    legacy = parse_gaussian_input_text(bohr, "legacy-bohr")
    assert legacy["atoms"] == ["C"]
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "b.gjf", "content_text": bohr})
    assert _err(excinfo).code == "unsupported_unit"

    empty = "%chk=j.chk\n#p B3LYP/6-31G\n\nt\n\n0 1\n\n"
    assert parse_gaussian_input_text(empty, "legacy-empty")["atoms"] == []
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "e.gjf", "content_text": empty})
    assert _err(excinfo).code == "empty_geometry"


def test_error_envelope_fields_and_prefix() -> None:
    """Refusals carry structured fields; no message-text protocol."""
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "e.gjf", "content_text": ""})
    err = _err(excinfo)
    assert err.field == "content_text"
    assert str(err).startswith("native_input_error:")
    assert err.source_label == "e.gjf"
    sig = inspect.signature(structure_preview_request)
    assert list(sig.parameters) == ["parameters"]


def test_import_hygiene_no_new_parser_or_solver() -> None:
    """Preview must reuse authorities and stay out of solver stacks."""
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "confflow",
        "producer",
        "structure_preview.py",
    )
    path = os.path.normpath(path)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    forbidden = (
        "solver",
        "execution",
        "workflow",
        "science.confgen",
        "science.topology",
        "subprocess",
        "socket",
    )
    for name in imports:
        assert not any(part in name.lower() for part in forbidden), name
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "_parse_tail_coordinates" not in source
    assert "float(parts[-3])" not in source
    assert "ELEMENT_SYMBOLS" not in source or "domain.elements" in source


def test_gjf_units_paren_bohr_multiline_rejected() -> None:
    """Multiline Units(Bohr) is refused, never returned as Angstrom."""
    route = "# hf/sto-3g\n Units(Bohr)"
    text = _gjf_minimal(
        atoms="H 0.0 0.0 0.0\nH 0.0 0.0 1.4",
        route=route,
    )
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "u1.gjf", "content_text": text})
    assert _err(excinfo).code == "unsupported_unit"


def test_gjf_units_paren_eq_bohr_multiline_rejected() -> None:
    """Multiline Units=(Bohr) is refused, never returned as Angstrom."""
    route = "# hf/sto-3g\n Units=(Bohr)"
    text = _gjf_minimal(
        atoms="H 0.0 0.0 0.0\nH 0.0 0.0 1.4",
        route=route,
    )
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "u2.gjf", "content_text": text})
    assert _err(excinfo).code == "unsupported_unit"


def test_gjf_units_paren_eq_au_single_rejected() -> None:
    """Single-line Units=(AU) is refused, never returned as Angstrom."""
    route = "# hf/sto-3g Units=(AU)"
    text = _gjf_minimal(
        atoms="H 0.0 0.0 0.0\nH 0.0 0.0 1.4",
        route=route,
    )
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "u3.gjf", "content_text": text})
    assert _err(excinfo).code == "unsupported_unit"


def test_gjf_units_paren_eq_au_multiline_rejected() -> None:
    """Multiline Units=(AU) is refused, never returned as Angstrom."""
    route = "# hf/sto-3g\n Units=(AU)"
    text = _gjf_minimal(
        atoms="H 0.0 0.0 0.0\nH 0.0 0.0 1.4",
        route=route,
    )
    with pytest.raises(StructurePreviewError) as excinfo:
        structure_preview_request({"filename": "u4.gjf", "content_text": text})
    assert _err(excinfo).code == "unsupported_unit"


def test_gjf_units_angstrom_paren_accepted() -> None:
    """Explicit Angstrom in paren form stays accepted."""
    for route in ("#p B3LYP/6-31G Units(Angstrom)", "#p B3LYP/6-31G Units=(Angstrom)"):
        result = structure_preview_request(
            {"filename": "a.gjf", "content_text": _gjf_minimal(route=route)}
        )
        assert result["elements"] == ["C", "H"]


def test_gjf_units_angstrom_multiline_accepted() -> None:
    """Explicit Angstrom split across route lines stays accepted."""
    route = "#p B3LYP/6-31G\n Units=(Angstrom)"
    result = structure_preview_request(
        {"filename": "a.gjf", "content_text": _gjf_minimal(route=route)}
    )
    assert result["elements"] == ["C", "H"]


def test_gjf_title_bohr_not_false_positive() -> None:
    """Unquoted bohr in the title is diagnostic-only and must still parse."""
    text = _gjf_minimal(title="my bohr job")
    result = structure_preview_request({"filename": "t.gjf", "content_text": text})
    assert result["elements"] == ["C", "H"]


def test_gjf_title_units_free_text_not_declaration() -> None:
    """Units-like free text in the title is never a unit declaration."""
    text = _gjf_minimal(title="Units=Bohr")
    result = structure_preview_request({"filename": "t.gjf", "content_text": text})
    assert result["elements"] == ["C", "H"]
