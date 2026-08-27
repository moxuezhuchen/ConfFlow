# Release Process

ConfFlow uses a GitHub Actions release workflow that builds, verifies,
attests, and publishes immutable tagged release artifacts. PyPI publication
remains separate manual or future work.

Automated by `.github/workflows/release.yml`:

- Build wheel and source distribution.
- Generate `SHA256SUMS`.
- Generate a CycloneDX SBOM as `sbom.cdx.json`.
- Generate GitHub build provenance attestation and cryptographically verify its
  subject digest, repository, workflow, tag ref, and commit before publishing.
- Bind the event ref/SHA, local annotated tag, and remote annotated tag before
  publishing, then re-read the remote tag after publishing.
- Require immutable releases to be enabled and the target release to be absent.
- Write release provenance, publish an explicit asset set, require the new
  release to be immutable, and download every asset to recheck bytes and hashes.

The current `gh` CLI treats `--signer-repo` and `--signer-workflow` as mutually
exclusive actor-identity policies. The workflow uses only the more precise
`--signer-workflow OWNER/REPOSITORY/.github/workflows/release.yml`; its full
identity includes the repository and workflow path. `--repo` remains the
required artifact/attestation lookup scope and is compatible with that policy.

Still manual or not configured:

- PyPI publishing.
- PyPI trusted publishing and any additional SLSA policy layers.

## 1. Choose The Version

Update the version in `pyproject.toml`:

```toml
[project]
version = "X.Y.Z"
```

Use a version that matches the intended release tag.

## 2. Update The Changelog

Update `CHANGELOG.md` before tagging. Include user-visible changes, compatibility changes, bug fixes, and security notes when applicable.

## 3. Run Local Checks

Run at least:

```bash
black --check confflow tests
ruff check .
mypy confflow
./scripts/test.sh -q
```

For coverage:

```bash
./scripts/test.sh --cov=confflow --cov-report=term-missing
```

Confirm GitHub Actions CI is green for the release commit.

## 4. Build Wheel And Source Distribution

The release artifact workflow builds wheel and source distribution only on tag
pushes matching `v*`. Manual dispatch is intentionally unavailable because the
release gate binds `GITHUB_REF` and `GITHUB_SHA` to an annotated remote tag.

For local verification, install build tooling if needed:

```bash
python -m pip install build
```

Build artifacts:

```bash
# Keep the wheel build rooted in the clean git checkout so __build__.py
# receives the commit and dirty-state provenance.
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/confflow-build.XXXXXX")"
python -m build --sdist --outdir "$BUILD_DIR"
python -m build --wheel --outdir "$BUILD_DIR"
echo "Release artifacts: $BUILD_DIR"
```

Expected outputs are under `$BUILD_DIR`, typically:

- `$BUILD_DIR/confflow-X.Y.Z-py3-none-any.whl`
- `$BUILD_DIR/confflow-X.Y.Z.tar.gz`

## 5. Generate Checksums

The release artifact workflow writes `dist/SHA256SUMS`. For local verification, generate SHA256 checksums:

```bash
python -m pip hash dist/*
```

Alternatively, use a platform checksum tool such as `sha256sum dist/*` when available. Publish checksums with the release notes.

## 6. SBOM Status

The release artifact workflow generates a CycloneDX SBOM from the controlled
runtime lock and stores it as `dist/sbom.cdx.json`. Generation is fail closed:
the release is not created if the SBOM is missing or invalid.

## 7. Tag And Publish A GitHub Release

### Owner immutable-release preflight

The workflow's `GITHUB_TOKEN` intentionally has no repository Administration
permission and therefore cannot read the administration-only
`immutable-releases` endpoint. Before creating the annotated tag, a repository
owner must use an authenticated administrative session to confirm immutable
releases are enabled and bind that result to the exact release commit:

```bash
REPOSITORY="moxuezhuchen/ConfFlow"
RELEASE_COMMIT="$(git rev-parse HEAD)"
test "$(gh api -H "X-GitHub-Api-Version: 2026-03-10" \
  "repos/${REPOSITORY}/immutable-releases" --jq .enabled)" = "true"
gh variable set RELEASE_IMMUTABLE_PREFLIGHT_SHA \
  --repo "$REPOSITORY" --body "$RELEASE_COMMIT"
test "$(gh variable get RELEASE_IMMUTABLE_PREFLIGHT_SHA \
  --repo "$REPOSITORY")" = "$RELEASE_COMMIT"
```

Do not pass a personal or administrative token into the workflow. At job
startup GitHub snapshots the repository variable; the workflow fails closed
unless it is non-empty and exactly equals `GITHUB_SHA` and the local annotated
tag's peeled commit. The workflow still verifies the created release's actual
`isImmutable=true` state after publication.

Create an annotated tag from the verified commit. Pushing a `v*` tag triggers the release artifact workflow:

```bash
git tag -a vX.Y.Z -m "ConfFlow X.Y.Z"
git push origin vX.Y.Z
```

The workflow creates the immutable GitHub Release itself. It uploads only its
explicit standard asset set, including the wheel, sdist, SBOM, attestation
bundle and verification record, provenance, release/install dependency lock,
wheelhouse manifest, release notes, and `SHA256SUMS`. It then downloads those
assets and verifies exact filenames, byte identity, and checksums. Do not create
the release manually.

### Failed v2.1.4 attempt

`v2.1.4` is a protected tag-only failed release. Run `33080394812` stopped at
the administration-only immutable-releases GET with HTTP 403 before creating a
GitHub Release. Consequently there is no GitHub Release or release assets for
`v2.1.4`. The protected tag must not be deleted, moved, or reused; `v2.1.5` is
the first fix-forward release line.

### Failed v2.1.5 attempt

`v2.1.5` is also a protected tag-only failed release. Run `33082629930`
reached cryptographic attestation verification, where `gh` rejected the
mutually exclusive `--signer-repo` and `--signer-workflow` flags. It stopped
before release creation, so there is no GitHub Release or release assets for
`v2.1.5`. The protected tag must not be deleted, moved, or reused; `v2.1.6` is
the next fix-forward release line.

## 8. PyPI Status

PyPI publication is not automated. Do not assume a package is available on PyPI unless maintainers have explicitly published it.

If PyPI publishing is introduced later, document token handling, trusted publishing, test PyPI validation, and rollback limitations.

## 9. Three-Layer Release / Install Provenance (since v1.4.4)

ConfFlow v1.4.4 introduces a three-layer provenance model that the
wheel build, the release workflow, and the deployer all participate in.
**The wheel never describes its own digest.**

### Layer 1 — Wheel-internal build provenance

`setup.py` (`BuildPyWithProvenance`) injects exactly two constants into
`confflow/__build__.py` at wheel build time:

```python
COMMIT: str | None = "<40-char git commit>"
DIRTY: bool | None = True/False
```

These describe *what source built this wheel* — they are not a hash of
the wheel file and they must never be treated as such. The wheel's own
filename and digest are deliberately absent from `__build__.py` and
must never be added back.

### Layer 2 — External `SHA256SUMS` (authoritative wheel digest)

The release artifact workflow writes `dist/SHA256SUMS` next to the
wheel. The deployer (`scripts/install_release_wheel.py`) requires this
file and refuses to install when the on-disk wheel digest does not
match the row in `SHA256SUMS`.

Format:

```
<sha256>  confflow-X.Y.Z-py3-none-any.whl
```

`SHA256SUMS` must contain exactly one row for the target wheel. Globs
(`*.whl`) and duplicate entries are rejected.

### Layer 2a - Controlled Python runtime dependencies

The 2.1.6 release/install target is CPython 3.12 / Linux x86_64 and is
derived from the verified 1.4.4 production venv. The committed release lock is
`release/confflow-2.1.6-py312-linux-x86_64.lock`; the matching wheelhouse
manifest is `release/confflow-2.1.6-py312-linux-x86_64.SHA256SUMS`. Together
they cover every direct and transitive distribution for that one deployment
target with exact versions and SHA-256 hashes.

This release/install lock is deliberately not described as the Python 3.10-3.13
development-lock matrix. Source CI still resolves `.[dev]` for each supported
Python version, so its passing matrix is not completion evidence for
the plan's separate multi-version development-lock work. The original resolver
version used to select the production-derived dependency set was not recorded.
Release verification and repeatable wheelhouse download use `pip==26.0.1`.

Wheelhouse regeneration from the checked-in selection and hashes:

```bash
python -m pip install --disable-pip-version-check "pip==26.0.1"
python -m pip download --disable-pip-version-check --only-binary=:all: \
  --require-hashes --dest <wheelhouse> \
  -r release/confflow-2.1.6-py312-linux-x86_64.lock
```

That command reproduces the wheelhouse; it does not claim to re-resolve or
update dependency selections. A future lock update must record the resolver
tool/version and regeneration inputs when the selections are made.

The installer requires both --dependency-lock and --wheelhouse. The
wheelhouse must contain only the manifest and the binary wheels listed by it.
Candidate and production mode both fail closed when either input is absent,
when a wheel is missing, extra, altered, an sdist, or incompatible with
Python 3.12 Linux x86_64. No system site-packages or network index is used.

The staged install sequence is:

1. pip install --no-index --find-links --require-hashes -r <lock>, with
   --only-binary=:all:.
2. pip install --no-index --no-deps <exact-confflow-wheel>.
3. pip check followed by the capability probe.

The install provenance records the lock digest, wheelhouse manifest digest,
Python version/implementation, and platform/machine identity alongside the
wheel and release attestation fields.

### Layer 3 — Target-venv `install-provenance.json`

After successful checksum verification the deployer writes
`<sys.prefix>/share/confflow/install-provenance.json` inside the
target venv. The schema is:

```json
{
  "schema": "confflow.install-provenance.v2",
  "package": "confflow",
  "version": "X.Y.Z",
  "wheel_filename": "confflow-X.Y.Z-py3-none-any.whl",
  "wheel_sha256": "<digest>",
  "dependency_lock_sha256": "<digest>",
  "wheelhouse_manifest_sha256": "<digest>",
  "python_version": "3.12.3",
  "python_implementation": "CPython",
  "platform": "linux-x86_64",
  "machine": "x86_64",
  "build_commit": "<40-char git commit>",
  "build_dirty": false,
  "release_repository": "<owner>/<repo>",
  "release_tag": "vX.Y.Z",
  "release_tag_commit": "<peeled commit>",
  "attestation_verified": true,
  "attestation_subject_digest": "<digest>",
  "installed_at": "<UTC timestamp>"
}
```

The capability probe (`confflow --capabilities --json`) reads this
record and surfaces it as:

```json
"producer": {
  "wheel": {"filename": "...", "sha256": "..."},
  "install_provenance": {"status": "verified", "reason_code": null}
}
```

When the file is missing or invalid the probe surfaces the v4
diagnostic shape (`wheel.filename/sha256 = null` plus a non-`verified`
status). JobDesk's production gate rejects every non-`verified`
status. The literal string `"unbound"` is never emitted.

### Gate A (pre-tag) vs Gate B (tagged) provenance rules

* **Gate A — pre-tag candidate** uses `--mode candidate`. The deployer
  writes `attestation_verified=false` even if an attestation is
  provided. Candidate venvs never pass JobDesk's production gate.
* **Gate B — tagged release** uses `--mode production` with an
  approved `--attestation`. `attestation_verified=true` only ever
  appears for Gate B installs.
* Gate A and Gate B are independent builds. Gate B's wheel digest is
  not back-filled into a Gate A wheel; each gate builds its own wheel
  from its own clean checkout.

### Attestation workflow status

GitHub Artifact Attestations are wired into the tagged release workflow.
The workflow attests the final wheel, exports the attestation bundle, and
writes a release attestation/provenance record bound to the tagged commit and
wheel digest. The deployer still validates the downloaded attestation JSON
and subject digest for `--mode production` Gate B installs; this is a release
integrity check, not a claim of full SLSA-style hardening.


## 10. Post-Release Checks

After publishing:

- Verify the GitHub Release points to the intended commit.
- Download artifacts and verify checksums.
- Review the SBOM if it was generated.
- Install the wheel in a clean environment.
- Run a smoke test such as `confflow --help`.
- Confirm release notes link to `SECURITY.md` for vulnerability reporting.

## 11. Rollback Or Failed Release

If a release fails before public announcement:

- Delete incorrect draft releases or artifacts.
- Delete the tag only if it has not been consumed externally.

If a release has already been consumed:

- Do not silently rewrite history.
- Publish a new patch version with a clear fix or yanked-release note.
- Document the issue and mitigation in `CHANGELOG.md` and the GitHub Release notes.
