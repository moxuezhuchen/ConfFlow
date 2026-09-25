#!/usr/bin/env python3

"""Gaussian native-input rendering for the V4 program adapter.

Pure formatting helpers extracted from the legacy Gaussian input writer. This
module owns V4-native copies of the keyword normalization, Link0 assembly,
freeze-flag formatting, and template rendering algorithms. It never imports
the legacy packages those algorithms were extracted from.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "ALLOWED_NATIVE_KEYS",
    "apply_freeze",
    "check_native_keys",
    "coerce_section_lines",
    "format_coordinates",
    "format_keyword_line",
    "format_memory_gb",
    "normalize_gaussian_keyword",
    "render_gaussian_input",
    "resolve_charge",
    "resolve_core_count",
    "resolve_extra_section",
    "resolve_keyword",
    "resolve_link0_lines",
    "resolve_multiplicity",
    "resolve_write_chk",
    "sanitize_job_name",
    "scan_keyword_from_ts",
]

#: Strict native vocabulary accepted in ``ResolvedCalculationInputs.native``.
ALLOWED_NATIVE_KEYS: frozenset[str] = frozenset(
    {
        "keyword",
        "extra_sections",
        "gaussian_extra",
        "link0",
        "modredundant",
        "write_chk",
    }
)

_BYTES_PER_GB: int = 1024**3

_MAX_JOB_LENGTH: int = 128

_JOB_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9_.\-]+")
_HASH_PREFIX_PATTERN = re.compile(r"^\s*#+\s*")
_P_REQUEST_PATTERN = re.compile(r"^[pP](?:\s|$)")
_KEYWORD_NORMALIZE_PATTERN = re.compile(r"^\s*(?:#\s*[pPnNtT]?\s*)+")
_OPT_GROUP_PATTERN = re.compile(r"(?i)\bopt\s*(?:=\s*)?\(([^)]*)\)")
_FREQ_TOKEN_PATTERN = re.compile(r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?")
_WHITESPACE_PATTERN = re.compile(r"\s+")

#: Optimization items dropped when a TS keyword is rewritten for a scan job.
_REMOVE_OPT_ITEMS: frozenset[str] = frozenset(
    {"calcfc", "tight", "ts", "noeigentest", "rcfc", "readfc"}
)

#: String values that disable checkpoint writing, mirroring the legacy rule.
_NEGATIVE_WRITE_CHK: frozenset[str] = frozenset({"0", "false", "no", "off"})


def sanitize_job_name(value: str, *, fallback: str = "job") -> str:
    """Sanitize a logical key into a filesystem-safe Gaussian job name.

    Parameters
    ----------
    value : str
        Candidate job name, usually the work-item logical key.
    fallback : str, optional
        Name used when nothing sanitizable remains.

    Returns
    -------
    str
        Deterministic job name containing only ``[A-Za-z0-9_.-]``.
    """
    cleaned = _JOB_SANITIZE_PATTERN.sub("_", str(value).strip()).strip("._")
    if not cleaned:
        cleaned = _JOB_SANITIZE_PATTERN.sub("_", str(fallback).strip()).strip("._")
    if not cleaned:
        cleaned = "job"
    return cleaned[:_MAX_JOB_LENGTH]


def check_native_keys(native: Mapping[str, Any]) -> None:
    """Validate the strict Gaussian native vocabulary.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.

    Raises
    ------
    ValueError
        Raised when *native* is not a mapping, carries an unknown key, or
        carries the rejected ``blocks`` key.
    """
    if not isinstance(native, Mapping):
        raise ValueError("native_input_error: Gaussian native options must be a mapping")
    if "blocks" in native:
        if isinstance(native["blocks"], Mapping):
            raise ValueError(
                "native_input_error: Gaussian 'blocks' must be a string extra section; "
                "dict blocks use ORCA syntax. "
                "Use 'modredundant' or 'link0' for Gaussian-specific sections."
            )
        raise ValueError(
            "native_input_error: Gaussian does not accept a 'blocks' key; "
            "use 'extra_sections' for string sections, 'modredundant', or 'link0'."
        )
    unknown = sorted(str(key) for key in native.keys() if key not in ALLOWED_NATIVE_KEYS)
    if unknown:
        raise ValueError(
            "native_input_error: Gaussian got unknown native key(s) "
            f"{unknown}; allowed keys are {sorted(ALLOWED_NATIVE_KEYS)}"
        )


def normalize_gaussian_keyword(value: str) -> str:
    """Strip leading route-section markers from a Gaussian keyword line.

    Parameters
    ----------
    value : str
        Raw keyword text, optionally starting with ``#``, ``#p``, or similar.

    Returns
    -------
    str
        Keyword body without leading markers, or ``""`` when nothing remains.
    """
    return _KEYWORD_NORMALIZE_PATTERN.sub("", value).strip() or ""


def format_keyword_line(keyword: str) -> str:
    """Format a raw keyword value as a Gaussian route-section line.

    Parameters
    ----------
    keyword : str
        Required non-empty keyword text.

    Returns
    -------
    str
        ``#<keyword>`` when the text already starts with a ``#p``-style
        request, otherwise ``# <keyword>`` with markers normalized away.

    Raises
    ------
    ValueError
        Raised when nothing remains after normalization.
    """
    keyword_raw = _HASH_PREFIX_PATTERN.sub("", keyword).strip()
    if _P_REQUEST_PATTERN.match(keyword_raw):
        line = f"#{keyword_raw}".rstrip()
    else:
        line = f"# {normalize_gaussian_keyword(keyword)}".rstrip()
    if not line.replace("#", "").strip():
        raise ValueError("native_input_error: Gaussian 'keyword' is empty after normalization")
    return line


def resolve_keyword(native: Mapping[str, Any]) -> str:
    """Return the required non-empty ``keyword`` entry from native options.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.

    Returns
    -------
    str
        Stripped keyword text, still unformatted.

    Raises
    ------
    ValueError
        Raised when ``keyword`` is missing, not a string, or blank.
    """
    value = native.get("keyword")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("native_input_error: Gaussian 'keyword' must be a non-empty string")
    return value.strip()


def coerce_section_lines(value: Any, key: str) -> list[str]:
    """Coerce a string or string-list native entry to stripped lines.

    Parameters
    ----------
    value : Any
        Candidate entry value; ``None`` means absent.
    key : str
        Entry name used in error messages.

    Returns
    -------
    list[str]
        Stripped non-empty lines in declared order.

    Raises
    ------
    ValueError
        Raised when *value* is neither a string nor a list/tuple of scalars,
        or when a sequence entry is itself a container.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, (dict, list, tuple, set, frozenset)):
                raise ValueError(
                    f"native_input_error: Gaussian {key!r} entries must be strings, "
                    f"got {type(item).__name__}"
                )
            text = str(item).strip()
            if text:
                lines.append(text)
        return lines
    raise ValueError(
        f"native_input_error: Gaussian {key!r} must be a string or a list of strings, "
        f"got {type(value).__name__}"
    )


def resolve_extra_section(native: Mapping[str, Any]) -> str:
    """Resolve the trailing extra-section text from native options.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.

    Returns
    -------
    str
        ``""`` when no extra content was declared, otherwise the joined
        ``extra_sections``/``gaussian_extra`` lines followed by any
        ``modredundant`` lines, ending with a newline.

    Raises
    ------
    ValueError
        Raised when ``extra_sections`` and ``gaussian_extra`` are both
        non-empty but disagree.
    """
    primary = coerce_section_lines(native.get("extra_sections"), "extra_sections")
    alias = coerce_section_lines(native.get("gaussian_extra"), "gaussian_extra")
    if primary and alias and primary != alias:
        raise ValueError(
            "native_input_error: Gaussian 'extra_sections' and 'gaussian_extra' "
            "conflict; declare only one."
        )
    lines = list(primary or alias)
    lines.extend(coerce_section_lines(native.get("modredundant"), "modredundant"))
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def resolve_write_chk(native: Mapping[str, Any]) -> bool:
    """Resolve whether a checkpoint file is requested.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.

    Returns
    -------
    bool
        The ``write_chk`` flag, defaulting to ``True`` when absent. String
        values follow the legacy negative vocabulary (``0``, ``false``,
        ``no``, ``off`` disable writing).
    """
    value = native.get("write_chk")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _NEGATIVE_WRITE_CHK


def resolve_core_count(cores: int | None) -> int:
    """Validate the resolved per-item core count.

    Parameters
    ----------
    cores : int | None
        Cores reserved per work item.

    Returns
    -------
    int
        The validated core count used for ``%nprocshared``.

    Raises
    ------
    ValueError
        Raised when *cores* is missing, not an integer, or below one.
    """
    if cores is None:
        raise ValueError(
            "native_input_error: Gaussian resources are not resolved; " "'cores_per_item' is None"
        )
    if isinstance(cores, bool) or not isinstance(cores, int):
        raise ValueError(
            "native_input_error: Gaussian 'cores_per_item' must be an integer, " f"got {cores!r}"
        )
    if cores < 1:
        raise ValueError(f"native_input_error: Gaussian 'cores_per_item' must be >= 1, got {cores}")
    return cores


def format_memory_gb(memory_bytes: int | None) -> str:
    """Format per-item memory bytes as an integer-gigabyte ``%mem`` value.

    Parameters
    ----------
    memory_bytes : int | None
        Memory reserved per work item in bytes.

    Returns
    -------
    str
        Integer gigabytes with a ``GB`` suffix, minimum ``1GB``.

    Raises
    ------
    ValueError
        Raised when *memory_bytes* is missing, not an integer, or not positive.
    """
    if memory_bytes is None:
        raise ValueError(
            "native_input_error: Gaussian resources are not resolved; "
            "'memory_per_item_bytes' is None"
        )
    if isinstance(memory_bytes, bool) or not isinstance(memory_bytes, int):
        raise ValueError(
            "native_input_error: Gaussian 'memory_per_item_bytes' must be an integer, "
            f"got {memory_bytes!r}"
        )
    if memory_bytes <= 0:
        raise ValueError(
            "native_input_error: Gaussian 'memory_per_item_bytes' must be > 0, "
            f"got {memory_bytes}"
        )
    return f"{max(1, int(memory_bytes / _BYTES_PER_GB))}GB"


def resolve_charge(charge: int | None) -> int:
    """Validate the resolved total charge.

    Parameters
    ----------
    charge : int | None
        Total charge of the structure.

    Returns
    -------
    int
        The validated charge.

    Raises
    ------
    ValueError
        Raised when *charge* is missing or not an integer; the adapter never
        defaults it silently.
    """
    if charge is None:
        raise ValueError(
            "native_input_error: Gaussian 'charge' is not resolved (None); "
            "declare the charge explicitly instead of defaulting it"
        )
    if isinstance(charge, bool) or not isinstance(charge, int):
        raise ValueError(
            f"native_input_error: Gaussian 'charge' must be an integer, got {charge!r}"
        )
    return charge


def resolve_multiplicity(multiplicity: int | None) -> int:
    """Validate the resolved spin multiplicity.

    Parameters
    ----------
    multiplicity : int | None
        Spin multiplicity of the structure.

    Returns
    -------
    int
        The validated multiplicity.

    Raises
    ------
    ValueError
        Raised when *multiplicity* is missing or not an integer; the adapter
        never defaults it silently.
    """
    if multiplicity is None:
        raise ValueError(
            "native_input_error: Gaussian 'multiplicity' is not resolved (None); "
            "declare the multiplicity explicitly instead of defaulting it"
        )
    if isinstance(multiplicity, bool) or not isinstance(multiplicity, int):
        raise ValueError(
            "native_input_error: Gaussian 'multiplicity' must be an integer, "
            f"got {multiplicity!r}"
        )
    return multiplicity


def _point_triple(point: Sequence[float], position: int) -> tuple[float, float, float]:
    """Return validated ``(x, y, z)`` floats for one atom position.

    Parameters
    ----------
    point : Sequence[float]
        Candidate coordinate triple.
    position : int
        1-based atom position used in error messages.

    Returns
    -------
    tuple[float, float, float]
        Validated coordinate triple.

    Raises
    ------
    ValueError
        Raised when *point* is not exactly three real numbers.
    """
    try:
        triple = (float(point[0]), float(point[1]), float(point[2]))
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(
            "native_input_error: Gaussian structure coordinates "
            f"for atom {position} must be three real numbers"
        ) from exc
    if len(tuple(point)) != 3:
        raise ValueError(
            "native_input_error: Gaussian structure coordinates "
            f"for atom {position} must have exactly 3 values"
        )
    return triple


def _require_matching_lengths(atoms: Sequence[str], coordinates: Sequence[Sequence[float]]) -> None:
    """Raise a native input error when atom and coordinate counts differ.

    Parameters
    ----------
    atoms : Sequence[str]
        Element symbols in atom order.
    coordinates : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom.

    Raises
    ------
    ValueError
        Raised when the two sequences have different lengths.
    """
    if len(atoms) != len(coordinates):
        raise ValueError(
            "native_input_error: Gaussian structure has "
            f"{len(atoms)} atoms but {len(coordinates)} coordinate triples"
        )


def format_coordinates(atoms: Sequence[str], coordinates: Sequence[Sequence[float]]) -> list[str]:
    """Format structure atoms and coordinates as Gaussian input lines.

    Parameters
    ----------
    atoms : Sequence[str]
        Element symbols in atom order; already canonical upstream.
    coordinates : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom.

    Returns
    -------
    list[str]
        ``"%-2s %12.6f %12.6f %12.6f"`` lines in atom order.

    Raises
    ------
    ValueError
        Raised when atom and coordinate counts differ or a triple is invalid.
    """
    _require_matching_lengths(atoms, coordinates)
    lines = []
    for position, (symbol, point) in enumerate(zip(atoms, coordinates), start=1):
        x, y, z = _point_triple(point, position)
        lines.append(f"{symbol:<2s} {x:>12.6f} {y:>12.6f} {z:>12.6f}")
    return lines


def apply_freeze(
    atoms: Sequence[str],
    coordinates: Sequence[Sequence[float]],
    freeze_indices: Sequence[int] | None,
) -> list[str]:
    """Format coordinates with frozen atoms flagged ``-1``.

    Parameters
    ----------
    atoms : Sequence[str]
        Element symbols in atom order; already canonical upstream.
    coordinates : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom.
    freeze_indices : Sequence[int] | None
        1-based frozen atom indices; ``None`` or empty means no freeze.

    Returns
    -------
    list[str]
        Plain coordinate lines when nothing is frozen, otherwise every line
        gains a flag column (``-1`` frozen, ``0`` free) formatted as
        ``"%-2s flag %12.6f %12.6f %12.6f"``.

    Raises
    ------
    ValueError
        Raised when a freeze index is not integer-like or the geometry is
        malformed. Out-of-range indices never match and are ignored.
    """
    if not freeze_indices:
        return format_coordinates(atoms, coordinates)
    try:
        frozen = {int(index) for index in freeze_indices}
    except (TypeError, ValueError) as exc:
        raise ValueError("native_input_error: Gaussian freeze indices must be integers") from exc
    _require_matching_lengths(atoms, coordinates)
    lines = []
    for position, (symbol, point) in enumerate(zip(atoms, coordinates), start=1):
        x, y, z = _point_triple(point, position)
        flag = -1 if position in frozen else 0
        lines.append(f"{symbol:<2s} {flag} {x:>12.6f} {y:>12.6f} {z:>12.6f}")
    return lines


def resolve_link0_lines(
    *,
    job: str,
    write_chk: bool,
    oldchk_name: str | None,
    user_link0: Any,
) -> list[str]:
    """Assemble Link0 directive lines in deterministic order.

    Parameters
    ----------
    job : str
        Sanitized job name used for the checkpoint file name.
    write_chk : bool
        Whether to lead with a ``%Chk=<job>.chk`` line.
    oldchk_name : str | None
        Staged checkpoint local name for ``%OldChk``; ignored when blank.
    user_link0 : Any
        Extra user Link0 lines as a string or string list.

    Returns
    -------
    list[str]
        ``%Chk``, then ``%OldChk``, then user lines.

    Raises
    ------
    ValueError
        Raised when *user_link0* has an unsupported shape.
    """
    lines: list[str] = []
    if write_chk:
        lines.append(f"%Chk={job}.chk")
    if oldchk_name is not None and str(oldchk_name).strip():
        lines.append(f"%OldChk={str(oldchk_name).strip()}")
    lines.extend(coerce_section_lines(user_link0, "link0"))
    return lines


def render_gaussian_input(
    *,
    link0_lines: Sequence[str],
    cores: int,
    memory: str,
    keyword_line: str,
    job: str,
    charge: int,
    multiplicity: int,
    coord_lines: Sequence[str],
    extra_section: str,
) -> str:
    """Render the complete Gaussian input file content.

    Parameters
    ----------
    link0_lines : Sequence[str]
        Link0 directives, each rendered on its own leading line.
    cores : int
        ``%nprocshared`` value.
    memory : str
        ``%mem`` value such as ``"4GB"``.
    keyword_line : str
        Formatted route-section line starting with ``#``.
    job : str
        Job title line.
    charge : int
        Total charge line value.
    multiplicity : int
        Spin multiplicity line value.
    coord_lines : Sequence[str]
        Formatted coordinate lines.
    extra_section : str
        Trailing extra-section text (``""`` or ending with a newline).

    Returns
    -------
    str
        Full ``.gjf`` file content following the V4 template, which renders
        ``%nprocshared`` where the legacy template used ``%nproc``.
    """
    link0 = "".join(f"{line.rstrip()}\n" for line in link0_lines)
    coordinates = "\n".join(coord_lines)
    return (
        f"{link0}%nprocshared={cores}\n"
        f"%mem={memory}\n"
        f"{keyword_line}\n"
        f"\n"
        f"{job}\n"
        f"\n"
        f"{charge} {multiplicity}\n"
        f"{coordinates}\n"
        f"\n"
        f"{extra_section}\n"
        f"\n"
    )


def scan_keyword_from_ts(keyword: str | None) -> str | None:
    """Rewrite a TS keyword line into one suitable for a scan job.

    Parameters
    ----------
    keyword : str | None
        TS keyword text.

    Returns
    -------
    str | None
        Rewritten keyword with TS-only optimization items (``calcfc``,
        ``tight``, ``ts``, ``noeigentest``, ``rcfc``, ``readfc``) removed
        from ``opt(...)`` groups and ``freq`` tokens dropped, or ``None``
        when nothing usable remains.
    """
    if not isinstance(keyword, str):
        return None
    text = keyword.strip()
    if not text:
        return None

    def _rewrite_opt_group(match: re.Match[str]) -> str:
        full = match.group(0)
        inner = match.group(1) or ""
        has_equal = "=" in full
        items = [item.strip() for item in inner.split(",") if item.strip()]
        kept = [
            item for item in items if item.split("=")[0].strip().lower() not in _REMOVE_OPT_ITEMS
        ]
        if not kept:
            return "opt"
        return f"opt{'=' if has_equal else ''}({','.join(kept)})"

    rewritten = _OPT_GROUP_PATTERN.sub(_rewrite_opt_group, text)
    rewritten = _FREQ_TOKEN_PATTERN.sub(" ", rewritten)
    rewritten = _WHITESPACE_PATTERN.sub(" ", rewritten).strip()
    return rewritten or None
