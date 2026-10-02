# IS.1b 补充说明（配合 PLAN.md §8 的 IS.1b 卡，规则已由用户确认）

## 环境
- 工作树 /opt/cf-worktrees/exec-cf-is，分支 implementation/input-simplification，HEAD 应为 934060100b0a105ea15bdb9b2d338230a4de774c，工作树干净。不符就停止报告。
- 只改 docs/refactor/paths_equivalence/ 下的文件（已有的 run_equivalence.py、cases.json、result.json、README.md；新增 fixtures_h.json 等）。不得修改 confflow/、tests/。
- 运行测试和脚本一律用 `python3 …`，cwd 是 exec-cf-is。不要安装任何包（RDKit 已装好）。

## 要做的事（按顺序；先自检，再出结论）
1. 在 run_equivalence.py 里加入 `proper_rotation_rmsd(a, b)`：两个 (N,3) 数组，原子顺序相同；内部直接调用 `confflow.science.cluster.kabsch_rmsd`（已实现只含真旋转的 Kabsch，见 cluster.py:27-42）。所有"等价/去重/集合相等"判断都用它，阈值常量 `TOL = 1e-5`（Å）。原来 IS.1 里逐原子最大偏差的比较，阈值也改为 1e-5。
2. 自检 SC1/SC2/SC3（见 PLAN 的 IS.1b 卡第 2 条）在每次运行（包括 --check）开始时执行；任一失败，打印原因并以非零退出，不写 result.json。自检用的分子来自 fixtures_h.json：
   - SC1：fixtures_h.json["selfcheck"]["rigid"]（带氢的非平面结构）+ 固定种子的随机真旋转（用 numpy 的 `default_rng(12345)`）和平移 → RMSD ≤ 1e-5；
   - SC2：fixtures_h.json["selfcheck"]["chiral"]（CHFClBr 或 2-丁醇的带氢坐标），镜像 x → −x → RMSD > 1e-5；
   - SC3：fixtures_h.json["selfcheck"]["dihedral"]（带氢的 n-丁烷），绕中间 C–C 键把一端（连同它上面的氢）转 60° → RMSD > 1e-5。
   三个自检的实际数值写入 result.json 的 `selfcheck` 节和 README。
3. 生成 fixtures_h.json（一次性，生成脚本也放在本目录，叫 make_fixtures_h.py，运行后把坐标写进 fixtures_h.json，之后所有步骤都只读 json，不再调用 RDKit）：用 `rdkit.Chem.AddHs` 加氢、`AllChem.EmbedMolecule(mol, randomSeed=7)`、`AllChem.MMFFOptimizeMolecule(mol)`，保存原子序列（元素符号）、坐标、键表。分子：n-丁烷（C4H10）、1-丙醇（CH3CH2CH2OH）、丙胺（CH3CH2CH2NH2）、CHFClBr、一个用于 SC1 的非平面带氢分子（可复用 1-丙醇）。为了让 legacy 的原子序号与 RDKit 一致，保持 RDKit 的原子顺序（重原子在前，氢在后）。
4. 在 cases.json 里新增 "hydrogen_cases"（case_id 以 "h_" 开头）：
   - h_butane_terminal / h_butane_terminal_bare：路径 start=1（甲基碳）, end=4（另一端甲基碳）, move="end"；前者 angles=[0,120,240]，后者 bare（native 里不写 angle_step）；
   - h_propanol_oh / h_propanol_oh_bare：路径从 OH 的氧原子到另一端的甲基碳（先用分子的键表确认原子序号，不要猜）；
   - h_propylamine_nh2 / h_propylamine_nh2_bare：路径从 NH2 的氮原子到甲基碳；
   - h_butane_internal / h_propanol_internal / h_propylamine_internal：路径两端都是非末端重原子（内部键，如 2→3），angles=[0,120,240]。
   每个用例按 IS.1 的流程跑 legacy 与 v3：端点在末端重原子上的，v3 会拒绝；按 IS.1 的对照声明方式构造对照（把末端端点换成它唯一的重原子邻居），对照集合是"v3 能代表的构象"。内部键用例 v3 直接可以运行，期望 EQUIVALENT。
5. 标记规则（用叠合不变的度量，阈值 1e-5）：
   - EQUIVALENT：状态相同，且 legacy 与 v3 的结构集合相同（互相都能找到对应）；
   - LEGACY_ONLY_TERMINAL_ROTOR：端点在末端重原子上，v3 拒绝，对照 COMPLETED，且 legacy 去重后存在在对照集合里找不到对应的结构。记录 legacy_count、control_count、legacy_only_count，以及 symmetry_aware_legacy_only（只允许置换同一个端基重原子上的氢原子，再判断能否找到对应；CH3 有 3! 个置换，NH2 有 2 个，OH 只有 1 个氢则无置换）；
   - BOTH_REJECT：legacy 与 v3 都以非 COMPLETED 结束，仅原因不同，记录两边原因；
   - V3_EMPTY_DEGENERATE：legacy 产出结构，v3（或对照）COMPLETED 但发布 0 个结构，且输入链所有原子共线（任意三个原子构成的叉积范数 < 1e-9）。记录共线判定数据；
   - NOT_EQUIVALENT：其他任何差异。
   **结果是什么就记录什么**：LEGACY_ONLY_TERMINAL_ROTOR 的期望是 0，但如果实测大于 0，照实记录并提交，不要改判定、阈值或用例来"凑成 0"。
6. 原有的 28 个无氢用例保留，在新度量下重新判定，写入 result.json 的 `no_hydrogen_regression` 节，**不计入结论**。含氢用例的结果写入 `hydrogen_cases` 节，README 的计数表只统计含氢用例。
7. README：写明阈值 1e-5、三个自检的实际数值、四种标记的含义、含氢用例逐个的标记与计数、`LEGACY_ONLY_TERMINAL_ROTOR` 的计数（以及 symmetry_aware 的计数）。
8. 重复性：`run_equivalence.py` 生成后，连续运行两次 `--check`，两次都必须逐字节相同。

## 自检（原始输出贴进报告）
- ruff check docs/refactor/paths_equivalence && black --check docs/refactor/paths_equivalence
- python3 docs/refactor/paths_equivalence/make_fixtures_h.py 之后 fixtures_h.json 已写入；再运行 `run_equivalence.py`；再 `--check` 两次。
- 打印 result.json 的 selfcheck 节，打印 hydrogen_cases 每个用例的 verdict 与计数。
- git status 只含 docs/refactor/paths_equivalence/ 下的文件。

## 提交
- 标题：test(confgen): compare terminal-endpoint paths up to proper rotation with hydrogen-bearing cases
- Removed-Tests: 0；Added-Tests: 0；Behavior-Change: none。Verification 里写自检数值和含氢用例的标记计数。
