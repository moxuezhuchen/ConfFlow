# STATUS — FIX1R closing record (stage-accepted, NOT merged; final full/golden running)

> Formal tree `/tmp/fix1r-exec` HEAD `71cf6e241ebaccf0c4c914f616463fe4c77e1217` (`fix/confgen-1r`, base `213b3060a86abc7b036f6ec54bb28a1486d26419`). main/master untouched. Final full/golden is running independently at root; nothing below claims final green.

## 1. Merged (done)

- L0 (L0.0 baseline, L0 integration, runtime-fixture-fix, ci-scope) — merged.
- FIX1A (B1 baseline, A1–A6, CAP-SIG/cleanup, root-final-2: 4862 nodes / 4852 passed / 10 skipped / wall 265s / 93 reports byte-equal B1 / golden ok) — merged at main `213b306` (PR #104/#105).

## 2. FIX1R branch-committed, stage-accepted, final pending (NOT merged)

- Branch `fix/confgen-1r` commits (all verified by `git log --format=%H %s` on formal tree):
  F1 `5e455aa0df433cebe8ff5cba0b590b6ce0eb626f` → R1 `70b0d4415134ea3230b0af6b7a81dabf90c43651` → R2 `38b5d4a6443544741bfc9870ac4ad0838adf7c0e` → R3 `f3c2eeffd06c83a159f5c7d4c21d1418661f31ab` → K0 `d964ba7e75999b6a7eb4fe48350b19dcdeae925f` → R4 `27e046c138090e68d54b2d0a711b392c7af0a3d8` → Q9 `ccf167f227d21322d1a251591440e75f48123538` → R5 `10c7ffe0caf504be50228684a9b64e484b92fdeb` → R4G `45ff3578129c39dc95572a276dafc304ad80a45c` → R6 `4e5e36a51da3a4e554aed1d2afee27bcea52ac66` → R7 `71cf6e241ebaccf0c4c914f616463fe4c77e1217`.
- F1 is branch-only (test-only fixtures), NOT merged. Do not list F1 as merged.
- Stage roots: R4 v3 218 + v4 11 tests, collect 4951 (`/tmp/fix1r-r4-v4-output/ROOT-STAGE-ACCEPTANCE.json`); R5 15 tests, 23 reports 0 diff, collect 4966 (`/tmp/fix1r-r5-v2-output/ROOT-REVIEW.json`); R4G 30 combined tests, 23 byte-equal (`/tmp/fix1r-r4g-final-output/ROOT-ACCEPTANCE.json`); R6 187 tests, 16 nodes 23 equal, collect 4979 (`/tmp/fix1r-r6-final-output/ROOT-STAGE-ACCEPTANCE.json`); R7 11 tests, rpdd 3/3 + mch crest-first explicit 6/6 + synchx 2/2, patch cmp `R7-v2.patch` consistent (`/tmp/fix1r-r7-final-output/ROOT-STAGE-ACCEPTANCE.json`, sha `75a32dcd9d29c78ee1ec63c1b8917fb28948616274b69c44f2099355ea1ff32a`, collect 4990).
- R7 limits on record: explicit + crest-first required for any 100% claim; glucose crest-default 113/155 → explicit 127/155, the remaining 28 is a measured CP-recall gap without per-case causal attribution (no relaxation, no 100% claim); other measured gaps (mch original-input default 3/6 / explicit 4/6; mch crest-first default 5/6 / explicit 6/6; chexene/nap original-input shortfalls per `/tmp/fix1r-r7-tool-v3-output/SUMMARY.json`) are recorded as measured recall shortfalls without causal attribution; post-seed RMSD (`match_after_opt.py`) is user-side only, not done here.
- Final milestone full/golden: independently running at root, PENDING. This card does not merge and does not claim final green.

## 3. Still mandatory after this card (not done)

- FIX1D, L1, L2, L3, E, J, G remain mandatory follow-ups. K0 rescued 0 (`/tmp/fix1r-k0-root-independent-counts.json` S1/S2/S3 rescued 0/0/0, 84 rows), so K1/K2 move to FIX2 by plan.

## 4. Explicitly NOT in this round

- FIX2 (K1/K2) is not done in this round. No default-backend/preset/analysis payloads are smuggled into this card.
