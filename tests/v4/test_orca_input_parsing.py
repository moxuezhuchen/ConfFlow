#!/usr/bin/env python3

"""J1-parser pre-card tests: single inline ORCA ``* xyz`` input parsing.

Scope: only the default-Angstrom single ``* xyz`` segment is supported;
the full ORCA input syntax is explicitly NOT claimed.
"""

from __future__ import annotations

import ast
import inspect
import math
import os

import pytest

from confflow.domain.structure import StructureRecord
from confflow.programs.orca.input_parsing import (
    OrcaInputParseError,
    parse_orca_input_text,
)
from confflow.programs.orca.rendering import format_coord_lines, render_orca_input

RPDD_PATH = "/mnt/c/dft/wcm/rpdd.gjf"
RPDD_SHA_PREFIX = "4929b48c"


def _minimal_inp(
    header: str = "* xyz 0 1",
    atoms: str = "O 0.0 0.0 0.0\nH 0.76 0.59 0.0\nH 0.76 -0.59 0.0",
    pre: str = "! B3LYP def2-SVP Opt\n%pal nprocs 2 end\n%maxcore 500\n",
    source_label: str = "text",
) -> str:
    """Build a minimal inline document without touching the filesystem."""
    return f"{pre}{header}\n{atoms}\n*\n"


def _quantized(values: list[list[float]]) -> list[list[float]]:
    """Return the renderer ``%.6f`` quantization of *values*."""
    return [[float(f"{v:.6f}") for v in point] for point in values]


def _err(excinfo: pytest.ExceptionInfo[OrcaInputParseError]) -> OrcaInputParseError:
    """Narrow pytest error info to the structured refusal type."""
    value = excinfo.value
    assert isinstance(value, OrcaInputParseError)
    return value


def test_renderer_roundtrip_water_quantized_exact() -> None:
    """Renderer water vector round-trips to its true quantized values."""
    atoms = ["O", "H", "H"]
    coords = [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [0.76, -0.59, 0.0]]
    coords_text = format_coord_lines(atoms, coords)
    text = render_orca_input(
        keyword="B3LYP D3BJ def2-SVP Opt",
        cores=2,
        maxcore="500",
        blocks_text="",
        freeze=None,
        charge=0,
        multiplicity=1,
        coords_text=coords_text,
    )
    result = parse_orca_input_text(text)
    assert result["elements"] == atoms
    assert result["coordinates"] == _quantized(coords)
    assert result["charge"] == 0
    assert result["multiplicity"] == 1
    assert result["source_format"] == "orca-inp"
    assert isinstance(result["warnings"], list)
    record = StructureRecord(
        id="t1-water",
        atoms=tuple(result["elements"]),
        coordinates=tuple(tuple(p) for p in result["coordinates"]),
        charge=result["charge"],
        multiplicity=result["multiplicity"],
    )
    assert len(record.atoms) == 3


def test_renderer_roundtrip_blocks_freeze_quantized_exact() -> None:
    """Renderer blocks+freeze vector round-trips to quantized values."""
    atoms = ["C", "C", "O"]
    coords = [[0.123456789, -0.0000015, 3.14159265], [1.5, 2.5, -3.5], [0.0, 0.0, 1.2]]
    coords_text = format_coord_lines(atoms, coords)
    text = render_orca_input(
        keyword="PBE0 def2-SVP Opt",
        cores=4,
        maxcore="1000",
        blocks_text="%geom\n  MaxIter 50\nend\n",
        freeze=[1],
        charge=1,
        multiplicity=2,
        coords_text=coords_text,
    )
    result = parse_orca_input_text(text)
    assert result["elements"] == atoms
    assert result["coordinates"] == _quantized(coords)
    assert result["charge"] == 1
    assert result["multiplicity"] == 2
    assert result["source_format"] == "orca-inp"
    record = StructureRecord(
        id="t1-blocks",
        atoms=tuple(result["elements"]),
        coordinates=tuple(tuple(p) for p in result["coordinates"]),
        charge=result["charge"],
        multiplicity=result["multiplicity"],
    )
    assert record.charge == 1


def test_handwritten_high_precision_passthrough_exact() -> None:
    """Handwritten inline keeps full float values (independent of renderer)."""
    text = _minimal_inp(atoms="C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001")
    result = parse_orca_input_text(text)
    assert result["elements"] == ["C", "H"]
    assert result["coordinates"][0] == [1.23456789, -0.0000015, 3.14159265]
    assert result["coordinates"][1] == [0.0, 0.0, 1.00000001]
    # Exact equality, no tolerance widening.
    assert result["coordinates"][0][0] == float("1.23456789")
    record = StructureRecord(
        id="t1-precise",
        atoms=tuple(result["elements"]),
        coordinates=tuple(tuple(p) for p in result["coordinates"]),
    )
    assert math.isfinite(record.coordinates[0][2])


def test_element_order_and_duplicates_preserved() -> None:
    """Atom order is preserved verbatim; no sorting or dedup."""
    text = _minimal_inp(atoms="H 0 0 1\nO 0 0 0\nH 0 0 -1")
    result = parse_orca_input_text(text)
    assert result["elements"] == ["H", "O", "H"]


def test_case_insensitive_header_and_crlf() -> None:
    """Upper/mixed-case headers and CRLF line endings parse."""
    text = (
        "! B3LYP def2-SVP Opt\r\n%pal nprocs 2 end\r\n"
        "* XYZ 0 1\r\nO 0.0 0.0 0.0\r\nH 0.76 0.59 0.0\r\n*\r\n"
    )
    result = parse_orca_input_text(text)
    assert result["elements"] == ["O", "H"]
    mixed = _minimal_inp(header="* XyZ -1 2")
    out = parse_orca_input_text(mixed)
    assert out["charge"] == -1
    assert out["multiplicity"] == 2


def test_header_whitespace_tolerant() -> None:
    """Leading/trailing and multi-space headers are tolerated."""
    text = _minimal_inp(header="   *   xyz   0   1   ")
    result = parse_orca_input_text(text)
    assert result["charge"] == 0


def test_numeric_atomic_number_normalized_with_warning() -> None:
    """Digit symbols normalize with a factual warning."""
    text = _minimal_inp(atoms="6 0 0 0\n1 0 0 1")
    result = parse_orca_input_text(text)
    assert result["elements"] == ["C", "H"]
    assert any("normalized" in w for w in result["warnings"])


def test_leading_trailing_blank_warning() -> None:
    """Leading/trailing blanks are ignored with a factual warning."""
    text = "\n" + _minimal_inp()
    result = parse_orca_input_text(text)
    assert result["elements"][0] == "O"
    assert any("blank" in w for w in result["warnings"])


def test_bohr_header_rejected() -> None:
    """Inline ``bohr`` unit is refused, never returned as Angstrom."""
    text = _minimal_inp(header="* xyz 0 1 bohr")
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(text)
    err = _err(excinfo)
    assert isinstance(err, ValueError)
    assert err.code == "unsupported_unit"
    assert err.line == 4
    assert err.field == "content_text"
    assert str(err).startswith("native_input_error:")
    assert "bohr" in str(err).lower()
    assert "angstrom" in str(err).lower()


def test_bohr_geom_block_rejected() -> None:
    """``%geom Units Bohr`` is refused as an explicit unit declaration."""
    pre = "! B3LYP def2-SVP Opt\n%geom\n Units Bohr\nend\n%pal nprocs 2 end\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(pre=pre))
    assert _err(excinfo).code == "unsupported_unit"


def test_bohr_route_units_eq_rejected() -> None:
    """``units=bohr`` route-style declaration is refused."""
    pre = "! B3LYP Opt\n# units=bohr is a comment and must NOT fail\nunits=bohr\n"
    # The pure comment line above must not trigger; the bare units line must.
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(pre=pre))
    assert _err(excinfo).code == "unsupported_unit"


def test_bohr_bang_line_rejected() -> None:
    """``!``-line Bohr token is refused."""
    pre = "! B3LYP Bohr Opt\n%pal nprocs 2 end\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(pre=pre))
    assert _err(excinfo).code == "unsupported_unit"


def test_other_unit_nm_rejected() -> None:
    """Non-Bohr unsupported units are also refused (not only Bohr word)."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(header="* xyz 0 1 nm"))
    assert _err(excinfo).code == "unsupported_unit"


def test_explicit_angstrom_extra_token_rejected_minimal_scope() -> None:
    """Only the default Angstrom header is supported; extras are refused."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(header="* xyz 0 1 Angstrom"))
    assert _err(excinfo).code == "unsupported_unit"


def test_percent_coords_block_rejected() -> None:
    """``%coords`` alternative geometry form is refused."""
    pre = "! B3LYP Opt\n%coords\n C 0 0 0\nend\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(pre=pre))
    assert _err(excinfo).code == "invalid_header"


def test_intcoords_header_rejected() -> None:
    """``* intcoords`` alternative geometry form is refused."""
    text = "! B3LYP Opt\n* intcoords 0 1\nC 0 0 0\n*\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(text)
    assert _err(excinfo).code == "invalid_header"


def test_bohr_comment_and_quoted_title_not_false_positive() -> None:
    """Pure comments and quoted titles mentioning Bohr must not fail."""
    pre = (
        "# this comment mentions bohr and must be ignored\n"
        "// another bohr comment\n"
        '%base "my bohr job"\n'
        "! B3LYP def2-SVP Opt\n"
    )
    result = parse_orca_input_text(_minimal_inp(pre=pre))
    assert result["elements"][0] == "O"


def test_multiple_geometries_rejected() -> None:
    """Two ``* xyz`` sections are refused."""
    text = "! B3LYP Opt\n* xyz 0 1\nC 0 0 0\n*\n" "--Link1--\n* xyz 0 1\nH 0 0 1\n*\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(text)
    assert _err(excinfo).code == "multiple_geometries"


def test_xyz_plus_xyzfile_mixed_rejected() -> None:
    """``* xyz`` mixed with ``* xyzfile`` is refused as multiple."""
    text = "! B3LYP Opt\n* xyz 0 1\nC 0 0 0\n*\n* xyzfile 0 1 foo.xyz\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(text)
    assert _err(excinfo).code == "multiple_geometries"


def test_incomplete_missing_closure() -> None:
    """Unclosed ``* xyz`` section is refused."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("! B3LYP Opt\n* xyz 0 1\nC 0 0 0\n")
    assert _err(excinfo).code == "incomplete_geometry"


def test_incomplete_missing_header() -> None:
    """Document without any ``* xyz`` header is refused."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("! B3LYP Opt\nC 0 0 0\n")
    assert _err(excinfo).code == "incomplete_geometry"


def test_empty_geometry_direct_closure() -> None:
    """Header immediately closed is refused as empty."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("! B3LYP Opt\n* xyz 0 1\n*\n")
    assert _err(excinfo).code == "empty_geometry"


def test_empty_geometry_only_blanks() -> None:
    """Header with only blank lines is refused as empty."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("! B3LYP Opt\n* xyz 0 1\n   \n\n*\n")
    assert _err(excinfo).code == "empty_geometry"


@pytest.mark.parametrize("header", ["* xyz 0.5 1", "* xyz a 1", "* xyz 0 0", "* xyz 0", "* xyz"])
def test_invalid_headers(header: str) -> None:
    """Malformed headers (non-int, mult<1, arity) are refused."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(header=header))
    assert _err(excinfo).code == "invalid_header"


def test_five_token_freeze_style_rejected_no_partial() -> None:
    """Gaussian freeze-style five tokens fail the whole document."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(atoms="C -1 0.0 0.0 0.0"))
    err = _err(excinfo)
    assert err.code == "unknown_coordinate_token"
    assert err.line is not None


def test_trailing_comment_extra_token_rejected() -> None:
    """Row-end comments (fifth token) fail the whole document."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(atoms="C 0.0 0.0 0.0 # comment"))
    assert _err(excinfo).code == "unknown_coordinate_token"


@pytest.mark.parametrize("symbol", ["Xx", "Q", "X", "C1", "Dd"])
def test_dummy_and_unknown_elements(symbol: str) -> None:
    """Dummy/unknown symbols are refused with an explicit code."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(atoms=f"{symbol} 0 0 0"))
    assert _err(excinfo).code == "unknown_element"


@pytest.mark.parametrize("coord", ["nan", "inf", "-inf", "abc", "1E999"])
def test_invalid_coordinates(coord: str) -> None:
    """Non-finite/non-numeric coordinates are refused."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(atoms=f"C {coord} 0 0"))
    assert _err(excinfo).code == "invalid_coordinate"


def test_xyzfile_missing_external_context_and_zero_file_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``* xyzfile`` is refused without opening any file."""
    calls: list[str] = []

    def _forbidden_open(*args: object, **kwargs: object) -> object:
        calls.append(str(args[0]) if args else "")
        raise AssertionError("file open must not be called")

    monkeypatch.setattr("builtins.open", _forbidden_open)
    text = "! B3LYP Opt\n* xyzfile 0 1 foo.xyz\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(text)
    err = _err(excinfo)
    assert err.code == "missing_external_context"
    assert err.filename == "foo.xyz"
    assert "foo.xyz" in str(err)
    assert calls == []


def test_error_envelope_fields_and_prefix() -> None:
    """Refusals carry structured fields; producers must not parse text."""
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(header="* xyz 0 0"), source_label="job.inp")
    err = _err(excinfo)
    assert err.code == "invalid_header"
    assert err.line == 4
    assert err.source_label == "job.inp"
    assert err.field == "content_text"
    message = str(err)
    assert message.startswith("native_input_error:")
    assert "orca-inp invalid_header" in message


def test_source_label_propagates() -> None:
    """Custom source labels are carried on success path diagnostics."""
    result = parse_orca_input_text(_minimal_inp(), source_label="job.inp")
    assert result["source_format"] == "orca-inp"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("no geometry here", source_label="job.inp")
    assert _err(excinfo).source_label == "job.inp"


def test_signature_is_text_only() -> None:
    """API accepts text only; no path parameter exists."""
    sig = inspect.signature(parse_orca_input_text)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["text", "source_label"]
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind == inspect.Parameter.KEYWORD_ONLY
    assert params[1].default == "text"


def test_import_hygiene_stdlib_plus_domain_only() -> None:
    """New module must not pull solver/producer/execution/workflow I/O."""
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "confflow",
        "programs",
        "orca",
        "input_parsing.py",
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
        "os",
        "sys",
        "pathlib",
        "io",
        "solver",
        "producer",
        "execution",
        "workflow",
        "science",
        "subprocess",
        "socket",
    )
    for name in imports:
        lowered = name.lower()
        assert not any(part in lowered for part in forbidden), f"forbidden import: {name}"
    assert any("domain.elements" in name for name in imports)


def test_gaussian_authority_parity_snapshot() -> None:
    """Snapshot: existing Gaussian authority parses the same minimal block.

    This records current behavior only. GJF upper-layer preview routing
    remains a follow-up card and is NOT claimed as implemented here.
    """
    from confflow.core.gaussian_input import parse_gaussian_input_text

    gjf = "%chk=job.chk\n#p B3LYP/6-31G Opt\n\ntitle\n\n0 1\nC 0.0 0.0 0.0\nH 0.0 0.0 1.0\n\n"
    parsed = parse_gaussian_input_text(gjf, "snapshot")
    assert parsed["charge"] == 0
    assert parsed["multiplicity"] == 1
    assert parsed["atoms"] == ["C", "H"]


def test_gaussian_bohr_silent_snapshot() -> None:
    """Snapshot danger record: old Gaussian parser silently keeps Bohr as-is.

    The new ORCA API must NOT copy this behavior (it refuses Bohr).
    Upper-layer GJF preview handling stays in the follow-up card.
    """
    from confflow.core.gaussian_input import parse_gaussian_input_text

    gjf = "%chk=job.chk\n#p B3LYP/6-31G Opt units=bohr\n\ntitle\n\n0 1\nC 0.0 0.0 1.0\n\n"
    parsed = parse_gaussian_input_text(gjf, "snapshot-bohr")
    assert parsed["atoms"] == ["C"]
    assert parsed["coords"] == [[0.0, 0.0, 1.0]]


def test_gaussian_link1_first_segment_snapshot() -> None:
    """Snapshot: old Gaussian parser keeps the first Link1 segment silently."""
    from confflow.core.gaussian_input import parse_gaussian_input_text

    gjf = (
        "%chk=a.chk\n#p B3LYP/6-31G\n\nt\n\n0 1\nC 0 0 0\n\n"
        "--Link1--\n%chk=b.chk\n#p B3LYP/6-31G\n\nt2\n\n0 1\nH 0 0 1\n\n"
    )
    parsed = parse_gaussian_input_text(gjf, "snapshot-link1")
    assert parsed["atoms"] == ["C"]
    # The new ORCA API explicitly refuses multi-geometry Link1-style input.
    link1_inp = "! B3LYP Opt\n* xyz 0 1\nC 0 0 0\n*\n--Link1--\n* xyz 0 1\nH 0 0 1\n*\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(link1_inp)
    assert _err(excinfo).code == "multiple_geometries"


def test_gaussian_empty_returns_empty_snapshot() -> None:
    """Snapshot: old Gaussian parser returns empty atoms without error."""
    from confflow.core.gaussian_input import parse_gaussian_input_text

    gjf = "%chk=job.chk\n#p B3LYP/6-31G\n\ntitle\n\n0 1\n\n"
    parsed = parse_gaussian_input_text(gjf, "snapshot-empty")
    assert parsed["atoms"] == []
    # New ORCA API must refuse empty geometry instead.
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("! B3LYP Opt\n* xyz 0 1\n*\n")
    assert _err(excinfo).code == "empty_geometry"


@pytest.mark.skipif(
    not os.path.exists(RPDD_PATH), reason="optional read-only probe; no local path required"
)
def test_rpdd_readonly_optional_probe() -> None:
    """Optional read-only probe of the user rpdd file (skipped when absent)."""
    import hashlib

    from confflow.core.gaussian_input import parse_gaussian_input_text

    with open(RPDD_PATH, "rb") as handle:
        raw = handle.read()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest.startswith(RPDD_SHA_PREFIX)
    text = raw.decode("utf-8", errors="ignore")
    parsed = parse_gaussian_input_text(text, "rpdd-optional-probe")
    assert len(parsed["atoms"]) == 22
    assert parsed["charge"] == 0
    assert parsed["multiplicity"] == 1
    assert parsed["atoms"] == [
        "C",
        "C",
        "O",
        "O",
        "C",
        "C",
        "O",
        "O",
        "H",
        "H",
        "H",
        "C",
        "C",
        "C",
        "C",
        "H",
        "C",
        "H",
        "C",
        "H",
        "H",
        "H",
    ]
    for triple in parsed["coords"]:
        assert all(math.isfinite(v) for v in triple)


@pytest.mark.parametrize(
    "decl",
    [
        'units="bohr"',
        "units 'bohr'",
        'units="bohrs"',
        'units "bohrs"',
        'units="nm"',
        'units "nm"',
        'units="xyzzy"',
        'units "xyzzy"',
    ],
)
def test_v2_quoted_and_plural_units_rejected(decl: str) -> None:
    """V2 lexical fix: quoted/bare non-Angstrom values refuse (V1 accepted)."""
    pre = f"! B3LYP Opt\n{decl}\n"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text(_minimal_inp(pre=pre), source_label="units-probe.inp")
    err = _err(excinfo)
    assert err.code == "unsupported_unit"
    assert err.line == 2
    assert err.source_label == "units-probe.inp"
    assert err.field == "content_text"
    assert str(err).startswith("native_input_error:")


@pytest.mark.parametrize(
    "pre",
    [
        '! HF # units="bohr"\n',
        '%base "units=bohr"\n! B3LYP Opt\n',
        '%base "my bohr job"\n! B3LYP Opt\n',
        '# units="bohr"\n! B3LYP Opt\n',
    ],
)
def test_v2_comment_and_quoted_title_positive(pre: str) -> None:
    """Pure `#` comments and quoted titles never trigger unit refusal."""
    result = parse_orca_input_text(_minimal_inp(pre=pre))
    assert result["elements"][0] == "O"
    assert result["source_format"] == "orca-inp"


def test_v2_source_label_never_scanned() -> None:
    """A unit-like source label is diagnostic-only and never refused."""
    result = parse_orca_input_text(_minimal_inp(), source_label='units="bohr"')
    assert result["elements"][0] == "O"
    with pytest.raises(OrcaInputParseError) as excinfo:
        parse_orca_input_text("no geometry here", source_label='units="bohr"')
    err = _err(excinfo)
    assert err.source_label == 'units="bohr"'
    assert err.code == "incomplete_geometry"
