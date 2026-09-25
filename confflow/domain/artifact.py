#!/usr/bin/env python3

"""V4 artifact reference model.

An artifact is scientific content produced outside the process memory: a
checkpoint file, a native output, a trajectory.  Its *identity* is
``ArtifactRef.id`` plus its producer provenance; its *locator* says where the
bytes live.  File paths are never identity, and absolute machine paths are
never durable: the canonical locator kind is run-root-relative.

Structure-bound artifacts (for example a Gaussian checkpoint) carry
``subject_structure_id`` so downstream inputs can be matched by subject
identity instead of by list order or filename.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, overload

from ._immutable import FrozenDict
from .errors import InvalidArtifactError
from .retention import RetentionClass

__all__ = [
    "ArtifactLocator",
    "ArtifactRef",
    "ArtifactSet",
    "LocatorKind",
]

_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:[^\s]+$")
_CHECKSUM_PATTERN = re.compile(
    r"^(?P<algo>[a-z0-9][a-z0-9\-]*):(?P<hex>[0-9a-f]{32,})$", re.IGNORECASE
)


class LocatorKind(str, Enum):
    """Kinds of artifact locators.

    Attributes
    ----------
    RUN_RELATIVE
        POSIX path relative to the run root; the durable canonical kind.
    EXTERNAL_URI
        External URI for imported artifacts; never a machine-local path.
    """

    RUN_RELATIVE = "run_relative"
    EXTERNAL_URI = "external_uri"


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidArtifactError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise InvalidArtifactError(f"{field_name} must not have surrounding whitespace")
    return value


def _validate_relative_path(path: Any) -> str:
    if not isinstance(path, str) or not path:
        raise InvalidArtifactError("artifact locator path must be a non-empty string")
    if path != path.strip():
        raise InvalidArtifactError("artifact locator path must not have surrounding whitespace")
    if "\\" in path:
        raise InvalidArtifactError(
            "artifact locator path must use POSIX separators and be portable"
        )
    if "\x00" in path:
        raise InvalidArtifactError("artifact locator path must not contain NUL")
    if path.startswith("/"):
        raise InvalidArtifactError("artifact locator path must be run-root-relative")
    if ":" in path.split("/")[0]:
        raise InvalidArtifactError("artifact locator path must not contain a drive prefix")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise InvalidArtifactError(
            "artifact locator path must not contain empty, '.' or '..' segments"
        )
    return path


def _validate_uri(uri: Any) -> str:
    if not isinstance(uri, str) or not uri:
        raise InvalidArtifactError("artifact locator uri must be a non-empty string")
    if not _URI_PATTERN.match(uri):
        raise InvalidArtifactError(f"artifact locator uri must be an absolute URI: {uri!r}")
    return uri


@dataclass(frozen=True, slots=True)
class ArtifactLocator:
    """A typed, portable locator for artifact bytes.

    Parameters
    ----------
    kind : LocatorKind
        Locator kind; decides which target field is meaningful.
    path : str | None
        Run-root-relative POSIX path for ``RUN_RELATIVE``.
    uri : str | None
        Absolute URI for ``EXTERNAL_URI``.

    Raises
    ------
    InvalidArtifactError
        Raised when the target field does not match the locator kind.
    """

    kind: LocatorKind
    path: str | None = None
    uri: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LocatorKind):
            raise InvalidArtifactError("locator kind must be a LocatorKind")
        if self.kind is LocatorKind.RUN_RELATIVE:
            if self.uri is not None:
                raise InvalidArtifactError("run-relative locator must not carry a uri")
            _validate_relative_path(self.path)
        elif self.kind is LocatorKind.EXTERNAL_URI:
            if self.path is not None:
                raise InvalidArtifactError("external-uri locator must not carry a path")
            _validate_uri(self.uri)

    @classmethod
    def run_relative(cls, path: str) -> ArtifactLocator:
        """Build a run-root-relative locator."""
        return cls(LocatorKind.RUN_RELATIVE, path=path)

    @classmethod
    def external_uri(cls, uri: str) -> ArtifactLocator:
        """Build an external-URI locator."""
        return cls(LocatorKind.EXTERNAL_URI, uri=uri)

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {"kind": self.kind.value, "path": self.path, "uri": self.uri}


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """An immutable reference to a produced artifact.

    Parameters
    ----------
    id : str
        Artifact identity within the run; not derived from the locator.
    role : str
        Scientific role such as ``"checkpoint"``; the selector vocabulary.
    locator : ArtifactLocator
        Typed durable locator; paths are never identity.
    checksum : str | None
        Content checksum in ``<algo>:<hex>`` form, when verified.
    media_type : str | None
        Media type of the bytes, when known.
    program : str | None
        Producing program such as ``"gaussian"``, when applicable.
    producer_step_id : str | None
        Step that produced the artifact.
    producer_work_item_id : str | None
        Work item that produced the artifact.
    subject_structure_id : str | None
        Structure this artifact is bound to, for subject matching.
    retention : RetentionClass
        Declared retention intent; conservative default is ``RETAINED``.
    metadata : FrozenDict
        Non-semantic annotations.

    Raises
    ------
    InvalidArtifactError
        Raised when any artifact invariant is violated.
    """

    id: str
    role: str
    locator: ArtifactLocator
    checksum: str | None = None
    media_type: str | None = None
    program: str | None = None
    producer_step_id: str | None = None
    producer_work_item_id: str | None = None
    subject_structure_id: str | None = None
    retention: RetentionClass = RetentionClass.RETAINED
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.id, "id")
        _require_identifier(self.role, "role")
        if not isinstance(self.locator, ArtifactLocator):
            raise InvalidArtifactError("locator must be an ArtifactLocator")
        if self.checksum is not None:
            match = _CHECKSUM_PATTERN.match(str(self.checksum))
            if match is None:
                raise InvalidArtifactError(
                    "checksum must have the form <algo>:<hex> with at least 32 hex digits"
                )
            object.__setattr__(
                self,
                "checksum",
                f"{match.group('algo').lower()}:{match.group('hex').lower()}",
            )
        for name in (
            "media_type",
            "program",
            "producer_step_id",
            "producer_work_item_id",
            "subject_structure_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if not isinstance(self.retention, RetentionClass):
            raise InvalidArtifactError("retention must be a RetentionClass")
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    @property
    def is_content_verified(self) -> bool:
        """Return whether a content checksum is recorded."""
        return self.checksum is not None

    def digest_payload(self) -> dict[str, Any]:
        """Return this artifact's contribution to a work-item digest.

        The locator is deliberately excluded: moving identical bytes must not
        invalidate reuse.  Content identity is the checksum; when a checksum
        is not yet available, the artifact id and role still pin the reference.
        """
        return {
            "id": self.id,
            "role": self.role,
            "checksum": self.checksum,
            "subject_structure_id": self.subject_structure_id,
        }

    def has_same_content(self, other: ArtifactRef) -> bool:
        """Return whether *other* refers to the same verified bytes."""
        return (
            isinstance(other, ArtifactRef)
            and self.checksum is not None
            and self.checksum == other.checksum
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "id": self.id,
            "role": self.role,
            "locator": self.locator.to_dict(),
            "checksum": self.checksum,
            "media_type": self.media_type,
            "program": self.program,
            "producer_step_id": self.producer_step_id,
            "producer_work_item_id": self.producer_work_item_id,
            "subject_structure_id": self.subject_structure_id,
            "retention": self.retention.value,
            "metadata": self.metadata.thaw(),
        }


@dataclass(frozen=True, slots=True)
class ArtifactSet:
    """An ordered, immutable collection of artifact references."""

    artifacts: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.artifacts)
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, ArtifactRef):
                raise InvalidArtifactError(
                    f"ArtifactSet members must be ArtifactRef, got {type(record).__name__}"
                )
            if record.id in seen:
                raise InvalidArtifactError(f"duplicate artifact id in set: {record.id!r}")
            seen.add(record.id)
        object.__setattr__(self, "artifacts", records)

    def __iter__(self) -> Iterator[ArtifactRef]:
        return iter(self.artifacts)

    def __len__(self) -> int:
        return len(self.artifacts)

    @overload
    def __getitem__(self, index: int) -> ArtifactRef: ...

    @overload
    def __getitem__(self, index: slice) -> ArtifactSet: ...

    def __getitem__(self, index: int | slice) -> ArtifactRef | ArtifactSet:
        if isinstance(index, slice):
            return ArtifactSet(self.artifacts[index])
        return self.artifacts[index]

    @property
    def ids(self) -> tuple[str, ...]:
        """Return artifact ids in set order."""
        return tuple(record.id for record in self.artifacts)

    @property
    def is_empty(self) -> bool:
        """Return whether the set contains no artifacts."""
        return not self.artifacts

    @classmethod
    def of(cls, *artifacts: ArtifactRef) -> ArtifactSet:
        """Build a set from individual references."""
        return cls(tuple(artifacts))

    def by_id(self, artifact_id: str) -> ArtifactRef:
        """Return the artifact with *artifact_id*.

        Raises
        ------
        KeyError
            Raised when the id is not present in this set.
        """
        for record in self.artifacts:
            if record.id == artifact_id:
                return record
        raise KeyError(artifact_id)

    def get(self, artifact_id: str) -> ArtifactRef | None:
        """Return the artifact with *artifact_id*, or ``None``."""
        for record in self.artifacts:
            if record.id == artifact_id:
                return record
        return None

    def by_role(self, role: str) -> ArtifactSet:
        """Return the sub-set of artifacts whose role equals *role*."""
        return ArtifactSet(tuple(record for record in self.artifacts if record.role == role))

    def by_subject(self, subject_structure_id: str) -> ArtifactSet:
        """Return the sub-set of artifacts bound to *subject_structure_id*."""
        return ArtifactSet(
            tuple(
                record
                for record in self.artifacts
                if record.subject_structure_id == subject_structure_id
            )
        )

    def by_producer(self, producer_step_id: str) -> ArtifactSet:
        """Return the sub-set of artifacts produced by *producer_step_id*."""
        return ArtifactSet(
            tuple(
                record for record in self.artifacts if record.producer_step_id == producer_step_id
            )
        )

    def __add__(self, other: ArtifactSet) -> ArtifactSet:
        if not isinstance(other, ArtifactSet):
            raise TypeError(f"cannot add {type(other).__name__} to ArtifactSet")
        return ArtifactSet(self.artifacts + other.artifacts)
