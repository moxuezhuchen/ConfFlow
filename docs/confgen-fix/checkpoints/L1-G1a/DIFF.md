# L1-G1a DIFF (pre-rehearsal, pure Gaussian route mechanical move)

Base: `/tmp/l1-exec @ 01a260d149c99ab8c0571e7eebb6f1bab827d504` read-only.
Worktree `/tmp/l1-g1a-proto`, branch `refactor/l1-g1a-proto`.
No commit/push/merge/tag/install. Full/TS1/golden not run here (L1 milestone root only).

## What moved (WL-G1a only; G1b stages excluded)

- NEW `confflow/programs/gaussian/checkpoint_policy.py` (226 lines):
  11 canonical constants (`_QST_TOKEN_RE`, `_IRC_MANAGED_RE`, `_IRC_ITEM_RE`,
  `_OPT_PAREN_RE`, `_OPT_ASSIGN_RE`, `_OPT_BARE_RE`, `_FREQ_TOKEN_RE`,
  `_SP_MANAGED_RE`, `_READFC_CONFLICTS`, `_RCFC_CONFLICTS`,
  `_LINK0_CHECKPOINT_RE`, values byte-identical to `checkpoints.py:117-170`)
  + homomorphic `_refuse` (`domain.errors.InvalidBindingError`) + trio
  (`_add_opt_option:336`, `_add_irc_rcfc:388`, `_strip_managed_items:446`,
  bodies/signatures AST-exact incl nested `_paren_replace`/`_assign_replace`).
  Top imports only `__future__`/`re`/`...domain.errors`/`.path as _irc_path`/
  `.rendering as _gaussian_rendering` (same module objects, bodies unchanged).
  No stages, no `managed_keys`, no `ProgramName`/`registry`, no version/modes.
- MOD `confflow/producer/checkpoints.py` (1098 -> 939 lines; 27+/186-):
  delete trio bodies + 11 constant definitions, add one re-export
  `from ..programs.gaussian.checkpoint_policy import (...)  # noqa: F401`
  (14 names, same objects), delete now-useless `import re` and
  `from ..programs.gaussian import path as _irc_path` (whitelist定点).
  Keep `_gaussian_rendering`/`_energy_semantics`/`ProgramName`/registry,
  `__all__`/version/modes/wire (`1054-1072`)/lineage untouched, `_refuse` stays.
  Mirror comments mark moved sites (bidirectional mirror).
- NEW `tests/v4/test_l1_gaussian_policy_move.py` (192 lines, 8 tests):
  same-object, signatures, public surface, `__module__` is policy,
  policy has no producer/workflow/science, producer keeps G1b branches,
  readfc/rcfc/SP-edge fixed vectors. Old tests untouched.
- NEW `docs/confgen-fix/checkpoints/L1-G1a/{MANIFEST.json,DIFF.md}` + `LOG.md` L1-G1a append.

## Why no other change

- Only truly closed trio moved; `_check_write_chk`/`_check_target_link0`/
  `_native_scientific_core` need producer shape/owned keys (G1b wrappers).
  No science authority table copied; `ProgramName` stays producer (G1b).
- `__module__` of moved privates is now policy (recorded); public module unchanged.
- Producer still has Gaussian `If`/`Compare` (`ProgramName`, `_QST_TOKEN_RE`,
  `_LINK0_CHECKPOINT_RE`) + calls (`resolve_write_chk`, `coerce_section_lines`,
  `unsupported_method_finding`) for G1b; G1a does not fake-zero (see test).

## Verification (executor self-check; root acceptance separate)

- AST: trio 3 + `_refuse` bodies/signatures exact; 11 values exact;
  aliases `_irc_path`/`_gaussian_rendering` are path/rendering modules.
- Fixed corpus 28 vectors base-producer vs new-producer 0 mismatches;
  new producer vs policy 28 equal; identity 14/14 `is`; `_refuse` not `is`
  (homomorphic); SP edge `B3LYP/SP` kept, bare `SP` stripped.
- Tests: checkpoints(69)+boundary(68)+new(8)=145; intent+regressions 64;
  contracts 11. Collect 5001 -> 5009 (+8/-0).
- Static: `ruff check` clean, `black --check` clean,
  `mypy -n 1` clean (2 production files).
- Contract/boundary bytes: no production semantics change; boundary/contract
  tests above passed; `git diff` touches whitelist only.

## Raw freeze

- `/tmp/l1-g1a-output/L1-G1a-production-only.diff`
  (`git diff HEAD --binary -- confflow/producer/checkpoints.py confflow/programs/gaussian/checkpoint_policy.py`,
 含 new file mode).
- `/tmp/l1-g1a-output/L1-G1a-proto.diff` (`git diff HEAD --binary`, all new/modes).
- SHA256SUMS, probe JSONs, collect lists, card, formal prompt, report in same dir.
