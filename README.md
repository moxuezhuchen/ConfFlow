# ConfFlow

ConfFlow is a workflow automation tool for computational chemistry. Starting from XYZ inputs and a YAML configuration, it can run conformer generation, quantum-chemistry steps, deduplication and filtering, and a final text report.

[![CI](https://github.com/moxuezhuchen/ConfFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/moxuezhuchen/ConfFlow/actions/workflows/ci.yml) [![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Project Status

ConfFlow is currently a **public alpha preview**.

It is intended for early evaluation by users who are already comfortable working in computational chemistry environments, reviewing source code and configuration, and validating runs locally. It is not a stable release, **not production-ready**, and not intended for enterprise deployment.

At this stage, ConfFlow is suitable for:

- Early trial runs in isolated working directories
- Reviewing workflow design, configuration, and outputs
- Local validation in environments where Gaussian, ORCA, and RDKit are already understood and managed by the user

It is not recommended for:

- Unattended runs
- Non-isolated real production compute environments
- Untrusted YAML, XYZ, Gaussian keywords, ORCA blocks, or executable paths

## Features

- YAML-driven workflow execution from XYZ inputs
- Conformer generation with explicit chain-based rotation control
- Quantum-chemistry job orchestration for Gaussian 16 and ORCA
- Conformer deduplication and filtering based on RMSD, energy windows, and imaginary-frequency criteria
- Resume support for interrupted workflows
- TS rescue support through scan-based recovery when enabled
- Final text reports written to `<input_basename>.txt`
- Lowest-energy conformer export as a single-frame XYZ file
- Flexible-chain topology mapping for multi-input workflows with matching composition

## Installation

ConfFlow currently recommends source installation.

```bash
# Editable install
pip install -e .

# Standard install
pip install .

# Optional development dependencies
pip install -e ".[dev]"
```

Requirements and packaging notes:

- Python 3.10+
- Packaging is defined in `pyproject.toml`
- RDKit is required
- `numba` is optional and only used for acceleration when installed

## Quick Start

Run a workflow with an XYZ input and a YAML config:

```bash
# Run a workflow
confflow mol.xyz -c confflow.example.yaml
# Resume from a previous checkpoint
confflow mol.xyz -c confflow.example.yaml --resume
# Enable more detailed logging
confflow mol.xyz -c confflow.example.yaml --verbose
```

By default, CLI output is written to `<input_basename>.txt` in the input directory rather than streamed to the terminal. A common way to inspect progress is:

```bash
tail -f mol.txt
```

A minimal workflow example:

```yaml
global:
  gaussian_path: "/opt/g16/g16"
  cores_per_task: 4
  total_memory: "16GB"
  sandbox_root: "/scratch/confjobs"
  allowed_executables: ["g16", "/opt/orca/orca"]
  charge: 0
  multiplicity: 1

steps:
  - name: confgen
    type: confgen
    params:
      chains: ["1-2-3-4"]

  - name: opt_b3lyp
    type: calc
    params:
      iprog: g16
      itask: opt_freq
      keyword: "B3LYP/6-31G* opt freq"
```

For a fuller configuration example, see [`confflow.example.yaml`](confflow.example.yaml).

## Safe Evaluation / Operational Boundaries

ConfFlow should be evaluated carefully and in isolation.

- Use sanitized XYZ and YAML inputs when first testing the project
- Prefer a dedicated working directory rather than a directory containing valuable source or research data
- Review `sandbox_root` and `allowed_executables` before running real Gaussian or ORCA jobs
- Treat workflow YAML, XYZ metadata, Gaussian keywords, ORCA blocks, and executable paths as trusted input only

Important limitations:

- Use `--dry-run` to validate inputs/configuration and preview planned steps before launching external programs
- `--dry-run` is a planning aid, not a full sandbox or guarantee that a later real run cannot write files
- Running a workflow can write files, overwrite managed artifacts, clean stale outputs, and launch configured external executables
- ConfFlow is not a sandbox for untrusted workloads

ConfFlow is not recommended for unattended use or for non-isolated production compute environments.

## Platform and External Dependencies

- Python: 3.10 to 3.13 are covered by CI
- Operating systems: package metadata declares OS-independent support, but public CI currently runs on Ubuntu; other platforms should be validated locally
- Required Python dependencies include RDKit, NumPy, SciPy, PyYAML, Pydantic v2, psutil, and rich
- Gaussian 16 and ORCA must be installed, licensed, and configured by the user
- ConfFlow does not install, license, audit, or sandbox Gaussian, ORCA, or other third-party executables

## Command-Line Tools

| Command | Purpose |
| --- | --- |
| `confflow` | Run a YAML-defined workflow |
| `confgen` | Generate conformers in chain mode |
| `confcalc` | Run quantum-chemistry calculations directly |
| `confrefine` | Deduplicate and filter conformers |
| `confts` | TS-focused tooling, including scan rescue support |

Examples:

```bash
# Chain-based conformer generation
confgen mol.xyz --chain 1-2-3-4-5 --steps 180,180,180,180 -y
# Explicit angle sets
confgen mol.xyz --chain 1-2-3-4-5 --angles "0,120,240;0,60,120,180;180;0,120" -y
# Direct calculation on an existing trajectory
confcalc <search.xyz> -s <settings.ini>
```

See the [Command Reference](docs/COMMAND_REFERENCE.md) for the full CLI reference.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Usage](docs/USAGE.md)
- [Command Reference](docs/COMMAND_REFERENCE.md)
- [Keyword Reference](docs/KEYWORD_REFERENCE.md)
- [Security Model](docs/SECURITY_MODEL.md)
- [Public Alpha Notes](docs/PUBLIC_ALPHA.md)
- [Development Guide](docs/DEVELOPMENT.md)
- [Testing](docs/TESTING.md)
- [Release Process](docs/RELEASE.md)
- [Style Contract](docs/STYLE_CONTRACT.md)

## Security

ConfFlow prepares files and launches user-configured external programs. It should be treated as a trusted-input tool, not as an execution sandbox.

- Do not run untrusted YAML, XYZ, Gaussian keywords, ORCA blocks, or executable paths
- Logs, `.out`, `.err`, `.chk`, reports, databases, and backup files may contain sensitive structures, paths, keywords, and proprietary computational data
- Redact logs and artifacts before posting them publicly
- For real workloads, configure `sandbox_root` and `allowed_executables` in the YAML `global` section

Please report vulnerabilities or sensitive security issues through [SECURITY.md](SECURITY.md), not through public issues.

## Project Notes

Recent engineering work has focused on packaging cleanup, clearer execution boundaries, stronger typing, resume safety, and better workflow artifact handling. Details that matter for evaluators and contributors are documented in:

- [Public Alpha Notes](docs/PUBLIC_ALPHA.md)
- [Testing](docs/TESTING.md)
- [Release Process](docs/RELEASE.md)
- [Development Guide](docs/DEVELOPMENT.md)

## Cleanup

If you need to remove local caches and build artifacts:

```bash
find . -type d -name "__pycache__" -exec rm -rf {} +
rm -rf confflow.egg-info .mypy_cache .ruff_cache build dist htmlcov coverage.xml reports .pytest_cache_temp .coverage_temp
```

For test runs with temporary artifacts redirected out of the repository root, prefer:

```bash
./scripts/test.sh
```

## License

MIT License
