#!/usr/bin/env python3

"""Structured calculation model (RFC §18).

Implements the structured theory representation under ``params.theory`` for
calculation steps in Workflow V3:
* Reserved sub-namespace ``params.theory`` (program/task/method/basis/dispersion/solvent).
* Compiler from structured theory to target program keyword.
* Consistency validator between structured fields and raw keyword escape hatch.
* Program capability metadata and task normalization (preserving opt_freq as atomic).
* Deterministic editor metadata roundtrip.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Literal

ProgramName = Literal["g16", "orca"]
TaskName = Literal["opt", "sp", "freq", "opt_freq", "ts"]

__all__ = [
    "PROGRAM_CAPABILITIES",
    "TheorySpec",
    "compile_theory_keyword",
    "editor_metadata_to_theory",
    "normalize_program_name",
    "normalize_solvent",
    "normalize_task_name",
    "program_supports_task",
    "theory_to_editor_metadata",
    "validate_theory_keyword_consistency",
]

_THEORY_FIELDS = frozenset({"program", "task", "method", "basis", "dispersion", "solvent"})

_PROGRAM_ALIASES: dict[str, ProgramName] = {
    "1": "g16",
    "g16": "g16",
    "gaussian": "g16",
    "gau": "g16",
    "g09": "g16",
    "g03": "g16",
    "2": "orca",
    "orca": "orca",
}

_TASK_ALIASES: dict[str, TaskName] = {
    "0": "opt",
    "opt": "opt",
    "optimize": "opt",
    "1": "sp",
    "sp": "sp",
    "energy": "sp",
    "single_point": "sp",
    "singlepoint": "sp",
    "2": "freq",
    "freq": "freq",
    "frequency": "freq",
    "3": "opt_freq",
    "opt_freq": "opt_freq",
    "optfreq": "opt_freq",
    "opt+freq": "opt_freq",
    "opt-freq": "opt_freq",
    "opt_frequency": "opt_freq",
    "4": "ts",
    "ts": "ts",
    "transition_state": "ts",
    "transitionstate": "ts",
}

PROGRAM_CAPABILITIES: dict[str, dict[str, Any]] = {
    "g16": {
        "program": "g16",
        "name": "Gaussian",
        "tasks": ("opt", "sp", "freq", "opt_freq", "ts"),
        "supports_blocks_dict": False,
        "dispersion_models": ("d3bj", "d3", "d4"),
        "solvent_models": ("smd", "pcm", "cpcm"),
    },
    "orca": {
        "program": "orca",
        "name": "ORCA",
        "tasks": ("opt", "sp", "freq", "opt_freq", "ts"),
        "supports_blocks_dict": True,
        "dispersion_models": ("d3bj", "d3zero", "d4"),
        "solvent_models": ("cpcm", "smd"),
    },
}


def normalize_program_name(value: Any) -> ProgramName:
    """Normalize program name or alias to canonical 'g16' or 'orca'."""
    raw = str(value).strip().lower()
    if raw in _PROGRAM_ALIASES:
        return _PROGRAM_ALIASES[raw]
    raise ValueError(f"Unsupported calc program: {value}")


def normalize_task_name(value: Any) -> TaskName:
    """Normalize task name or alias to canonical TaskName.

    Preserves 'opt_freq' as atomic.
    """
    raw = str(value).strip().lower()
    if raw in _TASK_ALIASES:
        return _TASK_ALIASES[raw]
    raise ValueError(f"Unsupported calc task: {value}")


def program_supports_task(program: str, task: str) -> bool:
    """Return True if the specified program supports the task."""
    try:
        norm_prog = normalize_program_name(program)
        norm_task = normalize_task_name(task)
    except ValueError:
        return False
    caps = PROGRAM_CAPABILITIES.get(norm_prog)
    return caps is not None and norm_task in caps["tasks"]


def normalize_solvent(
    solvent: str | dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Parse solvent specification into (model, solvent_name).

    Supports:
    * 'water' -> (None, 'water')
    * 'SMD(water)' -> ('smd', 'water')
    * 'CPCM(water)' -> ('cpcm', 'water')
    * {'model': 'smd', 'solvent': 'water'} -> ('smd', 'water')
    """
    if solvent is None:
        return None, None
    if isinstance(solvent, dict):
        model = solvent.get("model")
        name = solvent.get("solvent") or solvent.get("name")
        return (
            str(model).strip().lower() if model else None,
            str(name).strip().lower() if name else None,
        )
    text = str(solvent).strip()
    if not text:
        return None, None
    match = re.match(r"^([A-Za-z0-9_-]+)\s*\(\s*([^)]+)\s*\)$", text)
    if match:
        return match.group(1).lower(), match.group(2).strip().lower()
    return None, text.lower()


@dataclass(frozen=True)
class TheorySpec:
    """Structured calculation theory specification (RFC §18)."""

    program: ProgramName | None = None
    task: TaskName | None = None
    method: str | None = None
    basis: str | None = None
    dispersion: str | None = None
    solvent: str | dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.program is not None:
            norm_prog = normalize_program_name(self.program)
            object.__setattr__(self, "program", norm_prog)
        if self.task is not None:
            norm_task = normalize_task_name(self.task)
            object.__setattr__(self, "task", norm_task)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, TheorySpec):
            return False
        return (
            self.program == other.program
            and self.task == other.task
            and self.method == other.method
            and self.basis == other.basis
            and self.dispersion == other.dispersion
            and self.solvent == other.solvent
        )

    def __hash__(self) -> int:
        solv_repr = (
            tuple(sorted((str(k), str(v)) for k, v in self.solvent.items()))
            if isinstance(self.solvent, dict)
            else self.solvent
        )
        return hash((self.program, self.task, self.method, self.basis, self.dispersion, solv_repr))

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable canonical dictionary."""
        out: dict[str, Any] = {}
        if self.program is not None:
            out["program"] = self.program
        if self.task is not None:
            out["task"] = self.task
        if self.method is not None:
            out["method"] = self.method
        if self.basis is not None:
            out["basis"] = self.basis
        if self.dispersion is not None:
            out["dispersion"] = self.dispersion
        if self.solvent is not None:
            out["solvent"] = (
                copy.deepcopy(self.solvent) if isinstance(self.solvent, dict) else self.solvent
            )
        return out

    @classmethod
    def from_dict(cls, data: Any) -> TheorySpec:
        """Construct from dictionary or string representation."""
        if isinstance(data, TheorySpec):
            return data
        if isinstance(data, str):
            text = data.strip()
            if "/" in text:
                m, b = text.split("/", 1)
                return cls(method=m.strip(), basis=b.strip())
            return cls(method=text)
        if not isinstance(data, dict):
            raise ValueError(f"theory must be a mapping, got {type(data).__name__}")
        unknown = sorted(set(data.keys()) - _THEORY_FIELDS)
        if unknown:
            raise ValueError(
                f"Unknown theory field(s): {', '.join(map(repr, unknown))}; "
                f"supported fields are: {', '.join(sorted(_THEORY_FIELDS))}"
            )
        program = data.get("program")
        task = data.get("task")
        method = data.get("method")
        basis = data.get("basis")
        dispersion = data.get("dispersion")
        solvent = data.get("solvent")

        return cls(
            program=normalize_program_name(program) if program is not None else None,
            task=normalize_task_name(task) if task is not None else None,
            method=str(method).strip() if method is not None else None,
            basis=str(basis).strip() if basis is not None else None,
            dispersion=str(dispersion).strip() if dispersion is not None else None,
            solvent=(
                copy.deepcopy(solvent)
                if isinstance(solvent, dict)
                else (str(solvent).strip() if solvent is not None else None)
            ),
        )


def compile_theory_keyword(
    theory: TheorySpec,
    program: str = "g16",
    task: str = "opt",
) -> str:
    """Compile structured theory metadata into a canonical keyword string."""
    eff_prog = normalize_program_name(theory.program or program)
    eff_task = normalize_task_name(theory.task or task)

    if not theory.method or not str(theory.method).strip():
        raise ValueError("theory requires non-empty 'method' to compile keyword")

    method = str(theory.method).strip()
    basis = str(theory.basis).strip() if theory.basis else None
    dispersion = str(theory.dispersion).strip() if theory.dispersion else None
    solv_model, solv_name = normalize_solvent(theory.solvent)

    if eff_prog == "g16":
        mb = f"{method}/{basis}" if basis else method
        parts = [mb]

        if dispersion:
            d_clean = re.sub(r"[-_\s]", "", dispersion.lower())
            if d_clean in ("d3bj", "gd3bj"):
                parts.append("empiricaldispersion=gd3bj")
            elif d_clean in ("d3", "gd3", "d30", "d3zero"):
                parts.append("empiricaldispersion=gd3")
            elif d_clean in ("d4", "gd4"):
                parts.append("empiricaldispersion=gd4")
            elif "empiricaldispersion=" in dispersion.lower():
                parts.append(dispersion)
            else:
                parts.append(f"empiricaldispersion={dispersion.lower()}")

        if eff_task == "opt":
            parts.append("opt")
        elif eff_task == "sp":
            parts.append("sp")
        elif eff_task == "freq":
            parts.append("freq")
        elif eff_task == "opt_freq":
            parts.append("opt freq")
        elif eff_task == "ts":
            parts.append("opt=(ts,calcfc,noeigen)")

        if solv_name:
            model_str = solv_model or "smd"
            parts.append(f"scrf=({model_str},solvent={solv_name})")

        return " ".join(parts)

    # ORCA
    parts = [method]
    if dispersion:
        d_clean = re.sub(r"[-_\s]", "", dispersion.lower())
        if d_clean in ("d3bj", "gd3bj"):
            parts.append("D3BJ")
        elif d_clean in ("d3", "gd3", "d30", "d3zero"):
            parts.append("D3Zero")
        elif d_clean in ("d4", "gd4"):
            parts.append("D4")
        else:
            parts.append(dispersion)

    if basis:
        parts.append(basis)

    if eff_task == "opt":
        parts.append("Opt")
    elif eff_task == "sp":
        parts.append("SP")
    elif eff_task == "freq":
        parts.append("Freq")
    elif eff_task == "opt_freq":
        parts.append("Opt Freq")
    elif eff_task == "ts":
        parts.append("OptTS")

    if solv_name:
        model_str = (solv_model or "CPCM").upper()
        parts.append(f"{model_str}({solv_name})")

    return " ".join(parts)


def validate_theory_keyword_consistency(
    theory: TheorySpec,
    keyword: str,
    program: str = "g16",
    task: str = "opt",
) -> None:
    """Validate that structured theory fields agree with the raw keyword string.

    Raises ValueError with an informative message if there is a conflict.
    """
    kw = (keyword or "").strip()
    if not kw:
        return

    # Validate-for-effect: raises ValueError on unknown program/task spellings
    # even when no structured field pins them (results intentionally unused).
    _eff_prog = normalize_program_name(theory.program or program)
    _eff_task = normalize_task_name(theory.task or task)
    kw_lower = kw.lower()

    if theory.program is not None:
        th_prog = normalize_program_name(theory.program)
        step_prog = normalize_program_name(program)
        if th_prog != step_prog:
            raise ValueError(
                f"theory program {theory.program!r} disagrees with program {program!r}"
            )
        if th_prog == "g16" and kw.startswith("!"):
            raise ValueError(f"theory program 'g16' disagrees with ORCA keyword {keyword!r}")
        if th_prog == "orca" and (kw.startswith("#") or "empiricaldispersion=" in kw_lower):
            raise ValueError(f"theory program 'orca' disagrees with Gaussian keyword {keyword!r}")

    if theory.task is not None:
        th_task = normalize_task_name(theory.task)
        step_task = normalize_task_name(task)
        if th_task != step_task:
            raise ValueError(f"theory task {theory.task!r} disagrees with task {task!r}")

        has_opt = bool(re.search(r"(?i)\bopt\b", kw_lower))
        has_freq = bool(re.search(r"(?i)\bfreq\b", kw_lower))
        has_ts = bool(re.search(r"(?i)\b(?:ts|optts)\b", kw_lower))

        if th_task == "opt_freq":
            if not (has_opt and has_freq):
                raise ValueError(
                    f"theory task 'opt_freq' disagrees with keyword {keyword!r} (missing opt or freq)"
                )
        elif th_task == "opt":
            if has_freq:
                raise ValueError(
                    f"theory task 'opt' disagrees with keyword {keyword!r} (keyword requests freq)"
                )
            if has_ts:
                raise ValueError(
                    f"theory task 'opt' disagrees with keyword {keyword!r} (keyword requests TS)"
                )
        elif th_task == "freq":
            if has_opt:
                raise ValueError(
                    f"theory task 'freq' disagrees with keyword {keyword!r} (keyword requests opt)"
                )
        elif th_task == "ts":
            if not has_ts:
                raise ValueError(
                    f"theory task 'ts' disagrees with keyword {keyword!r} (missing TS search directive)"
                )
        elif th_task == "sp":
            if has_opt or has_freq or has_ts:
                raise ValueError(
                    f"theory task 'sp' disagrees with keyword {keyword!r} (requests non-SP directives)"
                )

    if theory.method:
        m_clean = re.sub(r"[-_\s]", "", theory.method.lower())
        kw_clean = re.sub(r"[-_\s]", "", kw_lower)
        if m_clean not in kw_clean:
            raise ValueError(f"theory method {theory.method!r} disagrees with keyword {keyword!r}")

    if theory.basis:
        b_clean = re.sub(r"[-_\s]", "", theory.basis.lower())
        kw_clean = re.sub(r"[-_\s]", "", kw_lower)
        variants = {b_clean}
        if "631g*" in b_clean or "631g(d)" in b_clean:
            variants.update(["631g*", "631g(d)"])
        if "6311g*" in b_clean or "6311g(d)" in b_clean:
            variants.update(["6311g*", "6311g(d)"])
        if "631g**" in b_clean or "631g(d,p)" in b_clean:
            variants.update(["631g**", "631g(d,p)"])
        if not any(v in kw_clean for v in variants):
            raise ValueError(f"theory basis {theory.basis!r} disagrees with keyword {keyword!r}")

    if theory.dispersion:
        d_clean = re.sub(r"[-_\s]", "", theory.dispersion.lower())
        kw_clean = re.sub(r"[-_\s]", "", kw_lower)
        d_variants = {d_clean}
        if "d3bj" in d_clean:
            d_variants.update(["d3bj", "gd3bj"])
        elif "d3" in d_clean:
            d_variants.update(["d3", "gd3", "d3zero"])
        elif "d4" in d_clean:
            d_variants.update(["d4", "gd4"])
        if not any(v in kw_clean for v in d_variants):
            raise ValueError(
                f"theory dispersion {theory.dispersion!r} disagrees with keyword {keyword!r}"
            )

    if theory.solvent:
        _, s_name = normalize_solvent(theory.solvent)
        if s_name and s_name.lower() not in kw_lower:
            raise ValueError(
                f"theory solvent {theory.solvent!r} disagrees with keyword {keyword!r}"
            )


def theory_to_editor_metadata(theory: TheorySpec) -> dict[str, Any]:
    """Convert a TheorySpec into deterministic editor metadata."""
    return theory.to_dict()


def editor_metadata_to_theory(metadata: dict[str, Any]) -> TheorySpec:
    """Parse editor metadata back into a TheorySpec."""
    return TheorySpec.from_dict(metadata)
