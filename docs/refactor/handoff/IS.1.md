# IS.1 补充说明（配合 PLAN.md §8 的 IS.1 卡）

目标：在不改任何产品代码的前提下，记录"同一个 paths 声明，分别走 legacy 执行路径和 typed v3 执行路径"的结果，并判断是否等价。输出两个文件：cases.json（用例）、result.json（逐用例结论）。

## 环境
- 工作树 /opt/cf-worktrees/exec-cf-is，分支 implementation/input-simplification，HEAD 应为 f87da58...（用 `git rev-parse HEAD` 核对前缀 f87da58），工作树干净。
- 只新增 docs/refactor/paths_equivalence/ 下的文件：run_equivalence.py、capture_cases.py、cases.json、result.json、README.md。不得修改 confflow/、tests/ 下任何文件。
- 工具可以 import 本树 tests 里现成的辅助函数：`from tests.v4.test_repair_executors import _butane, _ctx, _item, _sci`（见该文件约 L66–125）。
- 运行测试一律用 `python3 -m pytest -o addopts="" -p no:cacheprovider`，cwd 是 exec-cf-is。

## 步骤
1. capture_cases.py：一个 pytest 插件（`-p capture_cases`，PYTHONPATH 指向本目录），在 pytest_configure 里包装 `confflow.execution.confgen_executor.ConfgenExecutor._run_legacy_paths`，把每次调用的输入 records（id、atoms、coordinates）和 native 写入一个列表，最后（pytest_unconfigure）按 `json.dumps(sort_keys=True)` 去重后写到 `cases.json`（带自增 case_id，按去重键排序）。运行：
   `PYTHONPATH=docs/refactor/paths_equivalence python3 -m pytest -q -o addopts="" -p capture_cases -p no:cacheprovider tests/v4/test_confgen_paths_phase0.py tests/v4/test_confgen_paths_audit.py`
   这两个文件分别有 44 和 26 个测试。
2. 手写补充用例（追加到 cases.json，case_id 以 "extra_" 开头），分子用 `_butane` 一类现成构造器：
   - extra_terminal_endpoint：端点是只有一个邻居的末端原子的路径（例如 butane 上 start=1, end=3，move="end"，angles=[0,120,240]）；
   - extra_bare_path：不给 angles/step 的 bare 声明 `{start, end, move}`，native 里不写 angle_step（默认 120）；
   - extra_bare_path_angle_step：同上，但 native 里 `angle_step: 60`。
3. run_equivalence.py（`--check` 重跑并与 result.json 逐字节比较；无参数则生成）对每个用例：
   a. 判断范围：native 的键集合 ⊆ {"paths","angle_step","bond_scale","strict_path_bond_check"} 才在范围内；否则记 `OUT_OF_SCOPE`，写明多出的键（例如 chains），不执行 v3。
   b. legacy 运行：`ConfgenExecutor().execute(_item("c1:g1","c1",records), _ctx(_sci(seed=11, native=FrozenDict(native)), tmpdir))`，tmpdir 用 tempfile 临时目录，每次新建。
   c. 映射成 v3 native：`{"schema_version": 3, "index_base": 1, "paths": [...]}`。每条 path 保留 start/end/move/id；有 angles 或 step 的原样；bare 声明补 `step: <native.get("angle_step", 120)>`。native 的 `strict_path_bond_check` 映射到 v3 顶层同名字段；`bond_scale` 映射到 `tolerances.bond_scale`（先在 confflow/workflow/v4/confgen_schema.py 里确认字段位置，约 L417；找不到就记入该用例的 `unmapped` 列表，不要猜）。
   d. v3 运行：同样的入口，`seed=11`，native 为映射结果。
   e. 比较（浮点阈值 1e-6 Å，原子顺序必须相同）：两边的最终状态（COMPLETED/FAILED）和失败原因代码；结构数量；legacy 的每个结构能否在 v3 的结构集合里找到逐原子最大偏差 ≤1e-6 的对应（集合相等，不要求顺序相同）。
   f. 标记：全部相同为 EQUIVALENT；只有"v3 因端点没有可测二面角框架而拒绝"（失败信息含 "no measurable dihedral frame"）的用例，进入 LEGACY_DEGENERATE 检验：另构造对照声明，把末端端点换成它唯一的邻居原子（start==end 或声明非法则直接判 NOT_EQUIVALENT），用 v3 运行；再把 legacy 输出按几何去重（逐原子最大偏差 ≤1e-6 视为同一个）；两边集合完全相同才标 LEGACY_DEGENERATE，并在 result.json 记录被替换的端点、去重前后数量和两边集合的摘要；否则标 NOT_EQUIVALENT；其余任何差异标 NOT_EQUIVALENT 并写出具体差异。
   g. result.json 用 `sort_keys=True, indent=1` 输出，不得包含时间戳、临时目录路径等不确定内容。同一个输入重复运行必须逐字节相同。
4. README.md：写明生成命令、两个文件的含义、三种标记的含义、各标记的用例计数（照实抄自 result.json）。

## 自检（原始输出贴进报告）
- cd /opt/cf-worktrees/exec-cf-is && ruff check docs/refactor/paths_equivalence && black --check docs/refactor/paths_equivalence
- python3 docs/refactor/paths_equivalence/run_equivalence.py 生成 result.json，再连续运行两次 `--check`，两次都必须成功（逐字节相同）。
- `python3 -c` 统计 result.json 里 EQUIVALENT / LEGACY_DEGENERATE / NOT_EQUIVALENT / OUT_OF_SCOPE 的数量，贴进报告。
- git status 只含 docs/refactor/paths_equivalence/ 下的新文件。
遇到 NOT_EQUIVALENT 不要想办法"修"：照实记录，提交，并在报告里列出每个 NOT_EQUIVALENT 用例的 case_id 和具体差异。

## 提交
- 标题：test(confgen): record legacy-vs-v3 paths equivalence golden
- Removed-Tests: 0；Added-Tests: 0；Behavior-Change: none。
