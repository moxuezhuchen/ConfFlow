#!/usr/bin/env python3

"""ORCA program adapter for ConfFlow Workflow V4.

The adapter is the file-format authority for ORCA: it renders ``.inp`` input,
builds the launch request, parses ``.out`` output into facts, and discovers
durable artifacts.  It never interprets scientific intent and never imports
the legacy calc runtime.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any, cast

from ...domain._immutable import FrozenDict
from ...domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ...domain.diagnostics import Diagnostic, DiagnosticSeverity
from ...execution.native import (
    GeometryOutput,
    InputFile,
    MaterializedNativeInput,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeResult,
    ParsedGeometry,
    ProducedFile,
    ProgramAdapter,
    ProgramName,
    ResolvedCalculationInputs,
)
from .parsing import (
    analyze_vibrational_frequencies,
    orca_error_details,
    parse_cartesian_blocks,
    parse_energies,
    parse_frequencies,
    parse_xyz_companion,
    read_log_text,
    termination_reached,
    true_vibrational_modes,
)
from .rendering import (
    ALLOWED_NATIVE_KEYS,
    format_coord_lines,
    render_orca_input,
    resolve_blocks_text,
    resolve_keyword,
    resolve_maxcore,
    sanitize_job_name,
)

__all__ = [
    "OrcaProgramAdapter",
]

#: Files considered for artifact discovery, in deterministic order.
_OUTPUT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("out", "native_output"),
    ("inp", "native_input"),
    ("xyz", "native_geometry"),
    ("finalensemble.xyz", "ensemble_geometries"),
    ("gbw", "checkpoint_wavefunction"),
    ("err", "stderr"),
)


def _input_error(message: str) -> ValueError:
    """Build a native-input ``ValueError`` carrying the stable error code.

    Parameters
    ----------
    message : str
        Human-readable explanation of the invalid input.

    Returns
    -------
    ValueError
        Exception whose message starts with ``"native_input_error"``.
    """
    return ValueError(f"{NativeErrorCode.NATIVE_INPUT_ERROR.value}: {message}")


def _require_keyword_for_mode(keyword: str, mode: str) -> None:
    """Require the job-type keyword matching a path/ensemble mode.

    All path/ensemble modes (``%irc`` / ``%neb`` / ``%goat``) are
    retired: a mode block under a plain ``Opt`` keyword would run a
    plain optimization and silently ignore the block.  The table is
    therefore empty and the check is a no-op retained for call-site
    stability; retired modes fail closed at definition validation
    (unknown native keys) and at materialization time.
    """
    tokens = keyword.split()
    upper = [token.upper() for token in tokens]
    required: tuple[str, ...] | None = dict[str, tuple[str, ...]]().get(mode)
    if required is None:
        return
    if not any(token in upper for token in required):
        raise _input_error(
            f"ORCA {mode} mode requires keyword {'/'.join(required)} "
            f"(got {keyword!r}); a mode block under a plain keyword "
            "runs a different job silently"
        )


def native_definition_errors(native: Any) -> tuple[str, ...]:
    """Return deterministic native-definition failures without rendering.

    The structure-independent half of ORCA native rendering: strict native
    vocabulary, required non-empty keyword and deterministic option values.
    Semantic validation calls this through the adapter before a document can
    be submitted; the renderer refuses on the same render helpers, so the
    requirement is never stated twice.

    Structure-dependent requirements (charge/multiplicity resolution,
    geometry, freeze indices, checkpoint consumption) stay in the
    runtime rendering path.
    """
    if not isinstance(native, Mapping):
        return ("native_input_error: ORCA native options must be a mapping",)
    errors: list[str] = []
    unknown = sorted(set(native) - set(ALLOWED_NATIVE_KEYS))
    if unknown:
        errors.append(f"native_input_error: ORCA unknown native keys: {', '.join(unknown)}")
    try:
        resolve_keyword(native)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        resolve_blocks_text(native)
    except ValueError as exc:
        errors.append(str(exc))
    # All path/ensemble modes (irc/neb/goat) are retired: any such key
    # is already reported as an unknown native key above.  No per-mode
    # option validation remains.
    # A non-blank explicit override is deterministic; a blank value means
    # "absent" and the renderer derives %maxcore from resolved resources (a
    # runtime/resource concern), so it must not be refused here.
    if native.get("maxcore") is not None and str(native.get("maxcore")).strip():
        try:
            resolve_maxcore(native, memory_bytes=None, cores=None)
        except ValueError as exc:
            errors.append(str(exc))
    return tuple(errors)


def _path_mode_option_errors(mode: str, section: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate one retired path/ensemble mode section (no-op).

    All modes (``irc``/``neb``/``goat``) are retired; the definition
    validator rejects their keys as unknown before this helper runs.
    Retained for call-site stability; always returns no errors.
    """
    del mode, section
    return ()


class OrcaProgramAdapter(ProgramAdapter):
    """File-format authority for ORCA native execution."""

    @property
    def program_name(self) -> ProgramName:
        """Return the program this adapter serves."""
        return ProgramName.ORCA

    @property
    def adapter_version(self) -> str:
        """Return the adapter contract version (folded into digests)."""
        return "confflow.program.orca.v2"

    @property
    def parser_version(self) -> str:
        """Return the parser contract version (folded into digests)."""
        return "confflow.program.orca.parser.v2"

    @property
    def input_extension(self) -> str:
        """Return the native input file extension without dot."""
        return "inp"

    @property
    def log_extension(self) -> str:
        """Return the native log file extension without dot."""
        return "out"

    @property
    def default_executable(self) -> str:
        """Return the default executable name used for PATH lookup."""
        return "orca"

    def validate_native_definition(self, native: Mapping[str, Any]) -> tuple[str, ...]:
        """Return deterministic native-definition failures without rendering.

        The adapter is the file-format authority: this method exposes the
        structure-independent half of native rendering to semantic validation,
        and :meth:`materialize_native_input` refuses on the same result before
        rendering anything.  Structure-dependent requirements stay in the
        rendering path.
        """
        return native_definition_errors(native)

    def materialize_native_input(
        self, inputs: ResolvedCalculationInputs
    ) -> MaterializedNativeInput:
        """Render the ORCA ``.inp`` file for one calculation.

        Parameters
        ----------
        inputs : ResolvedCalculationInputs
            Fully resolved calculation inputs.

        Returns
        -------
        MaterializedNativeInput
            Rendered native input ready to be written to the work directory.

        Raises
        ------
        ValueError
            Raised with a ``native_input_error`` message when charge,
            multiplicity, resources, or required native options are missing
            or malformed, or when unknown native keys are present.
        """
        if inputs.charge is None:
            raise _input_error("ORCA 'charge' must be resolved; got None")
        if inputs.multiplicity is None:
            raise _input_error("ORCA 'multiplicity' must be resolved; got None")
        if inputs.checkpoints:
            raise _input_error(
                "artifact_unsupported: ORCA declares no checkpoint input vocabulary; "
                f"{len(inputs.checkpoints)} checkpoint artifact(s) bound but unconsumable"
            )
        charge = int(inputs.charge)
        multiplicity = int(inputs.multiplicity)
        if multiplicity < 1:
            raise _input_error(f"ORCA 'multiplicity' must be >= 1, got {multiplicity}")

        native = inputs.native
        native_errors = self.validate_native_definition(native)
        if native_errors:
            raise ValueError(native_errors[0])

        keyword = resolve_keyword(native)
        blocks_text = resolve_blocks_text(native)

        fallback = inputs.work_item_id or inputs.step_id or "job"
        job = sanitize_job_name(inputs.logical_key, fallback=fallback)
        mode, extra_blocks, extra_files = self._resolve_path_mode(inputs, job=job)
        _require_keyword_for_mode(keyword, mode)
        if extra_blocks:
            blocks_text = f"{blocks_text}{extra_blocks}" if blocks_text else extra_blocks

        cores = inputs.resources.cores_per_item
        memory_bytes = inputs.resources.memory_per_item_bytes
        if cores is None:
            raise _input_error("ORCA 'cores_per_item' must be resolved; got None")
        maxcore = resolve_maxcore(native, memory_bytes=memory_bytes, cores=int(cores))

        freeze = inputs.freeze
        if freeze:
            atom_count = len(inputs.structure.atoms)
            for index in freeze:
                if int(index) < 1 or int(index) > atom_count:
                    raise _input_error(
                        f"ORCA freeze index {index!r} out of range for {atom_count} atoms"
                    )

        main_input_name = f"{job}.inp"
        coords_text = format_coord_lines(inputs.structure.atoms, inputs.structure.coordinates)
        content = render_orca_input(
            keyword=keyword,
            cores=int(cores),
            maxcore=maxcore,
            blocks_text=blocks_text,
            freeze=freeze,
            charge=charge,
            multiplicity=multiplicity,
            coords_text=coords_text,
        )
        metadata = FrozenDict(
            {
                "program": ProgramName.ORCA.value,
                "adapter_version": self.adapter_version,
                "keyword": keyword,
                "charge": charge,
                "multiplicity": multiplicity,
                "cores": int(cores),
                "maxcore": maxcore,
                "job": job,
                "main_input_name": main_input_name,
                "freeze": tuple(int(index) for index in freeze) if freeze else (),
                "mode": mode,
                "n_images": 0,
                "input_atoms": list(inputs.structure.atoms),
            }
        )
        return MaterializedNativeInput(
            program=ProgramName.ORCA,
            main_input_name=main_input_name,
            files=(InputFile(name=main_input_name, content=content), *extra_files),
            metadata=metadata,
        )

    @staticmethod
    def _resolve_path_mode(
        inputs: ResolvedCalculationInputs, *, job: str
    ) -> tuple[str, str, tuple[InputFile, ...]]:
        """Return ``(mode, extra_blocks, extra_files)`` for standard jobs.

        All path/ensemble modes (``irc``/``neb``/``goat``) are retired:
        any work item carrying them fails closed here instead of
        rendering.  Anything else is standard single-structure rendering.
        """
        del job
        native = inputs.native
        for retired in ("irc", "neb", "goat"):
            try:
                present = native.get(retired)
            except Exception:
                present = None
            if present is not None:
                raise _input_error(
                    f"native_input_error: ORCA {retired} mode is retired; "
                    f"native {retired!r} must not be declared"
                )
        return "standard", "", ()

    def build_execution_request(
        self,
        materialized: MaterializedNativeInput,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ) -> NativeExecutionRequest:
        """Build the launch request for rendered ORCA input.

        Parameters
        ----------
        materialized : MaterializedNativeInput
            Rendered native input from :meth:`materialize_native_input`.
        executable : str
            Resolved ORCA executable.
        work_dir : str
            Item work directory where the process runs.
        env : dict[str, str]
            Environment passed through unchanged.
        walltime_seconds : float | None
            Optional wall-clock limit.

        Returns
        -------
        NativeExecutionRequest
            Launch request with ``argv=[executable, basename]``.
        """
        job_base, _ = os.path.splitext(materialized.main_input_name)
        if not job_base:
            job_base = materialized.main_input_name
        return NativeExecutionRequest(
            executable=executable,
            argv=(executable, os.path.basename(materialized.main_input_name)),
            work_dir=work_dir,
            env=FrozenDict(dict(env)),
            walltime_seconds=walltime_seconds,
            stdout_file=f"{job_base}.out",
            stderr_file=f"{job_base}.err",
            metadata=FrozenDict(
                {
                    "program": ProgramName.ORCA.value,
                    "job": job_base,
                    "main_input_name": materialized.main_input_name,
                }
            ),
        )

    def parse_native_result(
        self,
        *,
        work_dir: str,
        log_file_name: str,
        materialized: MaterializedNativeInput,
    ) -> NativeResult:
        """Parse ORCA output files in *work_dir* into parser facts.

        Parameters
        ----------
        work_dir : str
            Item work directory holding native output files.
        log_file_name : str
            Native log file name (``<job>.out``).
        materialized : MaterializedNativeInput
            Rendered native input (carries job identity for metadata).

        Returns
        -------
        NativeResult
            Parser facts; a missing log yields ``terminated_normally=False``,
            ``NONE`` geometry, and a parser error diagnostic.
        """
        mode = materialized.metadata.get("mode", "standard")
        log_path = os.path.join(work_dir, log_file_name)
        text = read_log_text(log_path)
        if text is None:
            diagnostic = Diagnostic(
                code=NativeErrorCode.NATIVE_PARSE_ERROR.value,
                message=f"ORCA log file not found: {log_file_name}",
                severity=DiagnosticSeverity.ERROR,
                details=FrozenDict({"log_file_name": log_file_name}),
            )
            return NativeResult(
                program=ProgramName.ORCA,
                terminated_normally=False,
                geometry_output=GeometryOutput.NONE,
                final_geometry=None,
                energies_hartree=FrozenDict({}),
                frequencies_cm=(),
                native_metadata=FrozenDict(
                    {
                        "parser_version": self.parser_version,
                        "terminated_normally": False,
                    }
                ),
                produced_files=(),
                parser_diagnostics=(diagnostic,),
                log_file_name=log_file_name,
            )

        energies = parse_energies(text)
        terminated = termination_reached(text)
        if mode in ("irc", "neb", "neb_ts", "goat"):
            raise ValueError(
                "native_parse_error: ORCA path/ensemble mode "
                f"{mode!r} is retired; materialized mode must not be parsed"
            )
        committed = parse_frequencies(text)
        modes = true_vibrational_modes(committed)
        num_imag, lowest = analyze_vibrational_frequencies(committed)

        log_base, _ = os.path.splitext(log_file_name)
        geometry = parse_xyz_companion(os.path.join(work_dir, f"{log_base}.xyz"))
        if geometry is None:
            geometry = parse_cartesian_blocks(text)
        if geometry is not None:
            atoms, coords = geometry
            final_geometry = ParsedGeometry(
                atoms=tuple(atoms),
                coordinates=tuple(
                    cast("tuple[float, float, float]", tuple(point)) for point in coords
                ),
            )
            geometry_output = GeometryOutput.PRODUCED
        else:
            final_geometry = None
            geometry_output = GeometryOutput.NONE

        produced: list[ProducedFile] = []
        for suffix, role in _OUTPUT_CANDIDATES:
            if suffix == "out":
                name = log_file_name
            else:
                name = f"{log_base}.{suffix}"
            path = os.path.join(work_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            produced.append(ProducedFile(name=name, role=role, size_bytes=int(size)))

        metadata: dict[str, Any] = {
            "parser_version": self.parser_version,
            "terminated_normally": terminated,
        }
        if committed:
            metadata["num_imaginary_frequencies"] = int(num_imag)
            metadata["lowest_frequency_cm_1"] = lowest
        error_details = orca_error_details(text)
        if error_details:
            metadata["error_details"] = error_details

        # ORCA can exit normally after exhausting the geometry iterations.
        # Preserve termination as a native fact, but refuse its unconverged
        # structure/energy through an error diagnostic consumed by the executor.
        parser_diagnostics: list[Diagnostic] = []
        for marker, reason in (
            ("The optimization did not converge", "geometry_not_converged"),
            ("SCF NOT CONVERGED", "scf_not_converged"),
            ("SCF DID NOT CONVERGE", "scf_not_converged"),
        ):
            if marker.lower() in text.lower():
                parser_diagnostics.append(
                    Diagnostic(
                        code=NativeErrorCode.SCIENTIFIC_CHECK_ERROR.value,
                        message=f"ORCA reported {reason.replace('_', ' ')}.",
                        severity=DiagnosticSeverity.ERROR,
                        details=FrozenDict({"reason": reason}),
                    )
                )

        return NativeResult(
            program=ProgramName.ORCA,
            terminated_normally=terminated,
            geometry_output=geometry_output,
            final_geometry=final_geometry,
            energies_hartree=FrozenDict(energies),
            frequencies_cm=tuple(modes),
            native_metadata=FrozenDict(metadata),
            produced_files=tuple(produced),
            parser_diagnostics=tuple(parser_diagnostics),
            log_file_name=log_file_name,
        )

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
        """Discover durable artifacts with checksums and portable locators.

        Parameters
        ----------
        work_dir : str
            Item work directory holding native output files.
        run_relative_prefix : str
            Run-root-relative directory prefix for locators.
        native_result : NativeResult
            Parsed native result listing produced files.
        step_id : str
            Producing step id.
        work_item_id : str
            Producing work item id.
        subject_structure_id : str | None
            Structure the artifacts bind to, when applicable.

        Returns
        -------
        ArtifactSet
            One reference per produced file present on disk.
        """
        prefix = str(run_relative_prefix).strip("/")
        refs: list[ArtifactRef] = []
        for produced in native_result.produced_files:
            path = os.path.join(work_dir, produced.name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
            except OSError:
                continue
            locator_path = f"{prefix}/{produced.name}" if prefix else produced.name
            if work_item_id:
                artifact_id = f"{work_item_id}:{produced.name}"
            elif step_id:
                artifact_id = f"{step_id}:{produced.name}"
            else:
                artifact_id = produced.name
            # The wave-function file carries restart semantics, so it is a
            # ``checkpoint``; the native format rides in metadata, never in
            # the role (roles are semantic, not extension aliases).
            role = produced.role
            metadata: dict[str, Any] = {}
            if role == "checkpoint_wavefunction":
                role = "checkpoint"
                metadata["program_format"] = "orca_gbw"
            refs.append(
                ArtifactRef(
                    id=artifact_id,
                    role=role,
                    locator=ArtifactLocator.run_relative(locator_path),
                    checksum=f"sha256:{digest}",
                    program=ProgramName.ORCA.value,
                    producer_step_id=step_id or None,
                    producer_work_item_id=work_item_id or None,
                    subject_structure_id=subject_structure_id,
                    metadata=FrozenDict(metadata),
                )
            )
        return ArtifactSet(tuple(refs))

    def environment_probe(self, executable: str) -> dict[str, Any]:
        """Return safely measurable program facts, possibly empty.

        Parameters
        ----------
        executable : str
            Resolved ORCA executable (unused; no safe probe exists).

        Returns
        -------
        dict[str, Any]
            Empty mapping; file identity carries the environment digest.
        """
        del executable
        return {}
