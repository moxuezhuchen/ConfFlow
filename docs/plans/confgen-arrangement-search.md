# ConfGen: search across donor arrangements (`arrangement: search`)

Status: implemented, branch `feat/confgen-arrangement-search-20261020` (off main c201b49).

## Why

The search kept the input donor arrangement because every DG start was embedded on the input
metal-donor core. Full-engine test (2026-10-20, equal budget, 3 seeds; P = production keep,
M = half of the DG starts free):

| system | P best (kcal) | M best (kcal) | arrangements within 6 kcal (P / M) | cost M vs P |
|---|---|---|---|---|
| hf8 (CN8), budget 300 | 0.00 / 0.03 / 0.04 | 0.00 / 0.01 / 0.00 | 1 / 3 | +5 % |
| Zr7 TS (CN7), budget 600 | 5.70 / 5.72 / 7.53 | 0.01 / 6.98 / 0.00 | 1-2 / 1-5 | +8 % |

Turning the arrangement filter off without free starts changed nothing (bit-identical to P): the
starts are the barrier, not the filter. Label enumeration was not needed and costs far more at
CN >= 7 (2.5 % embedding success).

## User decisions (2026-10-20)

- Default changes from `keep` to searching across arrangements. Declared/perceived stereo states
  (tetrahedral hands, attack face, `fixed_states`, E/Z) stay fixed.
- Donor-set changes (dissociation, hemilabile, CN change) stay out of scope.
- Output: arrangement window + in-group window. An arrangement is published when its lowest
  minimum is within `window_kcal` of the global lowest; inside it, minima within `window_kcal` of
  that arrangement's own lowest. The input arrangement is always published. A table lists every
  arrangement found.
- This branch only tags arrangements; downstream per-arrangement Boltzmann is a later item.

## Design

1. `ExploreSettings.arrangement`: `"keep" | "enumerate" | "search"`, default `"search"`.
   Workflow `search.arrangement` gets the same values and default; shape `input` allows `keep`
   and `search` (not `enumerate`).
2. DG starts (`dg_seed.DGSeedSettings.free_arrangement`): a free start keeps every bound of a
   normal start except the donor-donor core bounds and the core coordinate map; metal-donor
   distances stay. In `search` mode every other global DG start is free (`_FREE_DG_SHARE = 0.5`,
   internal constant). `keep` and `enumerate` never use free starts.
3. Audit: `search` mode does not apply the keep-arrangement filter; everything else (topology,
   coordination contacts, tetrahedral/double-bond/torsion states, restraints) is unchanged.
   Template starts still use the input class (ideal shapes) or the input core (`input`).
4. Arrangement groups: leader clustering, the reference geometry is the first leader (`a0` = input
   arrangement), then minima lowest first (`a1`, `a2`, ...). Same arrangement = some admissible
   donor relabelling keeps every donor-metal-donor angle within `_ARR_TOL` (45 deg, the keep
   definition) and, for a chiral arrangement, the hand. Applies to every metal job (keep has only
   `a0`).
5. Release and publication use the window rule above (release: group window + 2 kcal as now).
6. Output: `SearchRecord.arrangement` (`to_dict()["arrangement"]`), xyz comment
   `arrangement=aN`, member metadata `search_arrangement`, `stats["arrangements"]` rows
   `{id, input, lowest_eh, rel_kcal, minima, published}`, `stats["arrangement_scope"] =
   "all_arrangements"` in search mode. WARNING `confgen_arrangement_changed` when the lowest
   published structure is not in `a0`.

## Work split

- Core (lead): `dg_seed.py`, `search.py`, `explore.py` + core unit tests.
- Workflow layer (subagent): `workflow/v4/confgen_schema.py`, `execution/confgen_search_worker.py`,
  `execution/confgen_search_run.py`, `producer/contract.py`, docs (`docs/CONFGEN_CAPABILITIES.md`,
  `docs/confgen-stable-chemistry-and-freeze-core.md`) + their tests.

## Acceptance

- Unit tests of touched modules pass locally (`pytest -n auto` on tests/v4 confgen + science).
- Regression: CN4-6 (TS1 + holdout systems) equal budget, no loss of best energy vs main.
- Zr7 budget 1500, 3 seeds on the 814 server: hit rate of the new lowest arrangement.

## Regression result (2026-10-21, 814 server, seeds 11-13)

main = `keep` (c201b49), branch = `search`. Best energy in kcal/mol relative to the union best of
both arms; wall time change is the mean over the three seeds.

| system | budget | main best | branch best | other arrangements (branch) | wall |
|---|---|---|---|---|---|
| axurer | 300 | 0.68 x3 | 0.00 x3 | new arrangement 0.7 below the input one, 3/3 | +3 % |
| akugod | 300 | 0.08 / 0.00 / 0.08 | 0.08 / 0.00 / 0.08 | none | +5 % |
| amozeh | 300 | 0.00 / 0.12 / 0.00 | 0.00 / 0.12 / 0.12 | none | +1 % |
| egozuv | 300 | 0.00 x3 | 0.00 x3 | 4.4-6.2 kcal, 2/3 | +1 % |
| cnetpa | 300 | 0.00 x3 | 0.00 x3 | none | +3 % |
| cd6_bpy3 | 300 | ~0 | ~0 | none | +14 % |
| al6_ts1 | 300 | 0.30 / 0.00 / 0.31 | 0.30 / 0.30 / 0.29 | 3.9 kcal, 1/3 | +8 % |
| ti7_salan | 300 | 0.00 x3 | 0.00 x3 | none | +4 % |
| hf8 | 300 | 0.03-0.05 | 0.00-0.03 | +2.5 kcal, 3/3 | +4 % |
| zr8_onn | 300 | ~0 | ~0 | none | +33 % |
| Zr7 TS | 1500 | 5.70 x3 | 5.70 / 5.72 / 0.00 | 3/3; the 5.7-lower one 1/3 | +5 % |

No loss of best energy beyond seed noise. Generation time and relaxation counts are equal between
arms; the extra wall time is in the xtb relaxations of free DG starts (see below).

## Free-start cost and the snapped re-embedding (2026-10-21)

Per-relaxation xtb logs (zr8_onn, seed 11): a free DG start needs 210 optimizer steps (52 s), a
fixed one 122 (28 s). zr8_onn runs about one DG start per round, and that relaxation is the
slowest job of the round, so ~37 free starts x ~24 s ≈ the 904 s (+33 %) wall-time increase.
Other systems relax many DG starts per round and pay 1-14 %.

The cause is the core angles, not the metal-donor distances. A free start's core is a random
arrangement, not a stable polyhedron: during relaxation its donor-metal-donor angles move by a
median maximum of 48 deg (fixed starts: 6 deg); steps vs angle change correlate at 0.76. An earlier
guess (metal-donor distances off by 0.1-0.2 Å) only correlated through the angles.

Change: each free start is re-embedded on its own core with the donors put back at their
metal-donor distances (angles kept, so the arrangement and hand are unchanged), the way a fixed
start is embedded, with the start's own seed index; if that fails the free start is kept (about
1 in 24 starts). Same-load regression (seeds 11-13, budget 300; Zr7 1500), old free starts vs
re-embedded:

| system | wall old / new | best and arrangements |
|---|---|---|
| zr8_onn | 3239 / 3207 s | unchanged |
| al6_ts1 | 2807 / 2801 s | unchanged |
| cd6_bpy3 | 1118 / 1114 s | unchanged |
| hf8 | 2102 / 2134 s | unchanged (+2.5 kcal arrangement 3/3) |
| Zr7 TS | 12919 (other load) / 12233 s | 5.7-lower arrangement 1/3 -> 3/3 (0.04 / 0.01 / 0.00) |

The re-embedding does not change the cost (steps 210 -> 198 on zr8_onn) but raised the Zr7 hit
rate at no cost (mechanism not verified; old runs together 3/6). Kept. The zr8_onn cost stays: it
is the price of relaxing a new arrangement and only shows when one DG relaxation is the critical
path of each round. Removing it needs rounds that do not wait for their slowest relaxation
(asynchronous scheduling), a separate item. A lower free share would cut exploration and is not
used.
