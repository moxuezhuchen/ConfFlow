#!/usr/bin/env python3

"""Gaussian checkpoint pure-route policy (L1-G1a mechanical move).

Moved verbatim from :mod:`confflow.producer.checkpoints`: the 9 canonical
route constants, the light refusal constructor :func:`_refuse`, and the duo
of pure route helpers :func:`_add_opt_option`,
:func:`_strip_managed_items` (bodies/signatures AST-exact).

Authority notes (imported, never copied):

- :mod:`confflow.programs.gaussian.rendering` (as ``_gaussian_rendering``) --
  keyword normalization via :func:`normalize_gaussian_keyword`;
- :mod:`confflow.domain.errors` -- light refusal construction only.

Science authority tables stay with their owners; G1b stages use
``ProgramName`` identity (``is not ProgramName.GAUSSIAN``) and the
rendering/energy_semantics authorities via data-value delegation.
The canonical constant values mirror ``confflow.producer.checkpoints`` which
re-exports the same objects (bidirectional mirror).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast

from ...domain.errors import DomainError, InvalidBindingError
from ...execution.native import ProgramName
from . import energy_semantics as _energy_semantics
from . import rendering as _gaussian_rendering

#: QST route token (mirrors ``named._QST_ITEM_PATTERN``; the compiler and
#: the named-structures adapter own the real rule, this is only the early
#: refusal so the error names checkpoint vocabulary).
_QST_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])QST[23](?![0-9])", re.IGNORECASE)

#: Managed IRC item in all its forms (bare ``IRC``, ``IRC(...)``,
#: ``IRC=X``, ``IRC=(...)``), for the method comparison strip.  This
#: pattern only removes the item text.
_IRC_MANAGED_RE = re.compile(
    r"(?<![A-Za-z0-9])IRC(?![A-Za-z0-9])" r"(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s,()]+)?",
    re.IGNORECASE,
)

#: Opt route item forms (mirrors ``rendering._OPT_PAREN_RE`` /
#: ``_OPT_ASSIGN_RE`` / ``_OPT_BARE_RE``; the compiler and the adapter own
#: the real rule, this only locates the edit span for the managed option).
_OPT_PAREN_RE = re.compile(r"(?i)\bopt\s*(=)?\s*\(([^)]*)\)")
_OPT_ASSIGN_RE = re.compile(r"(?i)\bopt\s*=\s*([^\s()]+)")
_OPT_BARE_RE = re.compile(r"(?i)\bopt\b")

#: Frequency token (mirrors ``rendering._FREQ_TOKEN_PATTERN``; used only to
#: strip the helper-managed job-type items for the method comparison).
_FREQ_TOKEN_RE = re.compile(r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?")

#: Standalone single-point job-type token for the method comparison.  An
#: explicit Gaussian ``SP`` is a valid job type (single point is otherwise
#: the default), not a method change, so it strips exactly like the other
#: managed job-type items.  Requiring whitespace or string boundaries
#: keeps letters ``SP`` inside functional/basis keywords (for
#: example ``B3LYP/SP``, ``CSP``) or other scientific keywords/options
#: untouched: only a whitespace-delimited standalone ``SP`` item is removed.
_SP_MANAGED_RE = re.compile(r"(?i)(?<!\S)sp(?=\s|$)")

#: Force-constant options opposed to an explicit ``ReadFC`` inside one
#: ``Opt(...)`` group: reading stored force constants while computing them
#: (``CalcFC``/``CalcAll``) or voting the both-directions checkpoint form
#: (``RCFC``) is ambiguous, so the combination is refused.  An already
#: present ``ReadFC`` is the idempotent accept: a user card that already
#: spells the option keeps its keyword verbatim.
_READFC_CONFLICTS = frozenset({"rcfc", "calcfc", "calcall"})

#: User-managed checkpoint path directives: the adapter renders ``%Chk``
#: and ``%OldChk`` itself, so a target that manages them by hand is
#: refused instead of racing the renderer.
_LINK0_CHECKPOINT_RE = re.compile(r"(?i)%\s*(oldchk|chk)\b")


def _refuse(message: str) -> DomainError:
    """Build the refusal error for a checkpoint-reuse violation."""
    return InvalidBindingError(message)


def _add_opt_option(keyword: str, option: str, *, step_id: str) -> str:
    """Insert *option* into the first ``Opt`` item of *keyword*.

    The insertion mirrors the adapter-owned ``ensure_modredundant_keyword``
    shape handling (paren group, ``opt=X`` assignment, bare ``opt``); only
    the managed ``ReadFC`` option is ever inserted, never a naked keyword.
    """

    def _paren_replace(match: re.Match[str]) -> str:
        marker = match.group(1)
        items = [item.strip() for item in (match.group(2) or "").split(",") if item.strip()]
        lowered = {item.split("=")[0].strip().lower() for item in items}
        conflicts = sorted(lowered & _READFC_CONFLICTS)
        if conflicts:
            raise _refuse(
                f"target step {step_id!r} already declares opposed Opt "
                f"option(s) {conflicts}: an explicit Hessian source cannot be "
                "combined with computed force constants"
            )
        if "readfc" in lowered:
            return match.group(0)
        items.append(option)
        return f"opt{'=' if marker == '=' else ''}({','.join(items)})"

    updated, count = _OPT_PAREN_RE.subn(_paren_replace, keyword, count=1)
    if count:
        return updated

    def _assign_replace(match: re.Match[str]) -> str:
        current = match.group(1).strip()
        lowered = current.split("=")[0].strip().lower()
        if lowered == "readfc":
            return match.group(0)
        if lowered in _READFC_CONFLICTS:
            raise _refuse(
                f"target step {step_id!r} already declares opposed Opt "
                f"option {current!r}: an explicit Hessian source cannot be "
                "combined with computed force constants"
            )
        return f"opt=({current},{option})"

    updated, count = _OPT_ASSIGN_RE.subn(_assign_replace, keyword, count=1)
    if count:
        return updated
    if _OPT_BARE_RE.search(keyword) is not None:
        return _OPT_BARE_RE.sub(f"opt={option}", keyword, count=1)
    raise _refuse(
        f"target step {step_id!r} carries no Opt route item: mode 'readfc' "
        "explicitly modifies an Opt route only"
    )


def _strip_managed_items(keyword: str) -> str:
    """Remove helper-managed job-type items for the method comparison.

    Only the ``Opt(...)`` item in all its forms, the ``IRC`` item in all
    its forms, ``Freq`` tokens, and the standalone ``SP`` job-type token
    are removed: these are the items this helper understands and edits.
    Everything else (functional, basis set, solvation, extra keywords)
    must agree token-for-token, so no chemistry equivalence is ever
    guessed.  The ``SP`` strip requires a whitespace-delimited standalone
    token, so letters ``SP`` inside functional/basis keywords (for example
    ``B3LYP/SP``, ``CSP``) or other scientific keywords/options stay
    untouched.
    """
    text = _gaussian_rendering.normalize_gaussian_keyword(keyword)
    text = _OPT_PAREN_RE.sub(" ", text)
    text = _OPT_ASSIGN_RE.sub(" ", text)
    text = _OPT_BARE_RE.sub(" ", text)
    text = _IRC_MANAGED_RE.sub(" ", text)
    text = _FREQ_TOKEN_RE.sub(" ", text)
    text = _SP_MANAGED_RE.sub(" ", text)
    return " ".join(text.replace("#", " ").split()).lower()


# ---------------------------------------------------------------------------
# L1-G1b staged Gaussian policy (explicit per-row delegation, order kept by
# the producer wrapper). Each stage takes data values only (no Callables,
# no registry/lineage callbacks). Messages are byte-identical to the old
# producer bodies. Root priority: ProgramName keeps the original Enum
# object with ``is not ProgramName.GAUSSIAN`` identity; adapter keeps its
# original value without extra coercion.
# ---------------------------------------------------------------------------


def require_gaussian_program(*, step_id: str, program: str, program_name: ProgramName) -> None:
    """Refuse a non-Gaussian program end (old 254-259)."""
    if program_name is not ProgramName.GAUSSIAN:
        raise _refuse(
            f"step {step_id!r} uses program {program!r}: ORCA declares no "
            "checkpoint input vocabulary, so checkpoint reuse is refused "
            "instead of staging artifacts nothing can consume"
        )


def require_standard_adapter(*, step_id: str, execution_adapter: object) -> None:
    """Refuse a non-``standard`` execution adapter (old 260-266)."""
    if execution_adapter != "standard":
        raise _refuse(
            f"step {step_id!r} uses execution adapter {execution_adapter!r}: "
            "checkpoint reuse needs the 'standard' adapter; "
            "named-structure/QST shapes carry no checkpoint port"
        )


def require_no_qst(*, step_id: str, keyword: str) -> None:
    """Refuse a route carrying a QST item (old 279-283)."""
    if _QST_TOKEN_RE.search(keyword) is not None:
        raise _refuse(
            f"step {step_id!r} carries a QST route item: Gaussian QST "
            "rendering declares no checkpoint input vocabulary"
        )


def require_standard_checkpoint_role(*, port_present: bool, has_checkpoint_role: bool) -> None:
    """Refuse when the ``standard`` checkpoint port/role is absent (old 289-293)."""
    if not port_present or not has_checkpoint_role:
        raise _refuse(
            "the 'standard' adapter advertises no 'checkpoint' role on its "
            "'checkpoint' port; the reuse edge has no supported role"
        )


def require_artifact_checkpoint_role(*, port_present: bool, has_checkpoint_role: bool) -> None:
    """Refuse when the artifacts checkpoint port/role is absent (old 298-303)."""
    if not port_present or not has_checkpoint_role:
        raise _refuse(
            "the calculation contract advertises no 'checkpoint' role on its "
            "'artifacts' port; the reuse edge has no supported role"
        )


def ensure_source_write_chk(*, step_id: str, native: object) -> None:
    """Refuse a source that disables checkpoint writing (old 311-315).

    No write-back here; the producer wrapper sets
    ``native["write_chk"] = True`` after this passes.
    """
    from collections.abc import Mapping as _Mapping

    assert isinstance(native, _Mapping)
    if not _gaussian_rendering.resolve_write_chk(native):  # type: ignore[arg-type]
        raise _refuse(
            f"source step {step_id!r} disables native 'write_chk': "
            "there is no checkpoint file for the target to consume"
        )


def check_target_link0_core(*, step_id: str, link0_value: object) -> None:
    """Refuse user-managed checkpoint Link0 paths (old 324-333).

    ``coerce_section_lines`` ValueError propagates unchanged (never caught).
    """
    if link0_value is None:
        return
    lines = _gaussian_rendering.coerce_section_lines(link0_value, "link0")
    for line in lines:
        if _LINK0_CHECKPOINT_RE.search(line) is not None:
            raise _refuse(
                f"target step {step_id!r} manages checkpoint paths in native "
                f"'link0' ({line!r}): the adapter renders '%Chk'/'%OldChk' "
                "itself, so user-managed checkpoint paths are refused"
            )


def check_unsupported_method(*, target_id: str, target_keyword: str) -> None:
    """Refuse an unsupported method family on the target (old 928-933)."""
    finding = _energy_semantics.unsupported_method_finding(target_keyword)
    if finding is not None:
        raise _refuse(
            f"target step {target_id!r} requests method {finding.token!r}: "
            f"{finding.reason} (family {finding.family.value})"
        )


def check_route_cores(
    *,
    source_id: str,
    target_id: str,
    source_keyword: str,
    target_keyword: str,
    allow_method_change: bool,
) -> None:
    """Compare managed-strip route cores (old 934-943)."""
    source_core = _strip_managed_items(source_keyword)
    target_core = _strip_managed_items(target_keyword)
    if source_core != target_core and not allow_method_change:
        raise _refuse(
            f"source step {source_id!r} and target step {target_id!r} differ "
            "beyond the helper-managed Opt/IRC/Freq/SP items "
            f"({source_core!r} != {target_core!r}): a known differing method "
            "for Hessian reuse is refused by default; pass "
            "allow_method_change=True when the method change is intended"
        )


def native_scientific_core(native: Any, *, managed_keys: Any) -> dict[Any, Any]:
    """Return the scientific payload minus helper-managed keys.

    Runtime mirrors the old producer helper exactly (``.items`` + membership,
    no new shape rejection): Duck objects with ``.items`` succeed, objects
    without ``.items`` raise the same ``AttributeError`` text as before.
    Static types stay permissive via ``Any``/``Mapping`` + ``cast``.
    """
    return {
        key: value
        for key, value in cast(Mapping[Any, Any], native).items()
        if key not in managed_keys
    }


def check_native_payload_cores(
    *,
    source_id: str,
    target_id: str,
    source_native: object,
    target_native: object,
    allow_method_change: bool,
    managed_keys: frozenset[str],
) -> None:
    """Compare native scientific payloads (old 946-956)."""
    if (
        native_scientific_core(source_native, managed_keys=managed_keys)
        != native_scientific_core(target_native, managed_keys=managed_keys)
        and not allow_method_change
    ):
        raise _refuse(
            f"source step {source_id!r} and target step {target_id!r} carry "
            "different native scientific payloads (basis/ECP/extra sections, "
            "modredundant, or atom mapping): a known differing method for "
            "Hessian reuse is refused by default; pass "
            "allow_method_change=True when the method change is intended"
        )
