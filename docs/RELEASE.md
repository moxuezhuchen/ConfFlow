# Release Process

ConfFlow currently uses a mostly manual release process with a minimal GitHub Actions artifact workflow.

Automated by `.github/workflows/release.yml`:

- Build wheel and source distribution.
- Generate `SHA256SUMS`.
- Attempt to generate a CycloneDX SBOM as `sbom.cdx.json`.
- Upload the `dist/` bundle as a GitHub Actions artifact.

Still manual or not configured:

- PyPI publishing.
- GitHub Release creation and release note editing.
- Artifact provenance / attestation.
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
python -m build
```

Expected outputs are under `dist/`, typically:

- `dist/confflow-X.Y.Z-py3-none-any.whl`
- `dist/confflow-X.Y.Z.tar.gz`

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

## 9. Provenance And Attestation Status

Artifact provenance and GitHub artifact attestation are not currently configured. Future work should evaluate GitHub Artifact Attestations or a SLSA-compatible workflow after the release artifact process is stable.

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
