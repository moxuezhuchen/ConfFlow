# Control protocol schema bundle v1

`common.schema.json` defines shared identifiers, locators, state, errors, and
artifacts. `requests.schema.json` and `responses.schema.json` define the eight
operation messages. `input-manifest.schema.json` defines the ordered input file
set referenced by `prepare`. `worker-handoff.schema.json` is the producer-owned
candidate handoff envelope for a separate worker process: it binds one queued
run to an absolute workflow configuration, one input XYZ, its work directory,
and the corresponding byte digests. The worker additionally requires every
locator to remain below an owner-controlled, non-writable attempt root, rejects
symlinks and foreign-owned files, and copies configuration/input bytes into
the producer-owned StateRoot staging directory before execution. A worker must
validate this envelope and consume the already-queued producer launch token; it
must never call `prepare` again or use JobDesk/agent state as a second
authority. The same bundle is installed by the wheel under
`share/confflow/control_protocol/v1` and is loaded by the control adapter when
the source-tree copy is unavailable.

For this unpublished candidate only, the `prepare.input_manifest` content
locator is the canonical bytes of the worker-handoff envelope itself. Its
SHA-256 is therefore the digest persisted by `prepare` and checked by the
worker before it consumes the queued token. This is deliberately *not* the
stable `confflow.control.input-manifest.v1` payload used by the v1 producer
contract and is not accepted as a silent interpretation by stable JobDesk.
A paired consumer must upload the handoff JSON, use its canonical digest in
`prepare`, and retain the same path under the private attempt root. Until that
consumer contract is published, JobDesk remains unintegrated with this worker.
The candidate envelope has `tasks.maxItems: 1`; batched JobDesk input must be
split into one handoff/run per task or wait for a separately versioned batch
extension. The worker never truncates a batch to `tasks[0]`.
The handoff digest is SHA-256 over UTF-8 bytes produced by
`json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
allow_nan=False)` after removing fixture-only `_schema` metadata. This exact
profile is locked by the golden-fixture digest test and must be reproduced by
candidate consumers; it is not the RFC 8785 request-digest profile.
Each `task.input_xyz` must retain the `.xyz` suffix; other input formats are
not accepted by this worker boundary because the report sidecar uses the same
basename.
After a successful engine return, the worker publishes `{basename}.txt` and
`{basename}min.xyz` in the parent directory of the task `work_dir` (the remote
result base used by the JobDesk download contract); the JSON/state/manifest
files remain inside `work_dir`. These sidecars are published before the
producer commits `completed`.

## Candidate worker recovery

The unpublished worker candidate adds one producer-internal recovery event:
when a worker loses its per-token kernel lease while an attempt is `running`,
the next worker may atomically move that attempt back to `queued`, increment
the attempt number, issue a fresh launch token, and emit `requeued`. The old
token can no longer commit lifecycle callbacks. Recovery is fail-closed when
the prior lease marker lacks its worker PID/process-group identity or when a
prior process group or descendant still owns the attempt work directory; an
operator/supervisor must drain that process before retrying. This is not a new
public control operation or a stable v1 state transition; it is part of the
candidate worker release and must be versioned and covered by its candidate
contract tests before any consumer pin changes.

Crash recovery also requires the worker to be launched in a dedicated process
session, for example `setsid confflow-control-worker ...`. The lease marker
records this isolation. A marker from an ordinary shell/scheduler process
group is intentionally not recoverable automatically; an operator must drain
and re-submit through the supported isolated launcher.

The `_schema` member in files under `tests/fixtures/control_protocol/v1` is
fixture-inventory metadata only. It is removed before validation and must not be
sent as part of a wire request or response.
