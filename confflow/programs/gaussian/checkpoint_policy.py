#!/usr/bin/env python3

"""Gaussian checkpoint pure-route policy (L1-G1a mechanical move).

Moved verbatim from :mod:`confflow.producer.checkpoints`: the 11 canonical
route constants, the light refusal constructor :func:`_refuse`, and the three
pure route helpers :func:`_add_opt_option`, :func:`_add_irc_rcfc`,
:func:`_strip_managed_items` (bodies/signatures AST-exact).

Authority notes (imported, never copied):

- :mod:`confflow.programs.gaussian.path` (as ``_irc_path``) -- IRC option
  validation via :func:`parse_irc_route`;
- :mod:`confflow.programs.gaussian.rendering` (as ``_gaussian_rendering``) --
  keyword normalization via :func:`normalize_gaussian_keyword`;
- :mod:`confflow.domain.errors` -- light refusal construction only.

Science authority tables stay with their owners; this module holds no
``ProgramName``/registry/stage logic (G1b adds stages separately). The
canonical constant values mirror ``confflow.producer.checkpoints`` which
re-exports the same objects (bidirectional mirror).
"""

from __future__ import annotations

import re

from ...domain.errors import DomainError, InvalidBindingError
from . import path as _irc_path
from . import rendering as _gaussian_rendering

#: QST route token (mirrors ``named._QST_ITEM_PATTERN``; the compiler and
#: the named-structures adapter own the real rule, this is only the early
#: refusal so the error names checkpoint vocabulary).
_QST_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])QST[23](?![0-9])", re.IGNORECASE)

#: Managed IRC item in all its forms (bare ``IRC``, ``IRC(...)``,
#: ``IRC=X``, ``IRC=(...)``), for the method comparison strip.  Option
#: semantics stay with :func:`parse_irc_route`; this pattern only removes
#: the item text.
_IRC_MANAGED_RE = re.compile(
    r"(?<![A-Za-z0-9])IRC(?![A-Za-z0-9])" r"(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s,()]+)?",
    re.IGNORECASE,
)

#: Bare IRC route item locator for the ``RCFC`` edit span (option
#: semantics stay with :func:`parse_irc_route`, which already validated
#: the route before the edit span is located here).
_IRC_ITEM_RE = re.compile(r"(?<![A-Za-z0-9])IRC(?![A-Za-z0-9])", re.IGNORECASE)

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

#: Force-constant options opposed to the ``RCFC`` vote inside one ``IRC``
#: item: computing force constants (``CalcFC``/``CalcAll``) while voting
#: the checkpoint-read form is ambiguous.  An already present ``RCFC`` is
#: the idempotent accept.
_RCFC_CONFLICTS = frozenset({"calcfc", "calcall"})

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


def _add_irc_rcfc(keyword: str, *, step_id: str) -> str:
    """Insert the ``RCFC`` vote into the first ``IRC`` item of *keyword*.

    Direction votes and unknown options are validated by the existing
    :func:`parse_irc_route` authority first: an explicit
    forward/reverse vote conflicts with the both-directions ``RCFC`` form,
    and an already-present ``RCFC`` is refused instead of duplicated.  The
    bare ``IRC`` form already means both directions, so spelling ``RCFC``
    out is the explicit, digest-covered record of that intent.
    """
    try:
        route = _irc_path.parse_irc_route(keyword)
    except ValueError as exc:
        raise _refuse(f"target step {step_id!r} has an invalid IRC route: {exc}") from exc
    raw_options = tuple(str(item) for item in route.get("raw_options", ()))
    lowered_options = {item.upper() for item in raw_options}
    opposed = sorted(item for item in raw_options if item.lower() in _RCFC_CONFLICTS)
    if opposed:
        raise _refuse(
            f"target step {step_id!r} already declares opposed IRC "
            f"option(s) {opposed}: the checkpoint-read vote cannot be "
            "combined with computed force constants"
        )
    if "RCFC" in lowered_options:
        return keyword
    if route.get("mode") != "both":
        raise _refuse(
            f"target step {step_id!r} votes an explicit IRC direction "
            f"({route.get('mode')!r}): mode 'rcfc' needs both directions"
        )
    match = _IRC_ITEM_RE.search(keyword)
    if match is None:  # Unreachable: parse_irc_route already found the item.
        raise _refuse(f"target step {step_id!r} carries no IRC route item")
    tail = keyword[match.end() :]
    stripped = tail.lstrip()
    if stripped.startswith("("):
        closing = stripped.find(")")
        if closing < 0:
            raise _refuse(f"target step {step_id!r} has a malformed IRC option group")
        inner = stripped[1:closing].strip()
        replacement = f"IRC({inner},RCFC)" if inner else "IRC(RCFC)"
        return keyword[: match.start()] + replacement + stripped[closing + 1 :]
    if stripped.startswith("="):
        after = stripped[1:].lstrip()
        if after.startswith("("):
            closing = after.find(")")
            if closing < 0:
                raise _refuse(f"target step {step_id!r} has a malformed IRC option group")
            inner = after[1:closing].strip()
            replacement = f"IRC=({inner},RCFC)" if inner else "IRC=(RCFC)"
            return keyword[: match.start()] + replacement + after[closing + 1 :]
        token = after.split(",")[0].strip().split()[0] if after.strip() else ""
        if not token:
            raise _refuse(f"target step {step_id!r} has a malformed IRC= option")
        return keyword[: match.start()] + f"IRC=({token},RCFC)" + after[len(token) :]
    return keyword[: match.start()] + "IRC(RCFC)" + tail


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
