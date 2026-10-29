"""The one command-resolution entry for every executor that starts an external program.

A configured program is resolved once, on the PATH of the effective native environment
(the step's frozen snapshot: binding env over the inherited ambient policy), never on the
control process' PATH.  The result is the absolute file that the execution policy, the
identity measurement and the launch all name, so one file is checked, measured and run.

Policy matching keeps both forms (see ``execution_policy``): the configured name matches a
bare allow-list entry, and the realpath of the resolved absolute path matches an absolute
allow-list entry.  A name is never reduced to its basename, so an absolute allow-list entry
cannot be satisfied by a different file of the same name.

A resolution carries a cheap file fingerprint taken when it is located; :func:`verify_launch_unchanged`
re-checks it right before launch, so an in-place replacement after measurement is refused.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..domain.errors import DomainError
from .binding_resolution import effective_native_env
from .execution_policy import enforce_execution_policy

__all__ = [
    "LaunchFingerprint",
    "ResolvedCommand",
    "enforce_command_policy",
    "launch_environment",
    "locate_command",
    "pinned_or_located",
    "resolve_and_enforce_command",
    "resolve_command",
    "take_fingerprint",
    "verify_launch_unchanged",
]


@dataclass(frozen=True)
class LaunchFingerprint:
    """Cheap identity of the file a resolution names: stat fields plus the realpath."""

    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    real_path: str


def take_fingerprint(path: str) -> LaunchFingerprint:
    """Return the fingerprint of *path* (symlinks followed); raises ``OSError`` when unstatable."""
    st = os.stat(path)
    return LaunchFingerprint(
        device=st.st_dev,
        inode=st.st_ino,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        real_path=os.path.realpath(path),
    )


@dataclass(frozen=True)
class ResolvedCommand:
    """A configured program name and the one absolute file it resolved to.

    ``configured`` is the name as written in the configuration (bare name, relative or
    absolute path).  ``absolute`` is the file that is measured and launched.  ``fingerprint``
    is taken at resolution and is not part of equality.
    """

    configured: str
    absolute: str
    fingerprint: LaunchFingerprint | None = field(default=None, compare=False)

    @property
    def real_path(self) -> str:
        """Symlink-resolved form of :attr:`absolute`, the form absolute rules match."""
        return os.path.realpath(self.absolute)


def locate_command(configured: str, env: Mapping[str, str]) -> ResolvedCommand | None:
    """Return the resolution of *configured* in *env*, or ``None`` when it is not runnable.

    A name with a directory part (absolute or relative) is taken as a path and made
    absolute.  A bare name is searched on the ``PATH`` entry of *env* only.  The result
    must be a regular file with the execute bit.
    """
    if not isinstance(configured, str) or not configured:
        return None
    if os.path.dirname(configured):
        found: str | None = os.path.abspath(configured)
    else:
        located = shutil.which(configured, path=_path_of(env))
        found = os.path.abspath(located) if located else None
    if found is None or not os.path.isfile(found) or not os.access(found, os.X_OK):
        return None
    try:
        fingerprint = take_fingerprint(found)
    except OSError:
        return None
    return ResolvedCommand(configured=configured, absolute=found, fingerprint=fingerprint)


def verify_launch_unchanged(command: ResolvedCommand) -> None:
    """Refuse to launch when the file changed since *command* was resolved and measured.

    A resolution without a fingerprint (built by a caller directly) is not checked.
    """
    if command.fingerprint is None:
        return
    try:
        current: LaunchFingerprint | None = take_fingerprint(command.absolute)
    except OSError:
        current = None
    if current != command.fingerprint:
        raise DomainError(
            f"executable {command.absolute!r} changed after it was resolved and measured; "
            "refusing to launch"
        )


def resolve_command(configured: str, env: Mapping[str, str]) -> ResolvedCommand:
    """Resolve *configured* once in *env*, failing closed when it is not runnable."""
    resolved = locate_command(configured, env)
    if resolved is None:
        raise DomainError(
            f"executable {configured!r} did not resolve to an executable file on the "
            "effective environment PATH"
        )
    return resolved


def pinned_or_located(
    configured: str, env: Mapping[str, str], pinned: ResolvedCommand | None
) -> ResolvedCommand | None:
    """Return the command a launch uses: the pinned resolution, else one lookup in *env*.

    A pinned resolution is the one the application layer resolved and measured for this
    step.  The item reuses it for policy and launch and never searches PATH again, so the
    file that was measured is the file that starts.  Without a pin (a direct low-level
    caller) the name is resolved here, once, for the caller's own entry.
    """
    if pinned is None:
        return locate_command(configured, env)
    if pinned.configured != configured:
        raise DomainError(
            f"resolved command {pinned.configured!r} does not name the configured executable "
            f"{configured!r}; refusing to launch a different program"
        )
    return pinned


def enforce_command_policy(
    binding: Any,
    *,
    configured: str,
    resolved: ResolvedCommand | None,
    write_paths: tuple[str, ...] = (),
    label: str = "executable",
    check_program: bool = True,
) -> None:
    """Apply the binding's allow list and sandbox to one resolved command.

    The configured name is checked against bare entries, the realpath of the resolved file
    against absolute entries.  ``resolved=None`` (not runnable) checks the name only, so a
    forbidden program is refused by policy before it is reported as missing.
    """
    absolute = resolved.real_path if resolved is not None else None
    enforce_execution_policy(
        binding,
        executable=configured,
        resolved_executable=absolute,
        write_paths=write_paths,
        label=label,
        check_program=check_program,
    )


def resolve_and_enforce_command(
    binding: Any,
    configured: str,
    env: Mapping[str, str],
    *,
    label: str,
    write_paths: tuple[str, ...] = (),
    unresolved_message: str | None = None,
    pinned: ResolvedCommand | None = None,
) -> ResolvedCommand:
    """Resolve once in *env* (or reuse *pinned*), then enforce policy, then fail when not runnable.

    The policy is checked before the not-runnable error, so a program that is both
    forbidden and missing is reported as forbidden, and nothing is launched.
    ``unresolved_message`` replaces the default not-runnable text when a caller has its own
    wording for it (the native path keeps its measurement wording).  ``pinned`` is the
    application's resolution for the same configured name; it is reused, not searched again.
    """
    resolved = pinned_or_located(configured, env, pinned)
    enforce_command_policy(
        binding, configured=configured, resolved=resolved, write_paths=write_paths, label=label
    )
    if resolved is None:
        raise DomainError(
            unresolved_message
            or (
                f"{label}: {configured!r} did not resolve to an executable file on the "
                "effective environment PATH"
            )
        )
    return resolved


def launch_environment(native_env: Mapping[str, str] | None, binding: Any) -> Mapping[str, str]:
    """Return the environment a launch uses: the step snapshot, else binding over ambient."""
    if native_env is not None:
        return native_env
    return effective_native_env(binding, inherit=os.environ)


def _path_of(env: Mapping[str, str]) -> str:
    return env.get("PATH", os.defpath)
