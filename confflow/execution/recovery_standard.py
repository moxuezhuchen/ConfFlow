#!/usr/bin/env python3

"""V4 standard recovery policies: ``none`` and ``ts_rescue_scan``.

Recovery is explicit: a step declares a recovery profile, the executor
evaluates it after check failures, and any recovery execution is recorded
with diagnostics and provenance.  Rescue after an unconfirmed cancellation
never happens (the whole process boundary might still be live).

Policies never launch processes themselves; all native work goes through the
executor-supplied :class:`RescueDriver`, which keeps process control,
cancellation, and staging in one place.

Executor wiring contract
------------------------
- The ``ts_rescue_scan`` policy is constructed with the step's program
  adapter (``TsRescueScanPolicy(adapter)``): rendering constrained-scan and
  reoptimization inputs needs file-format knowledge that the frozen
  ``execute(context, driver)`` signature cannot receive otherwise.
- The executor merges the step's ``check_params`` (notably ``bond_atoms``)
  and the execution binding (``executable``, ``work_dir``, ``env``,
  ``walltime_seconds``) into ``RecoveryContext.params``; binding keys fall
  back to the adapter default executable, ``"rescue"``, an empty env, and no
  walltime.
- Modified scan inputs reuse the resolved calculation inputs with only the
  native keyword replaced (TS keyword rewritten to a constrained-scan
  keyword) and a Gaussian ``B a b F`` ModRedundant freeze directive
  appended; every other degree of freedom stays free and user freeze content
  is preserved.

Scan engine (compact port of the legacy rescue)
-----------------------------------------------
Coarse probes at ``r0 +/- k * coarse_step``, a fine grid around the peak,
peak selection by local maximum, then reoptimization with the original
keyword at the peak geometry.  Reoptimization is accepted only when drift,
RMSD, and (for freq keywords) imaginary-count validation pass.

Deliberate simplifications over the legacy scanner:

- Each scan point renders from the original input structure with the bond
  set to the target length; legacy chained each point onto the previous
  point's optimized geometry (an optimization, not a semantic).
- No terminal tables, marker files, or backup handling: the scan table is
  carried in the success diagnostic details, and lifecycle side effects
  belong to the executor.
- Only Gaussian constrained scans are supported (as in legacy); anything
  else declines with ``recovery_disabled_for_program``.
- At most one rescue attempt per work item: ``attempt >= 1`` declines with
  ``max_attempts_reached``.

Unbound policy instance
-----------------------
``RECOVERIES["ts_rescue_scan"]`` holds a policy constructed without an
adapter for registry wiring; it evaluates program gating from the failed
result alone and its ``execute`` declines (returns ``None``) until the
executor substitutes a step-bound ``TsRescueScanPolicy(adapter)``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

import numpy as np

from ..domain._immutable import FrozenDict
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.structure import Coordinates, StructureRecord
from .checks import CHECK_DEFAULTS
from .native import (
    GeometryOutput,
    MaterializedNativeInput,
    NativeExecutionResult,
    NativeResult,
    ProgramAdapter,
    ProgramName,
    ResolvedCalculationInputs,
)
from .recovery import RecoveryContext, RecoveryDecision, RecoveryExecution, RescueDriver

__all__ = [
    "NONE_RECOVERY_CONTRACT",
    "RECOVERIES",
    "SCAN_COARSE_STEP",
    "SCAN_FINE_HALF_WINDOW",
    "SCAN_FINE_STEP",
    "SCAN_MAX_STEPS",
    "SCAN_UPHILL_LIMIT",
    "TS_RESCUE_SCAN_CONTRACT",
    "NoneRecoveryPolicy",
    "TsRescueScanPolicy",
    "ensure_gaussian_modredundant_keyword",
    "make_scan_keyword_from_ts_keyword",
    "parse_ts_bond_atoms",
]

#: Contract versions folded into step digests; must match the registry specs.
NONE_RECOVERY_CONTRACT = "confflow.contract.recovery.none.v1"
TS_RESCUE_SCAN_CONTRACT = "confflow.contract.recovery.ts_rescue_scan.v1"

#: Diagnostic code for a successful rescue execution.
TS_RESCUE_SUCCEEDED_CODE = "ts_rescue_scan"

#: Scan stepping defaults (ported values from the legacy scan parameters).
SCAN_COARSE_STEP = 0.1
SCAN_FINE_STEP = 0.02
SCAN_MAX_STEPS = 60
SCAN_FINE_HALF_WINDOW = 0.1
SCAN_UPHILL_LIMIT = 10

#: Check-failure reasons that indicate a transition-state-type failure and
#: therefore a scan-worthy rescue candidate.
TS_RECOVERABLE_REASONS = frozenset(
    {
        "abnormal_termination",
        "geometry_missing",
        "frequencies_missing",
        "frequencies_missing_for_count",
        "imaginary_count_mismatch",
        "rmsd_exceeded",
        "bond_drift_exceeded",
        "parse_error",
        "execution_error",
    }
)

#: Native failure categories that indicate a scan-worthy rescue candidate.
RECOVERABLE_NATIVE_CODES = frozenset(
    {"native_parse_error", "native_execution_error", "scientific_check_error"}
)

#: Cancellation signals that always veto rescue.
CANCELLATION_SIGNALS = frozenset({"cancellation_error", "cancellation_unconfirmed"})

#: Option items stripped from an ``opt(...)`` group when rewriting a TS
#: keyword into a scan keyword (ported from the legacy keyword rewrite).
_REMOVE_OPT_ITEMS = frozenset({"calcfc", "tight", "ts", "noeigentest", "rcfc", "readfc"})

_OPT_PAREN_RE = re.compile(r"(?i)\bopt\s*(=)?\s*\(([^)]*)\)")
_OPT_ASSIGN_RE = re.compile(r"(?i)\bopt\s*=\s*([^\s()]+)")
_OPT_BARE_RE = re.compile(r"(?i)\bopt\b")
_FREQ_RE = re.compile(r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?")
_MODREDUNDANT_RE = re.compile(r"(?i)\bmodredundant\b")


def parse_ts_bond_atoms(value: Any) -> tuple[int, int] | None:
    """Parse the TS bond atom pair (1-based indices).

    Accepts a two-element list/tuple of integers or a string containing at
    least two positive integers (digit extraction).  Returns ``None`` when
    the value is missing, malformed, or names the same atom twice.

    (Ported from the legacy analysis helper; the small numeric helpers in
    this module are deliberately duplicated rather than imported from the
    check layer so recovery never depends on check wiring.)
    """
    if value is None:
        return None
    numbers: list[int] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            try:
                numbers.append(int(item))
            except (TypeError, ValueError):
                continue
    else:
        for match in re.findall(r"\d+", str(value)):
            try:
                numbers.append(int(match))
            except (TypeError, ValueError):
                continue
    if len(numbers) < 2:
        return None
    first, second = numbers[0], numbers[1]
    if first <= 0 or second <= 0 or first == second:
        return None
    return first, second


def keyword_requests_freq(keyword: str) -> bool:
    """Return whether *keyword* explicitly requests a frequency calculation."""
    if not keyword.strip():
        return False
    return _FREQ_RE.search(keyword) is not None


def make_scan_keyword_from_ts_keyword(keyword: str) -> str:
    """Rewrite a TS keyword line into one suitable for a scan job.

    TS-only optimizer items are dropped from ``opt(...)`` groups and any
    frequency request is removed; the result still names the optimization so
    the caller can add the ModRedundant directive separately.

    (Ported from the legacy keyword rewrite; ``confflow.core`` is a gated
    reference and must never be imported from execution code.)
    """
    text = (keyword or "").strip()
    if not text:
        return ""

    def _rewrite_opt_group(match: re.Match[str]) -> str:
        inner = match.group(1) or ""
        has_equal = "=" in match.group(0)
        kept: list[str] = []
        for item in (part.strip() for part in inner.split(",") if part.strip()):
            if item.split("=")[0].strip().lower() in _REMOVE_OPT_ITEMS:
                continue
            kept.append(item)
        if not kept:
            return "opt"
        return f"opt{'=' if has_equal else ''}({','.join(kept)})"

    text = re.sub(r"(?i)\bopt\s*(?:=\s*)?\(([^)]*)\)", _rewrite_opt_group, text)
    text = _FREQ_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def ensure_gaussian_modredundant_keyword(keyword: str) -> str:
    """Return a Gaussian keyword line that enables ``ModRedundant``.

    Preserves existing ``opt`` items and never duplicates the directive.

    (Ported from the legacy keyword rewrite.)
    """
    text = (keyword or "").strip()
    if not text or _MODREDUNDANT_RE.search(text):
        return text

    def _paren_repl(match: re.Match[str]) -> str:
        has_equal = match.group(1) == "="
        items = [item.strip() for item in match.group(2).split(",") if item.strip()]
        if not any(item.split("=")[0].strip().lower() == "modredundant" for item in items):
            items.append("modredundant")
        return f"opt{'=' if has_equal else ''}({','.join(items)})"

    updated, count = _OPT_PAREN_RE.subn(_paren_repl, text, count=1)
    if count:
        return updated

    def _assign_repl(match: re.Match[str]) -> str:
        return f"opt=({match.group(1).strip()},modredundant)"

    updated, count = _OPT_ASSIGN_RE.subn(_assign_repl, text, count=1)
    if count:
        return updated
    if _OPT_BARE_RE.search(text):
        return _OPT_BARE_RE.sub("opt=modredundant", text, count=1)
    return f"opt=modredundant {text}".strip()


def _ensure_has_opt(keyword: str) -> str:
    """Ensure *keyword* names an optimization."""
    text = (keyword or "").strip()
    if not text or _OPT_BARE_RE.search(text):
        return text
    return f"opt {text}".strip()


def scan_keyword_for_rescue(original_keyword: str) -> str:
    """Derive the constrained-scan keyword for a rescue, or ``""``."""
    base = make_scan_keyword_from_ts_keyword(original_keyword)
    base = _MODREDUNDANT_RE.sub(" ", base)
    base = _FREQ_RE.sub(" ", base)
    base = re.sub(r"\s+", " ", base).strip()
    base = _ensure_has_opt(base)
    if not base:
        return ""
    return ensure_gaussian_modredundant_keyword(base)


def _coords_to_array(coordinates: Coordinates) -> np.ndarray | None:
    """Return an ``(N, 3)`` array for *coordinates*, or ``None``."""
    try:
        array = np.array(coordinates, dtype=float)
    except (TypeError, ValueError):
        return None
    if array.ndim != 2 or array.shape[1] != 3 or array.shape[0] == 0:
        return None
    if not np.all(np.isfinite(array)):
        return None
    return array


def _kabsch_aligned_rmsd(first: np.ndarray, second: np.ndarray) -> float | None:
    """Return the reflection-free Kabsch-aligned RMSD, or ``None``."""
    if first.shape != second.shape or first.shape[0] == 0:
        return None
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        return None
    first_centered = first - first.mean(axis=0)
    second_centered = second - second.mean(axis=0)
    covariance = first_centered.T @ second_centered
    left, _singular, right_transposed = np.linalg.svd(covariance)
    correction = np.eye(3)
    det = float(np.linalg.det(left @ right_transposed))
    correction[-1, -1] = 1.0 if det >= 0.0 else -1.0
    aligned = first_centered @ (left @ correction @ right_transposed)
    return float(np.sqrt(np.mean(np.sum((second_centered - aligned) ** 2, axis=1))))


def _bond_length_of(coordinates: Coordinates, atom_a: int, atom_b: int) -> float | None:
    """Return the ``atom_a``-``atom_b`` distance (1-based), or ``None``."""
    count = len(coordinates)
    if atom_a < 1 or atom_b < 1 or atom_a > count or atom_b > count or atom_a == atom_b:
        return None
    first = coordinates[atom_a - 1]
    second = coordinates[atom_b - 1]
    distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)))
    return distance if math.isfinite(distance) else None


def _set_bond_length(
    coordinates: Coordinates, atom_a: int, atom_b: int, target: float
) -> Coordinates | None:
    """Return coordinates with the bond set to *target* (moves ``atom_b`` only).

    Returns ``None`` for out-of-range indices or a degenerate bond vector.
    """
    points = [tuple(float(v) for v in point) for point in coordinates]
    count = len(points)
    if atom_a < 1 or atom_b < 1 or atom_a > count or atom_b > count or atom_a == atom_b:
        return None
    if not math.isfinite(target) or target <= 0.0:
        return None
    origin = points[atom_a - 1]
    moving = points[atom_b - 1]
    delta = [b - a for a, b in zip(moving, origin)]
    length = math.sqrt(sum(component * component for component in delta))
    if not math.isfinite(length) or length <= 1e-10:
        return None
    unit = [component / length for component in delta]
    placed = tuple(origin[i] + unit[i] * float(target) for i in range(3))
    points[atom_b - 1] = (placed[0], placed[1], placed[2])
    return tuple(points)


def _find_local_max(
    points: list[tuple[float, float, Coordinates]],
) -> tuple[float, float, Coordinates] | None:
    """Return the highest-energy interior local maximum, or ``None``."""
    if len(points) < 3:
        return None
    ordered = sorted(points, key=lambda item: item[0])
    maxima: list[tuple[float, float, Coordinates]] = []
    for index in range(1, len(ordered) - 1):
        _r_prev, energy_prev, _c_prev = ordered[index - 1]
        radius, energy, coords = ordered[index]
        _r_next, energy_next, _c_next = ordered[index + 1]
        if energy_prev < energy and energy > energy_next:
            maxima.append((radius, energy, coords))
    if not maxima:
        return None
    return max(maxima, key=lambda item: item[1])


def _count_imaginary(frequencies: tuple[float, ...]) -> int | None:
    """Count imaginary modes with the 10 cm^-1 noise floor, or ``None``."""
    if not frequencies:
        return None
    count = 0
    for value in frequencies:
        try:
            frequency = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(frequency) or abs(frequency) <= 10.0:
            continue
        if frequency < 0.0:
            count += 1
    return count


def _scan_energy(native_result: NativeResult) -> float | None:
    """Return the scan-point energy (Gibbs preferred), or ``None``."""
    gibbs = native_result.energies_hartree.get("gibbs")
    if gibbs is not None:
        return float(gibbs)
    electronic = native_result.energies_hartree.get("electronic")
    return float(electronic) if electronic is not None else None


def _positive_float(value: Any, default: float) -> float | None:
    """Coerce a positive scan parameter, or ``None`` when invalid."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


class NoneRecoveryPolicy:
    """The ``none`` recovery profile: failures are reported as-is."""

    @property
    def name(self) -> str:
        """Return the recovery profile name."""
        return "none"

    @property
    def contract_version(self) -> str:
        """Return the recovery contract version (folded into digests)."""
        return NONE_RECOVERY_CONTRACT

    def evaluate(self, context: RecoveryContext) -> RecoveryDecision:
        """Decline recovery unconditionally."""
        return RecoveryDecision(attempt=False, reason="recovery_disabled")

    def execute(self, context: RecoveryContext, driver: RescueDriver) -> RecoveryExecution | None:
        """Never execute recovery; always return ``None``."""
        return None


class TsRescueScanPolicy:
    """Bond-scan rescue for a failed Gaussian transition-state calculation.

    Parameters
    ----------
    adapter : ProgramAdapter | None
        The step's program adapter, used to render constrained-scan and
        reoptimization inputs.  Wired by the executor at construction time
        because the frozen ``execute`` signature carries no adapter handle.
        ``None`` leaves an unbound instance whose ``execute`` always
        declines; program gating then relies on the failed result alone.
    """

    def __init__(self, adapter: ProgramAdapter | None = None) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        """Return the recovery profile name."""
        return "ts_rescue_scan"

    @property
    def contract_version(self) -> str:
        """Return the recovery contract version (folded into digests)."""
        return TS_RESCUE_SCAN_CONTRACT

    def _bond_pair(self, context: RecoveryContext) -> tuple[int, int] | None:
        """Resolve the scan bond from merged params (``bond_atoms`` first)."""
        raw = context.params.get("bond_atoms", context.params.get("atoms", None))
        return parse_ts_bond_atoms(raw)

    def evaluate(self, context: RecoveryContext) -> RecoveryDecision:
        """Decide whether a rescue scan should be attempted."""
        if not context.cancellation_confirmed:
            return RecoveryDecision(attempt=False, reason="cancellation_unconfirmed")
        if context.attempt >= 1:
            return RecoveryDecision(
                attempt=False,
                reason="max_attempts_reached",
                details=FrozenDict({"attempt": context.attempt}),
            )
        failed = context.failed_native_result
        program = failed.program.value if failed is not None else None
        adapter_program = self._adapter.program_name.value if self._adapter is not None else None
        adapter_is_gaussian = (
            self._adapter is not None and self._adapter.program_name is ProgramName.GAUSSIAN
        )
        failed_is_gaussian = failed is not None and failed.program is ProgramName.GAUSSIAN
        if failed is None:
            program_ok = adapter_is_gaussian
        elif self._adapter is None:
            program_ok = failed_is_gaussian
        else:
            program_ok = adapter_is_gaussian and failed_is_gaussian
        if not program_ok:
            return RecoveryDecision(
                attempt=False,
                reason="recovery_disabled_for_program",
                details=FrozenDict({"adapter_program": adapter_program, "failed_program": program}),
            )
        pair = self._bond_pair(context)
        if pair is None:
            return RecoveryDecision(attempt=False, reason="bond_atoms_missing")
        codes = {diagnostic.code for diagnostic in context.failure_diagnostics}
        reasons = {
            str(diagnostic.details.get("reason")) for diagnostic in context.failure_diagnostics
        }
        if codes & CANCELLATION_SIGNALS or reasons & CANCELLATION_SIGNALS:
            return RecoveryDecision(attempt=False, reason="failure_not_recoverable")
        recoverable = bool(
            (reasons & set(TS_RECOVERABLE_REASONS))
            or (codes & set(RECOVERABLE_NATIVE_CODES))
            or (failed is not None and not failed.terminated_normally)
        )
        if not recoverable:
            return RecoveryDecision(attempt=False, reason="failure_not_recoverable")
        return RecoveryDecision(
            attempt=True,
            reason="ts_failure_recoverable",
            details=FrozenDict({"bond_atoms": list(pair), "failed_program": program}),
        )

    def _request_kwargs(self, context: RecoveryContext) -> dict[str, Any] | None:
        """Build launch-request fields from merged binding params."""
        adapter = self._adapter
        if adapter is None:
            return None
        executable = context.params.get("executable", adapter.default_executable)
        if not isinstance(executable, str) or not executable.strip():
            executable = adapter.default_executable
        staged = context.params.get("work_dir", "rescue")
        if not isinstance(staged, str) or not staged.strip():
            return None
        env_raw = context.params.get("env", {})
        if isinstance(env_raw, Mapping):
            env = {str(key): str(value) for key, value in dict(env_raw).items()}
        elif isinstance(env_raw, dict):
            env = {str(key): str(value) for key, value in env_raw.items()}
        else:
            env = {}
        walltime = context.params.get("walltime_seconds", None)
        if walltime is not None:
            try:
                walltime_value = float(walltime)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                walltime_value = None
            if (
                walltime_value is None
                or not math.isfinite(walltime_value)
                or (walltime_value <= 0.0)
            ):
                walltime = None
            else:
                walltime = walltime_value
        return {
            "executable": executable,
            "work_dir": staged,
            "env": env,
            "walltime_seconds": walltime,
        }

    def _modified_inputs(
        self,
        context: RecoveryContext,
        coordinates: Coordinates,
        native_keyword: str,
        extra_directives: tuple[str, ...] = (),
    ) -> ResolvedCalculationInputs | None:
        """Render-ready inputs with replaced geometry and native keyword."""
        inputs = context.inputs
        try:
            transient = StructureRecord(
                id=inputs.structure.id,
                atoms=tuple(inputs.structure.atoms),
                coordinates=tuple(coordinates),
                charge=inputs.charge if inputs.charge is not None else (inputs.structure.charge),
                multiplicity=(
                    inputs.multiplicity
                    if inputs.multiplicity is not None
                    else (inputs.structure.multiplicity)
                ),
                parent_ids=tuple(inputs.structure.parent_ids),
                lineage_root_id=inputs.structure.lineage_root_id,
                group_key=inputs.structure.group_key,
                metadata=FrozenDict({}),
            )
        except Exception:
            return None
        native_map = dict(inputs.native)
        native_map["keyword"] = native_keyword
        if extra_directives:
            existing = native_map.get("gaussian_modredundant", [])
            if isinstance(existing, (list, tuple)):
                directives = [str(item).strip() for item in existing if str(item).strip()]
            elif existing is None:
                directives = []
            else:
                directives = [part.strip() for part in str(existing).splitlines() if part.strip()]
            for directive in extra_directives:
                if directive not in directives:
                    directives.append(directive)
            native_map["gaussian_modredundant"] = directives
        try:
            return ResolvedCalculationInputs(
                structure=transient,
                charge=inputs.charge,
                multiplicity=inputs.multiplicity,
                freeze=inputs.freeze,
                resources=inputs.resources,
                native=FrozenDict(native_map),
                checkpoints=tuple(inputs.checkpoints),
                extra_structures=inputs.extra_structures,
                step_id=inputs.step_id,
                work_item_id=inputs.work_item_id,
                logical_key=inputs.logical_key,
            )
        except Exception:
            return None

    def _run_point(
        self,
        context: RecoveryContext,
        driver: RescueDriver,
        coordinates: Coordinates,
        native_keyword: str,
        extra_directives: tuple[str, ...],
        stage: str,
        request_kwargs: Mapping[str, Any],
    ) -> tuple[NativeExecutionResult, NativeResult] | None:
        """Render, launch, and parse one native point; ``None`` on failure."""
        adapter = self._adapter
        if adapter is None:
            return None
        modified = self._modified_inputs(context, coordinates, native_keyword, extra_directives)
        if modified is None:
            return None
        try:
            materialized = adapter.materialize_native_input(modified)
            request = adapter.build_execution_request(
                materialized,
                executable=str(request_kwargs["executable"]),
                work_dir=str(request_kwargs["work_dir"]),
                env=dict(request_kwargs["env"]),
                walltime_seconds=request_kwargs["walltime_seconds"],
            )
        except Exception:
            return None
        try:
            execution_result, parsed = driver.run_native(materialized, request, stage=stage)
        except Exception:
            return None
        if parsed is None:
            return None
        return execution_result, parsed

    def execute(self, context: RecoveryContext, driver: RescueDriver) -> RecoveryExecution | None:
        """Run the constrained scan plus reoptimization; ``None`` on failure."""
        if self._adapter is None:
            return None
        pair = self._bond_pair(context)
        if pair is None:
            return None
        atom_a, atom_b = pair
        inputs = context.inputs
        base_coordinates: Coordinates = tuple(inputs.structure.coordinates)
        original_keyword = str(inputs.native.get("keyword") or "")
        scan_keyword = scan_keyword_for_rescue(original_keyword)
        if not scan_keyword:
            return None
        radius_initial = _bond_length_of(base_coordinates, atom_a, atom_b)
        if radius_initial is None:
            return None

        params = context.params
        coarse_step = _positive_float(params.get("scan_coarse_step", SCAN_COARSE_STEP), 0.0)
        fine_step = _positive_float(params.get("scan_fine_step", SCAN_FINE_STEP), 0.0)
        half_window = _positive_float(
            params.get("scan_fine_half_window", SCAN_FINE_HALF_WINDOW), 0.0
        )
        try:
            max_steps = int(params.get("scan_max_steps", SCAN_MAX_STEPS))
            uphill_limit = int(params.get("scan_uphill_limit", SCAN_UPHILL_LIMIT))
        except (TypeError, ValueError):
            return None
        if (
            coarse_step is None
            or fine_step is None
            or half_window is None
            or max_steps <= 0
            or uphill_limit <= 0
        ):
            return None
        try:
            coarse_k_max = max(1, min(int(round(1.0 / coarse_step)), 10))
        except (ValueError, ZeroDivisionError, OverflowError):
            coarse_k_max = 10

        request_kwargs = self._request_kwargs(context)
        if request_kwargs is None:
            return None
        freeze_directive = f"B {atom_a} {atom_b} F"

        def _scan_point(target: float) -> tuple[float, Coordinates] | None:
            adjusted = _set_bond_length(base_coordinates, atom_a, atom_b, target)
            if adjusted is None:
                return None
            outcome = self._run_point(
                context,
                driver,
                adjusted,
                scan_keyword,
                (freeze_directive,),
                f"rescue-scan-r{target:.3f}",
                request_kwargs,
            )
            if outcome is None:
                return None
            _execution_result, parsed = outcome
            energy = _scan_energy(parsed)
            if energy is None:
                return None
            if parsed.geometry_output is GeometryOutput.PRODUCED and (
                parsed.final_geometry is not None
            ):
                point_coords: Coordinates = tuple(parsed.final_geometry.coordinates)
            else:
                point_coords = adjusted
            return energy, point_coords

        initial = _scan_point(radius_initial)
        if initial is None:
            return None
        energy_0, coords_0 = initial
        points: list[tuple[float, float, Coordinates]] = [(radius_initial, energy_0, coords_0)]
        table: list[tuple[float, float, str]] = [(radius_initial, energy_0, "initial")]

        probes: dict[int, tuple[float, Coordinates] | None] = {}
        for direction in (+1, -1):
            probe = _scan_point(radius_initial + direction * coarse_step)
            probes[direction] = probe
            if probe is not None:
                energy, coords = probe
                points.append((radius_initial + direction * coarse_step, energy, coords))
                table.append((radius_initial + direction * coarse_step, energy, "coarse"))
        energy_minus = probes[-1][0] if probes[-1] is not None else None
        energy_plus = probes[+1][0] if probes[+1] is not None else None
        direct_fine = (
            energy_minus is not None
            and energy_plus is not None
            and energy_minus < energy_0
            and energy_plus < energy_0
        )

        step_limit = min(max_steps, coarse_k_max)
        if not direct_fine:
            for direction in (+1, -1):
                first = probes[direction]
                if first is None:
                    continue
                previous = first[0]
                best = previous
                stale = 0
                downs = 0
                rising = True
                for step in range(2, step_limit + 1):
                    radius = radius_initial + direction * coarse_step * step
                    outcome_point = _scan_point(radius)
                    if outcome_point is None:
                        stale += 1
                        rising = False
                        if stale >= uphill_limit:
                            break
                        continue
                    energy, coords = outcome_point
                    points.append((radius, energy, coords))
                    table.append((radius, energy, "coarse"))
                    if energy < previous:
                        downs += 1
                        rising = False
                    else:
                        downs = 0
                    if downs >= 2:
                        rising = False
                        break
                    previous = energy
                    if energy > best:
                        best = energy
                        stale = 0
                    else:
                        stale += 1
                        if stale >= uphill_limit:
                            break
                if rising and step_limit >= 2 and len(points) >= 3:
                    return None

        coarse_peak = _find_local_max(points)
        if coarse_peak is None:
            if direct_fine:
                coarse_peak = (radius_initial, energy_0, coords_0)
            else:
                return None
        radius_peak = coarse_peak[0]
        center = radius_initial if direct_fine else radius_peak
        left = center - half_window
        right = center + half_window
        fine_count = max(2, int(round((right - left) / fine_step)))
        fine_points: list[tuple[float, float, Coordinates]] = []
        for index in range(fine_count + 1):
            radius = left + fine_step * index
            outcome_point = _scan_point(radius)
            if outcome_point is None:
                continue
            energy, coords = outcome_point
            fine_points.append((radius, energy, coords))
            table.append((radius, energy, "fine"))
        fine_peak = _find_local_max(fine_points)
        if fine_peak is None:
            if fine_points:
                fine_peak = max(fine_points, key=lambda item: item[1])
            else:
                return None
        radius_best, _energy_best, coords_best = fine_peak

        reopt = self._run_point(
            context,
            driver,
            coords_best,
            original_keyword or scan_keyword,
            (),
            "rescue-reopt",
            request_kwargs,
        )
        if reopt is None:
            return None
        reopt_execution, reopt_parsed = reopt
        if not reopt_parsed.terminated_normally:
            return None
        if reopt_parsed.geometry_output is not GeometryOutput.PRODUCED or (
            reopt_parsed.final_geometry is None
        ):
            return None
        final_coordinates: Coordinates = tuple(reopt_parsed.final_geometry.coordinates)

        rmsd_default = float(CHECK_DEFAULTS["max_rmsd_from_input"]["threshold_angstrom"])
        drift_default = float(CHECK_DEFAULTS["bond_drift"]["threshold_angstrom"])
        try:
            rmsd_threshold = float(params.get("rmsd_threshold_angstrom", rmsd_default))
            drift_threshold = float(params.get("bond_drift_threshold_angstrom", drift_default))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(rmsd_threshold) or rmsd_threshold < 0.0:
            return None
        if not math.isfinite(drift_threshold) or drift_threshold < 0.0:
            return None

        peak_array = _coords_to_array(coords_best)
        final_array = _coords_to_array(final_coordinates)
        if peak_array is None or final_array is None or peak_array.shape != final_array.shape:
            return None
        rmsd = _kabsch_aligned_rmsd(final_array, peak_array)
        if rmsd is None or rmsd > rmsd_threshold:
            return None
        if not keyword_requests_freq(original_keyword):
            radius_peak_len = _bond_length_of(coords_best, atom_a, atom_b)
            radius_final = _bond_length_of(final_coordinates, atom_a, atom_b)
            if radius_peak_len is None or radius_final is None:
                return None
            if abs(radius_final - radius_peak_len) > drift_threshold:
                return None
        else:
            imaginary = _count_imaginary(tuple(reopt_parsed.frequencies_cm))
            if imaginary != 1:
                return None
        if _scan_energy(reopt_parsed) is None:
            return None

        try:
            reopt_inputs = self._modified_inputs(
                context, coords_best, original_keyword or scan_keyword
            )
            adapter = self._adapter
            if reopt_inputs is None or adapter is None:
                return None
            reopt_materialized: MaterializedNativeInput = adapter.materialize_native_input(
                reopt_inputs
            )
        except Exception:
            return None
        scan_rows = [
            {"radius_angstrom": radius, "energy_hartree": energy, "stage": stage}
            for radius, energy, stage in sorted(table, key=lambda row: row[0])
        ]
        radius_final_len = _bond_length_of(final_coordinates, atom_a, atom_b)
        diagnostic = Diagnostic(
            code=TS_RESCUE_SUCCEEDED_CODE,
            message=(
                f"TS rescue scan succeeded: peak bond {radius_best:.3f} A, "
                f"final bond "
                f"{radius_final_len:.3f} A"
                if radius_final_len is not None
                else f"TS rescue scan succeeded: peak bond {radius_best:.3f} A"
            ),
            severity=DiagnosticSeverity.INFO,
            step_id=context.step_id,
            work_item_id=context.work_item_id,
            logical_key=context.logical_key,
            details=FrozenDict(
                {
                    "reason": "rescued_by_scan",
                    "bond_atoms": [atom_a, atom_b],
                    "peak_bond_length_angstrom": float(radius_best),
                    "final_bond_length_angstrom": radius_final_len,
                    "rmsd_angstrom": rmsd,
                    "scan_points": scan_rows,
                }
            ),
        )
        return RecoveryExecution(
            native_result=reopt_parsed,
            execution_result=reopt_execution,
            materialized=reopt_materialized,
            diagnostics=(diagnostic,),
            metadata=FrozenDict({"rescued_by_scan": True, "scan_peak_bond": float(radius_best)}),
        )


#: Recovery policy instances keyed by profile name, for the executor to
#: consume and wire into the capability registry.  The scan entry is an
#: unbound instance (no adapter); the executor substitutes a step-bound
#: ``TsRescueScanPolicy(adapter)`` before executing rescue work.
RECOVERIES: dict[str, Any] = {
    "none": NoneRecoveryPolicy(),
    "ts_rescue_scan": TsRescueScanPolicy(),
}
