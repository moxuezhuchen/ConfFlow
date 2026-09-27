# V4 Terminal Arbitration & Generation Ownership Contract

Date: 2026-09-27. Branch: `repair/v4-terminal-arbitration`.
Closes the two final-review P1 races:

- **P1-1 cancellation publication race** — a durable cancel accepted before
  the completion terminal claim could still end as `completed`
  (`cancel_requested → completed`).
- **P1-2 stale generation writer** — a paused generation G1 could resume
  after G2 became current and overwrite `run_result.json` /
  `run_generation.json`, regressing the current generation to G1.

And the two final-recheck blockers:

- **P1 real control cancel** — `confflow control cancel` recorded
  `cancel_requested` and returned `ok` without claiming the run root's
  arbitration ordering; a completion that already owned the claim still won.
- **P0 stale step publication** — the step-result ownership check and the
  durable write were separate operations (TOCTOU), so a superseded
  generation could overwrite the current generation's StepResult and leave
  the manifest digest inconsistent with disk bytes.

The fix is one authority, not two patches: every terminal transition and
every generation mutation goes through
`confflow/persistence/arbitration.py`.

## Authority

- `run_generation.json` — public current-generation projection (schema
  unchanged; JobDesk reads it).
- `generation_ownership.json` — internal arbitration ledger (current
  generation id, cancel intent, terminal claim/confirmation, superseded
  ids).  Never consumed by JobDesk.
- `.generation.lock` — the mutual-exclusion region (`flock(LOCK_EX)` on
  POSIX) that makes every check + replace atomic.
- `run_result.json` — the published manifest, written inside the lock
  region of its generation's terminal claim.

The execution service is a **projection** of this authority: with a
configured `terminal_arbiter`, `lifecycle_terminal` / `lifecycle_cancelled`
reject any callback that contradicts the ledger winner, and
`service.cancel` refuses a cancel that lost the ordering.  The service
database can no longer become an independent winner.

## Linearization points

- **Completion**: `terminal_publication(generation, completed)` claims the
  terminal transition under the lock, writes the manifest, and confirms the
  terminal generation record — all inside one lock region.  A durable
  cancel intent recorded before the claim makes the claim return
  `cancelled` instead; no completed manifest is ever written for that
  generation.
- **Cancellation**: the durable cancel intent recorded under the lock
  (`record_cancel_intent`) plus the claim.  If completion already claimed,
  the cancel loses: `service.cancel` raises and completion is never
  reverted.  For beacon-only cancel paths (control worker), the beacon is a
  durable marker observed inside the same claim region; the controller
  re-reads the arbitration winner after touching it and rejects a lost
  cancel.
- **Control cancellation admission** (Astra P1): the real
  `confflow control cancel` runs `ExecutionService.cancel` with a
  `cancel_arbiter` resolved from the run's control-channel run-root pointer
  (`<state_root>/v1/runs/<run_id>/work/run_root`, published by
  `build_workflow_service`).  Admission durably claims the run root's
  ordering BEFORE any service-state mutation, so `cancel_requested` is
  never recorded for a cancel that lost.  While a terminal publication holds
  the lock across its manifest write, the non-blocking probe returns the
  already-visible terminal claim and the control response is an explicit
  `invalid_state_transition` failure; a stale unconfirmed claim with no live
  publication is revoked under the lock first, so it cannot block a
  legitimate cancel.  The CANCEL beacon remains only the worker stop signal.
- **Generation-owned publication** (Astra P0):
  `generation_publication_scope(run_root, expected_generation_id)` holds the
  lock while the caller writes.  `publish_step_result(expected_generation_id=…)`
  performs the expected-owner check and the atomic StepResult replace inside
  that one region, and every `run_state.json` write in the V4 application
  goes through the same fence.  A superseded generation raises
  `StaleGenerationError` before replacing a byte, so a manifest's step
  digest can never disagree with the durable StepResult bytes.
- **Generation current**: `begin_generation` installs the owner under the
  lock.  A newer generation immediately and permanently invalidates the
  older writer: `terminal_publication`, `finalize_generation`,
  `compare_and_set_generation`, step-boundary execution, and step
  publication all raise `StaleGenerationError` for a superseded owner.

## CAS / locking mechanism

- `compare_and_set_generation(run_root, expected_generation_id, record)`:
  load → verify current owner and terminal idempotence → atomic replace,
  all inside the lock.  A stale owner raises `StaleGenerationError`; a
  conflicting terminal rewrite raises `TerminalOwnershipLostError`.
- `flock` semantics: per open-file-description (works across threads and
  processes on the same host); released by the kernel when the holder dies
  (no stale locks); the lock file persists with mode 0600.  Bounded
  acquisition wait (120 s) with an explicit timeout error — lock
  acquisition only, never race mitigation.  Non-POSIX hosts fall back to a
  process-local lock; the durable execution service itself is POSIX-only.
- Scope: processes that share the run root on one host.  Network
  filesystems must provide working `flock` semantics.

## Crash recovery (ordering)

Claim → manifest durable → generation terminal durable → service
projection.  On the next lock acquisition:

- an unconfirmed claim with a same-generation terminal manifest is
  confirmed from the manifest (completion linearized);
- an unconfirmed claim without a manifest is revoked (it never linearized),
  so a later cancel or a newer generation wins deterministically;
- a confirmed claim whose public record is missing/stale is rewritten from
  the ledger.

The control worker applies the same authority during crash recovery: a
crashed running attempt whose run root already proves a terminal outcome
(manifest-backed completion, durable cancel, confirmed failure) is projected
onto the service aggregate instead of being rerun; only a run root with no
terminal winner is requeued for a fresh generation.

## Residuals (not P1)

- A writer already inside a step may still write that step's per-item store
  rows before the next ownership fence; terminal/generation truth and the
  JobDesk-visible current generation are unaffected.
- JobDesk's display wording for a cancelled generation that carries a
  failure note says "failed" in the warning sentence (status itself is
  correct).
