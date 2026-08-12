# Release Process

## Candidate versus production (2026-08-12)

The current architecture candidate is `7ef0df3`, based on the released
`6981935` / v2.0.0. It has passed isolated non-compute regression, static, and
build checks, but it is not a release artifact and must not be promoted by
changing `/usr/local/bin/confflow`, JobDesk server entries, or the production
venv. The paired JobDesk candidate is `908b153`.

Release publication, side-by-side installation, candidate acceptance, and
production endpoint promotion are separate approvals. Because this candidate
changes workflow execution and the control worker, promotion additionally
requires one separately authorized bounded real Gaussian/ORCA launcher
acceptance. Once formal release and side-by-side acceptance have passed, but
the real-launcher authorization is not granted, the correct terminal state is
“RELEASED AND SIDE-BY-SIDE VERIFIED; PRODUCTION PROMOTION NOT AUTHORIZED”.

The current candidate has not reached that state.

ConfFlow uses a GitHub Actions release workflow that builds, verifies,
attests, and publishes the tagged release artifacts. PyPI publication and
full SLSA-style hardening remain separate manual or future-work concerns.

Automated by `.github/workflows/release.yml`:

- Build wheel and source distribution.
- Generate `SHA256SUMS`.
- Generate a CycloneDX SBOM as `sbom.cdx.json`.
- Generate GitHub build provenance attestation and an exported attestation bundle.
- Write release provenance and publish the `dist/` bundle as a GitHub Release.

Still manual or not configured:

- PyPI publishing.
- Full SLSA-style release hardening.

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

The release artifact workflow builds wheel and source distribution on tag pushes matching `v*` and on manual dispatch.

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

The release artifact workflow attempts to generate a CycloneDX SBOM with `cyclonedx-bom` and stores it as `dist/sbom.cdx.json`. This is a first-pass software bill of materials, not a complete supply-chain attestation.

If SBOM generation fails, the workflow continues and still uploads the wheel/sdist/checksum artifacts. Treat SBOM completeness as an alpha preview improvement area until the workflow has been validated across releases.

## 7. Tag And Publish A GitHub Release

Create an annotated tag from the verified commit. Pushing a `v*` tag triggers the release artifact workflow:

```bash
git tag -a vX.Y.Z -m "ConfFlow X.Y.Z"
git push origin vX.Y.Z
```

After the workflow completes, download the `confflow-release-artifacts` bundle and create a GitHub Release from the tag. Attach:

- Wheel and source distribution.
- SHA256 checksums.
- Changelog excerpt.
- Known limitations and compatibility notes.

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

The 1.4.5 Linux x86_64 / CPython 3.12 runtime is based on the verified
1.4.4 production venv. The committed lock is
release/confflow-1.4.5-py312-linux-x86_64.lock and contains every direct
and transitive runtime distribution at an exact version with SHA-256 hashes.
The matching wheelhouse manifest is
release/confflow-1.4.5-py312-linux-x86_64.SHA256SUMS.

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
