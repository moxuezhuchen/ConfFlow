# ConfFlow

ConfFlow is an independent workflow automation tool for computational
chemistry. JobDesk can act as a GUI/remote-execution consumer through the
versioned capability and artifact contracts, but neither project embeds the
other's runtime implementation.

## Current architecture candidate (2026-08-12)

The isolated candidate is the `codex/post-phase-f-architecture-phase0` release
branch, based on stable `6981935` / v2.0.0. The release-preparation target is
package version `2.1.2`, fix-forwarded from the published v2.1.1 release after
side-by-side acceptance found a Windows fixture-entrypoint identity defect.
The superseded pre-publication v2.1.0 tag failed its release-only Linux
venv-path gate. The v2.1.0 tag
must not be reused.

| Role | Ref / identity | State |
|---|---|---|
| Dirty historical JobDesk checkout | `C:\dft\tool\jobdesk-dev` @ `89d232a` | preserved user-owned worktree and package metadata; not a release source |
| Dirty historical ConfFlow checkout | `/opt/ConfFlow` @ `10e457d` | preserved historical source; not a release source |
| ConfFlow stable baseline | `6981935` / v2.0.0 | released production baseline |
| ConfFlow architecture candidate | `codex/post-phase-f-architecture-phase0` / v2.1.2 release tag | isolated v2.1.2 fix-forward release candidate |
| Paired JobDesk candidate | `c01b082` | isolated consumer candidate |
| Production/promotion endpoint | v0.6.0 + v2.0.0 configured pairing | unchanged; no candidate endpoint authorized |

The workflow facade now delegates to planner, resume policy, executor, and
finalizer boundaries. `ExecutionService` retains repository CAS and lifecycle
ownership while pure request/artifact/cursor/identity policy lives in
`application/execution/policy.py`. The control worker consumes a validated
queued intent and delegates sidecar publication and workflow invocation through
focused security-boundary components. `control.v1` schemas, state names,
cursor semantics, errors, and artifact rules remain frozen.

Non-compute candidate acceptance, real-launcher scientific acceptance, release,
and production promotion are separate gates. The current candidate has not
passed or been authorized for the real Gaussian/ORCA launcher gate.

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

## ConfFlow ↔ JobDesk Capability Handshake (v2.1.2 candidate)

ConfFlow 2.1.2 implements a version/capability probe used by JobDesk to
validate compatibility before uploading or submitting workflow tasks:

```bash
confflow --version          # prints "2.1.2"
confflow --capabilities --json
```

Capability contract (JSON, schema version **4**):

```json
{
  "schema_version": 4,
  "version": "2.1.2",
  "capabilities": {
    "workflow_state": true,
    "resume": true,
    "dag": true,
    "control_worker": true
  },
  "artifacts": {
    "run_summary": "run_summary.json",
    "workflow_stats": "workflow_stats.json",
    "workflow_state": ".workflow_state.json",
    "run_report": "{basename}.txt",
    "min_xyz": "{basename}min.xyz",
    "output_manifest": "output_manifest.json"
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
    "commit": "<40-char git commit>",
    "dirty": false
  },
  "producer": {
    "package": "confflow",
    "version": "2.1.2",
    "build": {
      "commit": "<40-char git commit>",
      "dirty": false
    },
    "wheel": {
      "filename": "confflow-2.1.2-py3-none-any.whl",
      "sha256": "<external SHA-256SUMS digest>"
    },
    "install_provenance": {
      "status": "verified",
      "reason_code": null
    }
  },
  "executable": {
    "path": "/path/to/confflow",
    "sha256": "<executable SHA-256>",
    "python": "/path/to/installer/python"
  }
}
```

The release's `control_worker` value is `true` only on POSIX hosts with
`O_DIRECTORY` and `O_NOFOLLOW`; Windows installations report `false` and must
not accept the worker handoff.

The released JobDesk v0.6.0 pairing uses the stable v2.0.0 release; the
current JobDesk architecture candidate is tracked separately as `e6003be` and
targets v0.7.0. v1.4.6 remains rollback-only. The v2.1.2 candidate must be
paired with a consumer that
validates this capability contract before the first input upload and repeats
the preflight at submit time.

The candidate `confflow-control-worker` entrypoint is a producer-owned
handoff, not an agent-queue compatibility layer. Its `prepare.input_manifest`
locator must contain the canonical `worker-handoff.schema.json` envelope and
the persisted digest must be the envelope digest. The envelope is limited to
one task; a batch must be split before prepare. The worker stages the validated
configuration and input bytes under the private StateRoot, preserves the
original input basename for `{basename}.txt` and `{basename}min.xyz`, and
publishes those fixed sidecars beside the task work directory (the remote
result base) while keeping the normal JSON/manifest artifacts in the task
work directory.
Every worker that may be recovered after a crash must be launched in its own
session, for example with `setsid`; a marker from an ordinary shell process
group is intentionally not auto-recovered. The legacy stable JobDesk path has
no consumer for this handoff and must not silently send its private
`.jobdesk-control/input-manifest.json` to it.

### v4 contract additions

* **`producer`** block reports the *install* provenance: package name,
  version, build commit/dirty, the **wheel filename and SHA-256** that
  were actually deployed, and an `install_provenance.status` /
  `reason_code` snapshot. `status` is one of `"verified"`,
  `"missing"`, `"invalid"`. Only `"verified"` is acceptable as
  production input.
* **`executable`** block reports the resolved on-disk `confflow` path,
  its own SHA-256 (so a tampered or locally-rebuilt executable is
  detectable), and the `python` interpreter that hosts the venv.
* **`control_worker`** advertises the released producer-owned worker
  handoff. It is `true` only on POSIX hosts with secure directory-descriptor
  primitives; Windows installs report `false` and must not accept worker
  handoffs.
* **Six artifacts**, in addition to the v3 set:
  * `output_manifest` — machine-readable multi-terminal output
    index written alongside the run artifacts.
  * `run_summary`, `workflow_stats`, `workflow_state`, `run_report`,
    `min_xyz` — unchanged.

### Four content schemas stamped into producer artifacts

Each producer artifact carries a stable `content_schema` field. The
producer contract enumerates them; JobDesk matches the exact string,
not a prefix.

| Artifact | Filename | content_schema |
| --- | --- | --- |
| run_summary | `run_summary.json` | `confflow.run_summary.v1` |
| workflow_stats | `workflow_stats.json` | `confflow.workflow_stats.v1` |
| workflow_state | `.workflow_state.json` | `confflow.workflow_state.v1` |
| output_manifest | `output_manifest.json` | `confflow.output_manifest.v1` |

### Release / install provenance — three layers

ConfFlow no longer bakes its own wheel digest into the wheel.

1. **Wheel-internal build provenance** — `confflow.__build__.COMMIT`
   and `DIRTY` are set by `setup.py`'s build hook and describe only
   *what source built this wheel*, never the wheel file itself.
2. **External release provenance** — the release workflow writes a
   `SHA256SUMS` file next to the wheel in `dist/`. The deployer
   refuses to install on checksum mismatch.
3. **Target venv install provenance** — the deployer creates
   `<sys.prefix>/share/confflow/install-provenance.json` after
   verifying the wheel against `SHA256SUMS` (and, in production,
   against the approved attestation). The capability probe reads
   this file; the wheel's `__build__.COMMIT` is *not* trusted as
   the wheel's identity.

The capability probe surfaces `producer.wheel.filename` /
`producer.wheel.sha256` and `producer.install_provenance.status`
from this record. A `status` other than `"verified"` means the
an install without verified provenance is diagnostic-only; JobDesk's production gate rejects
it.

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
