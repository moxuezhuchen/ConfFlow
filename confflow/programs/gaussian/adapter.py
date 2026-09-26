#!/usr/bin/env python3

"""Gaussian program adapter for V4 native execution.

:class:`GaussianProgramAdapter` is the file-format authority for Gaussian. It
renders ``.gjf`` input, builds launch requests, parses ``.log`` output into
facts, discovers artifacts, and probes the environment. It never interprets
scientific intent and never launches processes itself.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from ...domain._immutable import FrozenDict
from ...domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ...domain.diagnostics import Diagnostic, DiagnosticSeverity
from ...execution.native import (
    GeometryOutput,
    InputFile,
    MaterializedNativeInput,
    NativeExecutionRequest,
    NativeResult,
    ParsedGeometry,
    ProducedFile,
    ProgramName,
    ResolvedCalculationInputs,
)
from . import parsing as _parsing
from . import rendering as _rendering

__all__ = [
    "ADAPTER_VERSION",
    "MEDIA_TYPES_BY_EXTENSION",
    "PARSER_VERSION",
    "GaussianProgramAdapter",
]

#: Adapter contract version folded into digests.
ADAPTER_VERSION: str = "confflow.program.gaussian.v1"

#: Parser contract version folded into digests.
PARSER_VERSION: str = "confflow.program.gaussian.parser.v1"

#: Media types recorded on discovered artifacts, keyed by file extension.
MEDIA_TYPES_BY_EXTENSION: dict[str, str] = {
    ".log": "text/plain",
    ".gjf": "text/plain",
    ".err": "text/plain",
    ".chk": "application/octet-stream",
}

_HASH_CHUNK_BYTES: int = 65536


def _job_from_materialized(materialized: MaterializedNativeInput) -> str:
    """Return the job name recorded at materialization time.

    Parameters
    ----------
    materialized : MaterializedNativeInput
        Rendered native input carrying adapter metadata.

    Returns
    -------
    str
        The ``job`` metadata value, or the main input stem when absent.
    """
    try:
        job = materialized.metadata.get("job")
    except (AttributeError, KeyError):
        job = None
    if isinstance(job, str) and job.strip():
        return job
    return os.path.splitext(os.path.basename(materialized.main_input_name))[0]


def _file_size(path: str) -> int | None:
    """Return the size of *path* in bytes, or ``None`` when unreadable.

    Parameters
    ----------
    path : str
        Filesystem path to inspect.

    Returns
    -------
    int | None
        File size in bytes, or ``None`` when the file is absent.
    """
    try:
        return int(os.path.getsize(path))
    except OSError:
        return None


def _sha256_checksum(path: str) -> str | None:
    """Return the ``sha256:<hex>`` checksum of *path*.

    Parameters
    ----------
    path : str
        Filesystem path to hash.

    Returns
    -------
    str | None
        Checksum string, or ``None`` when the file cannot be read.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return f"sha256:{digest.hexdigest()}"


class GaussianProgramAdapter:
    """File-format authority for Gaussian native execution.

    The adapter renders native input files, builds launch requests, parses
    native output into facts, discovers artifacts with checksums, and answers
    environment probes. It performs no process management.
    """

    @property
    def program_name(self) -> ProgramName:
        """Return the program this adapter serves."""
        return ProgramName.GAUSSIAN

    @property
    def adapter_version(self) -> str:
        """Return the adapter contract version folded into digests."""
        return ADAPTER_VERSION

    @property
    def parser_version(self) -> str:
        """Return the parser contract version folded into digests."""
        return PARSER_VERSION

    @property
    def input_extension(self) -> str:
        """Return the native input file extension without dot."""
        return "gjf"

    @property
    def log_extension(self) -> str:
        """Return the native log file extension without dot."""
        return "log"

    @property
    def default_executable(self) -> str:
        """Return the default executable name used for PATH lookup."""
        return "g16"

    @staticmethod
    def scan_keyword_from_ts(keyword: str | None) -> str | None:
        """Rewrite a TS keyword line into one suitable for a scan job.

        Parameters
        ----------
        keyword : str | None
            TS keyword text.

        Returns
        -------
        str | None
            Rewritten keyword, or ``None`` when nothing usable remains.
        """
        return _rendering.scan_keyword_from_ts(keyword)

    def materialize_native_input(
        self, inputs: ResolvedCalculationInputs
    ) -> MaterializedNativeInput:
        """Render the native input files for one calculation.

        Parameters
        ----------
        inputs : ResolvedCalculationInputs
            Fully resolved calculation inputs. Charge, multiplicity, freeze
            indices, resources, and the required ``keyword`` native entry
            must already be resolved.

        Returns
        -------
        MaterializedNativeInput
            Rendered ``<job>.gjf`` input with adapter metadata.

        Raises
        ------
        ValueError
            Raised as ``native_input_error`` when charge, multiplicity,
            resources, or native vocabulary entries are missing or invalid.
        """
        native = inputs.native
        _rendering.check_native_keys(native)
        charge = _rendering.resolve_charge(inputs.charge)
        multiplicity = _rendering.resolve_multiplicity(inputs.multiplicity)
        cores = _rendering.resolve_core_count(inputs.resources.cores_per_item)
        memory = _rendering.format_memory_gb(inputs.resources.memory_per_item_bytes)
        keyword_line = _rendering.format_keyword_line(_rendering.resolve_keyword(native))
        job = _rendering.sanitize_job_name(inputs.logical_key or "job")
        write_chk = _rendering.resolve_write_chk(native)
        oldchk_name: str | None = None
        if inputs.checkpoints:
            staged = inputs.checkpoints[0].local_name
            if isinstance(staged, str) and staged.strip():
                oldchk_name = staged.strip()
        link0_lines = _rendering.resolve_link0_lines(
            job=job,
            write_chk=write_chk,
            oldchk_name=oldchk_name,
            user_link0=native.get("link0"),
        )
        mode = self._execution_mode(inputs, keyword_line)
        extra_section = _rendering.resolve_extra_section(native)
        if mode in ("qst2", "qst3"):
            content = self._render_qst_input(
                inputs,
                mode,
                link0_lines=link0_lines,
                cores=cores,
                memory=memory,
                keyword_line=keyword_line,
                job=job,
                charge=charge,
                multiplicity=multiplicity,
                extra_section=extra_section,
            )
        else:
            coord_lines = _rendering.apply_freeze(
                tuple(inputs.structure.atoms),
                tuple(tuple(point) for point in inputs.structure.coordinates),
                inputs.freeze,
            )
            content = _rendering.render_gaussian_input(
                link0_lines=link0_lines,
                cores=cores,
                memory=memory,
                keyword_line=keyword_line,
                job=job,
                charge=charge,
                multiplicity=multiplicity,
                coord_lines=coord_lines,
                extra_section=extra_section,
            )
        main_input_name = f"{job}.gjf"
        metadata = FrozenDict(
            {
                "program": ProgramName.GAUSSIAN.value,
                "adapter_version": ADAPTER_VERSION,
                "job": job,
                "charge": charge,
                "multiplicity": multiplicity,
                "cores": cores,
                "mem": memory,
                "mode": mode,
                "input_atoms": list(inputs.structure.atoms),
            }
        )
        return MaterializedNativeInput(
            program=ProgramName.GAUSSIAN,
            main_input_name=main_input_name,
            files=(InputFile(name=main_input_name, content=content),),
            metadata=metadata,
        )

    @staticmethod
    def _execution_mode(inputs: ResolvedCalculationInputs, keyword_line: str) -> str:
        """Return the execution mode for these inputs: qst2/qst3/irc/standard.

        Named reactant/product slots select QST rendering (the route must
        carry the matching QST flavor); otherwise an IRC route selects
        single-structure IRC validation, and anything else is standard.
        No task enum exists — the mode is a rendering decision derived
        from input shape plus native route vocabulary.
        """
        from .named import parse_qst_route
        from .path import parse_irc_route

        slots = inputs.extra_structures
        reactant = slots.get("reactant")
        product = slots.get("product")
        has_named = (
            reactant is not None and len(reactant) > 0 and product is not None and len(product) > 0
        )
        if has_named:
            guess = slots.get("guess")
            has_guess = guess is not None and len(guess) > 0
            return parse_qst_route(keyword_line, has_guess=has_guess)
        try:
            parse_irc_route(keyword_line)
        except ValueError:
            return "standard"
        return "irc"

    @staticmethod
    def _render_qst_input(
        inputs: ResolvedCalculationInputs,
        mode: str,
        *,
        link0_lines: Any,
        cores: int,
        memory: str,
        keyword_line: str,
        job: str,
        charge: int,
        multiplicity: int,
        extra_section: str,
    ) -> str:
        """Render a QST2/QST3 input with reactant/product/guess specs."""
        from ...execution.atom_mapping import parse_atom_mapping, reorder_slot_to_reference
        from .named import render_qst_molecule_specs, validate_qst_slots

        slots = inputs.extra_structures
        reactant_set = slots.get("reactant")
        product_set = slots.get("product")
        guess_set = slots.get("guess")
        if reactant_set is None or len(reactant_set) == 0:
            raise ValueError("native_input_error: QST rendering requires a reactant structure")
        if product_set is None or len(product_set) == 0:
            raise ValueError("native_input_error: QST rendering requires a product structure")
        reactant = reactant_set[0]
        product = product_set[0]
        guess = guess_set[0] if guess_set is not None and len(guess_set) > 0 else None
        if mode == "qst2" and guess is not None:
            raise ValueError("native_input_error: QST2 route with a guess structure; use QST3")
        if mode == "qst3" and guess is None:
            raise ValueError("native_input_error: QST3 route without a guess structure")
        validate_qst_slots(
            reactant=reactant,
            product=product,
            guess=guess,
            charge=charge,
            multiplicity=multiplicity,
        )
        mapping = parse_atom_mapping(inputs.native.get("atom_mapping"))
        reference_atoms = tuple(reactant.atoms)
        product_atoms, product_coords = reorder_slot_to_reference(
            reference_atoms=reference_atoms,
            slot_atoms=tuple(product.atoms),
            slot_coords=tuple(tuple(point) for point in product.coordinates),
            mapping=mapping,
        )
        guess_atoms: Any = None
        guess_coords: Any = None
        if guess is not None:
            guess_atoms, guess_coords = reorder_slot_to_reference(
                reference_atoms=reference_atoms,
                slot_atoms=tuple(guess.atoms),
                slot_coords=tuple(tuple(point) for point in guess.coordinates),
                mapping=mapping,
            )
        specs = render_qst_molecule_specs(
            reactant_atoms=tuple(reactant.atoms),
            reactant_coords=tuple(tuple(point) for point in reactant.coordinates),
            product_atoms=product_atoms,
            product_coords=product_coords,
            guess_atoms=guess_atoms,
            guess_coords=guess_coords,
            charge=charge,
            multiplicity=multiplicity,
        )
        link0 = "".join(f"{line.rstrip()}\n" for line in link0_lines)
        return (
            f"{link0}%nprocshared={cores}\n"
            f"%mem={memory}\n"
            f"{keyword_line}\n"
            f"\n"
            f"{job}\n"
            f"\n"
            f"{specs}"
            f"\n"
            f"{extra_section}\n"
            f"\n"
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
        """Build the launch request for rendered native input.

        Parameters
        ----------
        materialized : MaterializedNativeInput
            Rendered native input from :meth:`materialize_native_input`.
        executable : str
            Resolved executable path or name, taken as given.
        work_dir : str
            Item work directory where the process runs.
        env : dict[str, str]
            Base environment; ``GAUSS_EXEDIR`` is added when *executable*
            is absolute.
        walltime_seconds : float | None
            Walltime limit passed through untouched.

        Returns
        -------
        NativeExecutionRequest
            Launch request with ``argv`` of ``[executable, <job>.gjf]`` and
            ``<job>.log``/``<job>.err`` capture files.
        """
        job = _job_from_materialized(materialized)
        main_name = os.path.basename(materialized.main_input_name)
        merged_env = dict(env)
        if os.path.isabs(executable):
            merged_env["GAUSS_EXEDIR"] = os.path.dirname(executable)
        return NativeExecutionRequest(
            executable=executable,
            argv=(executable, main_name),
            work_dir=work_dir,
            env=FrozenDict(merged_env),
            walltime_seconds=walltime_seconds,
            stdout_file=f"{job}.log",
            stderr_file=f"{job}.err",
            metadata=FrozenDict(
                {
                    "program": ProgramName.GAUSSIAN.value,
                    "adapter_version": ADAPTER_VERSION,
                    "job": job,
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
        """Parse native output files into parser facts.

        Parameters
        ----------
        work_dir : str
            Item work directory holding native output files.
        log_file_name : str
            Log file name inside *work_dir*.
        materialized : MaterializedNativeInput
            Rendered native input naming the ``.gjf``/``.chk`` files.

        Returns
        -------
        NativeResult
            Parser facts: termination, geometry, Hartree energies, true
            vibrational frequencies, produced files, and metadata. A missing
            or unreadable log yields a non-terminated result with geometry
            ``NONE`` and an error parser diagnostic.
        """
        log_path = os.path.join(work_dir, log_file_name)
        text = _parsing.read_log_text(log_path)
        if text is None:
            return NativeResult(
                program=ProgramName.GAUSSIAN,
                terminated_normally=False,
                geometry_output=GeometryOutput.NONE,
                final_geometry=None,
                energies_hartree=FrozenDict({}),
                frequencies_cm=(),
                native_metadata=FrozenDict(
                    {
                        "program": ProgramName.GAUSSIAN.value,
                        "parser_version": PARSER_VERSION,
                        "log_file": log_file_name,
                        "electronic_source": "absent",
                        "gibbs_source": "absent",
                        "gibbs_correction_source": "absent",
                    }
                ),
                produced_files=(),
                parser_diagnostics=(
                    Diagnostic(
                        code="native_parse_error",
                        message=(
                            "Gaussian log file "
                            f"{log_file_name!r} is missing or unreadable "
                            f"in {work_dir!r}"
                        ),
                        severity=DiagnosticSeverity.ERROR,
                    ),
                ),
                log_file_name=log_file_name,
            )
        energies, sources = _parsing.parse_energies(text)
        committed = _parsing.parse_frequencies(text)
        frequencies = _parsing.true_vibrational_modes(committed)
        mode = materialized.metadata.get("mode", "standard")
        if mode == "irc":
            return self._parse_irc_result(
                text,
                log_file_name=log_file_name,
                log_path=log_path,
                materialized=materialized,
                job=_job_from_materialized(materialized),
            )
        geometry = _parsing.parse_final_geometry(text)
        final_geometry: ParsedGeometry | None = None
        geometry_output = GeometryOutput.NONE
        if geometry is not None:
            atoms, coords = geometry
            final_geometry = ParsedGeometry(atoms=atoms, coordinates=coords)
            geometry_output = GeometryOutput.PRODUCED
        terminated = _parsing.check_termination(log_path)
        job = _job_from_materialized(materialized)
        input_name = os.path.basename(materialized.main_input_name)
        candidates = (
            (log_file_name, "native_output"),
            (input_name, "native_input"),
            (f"{job}.chk", "checkpoint"),
            (f"{job}.err", "stderr"),
        )
        produced: list[ProducedFile] = []
        for name, role in candidates:
            produced.append(
                ProducedFile(
                    name=name,
                    role=role,
                    size_bytes=_file_size(os.path.join(work_dir, name)),
                    checksum=None,
                )
            )
        metadata: dict[str, Any] = {
            "program": ProgramName.GAUSSIAN.value,
            "parser_version": PARSER_VERSION,
            "log_file": log_file_name,
            "electronic_source": sources.get("electronic", "absent"),
            "gibbs_source": sources.get("gibbs", "absent"),
            "gibbs_correction_source": sources.get("gibbs_correction", "absent"),
        }
        return NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=terminated,
            geometry_output=geometry_output,
            final_geometry=final_geometry,
            energies_hartree=FrozenDict(dict(energies)),
            frequencies_cm=frequencies,
            native_metadata=FrozenDict(metadata),
            produced_files=tuple(produced),
            parser_diagnostics=(),
            log_file_name=log_file_name,
        )

    def _parse_irc_result(
        self,
        text: str,
        *,
        log_file_name: str,
        log_path: str,
        materialized: MaterializedNativeInput,
        job: str,
    ) -> NativeResult:
        """Parse a Gaussian IRC log into path-endpoint facts.

        Endpoints come exclusively from the explicit direction banners in
        the log dialect; a missing direction is a parse error, never an
        inference.  The executor's path profile turns these facts into
        endpoint structures and fails the item when the set is partial.
        """
        from .path import irc_trajectory_facts, parse_irc_endpoints

        raw_atoms = materialized.metadata.get("input_atoms", [])
        atoms = tuple(str(symbol) for symbol in raw_atoms)
        endpoints = parse_irc_endpoints(text, atoms=atoms)
        facts = irc_trajectory_facts(text)
        input_name = os.path.basename(materialized.main_input_name)
        produced = [
            ProducedFile(name=log_file_name, role="native_output"),
            ProducedFile(name=input_name, role="native_input"),
            ProducedFile(name=f"{job}.chk", role="checkpoint"),
            ProducedFile(name=f"{job}.err", role="stderr"),
        ]
        metadata: dict[str, Any] = {
            "program": ProgramName.GAUSSIAN.value,
            "parser_version": PARSER_VERSION,
            "log_file": log_file_name,
            "mode": "irc",
            "trajectory_points": facts.get("points", {}),
            "trajectory_truncated": facts.get("truncated", False),
        }
        return NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=_parsing.check_termination(log_path),
            geometry_output=GeometryOutput.NONE,
            final_geometry=None,
            energies_hartree=FrozenDict(
                {
                    f"endpoint_{endpoint.direction}": endpoint.energy_hartree
                    for endpoint in endpoints
                    if endpoint.energy_hartree is not None
                }
            ),
            frequencies_cm=(),
            native_metadata=FrozenDict(metadata),
            produced_files=tuple(produced),
            parser_diagnostics=(),
            log_file_name=log_file_name,
            path_endpoints=endpoints,
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
            Item work directory holding produced files.
        run_relative_prefix : str
            Run-root-relative directory prefix for locators.
        native_result : NativeResult
            Parsed result listing produced files in discovery order.
        step_id : str
            Producing step id, recorded when non-empty.
        work_item_id : str
            Producing work item id, recorded when non-empty.
        subject_structure_id : str | None
            Structure the artifacts bind to, for subject matching.

        Returns
        -------
        ArtifactSet
            References for produced files present in *work_dir*, each with a
            ``sha256:<hex>`` checksum and a run-relative locator. Missing
            files are skipped silently.
        """
        records: list[ArtifactRef] = []
        for produced in native_result.produced_files:
            full_path = os.path.join(work_dir, produced.name)
            if not os.path.isfile(full_path):
                continue
            checksum = _sha256_checksum(full_path)
            if checksum is None:
                continue
            if run_relative_prefix:
                locator_path = f"{run_relative_prefix}/{produced.name}"
            else:
                locator_path = produced.name
            extension = os.path.splitext(produced.name)[1].lower()
            base = produced.name
            if work_item_id:
                artifact_id = f"{work_item_id}/{base}"
            else:
                artifact_id = base
            records.append(
                ArtifactRef(
                    id=artifact_id,
                    role=produced.role,
                    locator=ArtifactLocator.run_relative(locator_path),
                    checksum=checksum,
                    media_type=MEDIA_TYPES_BY_EXTENSION.get(extension),
                    program=ProgramName.GAUSSIAN.value,
                    producer_step_id=step_id or None,
                    producer_work_item_id=work_item_id or None,
                    subject_structure_id=subject_structure_id,
                    metadata=FrozenDict({}),
                )
            )
        return ArtifactSet(tuple(records))

    def environment_probe(self, executable: str) -> dict[str, Any]:
        """Return safely measurable program facts, possibly empty.

        Parameters
        ----------
        executable : str
            Resolved executable path or name; unused.

        Returns
        -------
        dict[str, Any]
            Empty mapping: no safe cheap probe exists, so file identity
            carries the environment digest.
        """
        del executable
        return {}
