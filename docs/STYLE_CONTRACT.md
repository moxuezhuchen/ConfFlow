# ConfFlow Style Contract

This document is the single source of truth for style consistency across code, input, and output.

## 1) Code style

- Formatter: `black` (line length: `100`)
- Lint/import/order: `ruff` (`E,F,I,B,UP,D`)
- Type check: `mypy`
- Test runner: `pytest`

Run locally:

```bash
black .
ruff check .
mypy confflow
pytest -q
```

### 1.1) File header

Every `.py` file must start with:

```python
#!/usr/bin/env python3
from __future__ import annotations
```

The `from __future__ import annotations` line must appear immediately after the shebang (or
after the module-level docstring when one is present between them).

### 1.2) Docstrings

- **Style**: NumPy style (sections delimited by underline: `Parameters`, `Returns`, `Raises`,
  `Examples`, etc.).
- **Language**: English only.
- Every public module, class, and function must have a docstring.
- Module-level docstrings should be a concise single line or a short paragraph.

Example:

```python
def load_xyz(path: str) -> list[list[str]]:
    """Load an XYZ file and return atom blocks.

    Parameters
    ----------
    path : str
        Path to the XYZ file.

    Returns
    -------
    list[list[str]]
        Parsed atom blocks.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    """
```

### 1.3) Type annotations

- Use PEP 604 syntax: `X | None` instead of `Optional[X]`, `X | Y` instead of `Union[X, Y]`.
- Do **not** import `Optional` or `Union` from `typing`.
- Use lowercase built-in generics: `list[int]`, `dict[str, Any]`, `tuple[int, ...]`.

### 1.4) Logging & messages

- All log messages and inline comments must be in **English**.
- Message level prefixes: `INFO`, `WARNING`, `ERROR`, `SUCCESS`.

### 1.5) Exports

- Every public module must declare `__all__` listing all public symbols.
- `__all__` should use the multi-line list format with trailing comma.
- Sub-package `__init__.py` files should re-export key symbols and declare `__all__`.

### 1.6) Exceptions

- Prefer custom exceptions from `confflow.core.exceptions` (`ConfFlowError`,
  `InputFileError`, `XYZFormatError`, `ValidationError`, `ConfigurationError`).
- Avoid bare `RuntimeError` or `ValueError` in application code; map them to the
  appropriate custom exception.

## 2) Input contract

- Workflow config key for TS bond is **only** `ts_bond_atoms`.
- Legacy key `ts_bond` is rejected in workflow YAML.
- CLI input path resolution always uses absolute paths internally.

## 3) Output contract

- All CLI commands write runtime logs to `<input_basename>.txt` in the input file directory.
- CLI exit codes are unified:
  - `0`: success
  - `1`: usage/input/config error
  - `2`: runtime failure
- Message style is English with uppercase level prefixes (`INFO`, `WARNING`, `ERROR`, `SUCCESS`).
- Text output width follows fixed `100` columns when emitted by shared console helpers.

## 4) Change policy

- Any change to user-facing messages, return codes, or output file naming must include tests.
- Any new CLI entrypoint must adopt the same output + exit code contract.
