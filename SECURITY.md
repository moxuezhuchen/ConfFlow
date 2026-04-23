# Security Policy

ConfFlow is an alpha preview computational chemistry workflow tool. It reads user-provided chemistry inputs and YAML configuration, writes workflow artifacts, and invokes external chemistry programs such as Gaussian and ORCA when configured to do so.

## Supported Versions

| Version | Supported |
| --- | --- |
| `main` branch | Security fixes are considered here first. |
| Latest tagged release | Currently supported when a release exists. |
| Older releases | Current support policy is not yet defined. |

The project currently does not provide a long-term support schedule.

## How To Report A Vulnerability

Prefer a private report instead of a public issue. If GitHub private vulnerability reporting or Security Advisories are enabled for this repository, use that channel.

If no private GitHub reporting channel is available, contact the maintainers privately through the repository owner before opening a public issue. The project currently does not publish a dedicated security email address.

Do not include sensitive paths, private molecular structures, proprietary calculation inputs, license details, tokens, environment variables, raw `.log` / `.out` / `.err` / `.chk` files, or private computational results in public issues.

## Expected Response

This is an alpha preview project maintained on a best-effort basis. A reasonable expected process is:

- Initial acknowledgement when a maintainer is available.
- Triage to determine whether the report is in scope.
- Private discussion of impact and reproduction details.
- Coordinated disclosure after a fix or mitigation is available, when practical.

No strict response SLA is currently guaranteed.

## Security Scope

In scope:

- Path traversal or unsafe deletion/overwrite behavior in ConfFlow-managed paths.
- Unsafe handling of configured executable paths.
- Leaks of sensitive input data through ConfFlow-generated logs or reports beyond documented behavior.
- Unsafe parsing of YAML, XYZ, Gaussian, ORCA, or workflow metadata handled by ConfFlow.

Out of scope:

- Bugs, licensing issues, or security behavior inside Gaussian, ORCA, RDKit, Python, operating systems, schedulers, shells, or other external tools.
- Results produced by untrusted or scientifically invalid input files.
- Running arbitrary untrusted YAML or chemistry inputs without sandboxing.

## Sensitive Data Handling Notes

ConfFlow may:

- Execute external programs configured by the user.
- Read and write the input directory, workflow directory, step directories, result databases, and backup directories.
- Generate or back up `.log`, `.out`, `.err`, `.chk`, XYZ, JSON, SQLite, and text report files.
- Pass selected environment variables through to external programs as part of normal process execution.

Logs and artifacts may contain local paths, executable paths, molecule structures, Gaussian/ORCA keywords, external program output, checkpoint references, and private calculation data. Review and redact artifacts before sharing them.

For stronger local isolation, use trusted YAML configuration, set `sandbox_root`, set `allowed_executables`, run in a dedicated working directory, and avoid running inputs from unknown sources.
