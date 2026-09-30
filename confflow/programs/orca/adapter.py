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
from typing import Any

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


#: Refusal shared by the definition validator and the renderer: a user native
#: RANDOMSEED is a second seed authority (one string, one rule).
_GOAT_RANDOMSEED_MESSAGE = (
    "native_input_error: native goat RANDOMSEED is a second seed authority; "
    "set the step seed instead (compile-time validation rejects this key "
    "before rendering)"
)


def _require_goat_seed(seed: object) -> int:
    """Validate the single-authority GOAT seed for native rendering.

    The typed step seed is required; the adapter renders the native
    boolean switch deterministically as ``RANDOMSEED false``.  Per the
    official ORCA 6.1 manual (``%goat`` Table 4.9), ``RANDOMSEED`` is
    a boolean randomization switch (default ``true``): "set it to
    false to have a deterministic GOAT run", with the caveat that
    geometry optimization can change due to numerical differences so
    it might not be fully deterministic in some cases.  ORCA 6.1
    exposes no numeric stream-selection mechanism: the installed
    6.1.1 binary's parser tolerates integers for this key, but live
    probes show no stream-selection behavior, so the integer step
    seed is never rendered as a native value.  It remains the
    workflow-level stochastic authority — required here, folded into
    the step semantic digest, carried in the remote envelope, and
    preserved across recovery — so distinct seeds never share
    identity even though they share native ``.inp`` bytes by design.
    Whether the deterministic flag yields bit-identical ensembles on
    larger search spaces is an open question — ConfFlow claims
    best-effort same-input reproducibility only.
    """
    if seed is None or isinstance(seed, bool) or not isinstance(seed, int):
        raise _input_error(
            "GOAT sampling requires the explicit typed step seed (single authority); "
            f"got {seed!r}"
        )
    return seed


def _require_keyword_for_mode(keyword: str, mode: str) -> None:
    """Require the job-type keyword matching a path/ensemble mode.

    Verified against the installed ORCA 6.1.1 binary: a ``%irc`` /
    ``%neb`` / ``%goat`` block under a plain ``Opt`` keyword runs a
    plain optimization and silently ignores the block.  The keyword
    must therefore name the job (``IRC`` / ``NEB`` / ``NEB-TS`` /
    ``GOAT``); otherwise rendering refuses instead of launching a
    silently wrong job.
    """
    tokens = keyword.split()
    upper = [token.upper() for token in tokens]
    required = {
        "irc": ("IRC",),
        "neb": ("NEB",),
        "neb_ts": ("NEB-TS",),
        "goat": ("GOAT",),
    }.get(mode)
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
    vocabulary, required non-empty keyword, block/path-mode option validation
    (``irc``/``goat``/``neb`` sections) and deterministic option values.
    Semantic validation calls this through the adapter before a document can
    be submitted; the renderer refuses on the same render helpers, so the
    requirement is never stated twice.

    Structure-dependent requirements (charge/multiplicity resolution,
    geometry, freeze indices, checkpoint consumption, the NEB product
    structure slot) stay in the runtime rendering path.
    """
    if not isinstance(native, Mapping):
        return ("native_input_error: ORCA native options must be a mapping",)
    errors: list[str] = []
    unknown = sorted(set(native) - set(ALLOWED_NATIVE_KEYS))
    if unknown:
        errors.append(f"native_input_error: ORCA unknown native keys: {', '.join(unknown)}")
    keyword: str | None = None
    try:
        keyword = resolve_keyword(native)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        resolve_blocks_text(native)
    except ValueError as exc:
        errors.append(str(exc))
    modes = [key for key in ("irc", "neb", "goat") if native.get(key) is not None]
    if len(modes) > 1:
        errors.append(
            f"native_input_error: ORCA accepts at most one path/ensemble mode, got {modes}; "
            "one WorkItem carries one native mode"
        )
    elif modes:
        mode = modes[0]
        section = native.get(mode)
        if not isinstance(section, Mapping):
            errors.append(f"native_input_error: ORCA '{mode}' native options must be a mapping")
        else:
            errors.extend(_path_mode_option_errors(mode, section))
            if keyword is not None:
                if mode == "neb" and section.get("neb_ts", False) is True:
                    mode = "neb_ts"
                try:
                    _require_keyword_for_mode(keyword, mode)
                except ValueError as exc:
                    errors.append(str(exc))
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
    """Validate one path/ensemble mode section with the real render helpers.

    ``irc``/``goat``/``neb`` option validation is structure-independent, so
    the definition validator exercises exactly the helpers the renderer calls.
    The NEB product endpoint name is a rendering decision, never a document
    member; a neutral valid name is used here and the product *structure*
    requirement stays in the runtime path.
    """
    from .goat import render_goat_blocks
    from .neb import render_neb_blocks
    from .path import render_irc_blocks

    try:
        if mode == "irc":
            render_irc_blocks(section)
        elif mode == "goat":
            if "RANDOMSEED" in section:
                return (_GOAT_RANDOMSEED_MESSAGE,)
            render_goat_blocks({"goat": dict(section)})
        elif mode == "neb":
            render_neb_blocks(section, product_xyz_name="neb_endpoint.xyz")
        else:  # pragma: no cover - the caller only passes the three modes
            return ()
    except ValueError as exc:
        return (str(exc),)
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
        return "confflow.program.orca.v1"

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
                "n_images": self._native_neb_images(native),
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
        """Return ``(mode, extra_blocks, extra_files)`` for path/ensemble jobs.

        At most one of the ``irc``/``neb``/``goat`` native sub-mappings may
        be present; NEB additionally requires named reactant/product slots
        and contributes the product endpoint XYZ file the ``%neb`` block
        points at.  Anything else is standard single-structure rendering.
        """
        from .goat import render_goat_blocks
        from .neb import render_neb_blocks
        from .path import render_irc_blocks

        native = inputs.native
        modes = [key for key in ("irc", "neb", "goat") if native.get(key) is not None]
        if len(modes) > 1:
            raise _input_error(
                f"ORCA accepts at most one path/ensemble mode, got {modes}; "
                "one WorkItem carries one native mode"
            )
        if not modes:
            return "standard", "", ()
        mode = modes[0]
        section = native.get(mode)
        if not isinstance(section, Mapping):
            raise _input_error(f"ORCA '{mode}' native options must be a mapping")
        if mode == "irc":
            return "irc", render_irc_blocks(section), ()
        if mode == "goat":
            _require_goat_seed(inputs.seed)
            user_goat = dict(section)
            if "RANDOMSEED" in user_goat:
                raise ValueError(_GOAT_RANDOMSEED_MESSAGE)
            # The step seed is workflow identity (digest/envelope), never
            # a native stream selector: ORCA 6.1 defines RANDOMSEED as a
            # boolean switch with no numeric stream semantics, so the
            # adapter always renders the deterministic ``false`` flag.
            user_goat["RANDOMSEED"] = False
            blocks = render_goat_blocks({"goat": user_goat})
            return "goat", blocks, ()
        slots = inputs.extra_structures
        product_set = slots.get("product") if hasattr(slots, "get") else None
        if product_set is None or len(product_set) == 0:
            raise _input_error("ORCA NEB rendering requires a product structure slot")
        product = product_set[0]
        product_xyz_name = f"{job}_neb_end.xyz"
        blocks = render_neb_blocks(section, product_xyz_name=product_xyz_name)
        neb_ts = section.get("neb_ts", False) is True
        lines = [str(len(product.atoms)), "product endpoint for NEB"]
        lines.extend(
            f"{symbol} {point[0]:.8f} {point[1]:.8f} {point[2]:.8f}"
            for symbol, point in zip(product.atoms, product.coordinates)
        )
        xyz_content = "\n".join(lines) + "\n"
        return (
            "neb_ts" if neb_ts else "neb",
            blocks,
            (InputFile(name=product_xyz_name, content=xyz_content),),
        )

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
            return self._parse_path_result(
                text,
                mode=mode,
                work_dir=work_dir,
                log_file_name=log_file_name,
                log_base=os.path.splitext(log_file_name)[0],
                materialized=materialized,
                terminated=terminated,
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
                coordinates=tuple(tuple(point) for point in coords),
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

    def _parse_path_result(
        self,
        text: str,
        *,
        mode: str,
        work_dir: str,
        log_file_name: str,
        log_base: str,
        materialized: MaterializedNativeInput,
        terminated: bool,
    ) -> NativeResult:
        """Parse IRC/NEB/GOAT output into path/ensemble facts.

        Endpoints and members come exclusively from explicit native
        direction/member markers; a missing marker is a parse error, never
        an inference.  NEB images and GOAT conformers ride as ensemble
        members with their semantic roles; an NEB-TS candidate appears only
        when the output explicitly reports an optimized TS.
        """
        from ...execution.native import NativeEnsembleMember, NativePathEndpoint
        from .ensemble_parse import parse_goat_ensemble
        from .neb import parse_neb_images
        from .path import parse_path_endpoints

        raw_atoms = materialized.metadata.get("input_atoms", [])
        atoms = tuple(str(symbol) for symbol in raw_atoms)
        endpoints: tuple[NativePathEndpoint, ...] = ()
        members: tuple[NativeEnsembleMember, ...] = ()
        extra_metadata: dict[str, Any] = {"mode": mode}
        if mode == "irc":
            endpoints = parse_path_endpoints(
                text, atoms=atoms, work_dir=work_dir, log_base=log_base
            )
        elif mode in ("neb", "neb_ts"):
            import dataclasses as _dataclasses

            from .neb import parse_neb_ts_candidate as _parse_neb_ts_candidate

            mep_path = os.path.join(work_dir, f"{log_base}_MEP_trj.xyz")
            try:
                with open(mep_path, encoding="utf-8") as handle:
                    mep_xyz_text = handle.read()
            except OSError as exc:
                raise ValueError(
                    "native_parse_error: NEB run produced no MEP trajectory "
                    f"file {mep_path!r}: {exc}"
                ) from exc
            raw_members = parse_neb_images(
                mep_xyz_text,
                atoms=atoms,
                n_images=self._materialized_neb_images(materialized),
            )
            members = tuple(
                _dataclasses.replace(member, role="neb_image") for member in raw_members
            )
            ts_candidate = _parse_neb_ts_candidate(text, atoms=atoms)
            if ts_candidate is not None:
                if (
                    mode == "neb_ts"
                    and "NEB-TS"
                    not in str(materialized.metadata.get("keyword", "")).upper().split()
                ):
                    raise ValueError(
                        "native_parse_error: NEB-TS candidate refused: the job "
                        "keyword carries no NEB-TS token, so the highest-energy "
                        "image is a path maximum, never an optimized TS"
                    )
                members = (*members, _dataclasses.replace(ts_candidate, role="neb_ts_candidate"))
            elif mode == "neb_ts":
                raise ValueError(
                    "native_parse_error: NEB-TS requested but the output reports "
                    "no highest-energy image / saddle point"
                )
        else:
            ensemble_path = os.path.join(work_dir, f"{log_base}.finalensemble.xyz")
            try:
                with open(ensemble_path, encoding="utf-8") as handle:
                    ensemble_xyz_text = handle.read()
            except OSError as exc:
                raise ValueError(
                    "native_parse_error: GOAT run produced no "
                    f"final-ensemble file {ensemble_path!r}: {exc}"
                ) from exc
            members = parse_goat_ensemble(text, ensemble_xyz_text=ensemble_xyz_text, atoms=atoms)
        produced: list[ProducedFile] = []
        for suffix, role in _OUTPUT_CANDIDATES:
            name = log_file_name if suffix == "out" else f"{log_base}.{suffix}"
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
        metadata.update(extra_metadata)
        return NativeResult(
            program=ProgramName.ORCA,
            terminated_normally=terminated,
            geometry_output=GeometryOutput.NONE,
            final_geometry=None,
            energies_hartree=FrozenDict(
                {
                    **{
                        f"endpoint_{endpoint.direction}": endpoint.energy_hartree
                        for endpoint in endpoints
                        if endpoint.energy_hartree is not None
                    },
                    **{
                        f"member_{member.member_index}": member.energy_hartree
                        for member in members
                        if member.energy_hartree is not None
                    },
                }
            ),
            frequencies_cm=(),
            native_metadata=FrozenDict(metadata),
            produced_files=tuple(produced),
            parser_diagnostics=(),
            log_file_name=log_file_name,
            path_endpoints=endpoints,
            ensemble_members=members,
        )

    @staticmethod
    def _native_neb_images(native: Any) -> int:
        """Return the NEB image count declared in *native* (0 when absent)."""
        section = native.get("neb") if hasattr(native, "get") else None
        if not isinstance(section, Mapping):
            return 0
        value = section.get("n_images", 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    @staticmethod
    def _materialized_neb_images(materialized: MaterializedNativeInput) -> int:
        """Return the NEB image count recorded at materialization time."""
        value = materialized.metadata.get("n_images", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 3:
            raise ValueError(
                "native_parse_error: materialized NEB metadata lacks a valid "
                f"n_images, got {value!r}"
            )
        return value

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
