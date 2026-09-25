#!/usr/bin/env python3

"""V4 native execution contracts.

This module is the shared authority for everything that crosses the native
process boundary.  It defines facts and requests, never workflow topology:

- :class:`ProgramName` is a scientific vocabulary (which program's file
  format), not a runtime dispatch enum.  Role/task names never appear here.
- :class:`NativeResult` reports parser facts.  Whether a missing geometry
  means "passthrough" or "failure" is decided by the result profile from
  declared checks, never inferred from a task name.
- :class:`ProgramAdapter` understands file formats.  It never sees the
  workflow graph, labels, YAML, or legacy config shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic
from ..domain.resources import ResourceRequest
from ..domain.structure import StructureRecord

__all__ = [
    "CancelOutcome",
    "GeometryOutput",
    "InputFile",
    "MaterializedNativeInput",
    "NativeError",
    "NativeErrorCode",
    "NativeExecutionRequest",
    "NativeExecutionResult",
    "NativeHandle",
    "NativeResult",
    "NativeStatus",
    "ParsedGeometry",
    "ProcessSupervisor",
    "ProducedFile",
    "ProgramAdapter",
    "ProgramName",
    "ResolvedCalculationInputs",
    "StagedArtifact",
]


class ProgramName(str, Enum):
    """Scientific program vocabulary for file-format dispatch."""

    GAUSSIAN = "gaussian"
    ORCA = "orca"


class GeometryOutput(str, Enum):
    """How the standard profile interprets a native geometry outcome.

    Attributes
    ----------
    PRODUCED
        The native program produced a new geometry.
    NONE
        No geometry was parsed.  The result profile maps this to passthrough
        semantics or to a ``geometry_required`` failure from declared checks.
    """

    PRODUCED = "produced"
    NONE = "none"


class NativeErrorCode(str, Enum):
    """Stable failure categories for native execution."""

    NATIVE_INPUT_ERROR = "native_input_error"
    ENVIRONMENT_ERROR = "environment_error"
    NATIVE_EXECUTION_ERROR = "native_execution_error"
    CANCELLATION_ERROR = "cancellation_error"
    NATIVE_PARSE_ERROR = "native_parse_error"
    SCIENTIFIC_CHECK_ERROR = "scientific_check_error"
    RECOVERY_FAILED = "recovery_failed"
    ARTIFACT_ERROR = "artifact_error"


@dataclass(frozen=True, slots=True)
class ParsedGeometry:
    """A geometry parsed from native output, in Ångström."""

    atoms: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "atoms", tuple(self.atoms))
        object.__setattr__(self, "coordinates", tuple(tuple(point) for point in self.coordinates))


@dataclass(frozen=True, slots=True)
class ProducedFile:
    """A file the native program left in the item work directory."""

    name: str
    role: str
    size_bytes: int | None = None
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class NativeResult:
    """Parser facts about one native execution.

    This is an intermediate layer, not the final work-item result.  Every
    field is a file-format fact; interpretation belongs to the result
    profile and the declared scientific checks.
    """

    program: ProgramName
    terminated_normally: bool
    geometry_output: GeometryOutput
    final_geometry: ParsedGeometry | None = None
    energies_hartree: FrozenDict = field(default_factory=FrozenDict)
    frequencies_cm: tuple[float, ...] = ()
    native_metadata: FrozenDict = field(default_factory=FrozenDict)
    produced_files: tuple[ProducedFile, ...] = ()
    parser_diagnostics: tuple[Diagnostic, ...] = ()
    log_file_name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.program, ProgramName):
            raise TypeError("program must be a ProgramName")
        if not isinstance(self.terminated_normally, bool):
            raise TypeError("terminated_normally must be a boolean")
        if not isinstance(self.geometry_output, GeometryOutput):
            raise TypeError("geometry_output must be a GeometryOutput")
        if self.geometry_output is GeometryOutput.PRODUCED and self.final_geometry is None:
            raise ValueError("PRODUCED geometry requires final_geometry")
        if self.final_geometry is not None and not isinstance(self.final_geometry, ParsedGeometry):
            raise TypeError("final_geometry must be a ParsedGeometry or None")
        for container in ("energies_hartree", "native_metadata"):
            value = getattr(self, container)
            if not isinstance(value, FrozenDict):
                object.__setattr__(self, container, FrozenDict(value))
        object.__setattr__(self, "frequencies_cm", tuple(self.frequencies_cm))
        object.__setattr__(self, "produced_files", tuple(self.produced_files))
        object.__setattr__(self, "parser_diagnostics", tuple(self.parser_diagnostics))

    @property
    def energy(self) -> float | None:
        """Return the preferred electronic energy in Hartree, if parsed."""
        value = self.energies_hartree.get("electronic")
        return float(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class InputFile:
    """One file the executor writes before launching the native program."""

    name: str
    content: str


@dataclass(frozen=True, slots=True)
class MaterializedNativeInput:
    """Rendered native input, ready to be written to the item work directory."""

    program: ProgramName
    main_input_name: str
    files: tuple[InputFile, ...] = ()
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.program, ProgramName):
            raise TypeError("program must be a ProgramName")
        if not self.main_input_name or not isinstance(self.main_input_name, str):
            raise ValueError("main_input_name must be a non-empty string")
        object.__setattr__(self, "files", tuple(self.files))
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))


@dataclass(frozen=True, slots=True)
class NativeExecutionRequest:
    """Everything a native process needs, and nothing else.

    No step graph, no predecessors, no labels, no YAML, no legacy config.
    ``argv[0]`` is the resolved executable; no shell is ever involved.
    """

    executable: str
    argv: tuple[str, ...]
    work_dir: str
    env: FrozenDict = field(default_factory=FrozenDict)
    walltime_seconds: float | None = None
    stdout_file: str = "stdout.log"
    stderr_file: str = "stderr.log"
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not self.executable or not isinstance(self.executable, str):
            raise ValueError("executable must be a non-empty string")
        object.__setattr__(self, "argv", tuple(self.argv))
        if not self.argv:
            raise ValueError("argv must not be empty")
        if not self.work_dir or not isinstance(self.work_dir, str):
            raise ValueError("work_dir must be a non-empty string")
        if self.walltime_seconds is not None and (
            not isinstance(self.walltime_seconds, (int, float)) or not self.walltime_seconds > 0
        ):
            raise ValueError("walltime_seconds must be a positive number or None")
        for container in ("env", "metadata"):
            value = getattr(self, container)
            if not isinstance(value, FrozenDict):
                object.__setattr__(self, container, FrozenDict(value))


@dataclass(frozen=True, slots=True)
class NativeExecutionResult:
    """Observed outcome of one native process."""

    exit_code: int | None
    wall_time_seconds: float
    timed_out: bool = False
    stdout_file: str = "stdout.log"
    stderr_file: str = "stderr.log"

    @property
    def exited_cleanly(self) -> bool:
        """Return whether the process exited with code 0 without timing out."""
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class NativeError:
    """A typed native-execution failure."""

    code: NativeErrorCode
    message: str
    program: ProgramName | None = None
    details: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, NativeErrorCode):
            raise TypeError("code must be a NativeErrorCode")
        if not self.message or not isinstance(self.message, str):
            raise ValueError("message must be a non-empty string")
        if self.program is not None and not isinstance(self.program, ProgramName):
            raise TypeError("program must be a ProgramName or None")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(self.details))


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    """A bound input artifact staged into the item work directory."""

    local_name: str
    role: str
    subject_structure_id: str | None = None
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedCalculationInputs:
    """Fully resolved inputs for one native calculation.

    Charge, multiplicity, freeze indices, and resources are already resolved
    by the single precedence authority before the adapter sees them.  An
    adapter that finds a required value missing must raise a native input
    error; it must never fall back to defaults of its own.
    """

    structure: StructureRecord
    charge: int | None
    multiplicity: int | None
    freeze: tuple[int, ...] | None
    resources: ResourceRequest
    native: FrozenDict = field(default_factory=FrozenDict)
    checkpoints: tuple[StagedArtifact, ...] = ()
    extra_structures: FrozenDict = field(default_factory=FrozenDict)
    step_id: str = ""
    work_item_id: str = ""
    logical_key: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.structure, StructureRecord):
            raise TypeError("structure must be a StructureRecord")
        if not isinstance(self.resources, ResourceRequest):
            raise TypeError("resources must be a ResourceRequest")
        for container in ("native", "extra_structures"):
            value = getattr(self, container)
            if not isinstance(value, FrozenDict):
                object.__setattr__(self, container, FrozenDict(value))
        object.__setattr__(self, "checkpoints", tuple(self.checkpoints))
        if self.freeze is not None:
            object.__setattr__(self, "freeze", tuple(self.freeze))


@dataclass(frozen=True, slots=True)
class NativeHandle:
    """Opaque identity of a launched native process.

    The handle carries process-boundary identity (pid, process group, session,
    creation time) so cancellation can prove that the boundary stopped.  It
    carries no program, workflow, or config knowledge.
    """

    key: str
    pid: int | None = None
    process_group_id: int | None = None
    session_id: int | None = None
    create_time: float | None = None
    submitted_at: float = 0.0


@dataclass(frozen=True, slots=True)
class NativeStatus:
    """One poll observation of a native process."""

    is_terminal: bool
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    """The verdict of a cancellation request.

    ``confirmed`` is true only when the supervisor proved the whole process
    boundary stopped.  An unconfirmed stop must never trigger recovery,
    restart, or duplicate execution.
    """

    confirmed: bool
    detail: str = ""


@runtime_checkable
class ProcessSupervisor(Protocol):
    """V4-owned native process boundary.

    The supervisor launches argv vectors without a shell, polls them, and
    cancels them with proof.  Provenance: the algorithms are extracted from
    the legacy executor's process mechanics; the interface is V4-only and
    carries no policy, coordinate, or config knowledge.
    """

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        """Launch the native process and return its handle."""
        ...

    def poll(self, handle: NativeHandle) -> NativeStatus:
        """Return the current status of the native process."""
        ...

    def cancel(self, handle: NativeHandle, *, grace_seconds: float = 2.0) -> CancelOutcome:
        """Request termination and prove whether the boundary stopped."""
        ...

    def collect(self, handle: NativeHandle) -> NativeExecutionResult:
        """Reap a terminal process and return its observed outcome."""
        ...


@runtime_checkable
class ProgramAdapter(Protocol):
    """File-format authority for one quantum-chemistry program.

    A program adapter renders native input, builds the launch request,
    parses native output into facts, and discovers artifacts.  It never
    interprets scientific intent: no role/task dispatch, no check logic,
    no recovery, no workflow graph.
    """

    @property
    def program_name(self) -> ProgramName:
        """Return the program this adapter serves."""
        ...

    @property
    def adapter_version(self) -> str:
        """Return the adapter contract version (folded into digests)."""
        ...

    @property
    def parser_version(self) -> str:
        """Return the parser contract version (folded into digests)."""
        ...

    @property
    def input_extension(self) -> str:
        """Return the native input file extension without dot."""
        ...

    @property
    def log_extension(self) -> str:
        """Return the native log file extension without dot."""
        ...

    @property
    def default_executable(self) -> str:
        """Return the default executable name used for PATH lookup."""
        ...

    def materialize_native_input(
        self, inputs: ResolvedCalculationInputs
    ) -> MaterializedNativeInput:
        """Render the native input files for one calculation."""
        ...

    def build_execution_request(
        self,
        materialized: MaterializedNativeInput,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ) -> NativeExecutionRequest:
        """Build the launch request for rendered native input."""
        ...

    def parse_native_result(
        self,
        *,
        work_dir: str,
        log_file_name: str,
        materialized: MaterializedNativeInput,
    ) -> NativeResult:
        """Parse native output files into parser facts."""
        ...

    def discover_artifacts(
        self,
        *,
        work_dir: str,
        run_relative_prefix: str,
        native_result: NativeResult,
        step_id: str,
        work_item_id: str,
        subject_structure_id: str | None,
    ) -> ArtifactSet:
        """Discover durable artifacts with checksums and portable locators."""
        ...

    def environment_probe(self, executable: str) -> dict[str, Any]:
        """Return safely measurable program facts, possibly empty.

        Probes must be cheap and side-effect free.  When no safe probe
        exists the adapter returns an empty mapping and file identity
        carries the environment digest.
        """
        ...
