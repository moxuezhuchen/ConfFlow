# ConfGen: search across donor arrangements (`arrangement: search`)

Status: in progress, branch `feat/confgen-arrangement-search-20261020` (off main c201b49).

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
