# ConfFlow

> **Archived**: ConfFlow is now part of the [JobDesk](https://github.com/moxuezhuchen/jobdesk) monorepo.
> This repository is kept for reference. All active development continues in JobDesk.

## ConfFlow (Archived)

ConfFlow was a workflow automation tool for computational chemistry. It has been migrated into the **JobDesk** monorepo as `jobdesk_app/workflow/` and `jobdesk_app/agent/`.

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

## ConfFlow ↔ JobDesk Capability Handshake (v1.4.3)

ConfFlow 1.4.3 implements a version/capability probe used by JobDesk to
validate compatibility before uploading or submitting workflow tasks:

```bash
confflow --version          # prints "1.4.3"
confflow --capabilities --json
```

Capability contract (JSON, schema version 3):

```json
{
  "schema_version": 3,
  "version": "1.4.3",
  "capabilities": {
    "workflow_state": true,
    "resume": true,
    "dag": true
  },
  "artifacts": {
    "run_summary": "run_summary.json",
    "workflow_stats": "workflow_stats.json",
    "workflow_state": ".workflow_state.json",
    "run_report": "{basename}.txt",
    "min_xyz": "{basename}min.xyz"
  },
  "commands": {
    "bash": true,
    "nohup": true,
    "setsid": true,
    "xargs": true,
    "sha256sum": true,
    "mktemp": true,
    "base64": true
  },
  "build": {
    "commit": "7b37c223d2c07a062ab62965911c3cd8d6641591",
    "dirty": false
  }
}
```

JobDesk requires `confflow>=1.4.3,<2.0`, validates the capability contract
before the first input upload, and repeats the preflight at submit time.
The `commands` block reports the host-side utilities that ConfFlow relies
on for shell launching, scratch staging, and integrity checks. The
`build` block surfaces the exact 40-character git commit that produced
the running wheel plus a `dirty` flag; a non-zero `dirty` value triggers a
non-fatal warning at submit time so operators can spot local rebuilds.

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
| `confrefine` | Deduplicate and filter conformers |
| `confts` | TS-focused tooling, including scan rescue support |

Calc-step execution ships only as a workflow step (`type: calc`) driven by
the YAML config; no standalone calc CLI is exposed in 1.4.3. Examples:

```bash
# Chain-based conformer generation
confgen mol.xyz --chain 1-2-3-4-5 --steps 180,180,180,180 -y
# Explicit angle sets
confgen mol.xyz --chain 1-2-3-4-5 --angles "0,120,240;0,60,120,180;180;0,120" -y
# Run a calc step (B3LYP optimization + frequency) from the workflow YAML
confflow search.xyz -c confflow.example.yaml
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
