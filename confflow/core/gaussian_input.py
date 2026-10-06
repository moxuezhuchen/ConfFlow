#!/usr/bin/env python3

"""Gaussian input and coordinate parsing helpers."""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = [
    "GaussianInputStrictError",
    "calculate_bond_length",
    "coords_lines_to_array",
    "parse_gaussian_input",
    "parse_gaussian_input_text",
]

#: Field path used by the future producer envelope.
STRICT_FIELD_PATH: str = "content_text"

#: Success marker for strict preview routing.
STRICT_SOURCE_FORMAT: str = "gaussian-gjf"

_QUOTED_RE = re.compile(r'"[^"\r\n]*"|\'[^\'\r\n]*\'')
_LINK1_RE = re.compile(r"--\s*link1\s*--", re.IGNORECASE)
_UNITS_EQ_RE = re.compile(r"(?i)\bunits?\s*=\s*[\"']?\s*([A-Za-z.]+)\s*[\"']?")
_UNITS_SPACE_RE = re.compile(r"(?i)\bunits?\s+[\"']?\s*([A-Za-z.]+)\s*[\"']?")
_UNITS_PAREN_RE = re.compile(r"(?i)\bunits?\s*(?:=\s*)?\(\s*[\"']?\s*([A-Za-z.]+)\s*[\"']?\s*\)")
_BOHR_TOKEN_RE = re.compile(r"(?i)\bbohrs?\b")
_AU_TOKEN_RE = re.compile(r"(?i)\bAU\b")
_QM_LINE_RE = re.compile(r"^\s*-?\d+\s+-?\d+\s*$")

_ANGSTROM_TOKENS: frozenset[str] = frozenset({"angstrom", "angstroms", "angs", "ang"})


class GaussianInputStrictError(ValueError):
    """Structured strict refusal for preview projection.

    The human message always starts with ``native_input_error`` for
    consistency with the ORCA shared parser. Producers and future
    authoring consumers must read the structured ``code``/``line``/
    ``source_label``/``field`` attributes, never parse the message
    text as a protocol.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        line: int | None = None,
        source_label: str = "text",
        field: str = STRICT_FIELD_PATH,
        filename: str | None = None,
    ) -> None:
        self.code = code
        self.line = line
        self.source_label = source_label
        self.field = field
        self.filename = filename
        where = f"line {line}" if line is not None else "line ?"
        super().__init__(
            f"native_input_error: gaussian-gjf {code}: {message} "
            f"({where}, field {field}, source {source_label})"
        )


def _strict_fail(
    code: str,
    detail: str,
    *,
    line: int | None = None,
    source_label: str = "text",
    filename: str | None = None,
) -> GaussianInputStrictError:
    """Build a structured strict refusal."""
    return GaussianInputStrictError(
        code,
        detail,
        line=line,
        source_label=source_label,
        field=STRICT_FIELD_PATH,
        filename=filename,
    )


def _strict_quoted_spans(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of quoted substrings in *text*."""
    return [(m.start(), m.end()) for m in _QUOTED_RE.finditer(text)]


def _strict_normalize_unit(token: str) -> str:
    """Normalize a unit token for comparison (case-insensitive)."""
    return token.strip().lower().rstrip(".")


def _strict_is_angstrom(token: str) -> bool:
    """Return whether *token* names an explicit Angstrom unit."""
    return _strict_normalize_unit(token) in _ANGSTROM_TOKENS


def _strict_route_text(lines: list[str]) -> tuple[str, list[int]]:
    """Return complete Gaussian route logical text and its line numbers."""
    route_lines: list[str] = []
    route_linenos: list[int] = []
    inside = False
    for idx, raw in enumerate(lines):
        lineno = idx + 1
        stripped = raw.strip()
        if not inside:
            if stripped.startswith("#"):
                inside = True
                route_lines.append(raw)
                route_linenos.append(lineno)
        else:
            if not stripped:
                inside = False
            else:
                route_lines.append(raw)
                route_linenos.append(lineno)
    return "\n".join(route_lines), route_linenos


def _strict_check_units(lines: list[str], *, source_label: str) -> None:
    """Reject explicit non-Angstrom unit declarations (thin validation).

    Route-only boundary: only the complete route logical block (``#``
    through continuation lines until the blank line, newlines included)
    is inspected. Structured shapes cover ``units = X``, ``units X``,
    ``units(X)`` and ``units=(X)`` with optional quotes/case/parens and
    cross-line whitespace. Bare ``bohr``/``AU`` tokens in route are also
    refused. Titles and other free text are never scanned, so a bare
    ``bohr`` or ``Units=Bohr`` in a title is diagnostic-only. Quoted
    spans inside route are excluded lexically, but a quoted declaration
    value (``units="bohr"``) stays a real declaration. ``source_label``
    is diagnostic-only and never scanned.
    """
    route_text, route_linenos = _strict_route_text(lines)
    if not route_text.strip():
        return
    spans = _strict_quoted_spans(route_text)

    def _in_quotes(pos: int) -> bool:
        return any(s <= pos < e for s, e in spans)

    def _lineno_at(pos: int) -> int:
        idx = route_text[:pos].count("\n")
        if 0 <= idx < len(route_linenos):
            return route_linenos[idx]
        return route_linenos[0] if route_linenos else 1

    def _stripped_at(lineno: int) -> str:
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()
        return route_text.strip().splitlines()[0] if route_text else ""

    for pattern in (_UNITS_PAREN_RE, _UNITS_EQ_RE, _UNITS_SPACE_RE):
        for match in pattern.finditer(route_text):
            if _in_quotes(match.start()):
                continue
            unit = match.group(1)
            if _strict_is_angstrom(unit):
                continue
            lineno = _lineno_at(match.start())
            stripped = _stripped_at(lineno)
            raise _strict_fail(
                "unsupported_unit",
                f"declares unsupported unit {unit!r} at line {lineno}: "
                f"{stripped}; default is angstrom",
                line=lineno,
                source_label=source_label,
            )
    for pattern, label in ((_BOHR_TOKEN_RE, "bohr"), (_AU_TOKEN_RE, "AU")):
        for match in pattern.finditer(route_text):
            if _in_quotes(match.start()):
                continue
            lineno = _lineno_at(match.start())
            stripped = _stripped_at(lineno)
            raise _strict_fail(
                "unsupported_unit",
                f"declares unsupported unit {label!r} at line {lineno}: "
                f"{stripped}; default is angstrom",
                line=lineno,
                source_label=source_label,
            )


def _parse_tail_coordinates(parts: list[str]) -> tuple[float, float, float]:
    """Parse the trailing ``x y z`` coordinate triplet from a tokenized line."""
    return float(parts[-3]), float(parts[-2]), float(parts[-1])


def coords_lines_to_array(
    coords_lines: list[str],
) -> list[tuple[str, float, float, float]] | None:
    """Convert coordinate lines to a list of ``(symbol, x, y, z)`` tuples."""
    try:
        result = []
        for line in coords_lines:
            parts = line.split()
            if len(parts) < 4:
                return None

            symbol = parts[0]
            x, y, z = _parse_tail_coordinates(parts)
            result.append((symbol, x, y, z))

        return result
    except (ValueError, TypeError, IndexError):
        return None


def parse_gaussian_input(filepath: str) -> dict[str, Any]:
    """Parse a Gaussian input file (.gjf/.com)."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return parse_gaussian_input_text(text, filepath)
    except OSError as e:
        raise OSError(f"Failed to read Gaussian input {filepath}: {e}") from e


def parse_gaussian_input_text(
    text: str, source_label: str = "text", *, strict: bool = False
) -> dict[str, Any]:
    """Parse Gaussian input text.

    Prefer the charge/multiplicity line that is immediately followed by a
    coordinate block. This avoids mistaking an all-numeric title line such
    as ``1 1`` for the real QM header.
    """
    from ..science.data import get_element_symbol

    def _looks_like_coordinate_line(raw_line: str) -> bool:
        parts = raw_line.split()
        if len(parts) < 4:
            return False
        try:
            _parse_tail_coordinates(parts)
        except (ValueError, TypeError, IndexError):
            return False
        return True

    if strict:
        return _parse_gaussian_input_text_strict(
            text, source_label=source_label, get_element_symbol=get_element_symbol
        )

    lines = text.splitlines()
    qm_idx = None
    fallback_qm_idx = None
    fallback_charge = 0
    fallback_mult = 1
    charge = 0
    mult = 1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^\s*-?\d+\s+-?\d+\s*$", s):
            parts = s.split()
            if fallback_qm_idx is None:
                fallback_qm_idx = i
                fallback_charge = int(parts[0])
                fallback_mult = int(parts[1])
            for candidate in lines[i + 1 :]:
                candidate_stripped = candidate.strip()
                if not candidate_stripped:
                    break
                if _looks_like_coordinate_line(candidate_stripped):
                    qm_idx = i
                    charge = int(parts[0])
                    mult = int(parts[1])
                    break
            if qm_idx is not None:
                break

    # Fall back to the first numeric pair only when no coordinate-backed
    # header is found. This keeps compatibility with minimal inputs while
    # still preferring unambiguous Gaussian structure blocks.
    if qm_idx is None and fallback_qm_idx is not None:
        qm_idx = fallback_qm_idx
        charge = fallback_charge
        mult = fallback_mult

    if qm_idx is None:
        raise ValueError(f"Cannot find charge/multiplicity line in {source_label}")

    atoms: list[str] = []
    coords_list: list[list[float]] = []
    coords_formatted: list[str] = []
    raw_coords_lines: list[str] = []

    for ln in lines[qm_idx + 1 :]:
        raw_ln = ln.strip()
        if not raw_ln:
            break
        parts = raw_ln.split()
        if len(parts) < 4:
            break

        raw_coords_lines.append(raw_ln)
        sym = parts[0]
        if sym.isdigit():
            sym = get_element_symbol(int(sym))

        try:
            x, y, z = _parse_tail_coordinates(parts)
        except (ValueError, TypeError, IndexError):
            break
        atoms.append(sym)
        coords_list.append([x, y, z])
        coords_formatted.append(f"{sym} {x:.8f} {y:.8f} {z:.8f}")

    return {
        "charge": charge,
        "multiplicity": mult,
        "atoms": atoms,
        "coords": coords_list,
        "coords_lines": coords_formatted,
        "raw_coords_lines": raw_coords_lines,
    }


def _parse_gaussian_input_text_strict(
    text: str, *, source_label: str, get_element_symbol: Any
) -> dict[str, Any]:
    """Strict whole-fail-closed parse used only by structure preview."""
    from ..domain.elements import ELEMENT_SYMBOLS, canonical_element_symbol
    from ..domain.errors import ElementSymbolError

    if not isinstance(text, str):
        raise _strict_fail(
            "invalid_header",
            f"input text must be str, got {type(text).__name__}",
            line=None,
            source_label=str(source_label),
        )
    if not isinstance(source_label, str):
        source_label = str(source_label)

    if _LINK1_RE.search(text):
        first = None
        for lineno, raw in enumerate(text.splitlines(), start=1):
            if _LINK1_RE.search(raw):
                first = lineno
                break
        raise _strict_fail(
            "multiple_geometries",
            f"refuses multiple geometry sections: '--Link1--' at line {first}; "
            "only single cartesian geometry is supported",
            line=first,
            source_label=source_label,
        )

    lines = text.splitlines()
    _strict_check_units(lines, source_label=source_label)

    def _looks_like_coordinate(raw_line: str) -> bool:
        parts = raw_line.split()
        if len(parts) < 4:
            return False
        try:
            _parse_tail_coordinates(parts)
        except (ValueError, TypeError, IndexError):
            return False
        return True

    backed: list[tuple[int, int, int]] = []
    all_headers: list[tuple[int, int, int]] = []
    fallback_idx: int | None = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if _QM_LINE_RE.match(s):
            parts = s.split()
            entry = (i, int(parts[0]), int(parts[1]))
            all_headers.append(entry)
            if fallback_idx is None:
                fallback_idx = i
            for candidate in lines[i + 1 :]:
                cs = candidate.strip()
                if not cs:
                    break
                if _looks_like_coordinate(cs):
                    backed.append(entry)
                    break

    if not all_headers:
        raise _strict_fail(
            "incomplete_geometry",
            "no coordinate-backed charge/multiplicity section found; "
            "only single cartesian geometry is supported",
            line=None,
            source_label=source_label,
        )
    if len(all_headers) == 1:
        qm_idx, charge, mult = all_headers[0]
    elif len(backed) == 1:
        qm_idx, charge, mult = backed[0]
    else:
        first = backed[0][0] + 1 if backed else all_headers[0][0] + 1
        raise _strict_fail(
            "multiple_geometries",
            f"refuses multiple geometry sections: found {len(all_headers)} "
            f"charge/multiplicity headers (first at line {first})",
            line=first,
            source_label=source_label,
        )
    if mult < 1:
        raise _strict_fail(
            "invalid_header",
            f"charge/multiplicity at line {qm_idx + 1} must satisfy "
            f"multiplicity >= 1: '{lines[qm_idx].strip()}'",
            line=qm_idx + 1,
            source_label=source_label,
        )

    warnings: list[str] = []
    if fallback_idx is not None and fallback_idx != qm_idx:
        warnings.append(
            f"line {fallback_idx + 1}: numeric title ignored, "
            f"using coordinate-backed header at line {qm_idx + 1}"
        )

    atoms: list[str] = []
    coords_list: list[list[float]] = []
    coords_formatted: list[str] = []
    raw_coords_lines: list[str] = []
    end_idx: int | None = None

    for offset, ln in enumerate(lines[qm_idx + 1 :], start=qm_idx + 2):
        raw_ln = ln.strip()
        if not raw_ln:
            end_idx = offset
            break
        parts = raw_ln.split()
        if len(parts) != 4:
            raise _strict_fail(
                "unknown_coordinate_token",
                f"line {offset} has {len(parts)} tokens, expected 4 " f"'<symbol> x y z': {raw_ln}",
                line=offset,
                source_label=source_label,
            )
        sym_tok = parts[0]
        if re.match(r"^[+-]?\d+$", sym_tok):
            number = int(sym_tok)
            if number < 1 or number >= len(ELEMENT_SYMBOLS):
                raise _strict_fail(
                    "unknown_element",
                    f"line {offset} has unknown element {sym_tok!r}: {raw_ln}",
                    line=offset,
                    source_label=source_label,
                )
            symbol = get_element_symbol(number)
            warnings.append(
                f"line {offset}: numeric atomic number {sym_tok} normalized to {symbol}"
            )
        else:
            try:
                symbol = canonical_element_symbol(sym_tok)
            except ElementSymbolError:
                raise _strict_fail(
                    "unknown_element",
                    f"line {offset} has unknown element {sym_tok!r}: {raw_ln}",
                    line=offset,
                    source_label=source_label,
                ) from None
        try:
            point = (float(parts[1]), float(parts[2]), float(parts[3]))
        except (TypeError, ValueError):
            raise _strict_fail(
                "invalid_coordinate",
                f"line {offset} has non-finite/non-numeric coordinate: {raw_ln}",
                line=offset,
                source_label=source_label,
            ) from None
        if not all(math.isfinite(v) for v in point):
            raise _strict_fail(
                "invalid_coordinate",
                f"line {offset} has non-finite/non-numeric coordinate: {raw_ln}",
                line=offset,
                source_label=source_label,
            )
        atoms.append(symbol)
        coords_list.append([point[0], point[1], point[2]])
        coords_formatted.append(f"{symbol} {point[0]:.8f} {point[1]:.8f} {point[2]:.8f}")
        raw_coords_lines.append(raw_ln)

    if not atoms:
        raise _strict_fail(
            "empty_geometry",
            "geometry block is empty",
            line=qm_idx + 1,
            source_label=source_label,
        )

    if end_idx is not None:
        for lineno in range(end_idx + 1, len(lines) + 1):
            rest = lines[lineno - 1].strip()
            if rest:
                raise _strict_fail(
                    "trailing_content",
                    f"refuses trailing content at line {lineno}: {rest}; "
                    "only single cartesian geometry is supported",
                    line=lineno,
                    source_label=source_label,
                )

    return {
        "charge": charge,
        "multiplicity": mult,
        "atoms": atoms,
        "coords": coords_list,
        "coords_lines": coords_formatted,
        "raw_coords_lines": raw_coords_lines,
        "warnings": warnings,
        "source_format": STRICT_SOURCE_FORMAT,
    }


def calculate_bond_length(coords_lines: list[str], atom1: int, atom2: int) -> float | None:
    """Calculate the distance between two atoms."""
    coords_array = coords_lines_to_array(coords_lines)
    if coords_array is None:
        return None

    if atom1 < 1 or atom2 < 1 or atom1 > len(coords_array) or atom2 > len(coords_array):
        return None

    _, x1, y1, z1 = coords_array[atom1 - 1]
    _, x2, y2, z2 = coords_array[atom2 - 1]

    dx, dy, dz = x1 - x2, y1 - y2, z1 - z2
    return float((dx * dx + dy * dy + dz * dz) ** 0.5)
