#!/usr/bin/env python3

"""Producer ``structure_preview`` thin projection (parsing only).

Future authoring interface frozen for L1 pairing/JD consumption: ``structure_preview_request(parameters)`` takes ``filename`` (required non-empty str, routing hint only, never opened), ``content_text`` (required str, single input document, paths never accepted), optional ``source_format_hint``.
Hint is canonical xyz/gaussian-gjf/orca-inp case-insensitive (gjf/gf/com map to gaussian-gjf, inp to orca-inp); when present it must agree with filename suffix, conflicts fail closed; unknown suffix requires the hint.
Ok result preserves input atom order and full float precision verbatim with no sorting/dedup/unit conversion/re-perception/optimization; ``index_base`` is always 1; coordinates in Angstrom; ``warnings`` holds factual notes only.
``charge``/``multiplicity`` are source values only (XYZ omits them); no scientific defaults fabricated.
Failures raise ``StructurePreviewError`` (a ``ValueError``) with structured ``code``/``line``/``field``/``source_label``; consumers must read attributes, never parse message text. Stable codes: ``invalid_parameters``, ``invalid_format``, ``multiple_geometries``, ``empty_geometry``, ``incomplete_geometry``, ``invalid_header``, ``unknown_element``, ``invalid_coordinate``, ``unknown_coordinate_token``, ``unsupported_unit``, ``unsupported_format``, ``trailing_content``, ``missing_external_context``, ``invalid_structure``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, cast

__all__ = [
    "INDEX_BASE",
    "SOURCE_FORMATS",
    "StructurePreviewError",
    "structure_preview_request",
]

#: Picker table base (``id = i + 1``).
INDEX_BASE: int = 1

#: Canonical source formats.
SOURCE_FORMATS: tuple[str, ...] = ("xyz", "gaussian-gjf", "orca-inp")

#: Field path used by all refusals.
FIELD_PATH: str = "content_text"


class StructurePreviewError(ValueError):
    """Structured preview refusal carrying producer-envelope fields."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        line: int | None = None,
        source_label: str = "text",
        field: str = FIELD_PATH,
        filename: str | None = None,
        source_format: str | None = None,
    ) -> None:
        self.code = code
        self.line = line
        self.source_label = source_label
        self.field = field
        self.filename = filename
        self.source_format = source_format
        where = f"line {line}" if line is not None else "line ?"
        fmt = source_format if source_format is not None else "structure-preview"
        super().__init__(
            f"native_input_error: {fmt} {code}: {message} "
            f"({where}, field {field}, source {source_label})"
        )


def _fail(
    code: str,
    detail: str,
    *,
    line: int | None = None,
    source_label: str = "text",
    filename: str | None = None,
    source_format: str | None = None,
) -> StructurePreviewError:
    """Build a structured refusal."""
    return StructurePreviewError(
        code,
        detail,
        line=line,
        source_label=source_label,
        field=FIELD_PATH,
        filename=filename,
        source_format=source_format,
    )


def _normalize_hint(value: str) -> str | None:
    """Map a hint token to its canonical source format, if known."""
    token = str(value).strip().lower()
    if token in ("xyz",):
        return "xyz"
    if token in ("gaussian-gjf", "gaussian", "gjf", "gf", "com"):
        return "gaussian-gjf"
    if token in ("orca-inp", "orca", "inp"):
        return "orca-inp"
    return None


def _suffix_format(filename: str) -> str | None:
    """Derive the canonical format from the filename suffix, if known."""
    lowered = str(filename).strip().lower()
    if lowered.endswith(".xyz"):
        return "xyz"
    if lowered.endswith(".gjf") or lowered.endswith(".gf") or lowered.endswith(".com"):
        return "gaussian-gjf"
    if lowered.endswith(".inp"):
        return "orca-inp"
    return None


def _resolve_source_format(filename: str, hint: Any, *, source_label: str) -> str:
    """Resolve routing strictly from suffix and optional hint."""
    suffix = _suffix_format(filename)
    canonical_hint: str | None = None
    if hint is not None:
        if not isinstance(hint, str) or not hint.strip():
            raise _fail(
                "invalid_format",
                f"source_format_hint must be a non-empty string: {hint!r}",
                line=None,
                source_label=source_label,
                filename=filename,
            )
        canonical_hint = _normalize_hint(hint)
        if canonical_hint is None:
            raise _fail(
                "invalid_format",
                f"unknown source_format_hint {hint!r}; " "expected xyz/gaussian-gjf/orca-inp",
                line=None,
                source_label=source_label,
                filename=filename,
            )
    if canonical_hint is not None and suffix is not None and canonical_hint != suffix:
        raise _fail(
            "invalid_format",
            f"source_format_hint {canonical_hint!r} conflicts with "
            f"filename suffix ({filename!r} implies {suffix!r})",
            line=None,
            source_label=source_label,
            filename=filename,
        )
    resolved = canonical_hint if canonical_hint is not None else suffix
    if resolved is None:
        raise _fail(
            "invalid_format",
            f"cannot route {filename!r}: unknown suffix and no "
            "source_format_hint; expected .xyz/.gjf/.gf/.com/.inp",
            line=None,
            source_label=source_label,
            filename=filename,
        )
    return resolved


def _preview_gaussian(content_text: str, *, filename: str, source_label: str) -> dict[str, Any]:
    """Project Gaussian text through the shared runtime authority."""
    from ..core.gaussian_input import (
        GaussianInputStrictError,
        parse_gaussian_input_text,
    )

    try:
        parsed = parse_gaussian_input_text(content_text, source_label, strict=True)
    except GaussianInputStrictError as exc:
        raise _fail(
            exc.code,
            str(exc),
            line=exc.line,
            source_label=exc.source_label,
            filename=filename,
            source_format="gaussian-gjf",
        ) from None
    except ValueError as exc:
        raise _fail(
            "invalid_header",
            f"gaussian input refused: {exc}",
            line=None,
            source_label=source_label,
            filename=filename,
            source_format="gaussian-gjf",
        ) from None
    elements = [str(v) for v in parsed["atoms"]]
    coordinates = [[float(v) for v in triple] for triple in parsed["coords"]]
    warnings = [str(v) for v in parsed.get("warnings", []) if str(v)]
    _validate_record(
        elements,
        coordinates,
        charge=parsed.get("charge"),
        multiplicity=parsed.get("multiplicity"),
        filename=filename,
        source_label=source_label,
        source_format="gaussian-gjf",
    )
    result: dict[str, Any] = {
        "elements": elements,
        "coordinates": coordinates,
        "index_base": INDEX_BASE,
        "source_format": "gaussian-gjf",
        "warnings": warnings,
        "charge": int(parsed["charge"]),
        "multiplicity": int(parsed["multiplicity"]),
    }
    return result


def _preview_orca(content_text: str, *, filename: str, source_label: str) -> dict[str, Any]:
    """Project ORCA text through the shared frozen authority."""
    from ..programs.orca.input_parsing import (
        OrcaInputParseError,
        parse_orca_input_text,
    )

    try:
        parsed = parse_orca_input_text(content_text, source_label=source_label)
    except OrcaInputParseError as exc:
        raise _fail(
            exc.code,
            str(exc),
            line=exc.line,
            source_label=exc.source_label,
            filename=filename,
            source_format="orca-inp",
        ) from None
    elements = [str(v) for v in parsed["elements"]]
    coordinates = [[float(v) for v in triple] for triple in parsed["coordinates"]]
    warnings = [str(v) for v in parsed.get("warnings", []) if str(v)]
    _validate_record(
        elements,
        coordinates,
        charge=parsed.get("charge"),
        multiplicity=parsed.get("multiplicity"),
        filename=filename,
        source_label=source_label,
        source_format="orca-inp",
    )
    return {
        "elements": elements,
        "coordinates": coordinates,
        "index_base": INDEX_BASE,
        "source_format": "orca-inp",
        "warnings": warnings,
        "charge": int(parsed["charge"]),
        "multiplicity": int(parsed["multiplicity"]),
    }


def _parse_xyz_frames_strict(content_text: str) -> list[dict[str, Any]]:
    """Split XYZ text into frames with strict file-reader semantics."""
    from ..core.elements import canonicalize_element_symbol
    from ..core.gaussian_input import coords_lines_to_array

    text_lines = content_text.splitlines()
    total = len(text_lines)
    position = 0
    line_number = 0
    frames: list[dict[str, Any]] = []
    while True:
        header: str | None = None
        while position < total:
            candidate = text_lines[position].strip()
            position += 1
            line_number += 1
            if candidate:
                header = candidate
                break
        if header is None:
            break
        if not header.isdigit():
            raise ValueError(f"xyz text: line {line_number}: invalid atom-count line: {header!r}")
        try:
            num_atoms = int(header)
        except ValueError:
            raise ValueError(
                f"xyz text: line {line_number}: cannot parse atom count: {header!r}"
            ) from None
        if position >= total:
            raise ValueError(f"xyz text: line {line_number + 1}: missing comment line")
        comment = text_lines[position].strip()
        position += 1
        line_number += 1
        atoms: list[str] = []
        coordinates: list[list[float]] = []
        for _atom_offset in range(num_atoms):
            if position >= total:
                raise ValueError(
                    f"xyz text: line {line_number + 1}: incomplete frame: declared "
                    f"{num_atoms} atoms but file ended early"
                )
            raw = text_lines[position].strip()
            position += 1
            line_number += 1
            parts = raw.split()
            if len(parts) < 4:
                raise ValueError(
                    f"xyz text: line {line_number}: coordinate line has fewer than 4 "
                    f"columns: {raw!r}"
                )
            try:
                atom = canonicalize_element_symbol(parts[0])
            except ValueError as exc:
                raise ValueError(f"xyz text: line {line_number}: {exc}") from None
            triple = coords_lines_to_array([raw])
            if triple is None:
                raise ValueError(
                    f"xyz text: line {line_number}: cannot parse coordinates from line: " f"{raw!r}"
                )
            atoms.append(atom)
            _symbol, x, y, z = triple[0]
            coordinates.append([x, y, z])
        frames.append(
            {
                "natoms": num_atoms,
                "comment": comment,
                "atoms": atoms,
                "coords": coordinates,
                "frame_index": len(frames),
            }
        )
    if not frames:
        raise ValueError("xyz text: no valid xyz frames found")
    return frames


def _preview_xyz(content_text: str, *, filename: str, source_label: str) -> dict[str, Any]:
    """Project XYZ text through strict in-memory frame parsing."""
    if not content_text.strip():
        raise _fail(
            "empty_geometry",
            "xyz text is empty",
            line=None,
            source_label=source_label,
            filename=filename,
            source_format="xyz",
        )
    try:
        frames = _parse_xyz_frames_strict(content_text)
    except (ValueError, OSError) as exc:
        message = str(exc).lower()
        if "no valid xyz frames" in message:
            code = "empty_geometry"
        elif "incomplete frame" in message or "missing comment" in message:
            code = "incomplete_geometry"
        elif "invalid element" in message:
            code = "unknown_element"
        elif "fewer than 4" in message:
            code = "unknown_coordinate_token"
        else:
            code = "invalid_coordinate"
        raise _fail(
            code,
            f"xyz input refused: {exc}",
            line=None,
            source_label=source_label,
            filename=filename,
            source_format="xyz",
        ) from None

    if not frames:
        raise _fail(
            "empty_geometry",
            "xyz input holds no geometry",
            line=None,
            source_label=source_label,
            filename=filename,
            source_format="xyz",
        )
    if len(frames) > 1:
        raise _fail(
            "multiple_geometries",
            f"refuses multiple xyz frames: found {len(frames)}; "
            "only single geometry is supported",
            line=None,
            source_label=source_label,
            filename=filename,
            source_format="xyz",
        )
    frame = frames[0]
    elements = [str(v) for v in frame["atoms"]]
    coordinates = [[float(v) for v in triple] for triple in frame["coords"]]
    for idx, triple in enumerate(coordinates, start=1):
        if len(triple) != 3 or not all(math.isfinite(v) for v in triple):
            raise _fail(
                "invalid_coordinate",
                f"xyz atom {idx} has non-finite coordinate",
                line=None,
                source_label=source_label,
                filename=filename,
                source_format="xyz",
            )
    _validate_record(
        elements,
        coordinates,
        charge=None,
        multiplicity=None,
        filename=filename,
        source_label=source_label,
        source_format="xyz",
    )
    warnings: list[str] = []
    comment = str(frame.get("comment", ""))
    if comment.strip():
        warnings.append("xyz comment preserved as diagnostic only")
    return {
        "elements": elements,
        "coordinates": coordinates,
        "index_base": INDEX_BASE,
        "source_format": "xyz",
        "warnings": warnings,
    }


def _validate_record(
    elements: list[str],
    coordinates: list[list[float]],
    *,
    charge: Any,
    multiplicity: Any,
    filename: str,
    source_label: str,
    source_format: str,
) -> None:
    """Validate the projected geometry through the domain record."""
    from ..domain.structure import StructureRecord

    try:
        StructureRecord(
            id="structure-preview",
            atoms=tuple(elements),
            coordinates=tuple(cast("tuple[float,float,float]", tuple(p)) for p in coordinates),
            charge=charge,
            multiplicity=multiplicity,
        )
    except Exception as exc:
        raise _fail(
            "invalid_structure",
            f"projected geometry refused by domain record: {exc}",
            line=None,
            source_label=source_label,
            filename=filename,
            source_format=source_format,
        ) from None


def structure_preview_request(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``{filename, content_text}`` to picker geometry."""
    if not isinstance(parameters, Mapping):
        raise _fail(
            "invalid_parameters",
            f"parameters must be a mapping, got {type(parameters).__name__}",
            line=None,
            source_label="text",
        )
    filename = parameters.get("filename")
    content_text = parameters.get("content_text")
    hint = parameters.get("source_format_hint")
    if not isinstance(filename, str) or not filename.strip():
        raise _fail(
            "invalid_parameters",
            "parameters.filename must be a non-empty string",
            line=None,
            source_label=str(filename) if isinstance(filename, str) else "text",
            filename=None,
        )
    if not isinstance(content_text, str):
        raise _fail(
            "invalid_parameters",
            f"parameters.content_text must be str, got {type(content_text).__name__}",
            line=None,
            source_label=filename,
            filename=filename,
        )
    allowed_keys = {"filename", "content_text", "source_format_hint"}
    extra = set(parameters.keys()) - allowed_keys
    if extra:
        raise _fail(
            "invalid_parameters",
            f"parameters holds unsupported keys: {sorted(extra)}",
            line=None,
            source_label=filename,
            filename=filename,
        )
    source_label = filename
    source_format = _resolve_source_format(filename, hint, source_label=source_label)
    if source_format == "gaussian-gjf":
        return _preview_gaussian(content_text, filename=filename, source_label=source_label)
    if source_format == "orca-inp":
        return _preview_orca(content_text, filename=filename, source_label=source_label)
    return _preview_xyz(content_text, filename=filename, source_label=source_label)
