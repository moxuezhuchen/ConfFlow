#!/usr/bin/env python3

"""
ConfFlow XYZ I/O - unified XYZ file read/write module.

Consolidates XYZ handling logic previously scattered across calc.py,
refine.py, viz.py, and utils.py.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Any

from .gaussian_input import (
    calculate_bond_length,
    coords_lines_to_array,
    parse_gaussian_input,
    parse_gaussian_input_text,
)
from .xyz_metadata import (
    ensure_conformer_cids,
    parse_comment_metadata,
    upsert_comment_kv,
    xyz_needs_cid_rewrite,
)

__all__ = [
    "upsert_comment_kv",
    "ensure_conformer_cids",
    "ensure_xyz_cids",
    "parse_comment_metadata",
    "iter_xyz_frames",
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


def ensure_xyz_cids(xyz_path: str, prefix: str = "A") -> None:
    """Read an XYZ file and ensure all conformers have CIDs; re-write if incomplete."""
    if not os.path.exists(xyz_path):
        return
    try:
        confs = read_xyz_file(xyz_path, parse_metadata=True)
        if confs and xyz_needs_cid_rewrite(confs):
            ensure_conformer_cids(confs, prefix=prefix)
            write_xyz_file(xyz_path, confs, atomic=True)
    except (OSError, ValueError, IndexError) as e:
        _io_logger.debug(f"ensure_xyz_cids: non-fatal error ({xyz_path}): {e}")


def _raise_xyz_parse_error(filepath: str, line_num: int, message: str) -> None:
    """Raise a detailed XYZ parse error with file/line context."""
    raise ValueError(f"{filepath}: line {line_num}: {message}")


def iter_xyz_frames(
    filepath: str,
    *,
    parse_metadata: bool = True,
    strict: bool = False,
):
    """Yield XYZ frames one at a time without reading the whole file into memory."""
    try:
        handle = open(filepath, encoding="utf-8")
    except OSError as e:
        raise OSError(f"Cannot read XYZ file {filepath}: {e}") from e

    frame_idx = 0
    line_num = 0

    try:
        while True:
            header = handle.readline()
            if not header:
                break
            line_num += 1
            line = header.strip()
            if not line:
                continue
            if not line.isdigit():
                if strict:
                    _raise_xyz_parse_error(filepath, line_num, f"invalid atom-count line: {line!r}")
                continue

            try:
                num_atoms = int(line)
            except ValueError:
                if strict:
                    _raise_xyz_parse_error(filepath, line_num, f"cannot parse atom count: {line!r}")
                continue

            comment_line = handle.readline()
            if not comment_line:
                if strict:
                    _raise_xyz_parse_error(filepath, line_num + 1, "missing comment line")
                break
            line_num += 1
            comment = comment_line.strip()

            atoms: list[str] = []
            coords: list[list[float]] = []
            malformed = False
            for _atom_offset in range(num_atoms):
                atom_line = handle.readline()
                if not atom_line:
                    if strict:
                        _raise_xyz_parse_error(
                            filepath,
                            line_num + 1,
                            f"incomplete frame: declared {num_atoms} atoms but file ended early",
                        )
                    malformed = True
                    break
                line_num += 1
                raw = atom_line.strip()
                parts = raw.split()
                if len(parts) < 4:
                    if strict:
                        _raise_xyz_parse_error(
                            filepath,
                            line_num,
                            f"coordinate line has fewer than 4 columns: {raw!r}",
                        )
                    malformed = True
                    break

                atoms.append(parts[0].upper())
                try:
                    x, y, z = float(parts[-3]), float(parts[-2]), float(parts[-1])
                except (ValueError, IndexError):
                    if strict:
                        _raise_xyz_parse_error(
                            filepath,
                            line_num,
                            f"cannot parse coordinates from line: {raw!r}",
                        )
                    malformed = True
                    break
                coords.append([x, y, z])

            if malformed:
                continue

            frame = {
                "natoms": num_atoms,
                "comment": comment,
                "atoms": atoms,
                "coords": coords,
                "frame_index": frame_idx,
            }
            if parse_metadata:
                frame["metadata"] = parse_comment_metadata(comment)
            frame_idx += 1
            yield frame
    finally:
        handle.close()


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
    conformers = list(iter_xyz_frames(filepath, parse_metadata=parse_metadata, strict=strict))

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
