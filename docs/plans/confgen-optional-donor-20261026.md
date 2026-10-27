# ConfGen 可选配位位点（半不稳定配体，C3）方案（2026-10-26）

来源：`CONFFLOW-NOTES.md` 2026-10-26 第 2 条（引发剂羰基 O82 时配位时不配位，现在只能 CN7、CN6 各跑一次再在下游合并）、
TS2 combos 冒烟（CN6 开放位被 O 填上即被 `coordination_class` 拒绝）。用户 2026-10-26：剩余大功能全部做。

## 1. 现状（调研结论）

- 配位原子集合在解析时固定（`binding_sites` 是唯一来源）；DG 边界、约束、审计、排布、标签都假定集合不变。
- `docs/CONFGEN_CAPABILITIES.md` 明写"解离、半柔性配体、配位数变化不在范围内"。
- 7 配位 Zr 只能用 `shapes: [input]`。

## 2. 设计：按配位状态分别搜索，合并发布

```yaml
coordination:
  metal_center: 1
  binding_sites:
    - {id: O39, kind: atom, atoms: [39]}
    - {id: O82, kind: atom, atoms: [82], optional: true}   # 新字段，默认 false
    ...
  shapes: [input]
```

规则（v1）：
- 仅 `shapes: [input]`；最多 2 个可选位点；必选位点 ≥ 2。
- **可选位点在输入结构里必须是配位的**（输入的 M–D 距离作为"配位"状态的参考距离）。输入里没配位的原子不能声明为可选——
  这种情况应换一个配位的输入（文档写明；v2 再考虑给定目标距离的"补位"）。
- 状态 = 可选位点"配位/解离"的全部组合（k 个可选位点 → 2^k 个状态，最多 4 个）。

每个状态是一次子搜索：
- 配位原子 = 必选 ∪ 该状态里"配位"的可选位点；DG、约束、审计照旧。
- 该状态里"解离"的可选位点（`detached`）：
  - DG：M–D 距离下界设为 `参考距离 × 1.5`（上界放开），不进核心模板，不钉；
  - 不加 M–D 约束；不计 `metal_donor_distance`、`donor_orientation`、`donor_stretch`、排布角度；
  - 在新接触规则里仍算"已声明"（它重新靠近金属不是 `coordination_class` 失败）。
- 预算与起点数：总量按状态数均分（`budget // n_states`，至少 1；`starts` 同理）；种子 `seed + 状态序号`。成本与原单次搜索相同。

合并：
- 各状态的通过结构合并后统一去重（`Identity`，几何不同自然分开）、统一能量窗口；**每个状态最低的结构总是保留**（同 a0 规则），
  避免 6 kcal 窗口整体丢掉一个配位状态。
- 每个结构按**实测距离**定状态：可选位点 d ≤ 1.3 × 参考距离 → 配位，否则解离（与 `donor_detached` 的 1.3 同口径）。
  实测状态可能与所在子搜索不同（例如解离状态里原子又配回去），以实测为准。

发布：
- 成员元数据 `search_optional_bound`：`{"82": true}`（1 基原子号 → 是否配位）和 `search_donor_state`（如 `"O82+"`/`"O82-"`，
  多位点用逗号连接）；`structures.xyz` 注释行加 ` donors=O82+`。
- INFO `confgen_donor_states`：每个状态的结构数、最低能（相对全局最低 kcal）、子搜索预算。
- `_high_cn_arrangement_diagnostic` 用实际配位数。

## 3. 接口

- 工作流 schema：`BindingSiteModel.optional: StrictBool = False`，校验见 §2 规则。
- 科学层：`BindingSite.optional`；`CoordinationSpec.detached_donors: tuple[int, ...]`（0 基，默认空）与
  `CoordinationSpec.donor_reference`（不变）。新函数 `search_spec.donor_states(native) -> list[DonorState]`，
  `DonorState(label: str, bound: dict[int, bool], native: dict)`：`native` 是该状态的子搜索规格（`binding_sites` 只含配位的位点，
  内部键 `coordination.detached_sites: [atom, ...]` 列出解离的可选位点；该内部键不进工作流 schema）。
  `resolve_search_coordination` 读 `detached_sites` 填 `detached_donors`。
- 运行层：worker 对每个 `DonorState` 跑一次现有搜索，合并、去重、窗口、发布。

## 4. 分工（Haiku 5.5 子代理，文件不重叠）

- A1：`workflow/v4/confgen_schema.py`、`science/confgen/search_spec.py`、`science/confgen/graph.py` + `tests/v4/test_confgen_optional_donor_spec.py`。
- A2：`science/confgen/dg_seed.py`、`science/confgen/search.py`（DG 边界、约束、审计对 `detached_donors` 的处理）+ `tests/v4/test_confgen_optional_donor_audit.py`。
- A3：`science/confgen/explore.py`（`_audit`、`_donor_stretch`、排布只用配位集合、实测状态标签、SearchRecord 字段在 search.py 由 A2 加）
  + `tests/v4/test_confgen_optional_donor_explore.py`。
- B：`execution/confgen_search_worker.py`、`execution/confgen_search_run.py`（状态循环、预算均分、合并、发布、诊断）+ 集成测试。
- 我：方案、验收、真实冒烟（Ti7/Zr7 夹具或 TS1 输入、小预算，本机 ≤10 分钟）、文档。

## 5. 验收

- 全部测试/mypy/black 26.10.1/ruff/行数预算/changed-coverage。
- 没有可选位点时，搜索结果与 main 逐字节相同（同种子）。
- 冒烟：一个 CN7 输入声明 1 个可选位点，小预算，两个状态都有通过结构，实测标签与距离一致。

## 6. 实测（2026-10-26，本机）

输入：TS1 Zr7（144 原子），O82 声明为可选位点（输入中已配位），`shapes: [input]`，预算 60（每状态 30），12 核，真实 xTB，每次运行约 6 分钟。

- 第一次运行（解离给体无约束）：O82 在每个结构中都重新配位，来自 O82- 子搜索。诊断按子搜索标签计数，显示的 O82- 结构数有误导性。
  修正：搜索阶段把解离给体保持在输入距离的 1.5 倍，诊断改为按实测状态计数。
- 第二、三次运行（修正后）：
  - O82+：发布 4 个结构，最低 0.00 kcal/mol。
  - O82- 子搜索：7 个通过的结构，收尾后全部 7 个都重新配位 O82（最低 +7.14 kcal/mol），因此没有发布 O82-（解离）结构。
    WARNING `confgen_donor_state_missing` 解释了这一点。
- 解释：在这个小预算下，GFN2 没有为该 TS 找到稳定的 O82- 极小；O82 一旦释放就会重新配位。更大的预算未验证。
- 对照：没有可选位点时，同种子、预算 30 的 `structures.xyz` 与 main 逐字节相同（md5 41fe07f4…），定义与语义摘要不变。
