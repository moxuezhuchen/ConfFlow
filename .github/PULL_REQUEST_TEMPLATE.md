## Summary

This PR contains a large test-suite refactor focused on readability, maintainability, and stability:

- Added shared test helpers and fixtures (`tests/_helpers.py`, updates to `tests/conftest.py`).
- Refactored many tests to use fixtures (`cd_tmp`, `input_xyz`, `config_yaml`, etc.) and parameterization.
- Moved selected coverage-only tests from `tests/coverage_push/` into top-level `tests/` after review.
- Added `tests/README.md` and `tests/coverage_push/README.md` with guidance.
- Added a CI job example `.github/workflows/coverage_push.yml` and a helper script `scripts/run_coverage_push.sh` to run `tests/coverage_push` separately.

## How to test locally

Run the full test suite and the coverage_push subset separately:

```bash
pytest -q tests
./scripts/run_coverage_push.sh
```

Ensure all tests pass and review the new `tests/README.md` for test rules.

## Checklist

- [ ] Ran `pytest -q tests` locally and all tests passed
- [ ] Verified `./scripts/run_coverage_push.sh` runs (CI-only tests)
- [ ] Reviewed moved tests to ensure no duplicate coverage
- [ ] Updated documentation where applicable

If you want, I can also split the CI job into a separate workflow file per your CI conventions or open a draft PR for review.
