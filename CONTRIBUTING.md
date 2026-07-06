# Contributing To ConfFlow

> **Notice — Archived Repository**
> ConfFlow has been folded into the [JobDesk](https://github.com/moxuezhuchen/jobdesk) monorepo as `jobdesk_app/workflow/` and `jobdesk_app/agent/`. This repository is **read-only / reference-only**. All active development, bug fixes, and feature work happen in JobDesk.
>
> The text below describes the historic contribution policy. **Pull requests opened here will be closed without merge.** If you need to change behaviour, open the issue / PR against JobDesk instead.

ConfFlow is an alpha preview computational chemistry workflow project. Contributions should stay focused, reviewable, and consistent with the existing Python code and documentation style.

## Development Setup

Clone the repository:

```bash
git clone https://github.com/moxuezhuchen/ConfFlow.git
cd ConfFlow
```

Create a Python 3.10+ environment and install development dependencies:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

See `docs/DEVELOPMENT.md` for more development notes.

## Local Checks

Recommended quick checks before opening a pull request:

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

See `docs/TESTING.md` for the test layout, fixtures, and current CI coverage boundaries.

## Pull Request Expectations

- Keep changes small and tied to one problem.
- Include tests for behavior changes and bug fixes.
- Update docs when user-facing commands, configuration, outputs, security assumptions, or compatibility change.
- Do not include private molecular structures, proprietary logs, license information, tokens, or credentials.
- Explain external program impact when changing Gaussian/ORCA input generation or execution behavior.
- Note any file deletion, overwrite, backup, or path policy changes explicitly.

## Reporting Bugs

> **For this archived repository:** open the bug against
> [moxuezhuchen/jobdesk](https://github.com/moxuezhuchen/jobdesk/issues) instead.
> Issues opened here may be transferred or closed without action.

Use the bug report issue template. Include:

- Operating system and Python version.
- ConfFlow version or commit SHA.
- RDKit version and Gaussian/ORCA version when relevant.
- Minimal sanitized XYZ/YAML input.
- Redacted logs or error snippets.

Do not post sensitive data publicly. For security issues, follow `SECURITY.md`.

## Feature Requests

> **For this archived repository:** open the feature request against
> [moxuezhuchen/jobdesk](https://github.com/moxuezhuchen/jobdesk/issues) instead.

Use the feature request template. Describe the scientific or workflow scenario, expected behavior, alternatives considered, and whether it requires new external program support or new workflow steps.
