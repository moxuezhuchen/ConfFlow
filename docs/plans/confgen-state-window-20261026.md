# ConfGen 每状态能量窗口方案（2026-10-26）

来源：`CONFFLOW-NOTES.md` 2026-10-24（全局 6 kcal 窗口会把一整个构象状态丢掉；已有 `flips=` 标签与 `state_changes` 报告）；
`confflow-usage-fixes-20261026.md` §51 暂缓项。用户 2026-10-26：剩余大功能全部做。叠在可选配位位点分支（C3）之上，因为改同一批发布代码。

## 1. 现状

- 发布：`explore._publishable` 按排布分组，每组自己的窗口（`a0` 总保留）；收尾前候选为窗口 +2 kcal。
- 状态标签：`_state_changes(final)` 在收尾后对 final 池做 `state_scan`，每个结构得到翻转单元编号（`flips=`）。
  窗口外的状态既不收尾也不发布，只在 `state_changes` 汇总里留一行"lowest_changed_kcal"。

## 2. 设计

```yaml
search:
  state_window_kcal: 12.0   # 默认 null（关闭，行为与现在逐字节相同）
  state_max: 2              # 每个状态最多补几个，默认 2
```

- 打开时，在选收尾候选之前，对约束阶段的整个池做一次 `state_scan`（与 `_state_changes` 同一函数、同一 `declared`），
  按翻转签名（翻转单元集合）分组。
- 每组：若该组最低能比全局最低高不超过 `state_window_kcal`，取该组最低的 `state_max` 个，并入收尾候选。
- 发布 = 现有窗口结果 ∪ 这些按状态补入的结构（收尾后重新确认仍属该状态；收尾后变了状态的照常按新标签发布）。
- 元数据 `search_kept_by`：`"window"` 或 `"state"`；注释行 ` kept=state`（仅打开时出现）。
- INFO `confgen_state_window`：补入的状态数、结构数、每个状态最低相对能。
- 成本：每补一个结构多一次收尾优化，最多 `状态数 × state_max`；状态数在 summary 里已知（TS1 冒烟 6 个单元）。
  为防止状态组合爆炸，补入状态总数上限 `state_max_states: 12`（默认），超出时按状态最低能排序截断并在诊断里说明。

## 3. 分工（Haiku 5.5）

- A：`workflow/v4/confgen_schema.py`（三个字段，校验：window>0，max≥1，max_states≥1）、`science/confgen/search_spec.py`/worker 设置透传
  + 测试。
- B：`science/confgen/explore.py`（池扫描、分组、候选并入、`kept_by` 标签）、`science/confgen/search.py`（SearchRecord 字段 `kept_by`，
  仅打开时进 to_dict）、`execution/confgen_search_run.py`（元数据、注释、诊断）+ 测试。
- 我：验收与真实冒烟（TS1，小预算，本机 ≤10 分钟）。

## 4. 验收

- 关闭时逐字节相同（现有测试 + 同种子对比）。
- 冒烟：打开后，原本只在 `state_changes` 里出现、窗口外的状态有结构发布，且标签一致。
