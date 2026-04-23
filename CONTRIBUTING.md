# Contributing To ConfFlow

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

Use the bug report issue template. Include:

- Operating system and Python version.
- ConfFlow version or commit SHA.
- RDKit version and Gaussian/ORCA version when relevant.
- Minimal sanitized XYZ/YAML input.
- Redacted logs or error snippets.

Do not post sensitive data publicly. For security issues, follow `SECURITY.md`.

## Feature Requests

Use the feature request template. Describe the scientific or workflow scenario, expected behavior, alternatives considered, and whether it requires new external program support or new workflow steps.
