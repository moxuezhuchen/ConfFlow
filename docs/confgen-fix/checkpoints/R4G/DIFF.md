# R4G — ring scope guards reach the Stage (DRAFT rehearsal)

Science card (`delete` 前置：R4；R6 前置：R4G）。基点
`ccf167f227d21322d1a251591440e75f48123538` 上的独立预演，
最终需 R5 正式 HEAD 上重放重验才冻结。

## 缺陷

CP 约束 Stage（`ring/stage.py::realize`）求解前不做范围校验：
chord（环内额外键）、identical-overlap、spiro 共原子、环内金属、
`preserve_input` 分支的同类越界在正式树上被 REALIZED（且产结构），
或以几何相关的偶然原因失败，而非显式 scope 原因。
根 probe（`/tmp/fix1r-r4g-root-probe/probe.py`）：
正式 `base-corrected2.log` 中 chord/overlap 均为 `realized`；
本卡修复后均为 `unsupported`（`fused_or_bridged` /
`overlapping_systems`），零结构。

## 生产改动（仅 `confflow/science/confgen/ring/stage.py`，+28/−0）

- `import` 既有 `validate_ring_system`（旧模板/realizer/`RingTolerances`
  全字段不动；R3 solver（`_R3_*` 常数/残差/审计/`realize_cp_target`）不动；
  `geometry`/`perception` 不动；回调签名不变）。
- `realize` 在处理所有 spec 前检查跨系统重复原子（与旧
  `realize_rings` 的 `seen` 表相同：`overlapping_systems`）。
- `preserve_input` 与 `enumerate` 两分支在求解前调用既有
  `validate_ring_system`（相同 global atoms/covalent graph/coord
  atoms/`others`，相同错误串；不新增阈值，不重排合法 target）。
- 旧 `validate` 对 `get_template` 的依赖本卡仍在（R6 再机械替换为
  `constants` 别名表验证）。

## 测试（新 `tests/v4/test_confgen_r4_scope_guards.py`，15 节点，+15/−0）

- 回归（基点树上 7 失败，修复后全过）：chord fused、identical
  overlap、spiro 共原子、chelate、preserve chelate、preserve
  coord_overlap、engine chord 零发布（基点发布 8）。
- 已有护栏（基点/修复后均过，不称新增）：nonbonded、macrocycle
  size、unknown template、enumerate coord_overlap。
- 合法对照（均过）：单环、linked 匹配 forms、disconnected 双环、
  engine 合法发布。

## 验收证据（见输出目录与 REPORT）

- 新测试基点失败 log（7 failed）与修复后 log（15 passed）。
- 根 probe 三份 log（base 两份 + 本树一份）。
- 16 节点 after capture：检查点 21 报告逐字节等（另 2 份与 v3 捕获等）。
- 全量 capture base/after 对账（仅新增本卡 engine 测试报告）。
- contract/boundary 两树逐字节等；collect +15/−0；ruff/black/mypy 过。
