#!/usr/bin/env python3

"""Executor, adapter, profile, check, and recovery descriptors.

Descriptors are immutable and carry per-contract version strings.  Version
strings fold into step semantic digests, so changing one descriptor only moves
the digests of steps that actually use it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.binding import Cardinality, Pairing, PortKind
from ..domain.canonical import typed_digest
from ..domain.errors import DomainError

__all__ = [
    "CheckSpec",
    "ENVIRONMENT_DIGEST_KIND",
    "ENVIRONMENT_DIGEST_KIND_V1",
    "ENVIRONMENT_DIGEST_KIND_V2",
    "ExecutionAdapterSpec",
    "ExecutionBinding",
    "ExecutionEnvironment",
    "ExecutorCapability",
    "ExecutorContract",
    "MEASUREMENT_STATES",
    "PortSpec",
    "RecoverySpec",
    "ResultProfileSpec",
    "allowed_port_pairings",
]


class ExecutorCapability(str, Enum):
    """Closed V4-1 step executor vocabulary."""

    CALCULATION = "calculation"
    CONFGEN = "confgen"
    STRUCTURE_TRANSFORM = "structure_transform"


_STRUCTURE_PORT_PAIRINGS = frozenset({Pairing.SINGLE, Pairing.PER_STRUCTURE, Pairing.BY_GROUP_KEY})
_VALUE_PORT_PAIRINGS = frozenset({Pairing.SINGLE, Pairing.BY_SUBJECT})


def allowed_port_pairings(kind: PortKind) -> tuple[Pairing, ...]:
    """Return the pairings a port of *kind* accepts, in ``Pairing`` order."""
    allowed = _STRUCTURE_PORT_PAIRINGS if kind is PortKind.STRUCTURE else _VALUE_PORT_PAIRINGS
    return tuple(pairing for pairing in Pairing if pairing in allowed)


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DomainError(f"{field_name} must be a non-empty, trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class PortSpec:
    """A declared input or output port of an executor contract.

    Parameters
    ----------
    name : str
        Port name, unique within the contract side.
    kind : PortKind
        Structure, artifact, or result data.
    cardinality : Cardinality
        How many values the port carries *per work item*.  ``ONE`` and
        ``ONE_OR_MORE`` imply the port is required.
    pairing : Pairing
        Matching semantics.  Structure ports allow ``single``,
        ``per_structure`` (fan-out driver), or ``by_group_key``; artifact and
        result ports allow only ``single`` or ``by_subject``, because
        positional matching is forbidden.
    roles : tuple[str, ...]
        For artifact ports: the artifact roles this port may select or emit.
        An empty tuple means role selectors are not valid for the port.
    description : str
        Human-readable description.
    """

    name: str
    kind: PortKind
    cardinality: Cardinality
    pairing: Pairing
    roles: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "port name")
        if not isinstance(self.kind, PortKind):
            raise DomainError("port kind must be a PortKind")
        if not isinstance(self.cardinality, Cardinality):
            raise DomainError("port cardinality must be a Cardinality")
        if not isinstance(self.pairing, Pairing):
            raise DomainError("port pairing must be a Pairing")
        if not isinstance(self.description, str):
            raise DomainError("port description must be a string")
        roles = tuple(self.roles)
        for index, role in enumerate(roles):
            _require_text(role, f"port roles[{index}]")
        if roles and self.kind is not PortKind.ARTIFACT:
            raise DomainError("only artifact ports may declare roles")
        if len(set(roles)) != len(roles):
            raise DomainError(f"port {self.name!r} declares duplicate roles")
        object.__setattr__(self, "roles", roles)
        if self.kind is PortKind.STRUCTURE:
            if self.pairing not in _STRUCTURE_PORT_PAIRINGS:
                raise DomainError(
                    f"structure port {self.name!r} pairing must be one of "
                    "single, per_structure, by_group_key"
                )
        elif self.pairing not in _VALUE_PORT_PAIRINGS:
            raise DomainError(
                f"{self.kind.value} port {self.name!r} pairing must be single or by_subject; "
                "positional matching is forbidden"
            )

    @property
    def is_required(self) -> bool:
        """Return whether the port demands at least one value per work item."""
        return self.cardinality in (Cardinality.ONE, Cardinality.ONE_OR_MORE)

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "cardinality": self.cardinality.value,
            "pairing": self.pairing.value,
            "roles": list(self.roles),
        }


@dataclass(frozen=True, slots=True)
class ExecutorContract:
    """Capability contract of a step executor.

    Parameters
    ----------
    capability : ExecutorCapability
        The executor capability this contract describes.
    contract_version : str
        Per-contract version string folded into semantic digests.
    input_ports : tuple[PortSpec, ...]
        Built-in input ports; empty when an execution adapter supplies them.
    output_ports : tuple[PortSpec, ...]
        Ports every step with this capability produces.
    passthrough_ports : FrozenDict | None
        Output port -> input port mapping for ports whose value is a pure
        passthrough of the input (the only ports a disabled step may forward).
    stochastic : bool
        Whether the executor performs stochastic sampling and therefore
        requires an explicit seed.
    requires_adapter : bool
        Whether steps declare an execution adapter that supplies input ports.
    description : str
        Human-readable description.
    """

    capability: ExecutorCapability
    contract_version: str
    output_ports: tuple[PortSpec, ...]
    input_ports: tuple[PortSpec, ...] = ()
    passthrough_ports: FrozenDict = field(default_factory=FrozenDict)
    stochastic: bool = False
    requires_adapter: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.capability, ExecutorCapability):
            raise DomainError("capability must be an ExecutorCapability")
        _require_text(self.contract_version, "contract_version")
        inputs = tuple(self.input_ports)
        for port in inputs:
            if not isinstance(port, PortSpec):
                raise DomainError("input_ports members must be PortSpec")
        input_names = [port.name for port in inputs]
        if len(set(input_names)) != len(input_names):
            raise DomainError(f"duplicate input port names in {self.capability.value!r}")
        object.__setattr__(self, "input_ports", inputs)
        ports = tuple(self.output_ports)
        for port in ports:
            if not isinstance(port, PortSpec):
                raise DomainError("output_ports members must be PortSpec")
        names = [port.name for port in ports]
        if len(set(names)) != len(names):
            raise DomainError(f"duplicate output port names in {self.capability.value!r}")
        object.__setattr__(self, "output_ports", ports)
        passthrough = dict(self.passthrough_ports or {})
        for out_port, in_port in passthrough.items():
            _require_text(out_port, "passthrough output port")
            _require_text(in_port, "passthrough input port")
            if out_port not in names:
                raise DomainError(
                    f"passthrough output port {out_port!r} is not declared by "
                    f"{self.capability.value!r}"
                )
        object.__setattr__(self, "passthrough_ports", FrozenDict(passthrough))
        if not isinstance(self.stochastic, bool):
            raise DomainError("stochastic must be a boolean")
        if not isinstance(self.requires_adapter, bool):
            raise DomainError("requires_adapter must be a boolean")
        if self.requires_adapter and inputs:
            raise DomainError(
                f"{self.capability.value!r} requires an adapter and must not "
                "declare built-in input ports"
            )

    def input_port(self, name: str) -> PortSpec | None:
        """Return the declared input port *name*, or ``None``."""
        for port in self.input_ports:
            if port.name == name:
                return port
        return None

    def output_port(self, name: str) -> PortSpec | None:
        """Return the declared output port *name*, or ``None``."""
        for port in self.output_ports:
            if port.name == name:
                return port
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "capability": self.capability.value,
            "contract_version": self.contract_version,
            "output_ports": [port.to_dict() for port in self.output_ports],
            "passthrough_ports": dict(self.passthrough_ports),
            "stochastic": self.stochastic,
            "requires_adapter": self.requires_adapter,
        }


@dataclass(frozen=True, slots=True)
class ExecutionAdapterSpec:
    """Input-port contract of an execution adapter.

    Adapters describe *how* a step's inputs are supplied to a native program:
    a single structure (``standard``) or a native input template
    (``native_template``).
    """

    name: str
    contract_version: str
    capability: ExecutorCapability
    input_ports: tuple[PortSpec, ...]
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "adapter name")
        _require_text(self.contract_version, "adapter contract_version")
        if not isinstance(self.capability, ExecutorCapability):
            raise DomainError("adapter capability must be an ExecutorCapability")
        ports = tuple(self.input_ports)
        for port in ports:
            if not isinstance(port, PortSpec):
                raise DomainError("input_ports members must be PortSpec")
        names = [port.name for port in ports]
        if len(set(names)) != len(names):
            raise DomainError(f"duplicate input port names in adapter {self.name!r}")
        object.__setattr__(self, "input_ports", ports)
        if not isinstance(self.description, str):
            raise DomainError("adapter description must be a string")

    def input_port(self, name: str) -> PortSpec | None:
        """Return the declared input port *name*, or ``None``."""
        for port in self.input_ports:
            if port.name == name:
                return port
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "name": self.name,
            "contract_version": self.contract_version,
            "capability": self.capability.value,
            "input_ports": [port.to_dict() for port in self.input_ports],
        }


@dataclass(frozen=True, slots=True)
class ResultProfileSpec:
    """What a result profile extracts from native output."""

    name: str
    contract_version: str
    supported_checks: tuple[str, ...]
    provides_structures: bool = False
    provides_results: bool = False
    provides_artifacts: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "result profile name")
        _require_text(self.contract_version, "result profile contract_version")
        checks = tuple(self.supported_checks)
        for index, check in enumerate(checks):
            _require_text(check, f"supported_checks[{index}]")
        object.__setattr__(self, "supported_checks", checks)
        for flag in ("provides_structures", "provides_results", "provides_artifacts"):
            if not isinstance(getattr(self, flag), bool):
                raise DomainError(f"{flag} must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "name": self.name,
            "contract_version": self.contract_version,
            "supported_checks": list(self.supported_checks),
            "provides_structures": self.provides_structures,
            "provides_results": self.provides_results,
            "provides_artifacts": self.provides_artifacts,
        }


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """A declarable scientific acceptance check."""

    name: str
    contract_version: str
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "check name")
        _require_text(self.contract_version, "check contract_version")
        if not isinstance(self.description, str):
            raise DomainError("check description must be a string")


@dataclass(frozen=True, slots=True)
class RecoverySpec:
    """A declarable recovery profile.

    Recovery is only ever triggered when a step explicitly declares it; no
    code path may infer recovery from a step role or task name.
    """

    name: str
    contract_version: str
    supported_capabilities: tuple[ExecutorCapability, ...]
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "recovery name")
        _require_text(self.contract_version, "recovery contract_version")
        capabilities = tuple(self.supported_capabilities)
        for capability in capabilities:
            if not isinstance(capability, ExecutorCapability):
                raise DomainError("recovery capabilities must be ExecutorCapability members")
        object.__setattr__(self, "supported_capabilities", capabilities)
        if not isinstance(self.description, str):
            raise DomainError("recovery description must be a string")


@dataclass(frozen=True, slots=True)
class ExecutionBinding:
    """Machine-specific execution settings for a step.

    Execution bindings are deliberately excluded from every scientific
    digest: changing the executable path, environment, sandbox, or walltime
    must never invalidate a computed result's reuse identity.
    """

    binding_id: str
    executable: str | None = None
    env: FrozenDict = field(default_factory=FrozenDict)
    sandbox: str | None = None
    allowed_executables: tuple[str, ...] = ()
    walltime_seconds: int | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_text(self.binding_id, "binding_id")
        for name in ("executable", "sandbox"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        executables = tuple(self.allowed_executables)
        for index, executable in enumerate(executables):
            _require_text(executable, f"allowed_executables[{index}]")
        object.__setattr__(self, "allowed_executables", executables)
        if self.walltime_seconds is not None:
            if isinstance(self.walltime_seconds, bool) or not isinstance(
                self.walltime_seconds, int
            ):
                raise DomainError("walltime_seconds must be an integer or None")
            if self.walltime_seconds <= 0:
                raise DomainError("walltime_seconds must be > 0")
        if not isinstance(self.env, FrozenDict):
            object.__setattr__(self, "env", FrozenDict(self.env))
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "binding_id": self.binding_id,
            "executable": self.executable,
            "env": self.env.thaw(),
            "sandbox": self.sandbox,
            "allowed_executables": list(self.allowed_executables),
            "walltime_seconds": self.walltime_seconds,
            "metadata": self.metadata.thaw(),
        }


#: Digest domain marker for :meth:`ExecutionEnvironment.digest` (identity v3).
#:
#: v3 is the versioned environment-identity rule: full executable content
#: identity plus the COMPLETE effective native environment the subprocess
#: receives — the producer's explicit inheritance policy (``os.environ``
#: ambient inheritance) overlaid with the declared binding env, or the
#: target-side equivalent (target defaults under the producer handoff
#: snapshot). There is exactly one mapping, used for both the subprocess
#: launch and the digest: an inherited variable the executable can read is
#: always part of identity, so changing or deleting it invalidates reuse.
#: Endpoint target locators, absolute paths, file stat (size/mtime), and
#: scheduler width remain operational provenance and never enter the digest.
#: v2 digests (declared-subset env with uninventoried ambient inheritance)
#: never equal v3 digests, so pre-fix generations fail closed at reuse
#: comparison instead of aliasing stale science.
ENVIRONMENT_DIGEST_KIND: Final[str] = "confflow.execution_environment.v3"

#: Superseded v2 marker (declared-subset env), retained so old digests are
#: recognizable (and unequal).
ENVIRONMENT_DIGEST_KIND_V2: Final[str] = "confflow.execution_environment.v2"

#: Superseded v1 marker, retained so old digests are recognizable (and unequal).
ENVIRONMENT_DIGEST_KIND_V1: Final[str] = "confflow.execution_environment.v1"

#: Allowed measurement states for :attr:`ExecutionEnvironment.measurement_status`.
MEASUREMENT_STATES: Final[frozenset[str]] = frozenset({"verified", "unknown"})


@dataclass(frozen=True, slots=True)
class ExecutionEnvironment:
    """Measured identity of the runtime environment.

    This is a separate digest axis from workflow, step, and work-item
    identity: it describes *where* a computation ran, not what was computed.

    Identity rule v3 (see ``ENVIRONMENT_DIGEST_KIND``):

    - IN: ``program``, ``program_version``, ``executable_digest`` (full
      content hash for native launches; ``None`` for pure executors), and
      ``relevant_env`` — the COMPLETE effective native environment the
      subprocess receives (producer ambient inheritance policy + declared
      binding env, or the target-side equivalent). There is one mapping:
      the launched environment and the digested environment are identical
      by construction, so no readable variable can change results without
      moving identity.
    - OUT: ``target`` (endpoint locator), absolute executable paths,
      file stat, scheduler width, GUI/presentation. They travel in
      ``metadata``/fields for audit, never in the digest.
    - ``unknown`` measurements (``measurement_status="unknown"``) carry a
      fresh ``unknown_nonce`` folded into the digest, so an unmeasured
      identity NEVER equals a verified one — or another unknown one.
      Unknown cannot silently stand in for verified equivalence; reuse
      comparison fails closed on inequality with no consumer changes.
    """

    program: str
    program_version: str | None = None
    executable_digest: str | None = None
    relevant_env: FrozenDict = field(default_factory=FrozenDict)
    target: str | None = None
    measurement_status: str = "verified"
    unknown_nonce: str | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_text(self.program, "program")
        for name in ("program_version", "executable_digest", "target"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        relevant = self.relevant_env
        if not isinstance(relevant, FrozenDict):
            if isinstance(relevant, Mapping):
                object.__setattr__(self, "relevant_env", FrozenDict(relevant))
                relevant = self.relevant_env
            else:
                raise DomainError("relevant_env must be a mapping")
        for key, value in relevant.items():
            if not isinstance(key, str) or not key or key != key.strip():
                raise DomainError("relevant_env keys must be non-empty, trimmed strings")
            if not isinstance(value, str):
                raise DomainError("relevant_env values must be strings")
        if self.measurement_status not in MEASUREMENT_STATES:
            raise DomainError(f"measurement_status must be one of {sorted(MEASUREMENT_STATES)}")
        if self.measurement_status == "unknown":
            if self.unknown_nonce is None:
                object.__setattr__(self, "unknown_nonce", uuid.uuid4().hex)
            elif (
                not isinstance(self.unknown_nonce, str)
                or not self.unknown_nonce
                or self.unknown_nonce != self.unknown_nonce.strip()
            ):
                raise DomainError("unknown_nonce must be a non-empty, trimmed string")
        elif self.unknown_nonce is not None:
            raise DomainError("a verified environment must not carry an unknown_nonce")
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    @classmethod
    def unknown(cls, program: str, *, reason: str) -> ExecutionEnvironment:
        """Build a fail-closed unmeasured environment for *program*.

        The returned digest is unique per construction (fresh nonce), so it
        can never satisfy a reuse equality check. *reason* is recorded in
        metadata for audit.
        """
        _require_text(program, "program")
        _require_text(reason, "reason")
        return cls(
            program=program,
            measurement_status="unknown",
            metadata=FrozenDict({"unknown_reason": reason}),
        )

    @property
    def is_verified(self) -> bool:
        """Return whether this environment was actually measured."""
        return self.measurement_status == "verified"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionEnvironment:
        """Rebuild a verified environment from its serialized form.

        The embedded digest must recompute exactly and the measurement
        must be verified: an unmeasured or tampered environment fails
        closed here, never at reuse comparison.
        """
        if not isinstance(payload, Mapping):
            raise DomainError("execution environment payload must be a mapping")
        data = dict(payload)
        claimed = data.pop("digest", None)
        try:
            rebuilt = cls(
                program=data.get("program"),
                program_version=data.get("program_version"),
                executable_digest=data.get("executable_digest"),
                relevant_env=data.get("relevant_env") or {},
                target=data.get("target"),
                measurement_status=data.get("measurement_status", "verified"),
                unknown_nonce=data.get("unknown_nonce"),
                metadata=data.get("metadata") or {},
            )
        except (TypeError, DomainError) as exc:
            raise DomainError(f"execution environment payload is invalid: {exc}") from exc
        if not rebuilt.is_verified:
            raise DomainError(
                "execution environment payload is not a verified measurement; "
                "unmeasured identity never stands in for execution truth"
            )
        if claimed is not None and claimed != rebuilt.digest():
            raise DomainError(
                "execution environment digest does not recompute; refusing tampered identity"
            )
        return rebuilt

    def digest(self) -> str:
        """Return the execution-environment digest (identity rule v2)."""
        if self.measurement_status == "unknown":
            return typed_digest(
                ENVIRONMENT_DIGEST_KIND,
                {
                    "unknown": True,
                    "program": self.program,
                    "nonce": self.unknown_nonce,
                },
            )
        return typed_digest(
            ENVIRONMENT_DIGEST_KIND,
            {
                "program": self.program,
                "program_version": self.program_version,
                "executable_digest": self.executable_digest,
                "relevant_env": self.relevant_env.thaw(),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "program": self.program,
            "program_version": self.program_version,
            "executable_digest": self.executable_digest,
            "relevant_env": self.relevant_env.thaw(),
            "target": self.target,
            "measurement_status": self.measurement_status,
            "unknown_nonce": self.unknown_nonce,
            "metadata": self.metadata.thaw(),
            "digest": self.digest(),
        }
