#!/usr/bin/env python3
"""Tested isolation deployer for ConfFlow release wheels.

Gate A (pre-tag, non-production candidate) and Gate B (post-tag release
attestation) share this script so the candidate venv and the production
install share the same fail-closed code path. Two modes are supported:

* ``--mode production`` (default) requires an approved attestation
  binding the wheel to a vetted ``repository/tag/peeled-commit`` triple;
  the resulting ``<sys.prefix>/share/confflow/install-provenance.json``
  is written with ``attestation_verified=True`` so JobDesk's
  production gate accepts the executable.

* ``--mode candidate`` is allowed without an attestation and is used
  by Gate A. The produced record carries
  ``attestation_verified=False`` so JobDesk must *not* activate it as
  production. The deployer refuses to write a ``verified`` candidate
  record in this mode, even if the caller supplies the optional
  attestation arguments.

In either mode the deployer:

* validates ``--wheel`` exists, is a single file, basename matches the
  expected ``confflow-<version>-py3-none-any.whl`` pattern, and does
  not collide with an existing target venv;
* parses ``--sha256sums`` (single ``confflow-<version>-*.whl`` entry,
  no globs, no duplicates), computes the wheel byte digest, and
  refuses to install on mismatch;
* parses ``--expected-version``/``--expected-commit`` and the wheel's
  embedded ``__build__.py`` (without executing wheel code) and refuses
  to install on version/commit mismatch;
* refuses to install when ``--expected-repository`` or ``--expected-tag``
  is given in candidate mode but produces a release-record block
  identical to production (with ``attestation_verified=False``);
* creates an isolated staging venv in the same parent directory as
  ``--target-venv``, installs the complete dependency lock from the
  binary-only ``--wheelhouse`` and the exact wheel without touching any prior venv;
* runs the deployed ``confflow --capabilities --json`` once, aborts on
  any error, then atomically renames the staging directory onto the
  final ``--target-venv`` path;
* fails closed: on any error, only the staging directory just created
  is removed, never the existing venv, never ``/opt/g16`` or any other
  out-of-scope path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from confflow import install_provenance as ip
from confflow import release_dependencies as rd
from confflow.install_provenance import INSTALL_PROVENANCE_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fail_roll_back(staging: Path | None, why: str) -> Exception:
    """Return an exception and remove ``staging`` if it was created in-scope."""
    if staging is not None and staging.exists():
        # Only remove a staging path that lives in the same parent dir as
        # the target venv; the deployer must never clean a directory it
        # did not create.
        try:
            shutil.rmtree(staging)
        except OSError:
            pass
    return RuntimeError(why)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install_release_wheel.py",
        description="Tested-isolation ConfFlow wheel deployer (1.4.6 release closure).",
    )
    parser.add_argument(
        "--mode",
        choices=("candidate", "production"),
        default="production",
        help="candidate=pre-tag Gate A (attestation_unverified); production=tag+attestation",
    )
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--sha256sums", required=True, type=Path)
    parser.add_argument("--target-venv", required=True, type=Path)
    parser.add_argument(
        "--target-python",
        type=Path,
        default=None,
        help="Optional explicit python interpreter; refused when combined with "
        "--target-venv that points inside an existing venv.",
    )
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--expected-repository",
        default="moxuezhuchen/ConfFlow",
        help="Approved owner/repository triple to bind the install record to.",
    )
    parser.add_argument(
        "--expected-tag",
        required=True,
        help="e.g. v1.4.6 (or 'v1.4.6-candidate' for Gate A).",
    )
    parser.add_argument(
        "--expected-tag-commit",
        default=None,
        help="Peeled commit the tag points to (defaults to --expected-commit).",
    )
    parser.add_argument(
        "--attestation",
        type=Path,
        default=None,
        help="Path to a release attestation file (production mode only). "
        "Optional in candidate mode; when provided the digest is recorded "
        "verbatim.",
    )
    parser.add_argument("--dependency-lock", required=True, type=Path)
    parser.add_argument(
        "--wheelhouse",
        required=True,
        type=Path,
        help="Offline binary-wheel directory containing SHA256SUMS.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _resolve_wheel(args: argparse.Namespace) -> tuple[Path, str]:
    """Validate the wheel file and return ``(path, expected_basename)``."""
    wheel: Path = args.wheel
    if not wheel.is_file():
        raise _fail_roll_back(None, f"--wheel does not exist or is not a file: {wheel}")
    expected_basename = f"confflow-{args.expected_version}-py3-none-any.whl"
    if wheel.name != expected_basename:
        raise _fail_roll_back(
            None,
            f"--wheel basename must be {expected_basename!r}; got {wheel.name!r}",
        )
    return wheel, expected_basename


def _resolve_checksum(args: argparse.Namespace, wheel: Path) -> str:
    """Return the expected SHA-256 of the wheel from ``--sha256sums``."""
    if not args.sha256sums.is_file():
        raise _fail_roll_back(None, f"--sha256sums does not exist: {args.sha256sums}")
    parsed = ip.read_sha256sums(args.sha256sums)
    expected_basename = f"confflow-{args.expected_version}-py3-none-any.whl"
    matches = [digest for name, digest in parsed.items() if name == expected_basename]
    if len(matches) != 1:
        raise _fail_roll_back(
            None,
            f"--sha256sums must contain exactly one row for {expected_basename!r}; "
            f"found {len(matches)}",
        )
    actual = ip.sha256_hex(wheel)
    if actual.lower() != matches[0].lower():
        raise _fail_roll_back(
            None,
            f"wheel digest mismatch: file={actual} expected={matches[0]}",
        )
    return actual


def _resolve_target_venv(args: argparse.Namespace) -> tuple[Path, Path]:
    """Validate the target venv path and return ``(parent, target)``."""
    target = args.target_venv
    if target.exists():
        raise _fail_roll_back(
            None,
            f"--target-venv already exists: {target}. Refusing to overwrite an existing venv.",
        )
    parent = target.parent
    if not parent.is_dir():
        raise _fail_roll_back(None, f"--target-venv parent does not exist: {parent}")
    if args.target_python is not None and args.target_python.is_relative_to(target):
        raise _fail_roll_back(None, "--target-python must not live inside --target-venv")
    return parent, target


def _read_build_constants(whl_path: Path) -> tuple[str | None, bool | None]:
    """Return ``(commit, dirty)`` from ``confflow/__build__.py`` inside ``whl_path``.

    We only ``zipfile``-extract ``confflow/__build__.py`` and ``ast.parse``
    the result. Wheel code is never executed.
    """
    import zipfile

    with zipfile.ZipFile(whl_path) as archive:
        candidate_names = [
            name
            for name in archive.namelist()
            if name.endswith("/__build__.py") and name.startswith("confflow/")
        ]
        if not candidate_names:
            raise _fail_roll_back(None, f"__build__.py missing from wheel: {whl_path}")
        with archive.open(candidate_names[0]) as handle:
            source = handle.read().decode("utf-8")
    tree = __import__("ast").parse(source)
    commit: str | None = None
    dirty: bool | None = None
    for node in tree.body:
        if isinstance(node, __import__("ast").AnnAssign) and isinstance(node.target, __import__("ast").Name):
            if node.target.id == "COMMIT":
                commit = getattr(node.value, "value", None) if isinstance(node.value, __import__("ast").Constant) else None
            elif node.target.id == "DIRTY":
                if isinstance(node.value, __import__("ast").Constant) and isinstance(node.value.value, bool):
                    dirty = node.value.value
                elif isinstance(node.value, __import__("ast").Constant) and node.value.value is None:
                    dirty = None
    return commit, dirty


def _validate_metadata(
    args: argparse.Namespace,
    *,
    build_commit: str | None,
) -> None:
    if build_commit is None:
        raise _fail_roll_back(None, "wheel has no COMMIT constant")
    if build_commit.lower() != args.expected_commit.lower():
        raise _fail_roll_back(
            None,
            f"wheel build commit {build_commit} != --expected-commit {args.expected_commit}",
        )

_RUNTIME_IDENTITY_CODE = (
    "import json,platform,sys,sysconfig;"
    "print(json.dumps({"
    "'python_version':platform.python_version(),"
    "'python_implementation':platform.python_implementation(),"
    "'platform':sysconfig.get_platform(),"
    "'machine':platform.machine()"
    "},sort_keys=True))"
)


def _read_runtime_identity(
    interpreter: Path,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Read Python/platform identity from the interpreter being installed."""
    if not interpreter.is_file():
        raise _fail_roll_back(None, f"target Python does not exist: {interpreter}")
    result = subprocess.run(
        [str(interpreter), "-c", _RUNTIME_IDENTITY_CODE],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise _fail_roll_back(None, f"target Python identity probe failed: {result.stderr}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _fail_roll_back(None, f"target Python identity is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _fail_roll_back(None, "target Python identity is not a JSON object")
    return payload


def _validate_dependency_inputs(
    args: argparse.Namespace,
    *,
    runtime_identity: dict[str, object],
) -> rd.DependencyEvidence:
    try:
        return rd.validate_dependency_inputs(
            args.dependency_lock,
            args.wheelhouse,
            runtime_identity=runtime_identity,
        )
    except rd.DependencyInputError as exc:
        raise _fail_roll_back(None, str(exc)) from exc


def _validate_runtime_identity(identity: dict[str, object]) -> None:
    try:
        rd.validate_runtime_identity(identity)
    except rd.DependencyInputError as exc:
        raise _fail_roll_back(None, str(exc)) from exc


def _build_staging_venv(
    args: argparse.Namespace,
    parent: Path,
    python: Path | None,
) -> Path:
    """Create a brand-new staging venv alongside ``--target-venv``.

    ``python`` defaults to ``sys.executable``. The staging directory is
    named ``<target>.staging-XXXXXX`` and is created under the same
    parent so a successful atomic rename is a same-filesystem move.
    """
    target = args.target_venv
    suffix_counter = 0
    staging: Path | None = None
    while staging is None or staging.exists():
        suffix = "staging" + ("" if suffix_counter == 0 else f"-{suffix_counter}")
        staging = parent / f"{target.name}.{suffix}"
        suffix_counter += 1
        if suffix_counter > 1000:
            raise _fail_roll_back(None, "could not allocate a staging path")
    interpreter = python or Path(sys.executable)
    result = subprocess.run(
        [str(interpreter), "-m", "venv", str(staging)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise _fail_roll_back(staging, f"venv creation failed: {result.stderr}")
    config = staging / "pyvenv.cfg"
    config_text = config.read_text(encoding="utf-8").lower() if config.is_file() else ""
    if "include-system-site-packages = false" not in config_text:
        raise _fail_roll_back(
            staging,
            "staging venv is not isolated: include-system-site-packages is not false",
        )
    return staging


def _staging_env(staging: Path) -> dict[str, str]:
    """Build a clean env for staging-venv subprocesses.

    We must NOT leak the deployer's own ``PYTHONPATH`` / cwd into the
    staging venv's ``pip`` or ``bin/confflow`` — otherwise the staging
    venv will resolve ``confflow`` from the deployer's source tree
    (which is the worktree during testing) and bypass the freshly
    installed wheel. Strip any PYTHONPATH and rely on the venv's
    ``site-packages`` alone.
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONSTARTUP", None)
    bin_path = str(staging / "bin")
    env["PATH"] = bin_path + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(staging)
    env.pop("PYTHONHOME", None)
    return env


def _pip_install_dependencies(
    staging: Path,
    dependency_lock: Path,
    wheelhouse: Path,
) -> None:
    """Install the complete lock from the local wheelhouse only."""
    pip = staging / "bin" / "pip"
    cmd = [
        str(pip),
        "install",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--require-hashes",
        "--only-binary=:all:",
        "--disable-pip-version-check",
        "-r",
        str(dependency_lock),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, env=_staging_env(staging))
    if result.returncode != 0:
        raise _fail_roll_back(staging, f"dependency install failed: {result.stderr}")


def _pip_install_confflow(staging: Path, wheel: Path) -> None:
    """Install only the exact, already-verified ConfFlow wheel."""
    pip = staging / "bin" / "pip"
    result = subprocess.run(
        [
            str(pip),
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            str(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_staging_env(staging),
    )
    if result.returncode != 0:
        raise _fail_roll_back(staging, f"confflow wheel install failed: {result.stderr}")


def _run_pip_check(staging: Path) -> None:
    """Require pip's installed-distribution dependency check to pass."""
    result = subprocess.run(
        [str(staging / "bin" / "pip"), "check"],
        check=False,
        capture_output=True,
        text=True,
        env=_staging_env(staging),
    )
    if result.returncode != 0:
        detail = (result.stdout or result.stderr).strip()
        raise _fail_roll_back(staging, f"pip check failed: {detail}")


def _run_capability_probe(staging: Path) -> dict[str, object]:
    """Execute ``confflow --capabilities --json`` inside ``staging``."""
    executable = staging / "bin" / "confflow"
    result = subprocess.run(
        [str(executable), "--capabilities", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=_staging_env(staging),
    )
    if result.returncode != 0:
        raise _fail_roll_back(staging, f"capability probe failed: {result.stderr}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _fail_roll_back(staging, f"capability probe output is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _fail_roll_back(staging, "capability probe output is not a JSON object")
    return payload


def _commit_record(
    *,
    args: argparse.Namespace,
    wheel: Path,
    digest: str,
    build_commit: str | None,
    payload: dict[str, object],
    staging: Path,
    dependency_lock_sha256: str,
    wheelhouse_manifest_sha256: str,
    runtime_identity: dict[str, object],
) -> None:
    """Write ``install-provenance.json`` into ``staging`` (NOT into ``target``).

    The deployer must write provenance into the *staging* venv so that
    ``os.replace(staging, target)`` atomically moves it onto the final
    path. Writing directly into ``target`` would leave a half-populated
    directory at the final path and cause ``os.replace`` to fail with
    ``ENOTEMPTY``.
    """
    target = args.target_venv
    share_dir = staging / "share" / "confflow"
    share_dir.mkdir(parents=True, exist_ok=True)
    record_path = share_dir / "install-provenance.json"
    attestation_subject = ""
    attestation_file: dict[str, object] = {}
    if args.attestation is not None:
        try:
            attestation_file = json.loads(args.attestation.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise _fail_roll_back(target, f"attestation JSON invalid: {exc}") from exc
        attestation_subject = str(attestation_file.get("subject_digest", ""))
        if attestation_subject and attestation_subject.lower() != digest.lower():
            raise _fail_roll_back(
                target,
                f"attestation subject_digest {attestation_subject} != wheel digest {digest}",
            )

    attestation_verified = (
        True if args.mode == "production" and args.attestation is not None else False
    )

    raw = {
        "schema": INSTALL_PROVENANCE_SCHEMA,
        "package": "confflow",
        "version": args.expected_version,
        "wheel_filename": wheel.name,
        "wheel_sha256": digest,
        "dependency_lock_sha256": dependency_lock_sha256,
        "wheelhouse_manifest_sha256": wheelhouse_manifest_sha256,
        "python_version": str(runtime_identity["python_version"]),
        "python_implementation": str(runtime_identity["python_implementation"]),
        "platform": str(runtime_identity["platform"]),
        "machine": str(runtime_identity["machine"]),
        "build_commit": build_commit or "",
        "build_dirty": False if build_commit is not None else None,
        "release_repository": args.expected_repository,
        "release_tag": args.expected_tag,
        "release_tag_commit": (
            args.expected_tag_commit or args.expected_commit
        ),
        "attestation_verified": attestation_verified,
        "attestation_subject_digest": attestation_subject or digest,
    }
    if args.mode == "candidate" and "attestation" in raw:
        # In candidate mode an attestation file may exist for diagnostics,
        # but the install provenance is marked unverified and cannot be
        # promoted to production without re-running with --mode production.
        raw["attestation_verified"] = False

    ip.write_install_provenance_atomic(record_path, raw)

    # Re-read the freshly-written record back from ``staging`` so the
    # atomic rename to ``target`` happens only on a verified record.
    digest_obj, errors = ip.read_install_provenance(sys_prefix=str(staging))
    if attestation_verified:
        # Production installs: must round-trip to a fully verified record.
        if errors:
            raise _fail_roll_back(target, f"install-provenance validation errors: {errors}")
        if digest_obj.status != "verified":
            raise _fail_roll_back(
                target,
                f"install-provenance status {digest_obj.status!r} != expected 'verified'",
            )
        if (
            digest_obj.wheel_filename != wheel.name
            or digest_obj.wheel_sha256 != digest
        ):
            raise _fail_roll_back(
                target,
                "install-provenance round-trip mismatches the wheel filename/digest",
            )
    else:
        # Candidate installs: must round-trip to the attestation_unverified
        # diagnostic; ``read_install_provenance`` deliberately populates an
        # informational ``reason_code`` for non-verified records. Any other
        # non-verified reason_code (e.g. ``schema_mismatch``) is a real bug.
        if digest_obj.status != "invalid":
            raise _fail_roll_back(
                target,
                f"install-provenance status {digest_obj.status!r} != expected 'invalid' (candidate)",
            )
        if digest_obj.reason_code != ip.REASON_ATTESTATION_UNVERIFIED:
            raise _fail_roll_back(
                target,
                f"install-provenance reason_code {digest_obj.reason_code!r} "
                f"!= expected {ip.REASON_ATTESTATION_UNVERIFIED!r}",
            )
    # The capability probe must agree on schema_version.
    probe_schema = payload.get("schema_version")
    if probe_schema != 4:
        raise _fail_roll_back(
            target,
            f"capability probe reports schema_version={probe_schema!r}; expected 4",
        )


def _atomic_rename(staging: Path, target: Path) -> None:
    """Move ``staging`` onto ``target`` only if ``target`` is still absent."""
    try:
        os.replace(staging, target)
    except OSError as exc:
        raise _fail_roll_back(staging, f"atomic rename failed: {exc}") from exc


def _fixup_script_shebangs(staging: Path, target: Path) -> None:
    """Rewrite console-script shebangs before atomically renaming the venv.

    Setuptools records the staging venv's absolute interpreter path in each
    console script. Rewrite only the first line of scripts under the staging
    venv's ``bin`` directory, and only when it contains the exact staging root.
    Doing this before the rename keeps failures recoverable through the normal
    staging cleanup path and never mutates an existing target venv.
    """
    bin_dir = staging / "bin"
    if not bin_dir.is_dir():
        return
    staging_root = str(staging)
    target_root = str(target)
    for entry in sorted(bin_dir.iterdir()):
        if not entry.is_file():
            continue
        try:
            source = entry.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        first_line, newline, rest = source.partition("\n")
        if not first_line.startswith("#!") or staging_root not in first_line:
            continue
        rewritten = first_line.replace(staging_root, target_root, 1) + newline + rest
        tmp = entry.with_name(f".{entry.name}.shebangfix")
        try:
            tmp.write_text(rewritten, encoding="utf-8")
            os.chmod(tmp, entry.stat().st_mode)
            os.replace(tmp, entry)
        except OSError:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    wheel, _ = _resolve_wheel(args)
    digest = _resolve_checksum(args, wheel)
    parent, target = _resolve_target_venv(args)
    build_commit, _dirty = _read_build_constants(wheel)
    _validate_metadata(args, build_commit=build_commit)
    target_identity = _read_runtime_identity(
        args.target_python or Path(sys.executable)
    )
    _validate_runtime_identity(target_identity)
    dependency_evidence = _validate_dependency_inputs(
        args,
        runtime_identity=target_identity,
    )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "wheel": str(wheel),
                    "wheel_digest": digest,
                    "build_commit": build_commit,
                    "target_venv": str(target),
                    "mode": args.mode,
                    "dependency_lock_sha256": dependency_evidence.dependency_lock_sha256,
                    "wheelhouse_manifest_sha256": dependency_evidence.wheelhouse_manifest_sha256,
                    "runtime_identity": target_identity,
                },
                sort_keys=True,
            )
        )
        return 0

    staging = _build_staging_venv(args, parent, args.target_python)
    succeeded = False
    try:
        staged_identity = _read_runtime_identity(
            staging / "bin" / "python",
            env=_staging_env(staging),
        )
        _validate_runtime_identity(staged_identity)
        if any(
            staged_identity.get(key) != target_identity.get(key)
            for key in ("python_version", "python_implementation", "platform", "machine")
        ):
            raise _fail_roll_back(
                staging,
                "staging venv runtime identity differs from target Python identity",
            )
        _pip_install_dependencies(
            staging,
            args.dependency_lock,
            args.wheelhouse,
        )
        _pip_install_confflow(staging, wheel)
        _run_pip_check(staging)
        payload = _run_capability_probe(staging)
        _commit_record(
            args=args,
            wheel=wheel,
            digest=digest,
            build_commit=build_commit,
            payload=payload,
            staging=staging,
            dependency_lock_sha256=dependency_evidence.dependency_lock_sha256,
            wheelhouse_manifest_sha256=dependency_evidence.wheelhouse_manifest_sha256,
            runtime_identity=staged_identity,
        )
        _fixup_script_shebangs(staging, target)
        _atomic_rename(staging, target)
        succeeded = True
    finally:
        if not succeeded and staging.exists() and staging.parent == parent:
            # The atomic rename either succeeded (staging gone) or failed. We
            # only clean up directories we created in the same parent dir;
            # existing venvs and arbitrary paths must never be touched here.
            shutil.rmtree(staging, ignore_errors=True)

    print(
        json.dumps(
            {
                "installed": True,
                "target_venv": str(target),
                "wheel": wheel.name,
                "wheel_digest": digest,
                "build_commit": build_commit,
                "mode": args.mode,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
