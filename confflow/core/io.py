#!/usr/bin/env python3

"""
ConfFlow XYZ I/O - unified XYZ file read/write module.

Consolidates XYZ handling logic previously scattered across calc.py,
refine.py, viz.py, and utils.py.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from typing import Any

__all__ = [
    "upsert_comment_kv",
    "ensure_conformer_cids",
    "ensure_xyz_cids",
    "parse_comment_metadata",
    "read_xyz_file",
    "read_xyz_file_safe",
    "write_xyz_file",
    "append_xyz_conformer",
    "coords_lines_to_array",
    "parse_gaussian_input",
    "parse_gaussian_input_text",
    "calculate_bond_length",
]

_io_logger = logging.getLogger("confflow.io")


def upsert_comment_kv(comment: str, key: str, value: Any) -> str:
    """Update or insert a key=value pair in a comment line (no numeric formatting).

    Parameters
    ----------
    comment : str
        Original comment string.
    key : str
        Metadata key.
    value : Any
        Value to set.

    Returns
    -------
    str
        Updated comment string.

    Notes
    -----
    - If ``key=xxx`` already exists, the first occurrence is replaced.
    - Otherwise ``" | key=value"`` is appended (or just ``key=value`` if empty).
    """
    comment = (comment or "").strip()
    key = str(key)
    val_str = str(value)

    pattern = re.compile(rf"(?P<prefix>^|[\s|,])(?P<k>{re.escape(key)})\s*=\s*(?P<v>[^\s|,]+)")
    m = pattern.search(comment)
    if not m:
        if not comment:
            return f"{key}={val_str}"
        return f"{comment} | {key}={val_str}"

    start, end = m.span("v")
    return comment[:start] + val_str + comment[end:]


def ensure_conformer_cids(
    conformers: list[dict[str, Any]],
    *,
    prefix: str = "A",
    start: int = 1,
    width: int = 6,
) -> list[dict[str, Any]]:
    """Ensure every conformer has a CID and write it back to comment/metadata."""
    next_id = start
    for conf in conformers:
        meta = conf.get("metadata")
        if not meta:
            meta = {}
            conf["metadata"] = meta

        existing_cid = meta.get("CID")
        if existing_cid:
            if "CID=" not in conf.get("comment", ""):
                conf["comment"] = upsert_comment_kv(
                    conf.get("comment", ""), "CID", str(existing_cid)
                )
            continue

        new_cid = f"{prefix}{next_id:0{width}d}"
        next_id += 1
        meta["CID"] = new_cid
        conf["comment"] = upsert_comment_kv(conf.get("comment", ""), "CID", new_cid)

    return conformers


def ensure_xyz_cids(xyz_path: str, prefix: str = "A") -> None:
    """Read an XYZ file and ensure all conformers have CIDs; re-write if incomplete."""
    if not os.path.exists(xyz_path):
        return
    try:
        confs = read_xyz_file(xyz_path, parse_metadata=True)
        if confs and (
            not confs.get("metadata")
            if isinstance(confs, dict)
            else (not confs[0].get("metadata") or "CID" not in confs[0]["metadata"])
        ):
            ensure_conformer_cids(confs, prefix=prefix)
            write_xyz_file(xyz_path, confs, atomic=True)
    except (OSError, ValueError, IndexError) as e:
        _io_logger.debug(f"ensure_xyz_cids: non-fatal error ({xyz_path}): {e}")


def parse_comment_metadata(comment: str) -> dict[str, Any]:
    """Parse key=value metadata from an XYZ comment line.

    Parameters
    ----------
    comment : str
        Comment string, e.g. ``"Rank=1 | E=-1.0 | G_corr=0.123"``.

    Returns
    -------
    dict[str, Any]
        Parsed dictionary; values are converted to float when possible.
    """
    meta: dict[str, Any] = {}
    # Match key=value patterns (compatible with space, comma, or pipe separators)
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s|,]+)", comment or ""):
        k, v = m.group(1), m.group(2)
        try:
            meta[k] = float(v)
        # Keep the legacy Energy alias in sync with E.
            if k == "Energy":
                meta["E"] = float(v)
        except (ValueError, TypeError):
            meta[k] = v
    return meta


def _raise_xyz_parse_error(filepath: str, line_num: int, message: str) -> None:
    """Raise a detailed XYZ parse error with file/line context."""
    raise ValueError(f"{filepath}: line {line_num}: {message}")


def read_xyz_file(
    filepath: str,
    parse_metadata: bool = True,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Read an XYZ file and return a list of conformers.

    Parameters
    ----------
    filepath : str
        Path to the XYZ file.
    parse_metadata : bool
        Whether to parse key=value metadata from comment lines.
    strict : bool
        When True, raise on malformed frames instead of silently skipping them.

    Returns
    -------
    list[dict[str, Any]]
        List of conformer dicts, each containing:

        - ``natoms``: number of atoms
        - ``comment``: raw comment line
        - ``atoms``: list of atom symbols (upper-case)
        - ``coords``: coordinate list ``[[x, y, z], ...]``
        - ``metadata``: metadata dict (if *parse_metadata* is True)
    """
    conformers = []

    try:
        with open(filepath, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        raise OSError(f"Cannot read XYZ file {filepath}: {e}") from e

    i = 0
    frame_idx = 0

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if not line.isdigit():
            if strict:
                _raise_xyz_parse_error(filepath, i + 1, f"invalid atom-count line: {line!r}")
            i += 1
            continue

        try:
            num_atoms = int(line)
        except ValueError:
            if strict:
                _raise_xyz_parse_error(filepath, i + 1, f"cannot parse atom count: {line!r}")
            i += 1
            continue

        if i + 2 + num_atoms > len(lines):
            if strict:
                _raise_xyz_parse_error(
                    filepath,
                    i + 1,
                    f"incomplete frame: declared {num_atoms} atoms but file ended early",
                )
            break

        comment = lines[i + 1].strip()

        atoms = []
        coords = []
        for j in range(num_atoms):
            atom_line = lines[i + 2 + j].strip()
            parts = atom_line.split()
            if len(parts) < 4:
                if strict:
                    _raise_xyz_parse_error(
                        filepath,
                        i + 3 + j,
                        f"coordinate line has fewer than 4 columns: {atom_line!r}",
                    )
                break

            atoms.append(parts[0].upper())
            try:
                # Use last three columns as coordinates (compatible with extra columns)
                x, y, z = float(parts[-3]), float(parts[-2]), float(parts[-1])
                coords.append([x, y, z])
            except (ValueError, IndexError):
                if strict:
                    _raise_xyz_parse_error(
                        filepath,
                        i + 3 + j,
                        f"cannot parse coordinates from line: {atom_line!r}",
                    )
                break

        if len(coords) == num_atoms:
            frame = {
                "natoms": num_atoms,
                "comment": comment,
                "atoms": atoms,
                "coords": coords,
                "frame_index": frame_idx,
            }

            if parse_metadata:
                frame["metadata"] = parse_comment_metadata(comment)

            conformers.append(frame)
            frame_idx += 1
        elif strict:
            _raise_xyz_parse_error(
                filepath,
                i + 1,
                f"frame {frame_idx + 1} is malformed or truncated",
            )

        i += 2 + num_atoms

    if strict and not conformers:
        raise ValueError(f"{filepath}: no valid XYZ frames found")

    return conformers


def read_xyz_file_safe(
    filepath: str,
    parse_metadata: bool = True,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Read an XYZ file safely; return an empty list on failure and log at debug level."""
    try:
        return read_xyz_file(filepath, parse_metadata=parse_metadata, strict=strict)
    except (OSError, ValueError) as e:
        _io_logger.debug(f"read_xyz_file_safe failed for {filepath}: {e}")
        return []


def append_xyz_conformer(filepath: str, coord_lines: list[str], comment: str) -> None:
    """Append a single conformer block to an XYZ file.

    Parameters
    ----------
    filepath : str
        Target XYZ file path. Created if it does not exist.
    coord_lines : list[str]
        Coordinate lines, each formatted as ``"ATOM  x  y  z"`` strings,
        matching the format stored in ``res["final_coords"]``.
    comment : str
        Comment line (second line of the XYZ block).
    """
    natoms = len(coord_lines)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"{natoms}\n{comment}\n" + "\n".join(coord_lines) + "\n")


def write_xyz_file(filepath: str, conformers: list[dict[str, Any]], atomic: bool = True) -> None:
    """Write conformers to an XYZ file.

    Parameters
    ----------
    filepath : str
        Output file path.
    conformers : list[dict[str, Any]]
        Conformer list; each element must contain ``natoms``, ``comment``,
        ``atoms``, and ``coords``.
    atomic : bool
        Whether to use atomic write mode (write to a temporary file first,
        then rename) to prevent corruption from concurrent writes.
    """

    def _write_to_file(f):
        for conf in conformers:
            natoms = conf.get("natoms", len(conf.get("atoms", [])))
            comment = conf.get("comment", "")
            atoms = conf.get("atoms", [])
            coords = conf.get("coords", [])

            if len(atoms) != len(coords):
                raise ValueError(
                    f"Atom count / coordinate count mismatch: {len(atoms)} vs {len(coords)}"
                )

            f.write(f"{natoms}\n")
            f.write(f"{comment}\n")

            for atom, (x, y, z) in zip(atoms, coords):
                f.write(f"{atom:<2s} {x:12.8f} {y:12.8f} {z:12.8f}\n")

    if atomic:
        # Atomic write: write to a temporary file, then atomically rename
        dir_path = os.path.dirname(filepath) or "."
        os.makedirs(dir_path, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(suffix=".xyz", dir=dir_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _write_to_file(f)
            # Atomic rename
            shutil.move(tmp_path, filepath)
        except OSError as e:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            _io_logger.error(f"Failed to write XYZ file: {filepath}, reason: {e}")
            raise
        except (ValueError, RuntimeError) as e:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            _io_logger.error(f"Failed to write XYZ file: {filepath}, reason: {e}")
            raise
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            _write_to_file(f)


def coords_lines_to_array(
    coords_lines: list[str],
) -> list[tuple[str, float, float, float]] | None:
    """Convert coordinate lines to a list of (symbol, x, y, z) tuples.

    Parameters
    ----------
    coords_lines : list[str]
        Lines such as ``["H 0.0 0.0 0.0", "C 1.0 0.0 0.0"]``.

    Returns
    -------
    list[tuple[str, float, float, float]] or None
        Parsed tuples, or None on parse failure.
    """
    try:
        result = []
        for line in coords_lines:
            parts = line.split()
            if len(parts) < 4:
                return None

            symbol = parts[0]
            # Take the last three float-convertible values
            xyz = []
            for tok in reversed(parts[1:]):
                try:
                    xyz.append(float(tok))
                    if len(xyz) == 3:
                        break
                except (ValueError, TypeError):
                    continue

            if len(xyz) != 3:
                return None

            z, y, x = xyz  # reversed
            result.append((symbol, float(x), float(y), float(z)))

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


def parse_gaussian_input_text(text: str, source_label: str = "text") -> dict[str, Any]:
    """Parse Gaussian input text.

    Returns
    -------
    dict[str, Any]
        Dictionary containing:

        - ``charge``: molecular charge
        - ``multiplicity``: spin multiplicity
        - ``atoms``: list of element symbols
        - ``coords``: coordinate list ``[[x, y, z], ...]``
        - ``coords_lines``: formatted coordinate lines ``["Sym x y z", ...]``
    """
    from .data import get_element_symbol

    lines = text.splitlines()
    qm_idx = None
    charge = 0
    mult = 1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^\s*-?\d+\s+-?\d+\s*$", s):
            qm_idx = i
            parts = s.split()
            charge = int(parts[0])
            mult = int(parts[1])
            break

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
        p = raw_ln.split()
        if len(p) < 4:
            break

        raw_coords_lines.append(raw_ln)
        sym = p[0]
        if sym.isdigit():
            sym = get_element_symbol(int(sym))

        # Handle possible frozen-atom columns (take the last three numeric values)
        xyz: list[float] = []
        for tok in reversed(p[1:]):
            try:
                xyz.append(float(tok))
            except (ValueError, TypeError):
                continue
            if len(xyz) == 3:
                break

        if len(xyz) != 3:
            break

        z, y, x = xyz
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


def calculate_bond_length(coords_lines: list[str], atom1: int, atom2: int) -> float | None:
    """Calculate the distance between two atoms.

    Parameters
    ----------
    coords_lines : list[str]
        Coordinate lines.
    atom1, atom2 : int
        1-based atom indices.

    Returns
    -------
    float or None
        Bond length in Ångström, or None on parse failure.
    """
    coords_array = coords_lines_to_array(coords_lines)
    if coords_array is None:
        return None

    if atom1 < 1 or atom2 < 1 or atom1 > len(coords_array) or atom2 > len(coords_array):
        return None

    _, x1, y1, z1 = coords_array[atom1 - 1]
    _, x2, y2, z2 = coords_array[atom2 - 1]

    dx, dy, dz = x1 - x2, y1 - y2, z1 - z2
    return float((dx * dx + dy * dy + dz * dz) ** 0.5)
