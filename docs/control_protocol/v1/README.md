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

## Candidate worker recovery

The unpublished worker candidate adds one producer-internal recovery event:
when a worker loses its per-token kernel lease while an attempt is `running`,
the next worker may atomically move that attempt back to `queued`, increment
the attempt number, issue a fresh launch token, and emit `requeued`. The old
token can no longer commit lifecycle callbacks. This is not a new public
control operation or a stable v1 state transition; it is part of the candidate
worker release and must be versioned and covered by its candidate contract
tests before any consumer pin changes.

The `_schema` member in files under `tests/fixtures/control_protocol/v1` is
fixture-inventory metadata only. It is removed before validation and must not be
sent as part of a wire request or response.
