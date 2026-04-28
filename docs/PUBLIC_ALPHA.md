# Public Alpha Preview

ConfFlow is suitable to publish as a public alpha preview. It is not production-ready.

## Current Status

- Public level: alpha preview.
- Installation path: source checkout and local editable install are the recommended paths.
- Release status: GitHub Actions can build wheel/sdist artifacts and checksums, but PyPI publishing is not automated.
- Security posture: documented security model, private-reporting guidance, CI, Dependabot, and non-blocking OpenSSF Scorecard are in place.

## Suitable For

- Users familiar with computational chemistry tools who want to inspect or trial the workflow.
- Contributors reviewing architecture, tests, configuration, and documentation.
- Small, sanitized examples in isolated directories.
- Early feedback on Gaussian/ORCA workflow automation assumptions.

## Not Suitable For

- Production-ready, unattended, or high-throughput runs without local validation.
- Running untrusted YAML, XYZ, Gaussian keywords, ORCA blocks, or executable paths.
- Workloads containing sensitive structures or proprietary logs unless local redaction and isolation are already handled.
- Treating generated release artifacts as a hardened supply-chain release.

## How To Give Feedback

- Use GitHub issues for bugs and feature requests.
- Include Python version, operating system, ConfFlow commit or version, RDKit version, and Gaussian/ORCA version when relevant.
- Share only minimal sanitized XYZ/YAML inputs and redacted log snippets.
- Follow `SECURITY.md` for vulnerability reports or sensitive behavior. Do not post private structures, license details, tokens, raw logs, or proprietary calculation data publicly.

## Known Limitations

- No dry-run, read-only, or preview mode yet.
- Public CI uses fake/mock external-program behavior; real Gaussian/ORCA environments still need local or site-specific validation.
- PyPI publishing, GitHub Release publishing, artifact provenance, and attestations are not automated.
- OpenSSF Scorecard is informational. Private repositories skip SARIF code-scanning upload by default and keep the result as a workflow artifact.
- Dependency security updates currently rely on Dependabot and existing GitHub security signals. `pip-audit` and `safety` are not part of the supported local or CI baseline; if added later, they should start as scheduled or non-blocking checks.
- Branch protection and GitHub About metadata must be configured manually in GitHub Settings.

## First Checks After Switching Public

- Confirm `CI` is green on `main`.
- Run or rerun `Scorecard` and confirm it follows the public SARIF upload path only when code scanning is available.
- Run `Release Artifacts` manually and confirm wheel/sdist, `SHA256SUMS`, and SBOM behavior are still as expected.
- Review Dependabot PR volume after the repository becomes public.
- Configure About description, website, topics, and `main` branch protection in GitHub Settings.

## Manual GitHub Settings

Recommended About description:

```text
Alpha-preview computational chemistry workflow automation for conformer generation, quantum-chemistry jobs, deduplication, and text reports.
```

Recommended website:

```text
https://github.com/moxuezhuchen/ConfFlow#readme
```

Recommended topics:

```text
computational-chemistry
quantum-chemistry
conformer-generation
workflow-automation
cheminformatics
molecular-modeling
gaussian
orca
rdkit
python
yaml
```

Recommended `main` branch protection:

- Disallow force pushes.
- Disallow branch deletion.
- Require pull requests before merging.
- Require one approving review.
- Dismiss stale approvals after new commits.
- Require conversation resolution.
- Require status checks before merging, using the CI and coverage check names shown by GitHub.
- Keep Scorecard informational; do not make it a required check while the project is in alpha preview.
