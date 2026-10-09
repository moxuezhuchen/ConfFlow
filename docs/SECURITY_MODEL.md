# ConfFlow Security Model

ConfFlow is an alpha preview tool for computational chemistry workflow automation. This document describes the intended security boundary for public users and contributors.

## Trusted Input Assumption

Treat all workflow inputs as trusted input:

- YAML workflow configuration.
- XYZ structures and metadata comments.
- Gaussian keywords, route sections, link0 options, and checkpoint references.
- ORCA keywords, blocks, and executable paths.

Do not run YAML or chemistry inputs supplied by an unknown party on a machine that contains sensitive data or valuable credentials. ConfFlow validates many paths and configuration shapes, but it is not a sandbox for untrusted workloads.

## External Executable Boundary

ConfFlow prepares input files and invokes configured external programs such as Gaussian and ORCA. It does not install, license, audit, or sandbox those programs.

ConfFlow is responsible for:

- Validating configured executable strings where supported.
- Avoiding `shell=True` execution for calculation commands.
- Writing input files and collecting known output artifacts.
- Recording task status, logs, backups, and reports.

External programs are responsible for:

- Their own parsing, execution, temporary files, environment handling, and license behavior.
- Scientific correctness and convergence behavior.
- Any side effects they perform outside ConfFlow control.

## Recommended Path And Executable Limits

Use these YAML `global` options when running real workloads:

```yaml
global:
  sandbox_root: "/scratch/confjobs"
  allowed_executables:
    - "/opt/g16/g16"
    - "/opt/orca/orca"
```

`sandbox_root` restricts managed paths such as `work_dir` and checkpoint input directories to an expected root. `allowed_executables` restricts Gaussian/ORCA executable settings to known single executable targets.

Even with these settings, run ConfFlow in a dedicated working area and keep unrelated files out of the workflow directory.

## File Read, Write, Overwrite, Delete, And Backup Behavior

ConfFlow may create or update:

- Workflow directories, step directories, and the run root itself.
- V4 formal run artifacts: `run_result.json` (the published run manifest),
  `run_generation.json` (durable execution-generation record),
  `imports/<input_name>.snapshot.json` (validated-input snapshot), the
  per-step SQLite work-item store (`work_items.sqlite`), and the
  `ensemble_report.json` / `confgen_states.jsonl.gz` confgen reports.
- Captured external program output such as `stdout.log` and `stderr.log`.
- `<input_basename>.txt` CLI output reports.

Legacy compatibility entrypoints such as `--rerun-failed` fail closed and do
not create their legacy artifacts.  The historical file names `search.xyz`,
`output.xyz`, `result.xyz`, `failed.xyz`, `manifest.json`, `confflow.log`,
checkpoint metadata files, and external-log backup copies have no creation
site in the current V4 code paths reviewed for this document; their absence
under every historical mode is not proven, so they are recorded as pending
verification rather than as current artifacts.

ConfFlow may remove stale step artifacts when manifest digests no longer match the current task. Path checks exist to reject obviously dangerous cleanup targets such as filesystem roots, home directories, repository roots, or paths outside configured sandbox roots.

Users should still assume workflow directories are mutable and should not point work directories at valuable source data directories.

## Logs And Sensitive Data

Logs, `.out`, `.err`, `.chk`, reports, database rows, and backup files may contain:

- Local filesystem paths and executable paths.
- Molecule coordinates and metadata.
- Gaussian/ORCA keywords and blocks.
- External program output, warnings, and errors.
- Checkpoint names or references.
- Private or proprietary computational data.

Do not post raw logs or artifacts publicly until they have been reviewed and redacted. Security-sensitive reports should follow `SECURITY.md`.

## Pre-Execution Validation Status

ConfFlow's formal V4 runtime validates a workflow document before executing it: `confflow v4 validate --workflow FILE --json` parses and compiles the exact bytes and returns structured diagnostics without running any step, and `confflow v4 contract --json` publishes the schema/capabilities an editor checks a document against.

The legacy `--dry-run` / `--config-show` planners were retired with the released V1/V2 configuration wire (Architecture Diet PR-9): V1/V2/V3 documents now fail closed with `unsupported_workflow_version` at the outermost version discriminator, before any managed-path validation, lease, directory creation or execution.

Validation is a planning and safety aid, not a full sandbox or complete read-only execution environment. A real workflow run can still write files, overwrite managed artifacts, clean stale outputs, and execute configured external programs.

## Network Behavior

The ConfFlow codebase currently does not implement active network calls as part of normal workflow execution. External programs, package managers, shells, schedulers, or user-provided wrappers are outside this statement and may have their own network behavior.
