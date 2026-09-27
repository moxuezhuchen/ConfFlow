#!/usr/bin/env python3

"""Optional JobDesk cross-repo integration dependency.

ConfFlow's normal CI (GitHub-hosted runners) has no JobDesk checkout: the
sibling repository is private, so no module in ``tests/`` may import
``jobdesk_v2`` at import time.  This module resolves the checkout lazily:

1. ``JOBDESK_V2_SRC`` environment variable, when set;
2. otherwise the local development default ``/opt/jobdesk-v2-v4/src``,
   only when that directory really exists.

When the checkout is absent, :class:`JobDeskUnavailable` lets the
``jobdesk`` fixture skip exactly the cross-repo tests.  When the checkout
exists but is not the pinned revision, :class:`JobDeskShaMismatch` fails
closed so a wrong revision can never produce false cross-repo evidence
(``JOBDESK_V2_ALLOW_ANY_SHA=1`` is the explicit development escape hatch).

ConfFlow-local tests never touch this module; no production code depends on
JobDesk.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_JOBDESK_SRC",
    "EXPECTED_JOBDESK_SHA",
    "JOBDESK_SRC_ENV",
    "JobDeskIntegration",
    "JobDeskMisconfigured",
    "JobDeskShaMismatch",
    "JobDeskUnavailable",
    "load_jobdesk",
]

#: Environment variable naming the JobDesk v2 ``src`` root.
JOBDESK_SRC_ENV = "JOBDESK_V2_SRC"

#: Local development default (used only when it really exists).
DEFAULT_JOBDESK_SRC = Path("/opt/jobdesk-v2-v4/src")

#: Pinned JobDesk revision the cross-repo evidence was produced against.
EXPECTED_JOBDESK_SHA = "678ffce01e8c26271661d78cf04ebe3dac5e28c9"

#: Explicit escape hatch for development against a different checkout.
ALLOW_ANY_SHA_ENV = "JOBDESK_V2_ALLOW_ANY_SHA"

_CACHED: JobDeskIntegration | None = None


class JobDeskUnavailable(RuntimeError):
    """The optional JobDesk checkout is not present on this machine."""


class JobDeskMisconfigured(RuntimeError):
    """``JOBDESK_V2_SRC`` is set but does not name a JobDesk checkout."""


class JobDeskShaMismatch(RuntimeError):
    """The JobDesk checkout exists but is not the pinned revision."""


@dataclass(frozen=True, slots=True)
class JobDeskIntegration:
    """Lazily imported JobDesk v2 consumer surfaces."""

    src: Path
    sha: str | None
    author_v4_document: Any
    parse_v4_contract_bytes: Any
    parse_result_bytes: Any
    ContractParseError: Any


def _checkout_revision(src: Path) -> str | None:
    """Return the checkout's git HEAD, or ``None`` when unreadable.

    Read straight from ``.git`` (HEAD -> ref -> object id, with a packed-refs
    fallback and ``gitdir:`` indirection for worktrees/submodules) so the
    check never depends on a ``git`` binary being on the current ``PATH``.
    """
    repo = src.parent
    git_dir = repo / ".git"
    try:
        if git_dir.is_file():
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return None
            git_dir = (repo / pointer.split(":", 1)[1].strip()).resolve()
        if not git_dir.is_dir():
            return None
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head.split(":", 1)[1].strip()
        ref_path = git_dir / ref
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip() or None
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith(("#", "^")):
                    continue
                object_id, _, name = line.partition(" ")
                if name.strip() == ref:
                    return object_id.strip() or None
        return None
    except OSError:
        return None


def _resolve_src() -> Path | None:
    """Resolve the JobDesk ``src`` root, or ``None`` when absent."""
    explicit = os.environ.get(JOBDESK_SRC_ENV)
    if explicit:
        candidate = Path(explicit)
        if not (candidate / "jobdesk_v2").is_dir():
            raise JobDeskMisconfigured(
                f"{JOBDESK_SRC_ENV}={explicit!r} does not contain a jobdesk_v2 package"
            )
        return candidate
    if (DEFAULT_JOBDESK_SRC / "jobdesk_v2").is_dir():
        return DEFAULT_JOBDESK_SRC
    return None


def _purge_conflicting_jobdesk_modules(src: Path) -> None:
    """Drop cached ``jobdesk_v2`` modules that came from another checkout.

    A development machine can hold more than one JobDesk checkout (the V2
    compatibility gate has its own default).  Module identity is global, so
    a cached ``jobdesk_v2`` from the wrong checkout would otherwise win and
    the pinned cross-repo surfaces would be missing.  Object references
    already held by other tests stay valid; only the import cache entry is
    dropped so this helper's imports resolve from the pinned ``src``.
    """
    src_text = str(src)
    for name in list(sys.modules):
        if name != "jobdesk_v2" and not name.startswith("jobdesk_v2."):
            continue
        module = sys.modules[name]
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            del sys.modules[name]
            continue
        try:
            resolved = str(Path(module_file).resolve())
        except OSError:
            del sys.modules[name]
            continue
        if src_text not in resolved:
            del sys.modules[name]
    importlib.invalidate_caches()


def load_jobdesk() -> JobDeskIntegration:
    """Return the imported JobDesk consumer surfaces.

    Raises :class:`JobDeskUnavailable` when the checkout is absent and
    :class:`JobDeskShaMismatch` when it is present at the wrong revision.
    Results are cached per process.
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    src = _resolve_src()
    if src is None:
        raise JobDeskUnavailable(
            "JobDesk v2 checkout not found (set JOBDESK_V2_SRC); "
            "cross-repo tests are skipped in this environment"
        )
    sha = _checkout_revision(src)
    allow_any = os.environ.get(ALLOW_ANY_SHA_ENV) == "1"
    if not allow_any:
        if sha is None:
            raise JobDeskShaMismatch(
                f"cannot verify the JobDesk checkout revision at {src.parent!r}; "
                f"expected {EXPECTED_JOBDESK_SHA} "
                f"(set {ALLOW_ANY_SHA_ENV}=1 to bypass)"
            )
        if sha != EXPECTED_JOBDESK_SHA:
            raise JobDeskShaMismatch(
                f"JobDesk checkout at {src.parent!r} is {sha}, expected "
                f"{EXPECTED_JOBDESK_SHA}; cross-repo evidence must come from the "
                f"pinned revision (set {ALLOW_ANY_SHA_ENV}=1 to bypass)"
            )
    src_text = str(src)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    _purge_conflicting_jobdesk_modules(src)
    from jobdesk_v2.application.cards.v4_provider import author_v4_document
    from jobdesk_v2.application.editor.contract.parse import ContractParseError
    from jobdesk_v2.application.editor.contract.v4 import parse_v4_contract_bytes
    from jobdesk_v2.application.runs.v4_results import parse_result_bytes

    _CACHED = JobDeskIntegration(
        src=src,
        sha=sha,
        author_v4_document=author_v4_document,
        parse_v4_contract_bytes=parse_v4_contract_bytes,
        parse_result_bytes=parse_result_bytes,
        ContractParseError=ContractParseError,
    )
    return _CACHED
