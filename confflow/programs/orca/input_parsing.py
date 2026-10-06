#!/usr/bin/env python3

"""ORCA ``.inp`` input-geometry parsing (single inline ``* xyz`` only).

Scope (fail-closed, minimal): this module parses exactly one inline
``* xyz <charge> <mult>`` ... ``*`` segment with default Angstrom units and
returns elements/coordinates for producer thin projection and runtime reuse.
It does NOT support the full ORCA input syntax (no ``%`` semantics, no
``!`` semantics, no internal/redundant coordinates, no external files).

Supported: single case-insensitive ``* xyz`` header, integer charge,
``multiplicity >= 1``, atom lines of exactly four tokens
``<symbol> x y z`` with finite floats, element order preserved.

Refused (whole document fails, never partial): multiple geometries,
missing/unclosed/empty geometry, malformed headers, unknown extra tokens,
unknown elements, non-finite coordinates, explicit Bohr (or other)
unit declarations, ``* xyzfile`` external references, and other
unsupported geometry forms (``* intcoords``-style headers, ``%coords``).
"""

from __future__ import annotations

import math
import re

from ...domain.elements import ELEMENT_SYMBOLS, canonical_element_symbol
from ...domain.errors import ElementSymbolError

__all__ = [
    "OrcaInputParseError",
    "parse_orca_input_text",
]

#: Success marker carried in the result dict.
SOURCE_FORMAT: str = "orca-inp"

#: Field path used by the future producer envelope.
FIELD_PATH: str = "content_text"

_ANGSTROM_TOKENS: frozenset[str] = frozenset({"angstrom", "angstroms", "angs", "ang"})

_BOHR_TOKENS: frozenset[str] = frozenset({"bohr", "bohrs"})

_OTHER_LENGTH_UNITS: frozenset[str] = frozenset({"nm", "pm", "fm", "au", "a.u.", "a.u"})

_QUOTED_RE = re.compile(r'"[^"\r\n]*"|\'[^\'\r\n]*\'')

_XYZ_HEADER_RE = re.compile(r"^\s*\*\s*xyz\b", re.IGNORECASE)
_XYZFILE_HEADER_RE = re.compile(r"^\s*\*\s*xyzfile\b", re.IGNORECASE)
_STAR_HEADER_RE = re.compile(r"^\s*\*\s*(\S+)", re.IGNORECASE)
_CLOSURE_RE = re.compile(r"^\s*\*\s*$")
_COORDS_BLOCK_RE = re.compile(r"^\s*%coords\b", re.IGNORECASE)
_UNITS_EQ_RE = re.compile(r"(?i)\bunits?\s*=\s*[\"']?\s*([A-Za-z.]+)\s*[\"']?")
_UNITS_SPACE_RE = re.compile(r"(?i)\bunits?\s+[\"']?\s*([A-Za-z.]+)\s*[\"']?")


class OrcaInputParseError(ValueError):
    """Structured ORCA input refusal carrying producer-envelope fields.

    The human message always starts with ``native_input_error`` for
    consistency with the renderer/adapter.  Producers must read the
    structured ``code``/``line``/``source_label``/``field`` attributes,
    never parse the message text as a protocol.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        line: int | None = None,
        source_label: str = "text",
        field: str = FIELD_PATH,
        filename: str | None = None,
    ) -> None:
        self.code = code
        self.line = line
        self.source_label = source_label
        self.field = field
        self.filename = filename
        where = f"line {line}" if line is not None else "line ?"
        super().__init__(
            f"native_input_error: orca-inp {code}: {message} "
            f"({where}, field {field}, source {source_label})"
        )


def _fail(
    code: str,
    detail: str,
    *,
    line: int | None = None,
    source_label: str = "text",
    filename: str | None = None,
) -> OrcaInputParseError:
    """Build a structured refusal."""
    return OrcaInputParseError(
        code,
        detail,
        line=line,
        source_label=source_label,
        field=FIELD_PATH,
        filename=filename,
    )


def _strip_quoted(text: str) -> str:
    """Remove double/single-quoted spans to avoid title false positives."""
    return _QUOTED_RE.sub(" ", text)


def _strip_inline_comment(line: str) -> str:
    """Remove a trailing ``#`` comment outside quotes.

    A ``#`` inside single/double quotes is part of a title and is kept.
    """
    in_single = False
    in_double = False
    for pos, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:pos]
    return line


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of quoted substrings in *text*."""
    return [(match.start(), match.end()) for match in _QUOTED_RE.finditer(text)]


def _is_pure_comment(stripped: str) -> bool:
    """Return whether a stripped line is a pure comment line."""
    return stripped.startswith("#") or stripped.startswith("//")


def _normalize_unit_token(token: str) -> str:
    """Normalize a unit token for comparison (case-insensitive)."""
    return token.strip().lower().rstrip(".")


def _is_angstrom_unit(token: str) -> bool:
    """Return whether *token* names an explicit Angstrom unit."""
    return _normalize_unit_token(token) in _ANGSTROM_TOKENS


def _is_bohr_unit(token: str) -> bool:
    """Return whether *token* names a Bohr/atomic unit."""
    return _normalize_unit_token(token) in _BOHR_TOKENS


def _is_other_length_unit(token: str) -> bool:
    """Return whether *token* names another unsupported length unit."""
    return _normalize_unit_token(token) in _OTHER_LENGTH_UNITS


def _check_structured_unit_declarations(lines: list[str], *, source_label: str) -> None:
    """Reject explicit unit declarations outside the geometry header.

    Only structured declaration shapes are inspected (``units = X``,
    ``units X``, ``!``-line standalone unit tokens); a bare ``bohr``
    substring elsewhere is never matched. Pure comment lines, trailing
    ``#`` comments, and quoted titles are excluded lexically, but a quoted
    declaration *value* (``units="bohr"``) stays a real declaration.
    ``source_label`` is diagnostic-only and is never scanned.
    """
    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if _is_pure_comment(stripped):
            continue
        # Skip geometry atom content: atom lines cannot carry unit
        # declarations by construction (exactly 4 tokens validated later).
        code = _strip_inline_comment(raw)
        if not code.strip():
            continue
        if _CLOSURE_RE.match(code):
            continue
        if _XYZ_HEADER_RE.match(code) or _XYZFILE_HEADER_RE.match(code):
            continue
        if _STAR_HEADER_RE.match(code):
            continue
        # %coords is a geometry form, handled as invalid_header elsewhere.
        if _COORDS_BLOCK_RE.match(code):
            raise _fail(
                "invalid_header",
                f"unsupported geometry form '%coords' at line {lineno}: "
                f"{stripped}; only single inline '* xyz' (default Angstrom) "
                "is supported",
                line=lineno,
                source_label=source_label,
            )
        spans = _quoted_spans(code)
        for pattern in (_UNITS_EQ_RE, _UNITS_SPACE_RE):
            for match in pattern.finditer(code):
                if any(start <= match.start() < end for start, end in spans):
                    continue
                unit = match.group(1)
                if _is_angstrom_unit(unit):
                    continue
                raise _fail(
                    "unsupported_unit",
                    f"declares unsupported unit {unit!r} at line {lineno}: "
                    f"{stripped}; default is angstrom",
                    line=lineno,
                    source_label=source_label,
                )
        deg_stripped = _strip_quoted(code).strip()
        if deg_stripped.startswith("!"):
            tokens = _strip_quoted(code).split()
            for token in tokens[1:]:
                cleaned = token.strip(",;()")
                if not cleaned:
                    continue
                if _is_angstrom_unit(cleaned):
                    continue
                if _is_bohr_unit(cleaned) or _is_other_length_unit(cleaned):
                    raise _fail(
                        "unsupported_unit",
                        f"declares unsupported unit {cleaned!r} at line "
                        f"{lineno}: {stripped}; default is angstrom",
                        line=lineno,
                        source_label=source_label,
                    )


def parse_orca_input_text(text: str, *, source_label: str = "text") -> dict:
    """Parse ORCA ``.inp`` text with a single inline ``* xyz`` geometry.

    Parameters
    ----------
    text : str
        Full ``.inp`` content as text.  Paths are never accepted.
    source_label : str
        Diagnostic-only label; never triggers any file open.

    Returns
    -------
    dict
        ``{"elements", "coordinates", "charge", "multiplicity",
        "source_format", "warnings"}`` with coordinates in Angstrom.

    Raises
    ------
    OrcaInputParseError
        Structured refusal (a ``ValueError``) carrying ``code``/``line``/
        ``source_label`` for the producer envelope.
    """
    if not isinstance(text, str):
        raise _fail(
            "invalid_header",
            f"input text must be str, got {type(text).__name__}",
            line=None,
            source_label=str(source_label),
        )
    if not isinstance(source_label, str):
        source_label = str(source_label)

    lines = text.splitlines()
    xyz_headers: list[tuple[int, list[str], str]] = []
    xyzfile_refs: list[tuple[int, str]] = []
    other_star: list[tuple[int, str, str]] = []
    closures: list[int] = []

    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if _CLOSURE_RE.match(raw):
            closures.append(lineno)
            continue
        if _XYZFILE_HEADER_RE.match(raw):
            xyzfile_refs.append((lineno, raw.strip()))
            continue
        if _XYZ_HEADER_RE.match(raw):
            tokens = stripped.split()
            xyz_headers.append((lineno, tokens, raw.strip()))
            continue
        star = _STAR_HEADER_RE.match(raw)
        if star is not None:
            other_star.append((lineno, star.group(1), raw.strip()))
            continue

    if xyzfile_refs and xyz_headers:
        first = min([n for n, _ in xyzfile_refs] + [n for n, _, _ in xyz_headers])
        raise _fail(
            "multiple_geometries",
            f"refuses multiple geometry sections: found {len(xyz_headers)} "
            f"'* xyz' header(s) plus {len(xyzfile_refs)} '* xyzfile' "
            f"reference(s) (first at line {first})",
            line=first,
            source_label=source_label,
        )
    if len(xyz_headers) >= 2:
        first = xyz_headers[0][0]
        raise _fail(
            "multiple_geometries",
            f"refuses multiple geometry sections: found {len(xyz_headers)} "
            f"'* xyz' headers (first at line {first})",
            line=first,
            source_label=source_label,
        )
    if xyzfile_refs and not xyz_headers:
        lineno, rawline = xyzfile_refs[0]
        tokens = rawline.split()
        filename = tokens[4] if len(tokens) >= 5 else ""
        if not filename:
            filename = rawline
        raise _fail(
            "missing_external_context",
            f"references external file {filename!r} via '* xyzfile' at line "
            f"{lineno}: producer has no file access",
            line=lineno,
            source_label=source_label,
            filename=filename,
        )
    if other_star:
        lineno, word, rawline = other_star[0]
        raise _fail(
            "invalid_header",
            f"unsupported geometry form '* {word}' at line {lineno}: "
            f"{rawline}; only single inline '* xyz' (default Angstrom) "
            "is supported",
            line=lineno,
            source_label=source_label,
        )
    if not xyz_headers:
        raise _fail(
            "incomplete_geometry",
            "no '* xyz' section found; only single inline '* xyz' "
            "(default Angstrom) is supported",
            line=None,
            source_label=source_label,
        )

    header_lineno, tokens, rawline = xyz_headers[0]
    # tokens[0] == '*', tokens[1] case-insensitive 'xyz'.
    rest = tokens[2:]
    if len(rest) > 2:
        unit = rest[2]
        raise _fail(
            "unsupported_unit",
            f"declares unsupported unit {unit!r} at line {header_lineno}: "
            f"{rawline}; default is angstrom",
            line=header_lineno,
            source_label=source_label,
        )
    if len(rest) != 2:
        raise _fail(
            "invalid_header",
            f"'* xyz' header at line {header_lineno} must be "
            f"'* xyz <int charge> <int mult>=1': {rawline}",
            line=header_lineno,
            source_label=source_label,
        )
    charge_tok, mult_tok = rest
    if not re.match(r"^[+-]?\d+$", charge_tok) or not re.match(r"^[+-]?\d+$", mult_tok):
        raise _fail(
            "invalid_header",
            f"'* xyz' header at line {header_lineno} must be "
            f"'* xyz <int charge> <int mult>=1': {rawline}",
            line=header_lineno,
            source_label=source_label,
        )
    charge = int(charge_tok)
    multiplicity = int(mult_tok)
    if multiplicity < 1:
        raise _fail(
            "invalid_header",
            f"'* xyz' header at line {header_lineno} must be "
            f"'* xyz <int charge> <int mult>=1': {rawline}",
            line=header_lineno,
            source_label=source_label,
        )

    # Explicit unit declarations elsewhere in the document refuse as
    # unsupported_unit (lexically excluding comments/quoted titles).
    _check_structured_unit_declarations(lines, source_label=source_label)

    early_closures = [c for c in closures if c < header_lineno]
    if early_closures:
        raise _fail(
            "invalid_header",
            f"stray '*' closure at line {early_closures[0]} before '* xyz' "
            f"header at line {header_lineno}",
            line=early_closures[0],
            source_label=source_label,
        )
    closures_after = [c for c in closures if c > header_lineno]
    if not closures_after:
        raise _fail(
            "incomplete_geometry",
            f"'* xyz' section at line {header_lineno} is not closed by '*'",
            line=header_lineno,
            source_label=source_label,
        )
    end_lineno = closures_after[0]
    if len(closures_after) > 1:
        raise _fail(
            "invalid_header",
            f"unexpected extra '*' closure at line {closures_after[1]} after "
            f"geometry closed at line {end_lineno}",
            line=closures_after[1],
            source_label=source_label,
        )

    elements: list[str] = []
    coordinates: list[list[float]] = []
    warnings: list[str] = []

    if lines and lines[0].strip() == "":
        warnings.append("line 1: leading blank ignored")
    if lines and lines[-1].strip() == "":
        warnings.append(f"line {len(lines)}: trailing blank ignored")

    n_atoms = 0
    for lineno in range(header_lineno + 1, end_lineno):
        raw = lines[lineno - 1]
        stripped = raw.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 4:
            raise _fail(
                "unknown_coordinate_token",
                f"line {lineno} has {len(parts)} tokens, expected 4 "
                f"'<symbol> x y z': {stripped}",
                line=lineno,
                source_label=source_label,
            )
        sym_tok, sx, sy, sz = parts
        if re.match(r"^[+-]?\d+$", sym_tok):
            number = int(sym_tok)
            if number < 1 or number >= len(ELEMENT_SYMBOLS):
                raise _fail(
                    "unknown_element",
                    f"line {lineno} has unknown element {sym_tok!r}: " f"{stripped}",
                    line=lineno,
                    source_label=source_label,
                )
            symbol = ELEMENT_SYMBOLS[number]
            warnings.append(
                f"line {lineno}: numeric atomic number {sym_tok} " f"normalized to {symbol}"
            )
        else:
            try:
                symbol = canonical_element_symbol(sym_tok)
            except ElementSymbolError:
                raise _fail(
                    "unknown_element",
                    f"line {lineno} has unknown element {sym_tok!r}: " f"{stripped}",
                    line=lineno,
                    source_label=source_label,
                ) from None
        try:
            point = (float(sx), float(sy), float(sz))
        except (TypeError, ValueError):
            raise _fail(
                "invalid_coordinate",
                f"line {lineno} has non-finite/non-numeric coordinate: " f"{stripped}",
                line=lineno,
                source_label=source_label,
            ) from None
        if not all(math.isfinite(value) for value in point):
            raise _fail(
                "invalid_coordinate",
                f"line {lineno} has non-finite/non-numeric coordinate: " f"{stripped}",
                line=lineno,
                source_label=source_label,
            )
        elements.append(symbol)
        coordinates.append([point[0], point[1], point[2]])
        n_atoms += 1

    if n_atoms == 0:
        raise _fail(
            "empty_geometry",
            "geometry block is empty",
            line=header_lineno,
            source_label=source_label,
        )

    return {
        "elements": elements,
        "coordinates": coordinates,
        "charge": charge,
        "multiplicity": multiplicity,
        "source_format": SOURCE_FORMAT,
        "warnings": warnings,
    }
