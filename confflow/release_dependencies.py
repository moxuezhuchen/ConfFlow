"""Fail-closed validation for the ConfFlow runtime lock and wheelhouse.

The release installer deliberately keeps dependency resolution separate from
the exact ConfFlow wheel install. This module validates the two offline
inputs before a staging venv is created, so a candidate or production install
cannot silently fall back to an index, an sdist, or host site-packages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .install_provenance import sha256_hex

WHEELHOUSE_MANIFEST_NAME = "SHA256SUMS"
_LOCK_DIRECTIVE = "--only-binary=:all:"
_LOCK_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[^\s]+)"
    r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-fA-F]{64})+)$"
)
try:
    from pip._vendor.packaging.utils import (
        canonicalize_name as _packaging_canonicalize_name,
    )
    from pip._vendor.packaging.utils import (
        parse_wheel_filename as _packaging_parse_wheel_filename,
    )
except ImportError:  # pragma: no cover - pip is present in every supported venv
    _packaging_canonicalize_name = None
    _packaging_parse_wheel_filename = None


class DependencyInputError(ValueError):
    """Raised when the lock or wheelhouse is incomplete or inconsistent."""


@dataclass(frozen=True)
class LockedDependency:
    """One exact distribution entry from the runtime lock."""

    name: str
    version: str
    hashes: tuple[str, ...]


@dataclass(frozen=True)
class DependencyEvidence:
    """Digests and selected wheel names recorded in install provenance."""

    dependency_lock_sha256: str
    wheelhouse_manifest_sha256: str
    wheel_filenames: tuple[str, ...]


def _canonicalize_name(name: str) -> str:
    if _packaging_canonicalize_name is not None:
        return str(_packaging_canonicalize_name(name))
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_runtime_identity(identity: dict[str, object]) -> None:
    """Require the release lock's CPython 3.12 Linux x86_64 target."""
    version = str(identity.get("python_version", ""))
    platform = str(identity.get("platform", "")).lower().replace("_", "-")
    machine = str(identity.get("machine", "")).lower()
    version_parts = version.split(".")
    if version_parts[:2] != ["3", "12"]:
        raise DependencyInputError(
            f"runtime lock requires Python 3.12; got {version or '<unknown>'}"
        )
    if platform != "linux-x86-64" or machine != "x86_64":
        raise DependencyInputError(
            "runtime lock requires Linux x86_64; "
            f"got platform={platform or '<unknown>'} machine={machine or '<unknown>'}"
        )


def parse_dependency_lock(path: Path) -> dict[str, LockedDependency]:
    """Parse the intentionally narrow, generated requirements lock format."""
    if not path.is_file():
        raise DependencyInputError(f"dependency lock does not exist: {path}")
    result: dict[str, LockedDependency] = {}
    directive_seen = False
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == _LOCK_DIRECTIVE:
            if directive_seen:
                raise DependencyInputError(
                    f"dependency lock repeats {_LOCK_DIRECTIVE!r} at line {line_number}"
                )
            directive_seen = True
            continue
        match = _LOCK_RE.fullmatch(line)
        if match is None:
            raise DependencyInputError(
                "dependency lock contains a non-exact or unhashed entry "
                f"at line {line_number}"
            )
        name = _canonicalize_name(match.group("name"))
        if name in result:
            raise DependencyInputError(
                f"dependency lock contains duplicate package {name!r}"
            )
        hashes = tuple(
            token.rsplit(":", 1)[1].lower()
            for token in match.group("hashes").split()
        )
        result[name] = LockedDependency(
            name=name,
            version=match.group("version"),
            hashes=hashes,
        )
    if not directive_seen:
        raise DependencyInputError(
            f"dependency lock must contain {_LOCK_DIRECTIVE!r}"
        )
    return result


def parse_wheelhouse_manifest(path: Path) -> dict[str, str]:
    """Parse a strict two-column SHA256SUMS manifest for binary wheels."""
    if not path.is_file():
        raise DependencyInputError(f"wheelhouse manifest does not exist: {path}")
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise DependencyInputError(
                f"wheelhouse manifest has malformed line {line_number}"
            )
        digest, filename = parts
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in digest)
        ):
            raise DependencyInputError(
                f"wheelhouse manifest has invalid SHA256 at line {line_number}"
            )
        if (
            not filename.endswith(".whl")
            or filename in {WHEELHOUSE_MANIFEST_NAME, ".", ".."}
            or "/" in filename
            or "\\" in filename
            or "*" in filename
        ):
            raise DependencyInputError(
                f"wheelhouse manifest has an invalid wheel filename at line {line_number}"
            )
        if filename in result:
            raise DependencyInputError(
                f"wheelhouse manifest contains duplicate filename {filename!r}"
            )
        result[filename] = digest.lower()
    return result


def _wheel_metadata(filename: str) -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    """Return canonical name, version, and wheel tags without executing code."""
    if _packaging_parse_wheel_filename is not None:
        try:
            name, version, _build, tags = _packaging_parse_wheel_filename(filename)
        except Exception as exc:
            raise DependencyInputError(
                f"wheelhouse contains an invalid wheel filename {filename!r}"
            ) from exc
        normalized_tags = tuple(
            (str(tag.interpreter), str(tag.abi), str(tag.platform)) for tag in tags
        )
        return _canonicalize_name(str(name)), str(version), normalized_tags
    # The fallback is only for a Python environment without pip's vendored
    # packaging module. Supported release wheel names do not use a build tag.
    parts = filename[:-4].split("-")
    if len(parts) != 5:
        raise DependencyInputError(
            f"wheelhouse contains an unparseable wheel filename {filename!r}"
        )
    return (
        _canonicalize_name(parts[0]),
        parts[1],
        ((parts[2], parts[3], parts[4]),),
    )


def _tag_is_compatible(
    tag: tuple[str, str, str],
) -> bool:
    interpreter, abi, platform = tag
    interpreter_ok = interpreter in {"py3", "py312", "cp312"}
    abi_ok = abi in {"none", "cp312", "abi3"}
    if abi == "abi3" and interpreter.startswith("cp"):
        suffix = interpreter[2:]
        interpreter_ok = suffix.isdigit() and int(suffix) <= 312
    if not interpreter_ok or not abi_ok:
        return False
    if platform == "any":
        return True
    return (
        platform == "linux_x86_64"
        or platform.endswith("_x86_64")
        and platform.startswith("manylinux")
    )


def validate_dependency_inputs(
    dependency_lock: Path,
    wheelhouse: Path,
    *,
    runtime_identity: dict[str, object],
) -> DependencyEvidence:
    """Validate the full offline dependency closure and return its digests."""
    validate_runtime_identity(runtime_identity)
    if not wheelhouse.is_dir():
        raise DependencyInputError(
            f"wheelhouse does not exist or is not a directory: {wheelhouse}"
        )
    manifest_path = wheelhouse / WHEELHOUSE_MANIFEST_NAME
    locked = parse_dependency_lock(dependency_lock)
    manifest = parse_wheelhouse_manifest(manifest_path)
    actual_wheels: dict[str, Path] = {}
    for entry in sorted(wheelhouse.iterdir()):
        if entry.name == WHEELHOUSE_MANIFEST_NAME:
            continue
        if entry.is_symlink() or not entry.is_file():
            raise DependencyInputError(
                f"wheelhouse contains a non-wheel entry: {entry.name!r}"
            )
        if not entry.name.endswith(".whl"):
            raise DependencyInputError(
                f"wheelhouse contains a non-binary-wheel entry: {entry.name!r}"
            )
        actual_wheels[entry.name] = entry
    manifest_names = set(manifest)
    actual_names = set(actual_wheels)
    missing_manifest_rows = sorted(actual_names - manifest_names)
    missing_wheels = sorted(manifest_names - actual_names)
    if missing_manifest_rows:
        raise DependencyInputError(
            "wheelhouse has unmanifested wheel(s): " + ", ".join(missing_manifest_rows)
        )
    if missing_wheels:
        raise DependencyInputError(
            "wheelhouse manifest refers to missing wheel(s): " + ", ".join(missing_wheels)
        )
    seen_packages: set[str] = set()
    for filename in sorted(actual_wheels):
        wheel_path = actual_wheels[filename]
        actual_digest = sha256_hex(wheel_path)
        manifest_digest = manifest[filename]
        if actual_digest != manifest_digest:
            raise DependencyInputError(
                f"wheelhouse SHA256 mismatch for {filename}: "
                f"file={actual_digest} manifest={manifest_digest}"
            )
        package_name, version, tags = _wheel_metadata(filename)
        if package_name not in locked:
            raise DependencyInputError(
                f"wheelhouse contains an extra dependency wheel: {filename}"
            )
        if package_name in seen_packages:
            raise DependencyInputError(
                f"wheelhouse contains multiple wheels for locked package {package_name!r}"
            )
        seen_packages.add(package_name)
        expected = locked[package_name]
        if version != expected.version:
            raise DependencyInputError(
                f"wheel version mismatch for {filename}: "
                f"lock={expected.version} file={version}"
            )
        if manifest_digest not in expected.hashes:
            raise DependencyInputError(
                f"wheel hash is not present in dependency lock for {filename}"
            )
        if not any(_tag_is_compatible(tag) for tag in tags):
            raise DependencyInputError(
                f"wheel is not compatible with Python 3.12 Linux x86_64: {filename}"
            )
    missing_packages = sorted(set(locked) - seen_packages)
    if missing_packages:
        raise DependencyInputError(
            "wheelhouse is missing locked dependency wheel(s): "
            + ", ".join(missing_packages)
        )
    return DependencyEvidence(
        dependency_lock_sha256=sha256_hex(dependency_lock),
        wheelhouse_manifest_sha256=sha256_hex(manifest_path),
        wheel_filenames=tuple(sorted(actual_wheels)),
    )


__all__ = [
    "DependencyEvidence",
    "DependencyInputError",
    "LockedDependency",
    "WHEELHOUSE_MANIFEST_NAME",
    "parse_dependency_lock",
    "parse_wheelhouse_manifest",
    "validate_dependency_inputs",
    "validate_runtime_identity",
]
