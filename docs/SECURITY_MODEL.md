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

`sandbox_root` restricts managed paths such as `work_dir`, `backup_dir`, and checkpoint input directories to an expected root. `allowed_executables` restricts Gaussian/ORCA executable settings to known single executable targets.

Even with these settings, run ConfFlow in a dedicated working area and keep unrelated files out of the workflow directory.

## File Read, Write, Overwrite, Delete, And Backup Behavior

ConfFlow may create or update:

- Workflow directories and step directories.
- `search.xyz`, `output.xyz`, `result.xyz`, `failed.xyz`, and related XYZ files.
- `results.db`, `.config_hash`, checkpoint metadata, and JSON summary files.
- `<input_basename>.txt` CLI output reports.
- `confflow.log` and backup copies of external program logs and outputs.

ConfFlow may remove stale step artifacts when resuming or when configuration hashes no longer match the current task. Path checks exist to reject obviously dangerous cleanup targets such as filesystem roots, home directories, repository roots, or paths outside configured sandbox roots.

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

## Dry-Run Status

ConfFlow provides a CLI `--dry-run` mode that validates inputs/configuration and previews planned workflow steps, input files, output paths, selected calculation settings, and configured executable paths without running workflow steps.

Dry-run is a planning and validation aid, not a full sandbox or complete read-only execution environment. A real workflow run can still write files, overwrite managed artifacts, clean stale outputs, and execute configured external programs.

## Network Behavior

The ConfFlow codebase currently does not implement active network calls as part of normal workflow execution. External programs, package managers, shells, schedulers, or user-provided wrappers are outside this statement and may have their own network behavior.
