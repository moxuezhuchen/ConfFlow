# R7 β-D-吡喃葡萄糖诊断脚本归档（`r7_glucose`）

本目录沉淀 R7 缺口一系列只读诊断的可复现脚本。计算逻辑与原脚本一致，
仅做归档参数化：原机器绝对路径硬编码全部集中到各文件头常量，
可用环境变量覆盖；优化产物大文件（`.xyz` / ORCA `.out` / `.trj`）未归档，
只保留脚本与小摘要（`*.json`，单个文件 ≤ 200KB）。

结论与事实链见 `../R7-GLUCOSE-RECALL.md`，此处只记录如何复现。

## 目录

| 子目录 | 内容 | 来源 |
|---|---|---|
| `diag/` | 只读诊断：`r7_diag_main.py`（28 miss 分类＋盆召回）、`r7_diag2_main.py`（最终发布召回＋畸变分组＋转子混淆）、`r7_card_measure.py`（默认 8 形式 vs 显式 38 形式）＋ 3 个摘要 JSON | `/tmp/r7diag-out`、`/tmp/r7diag2-out`、`/tmp/r7-card-out` |
| `postopt/` | D 诊断：`r7_gen_inputs.py`（生成 ORCA 输入）、`r7_postopt_analyze.py`（优化后 CP/RMSD/转子分析）、`r7_build_outputs.py`（组装状态表＋扩展混淆表）、`run_opt.sh`＋摘要 | `/tmp/r7d-out` |
| `joint/` | 联合最小试验（C_1 单叶 × 6 转子 = 729）：`r7_joint_run.py`（引擎真实运行）、`r7_joint_recall.py`（未优化 strict/RMSD）、`r7_joint_sample.py`（分层抽样 100）、`r7_joint_postopt.py`（优化后三表＋能量窗）、`r7_validate_torsion.py`（转子草案校验）、`torsion_draft.json`、`run_opt.sh`＋摘要 | `/tmp/r7joint-out`、`/tmp/r7joint-run` |
| `boat/` | 船区第二顺位（TB_0 / B_1 各 ×729）：`r7_boat_run.py`、`r7_boat_recall.py`、`r7_boat_sample.py`、`r7_boat_postopt.py`（含与 C_1-100、D-38 的并集问答）、`run_opt.sh`＋摘要 | `/tmp/r7boat-run` |

所有 Python 脚本保留 `python3 -I` 与 `sys.path.insert` 写法
（`-I` 下 `PYTHONPATH` 被忽略，脚本自插仓库根）；文件名均不以 `test_` 开头，
不会被 pytest 收集。

## 运行顺序

```
# 0. 环境变量（只需设置一次；CONFFLOW_REPO 缺省为脚本向上查找到的仓库根）
export R7_DATA_DIR=<root全体系输入beta_d_glucopyranose目录>  # 未发布数据，不在仓库内
export R7_DIAG_OUT=$PWD/r7diag-out R7_D_OUT=$PWD/r7d-out
export R7_JOINT_RUN_OUT=$PWD/r7joint-run R7_BOAT_RUN_OUT=$PWD/r7boat-run

# 1. 只读诊断（只读仓库，只写各自输出目录；需要 R7_DATA_DIR）
python3 -I docs/confgen-fix/tools/r7_glucose/diag/r7_diag_main.py
python3 -I docs/confgen-fix/tools/r7_glucose/diag/r7_diag2_main.py     # 需要 diag 输出（R7_DIAG_OUT）
python3 -I docs/confgen-fix/tools/r7_glucose/diag/r7_card_measure.py   # 无需 R7_DATA_DIR（仅用仓库夹具）

# 2. D 诊断：生成输入 → 仓库外优化 → 分析
python3 -I docs/confgen-fix/tools/r7_glucose/postopt/r7_gen_inputs.py  # 需要 R7_DATA_DIR
cd <R7_D_OUT> && run_opt.sh ./inputs.list ./opt_all  # 见下“外部依赖”
python3 -I docs/confgen-fix/tools/r7_glucose/postopt/r7_postopt_analyze.py
python3 -I docs/confgen-fix/tools/r7_glucose/postopt/r7_build_outputs.py

# 3. 联合最小试验（C_1 × 729；转子草案缺省用归档的 joint/torsion_draft.json）
python3 -I docs/confgen-fix/tools/r7_glucose/joint/r7_validate_torsion.py
python3 -I docs/confgen-fix/tools/r7_glucose/joint/r7_joint_run.py
python3 -I docs/confgen-fix/tools/r7_glucose/joint/r7_joint_recall.py  # 需要 R7_DATA_DIR
python3 -I docs/confgen-fix/tools/r7_glucose/joint/r7_joint_sample.py
cd <R7_JOINT_RUN_OUT> && run_opt.sh ./inputs_joint100.list ./opt_joint100
python3 -I docs/confgen-fix/tools/r7_glucose/joint/r7_joint_postopt.py  # 需要 R7_D_OUT（含 D 的 seeds_opt 与 csv）

# 4. 船区第二顺位（TB_0 / B_1 各 ×729）
python3 -I docs/confgen-fix/tools/r7_glucose/boat/r7_boat_run.py
python3 -I docs/confgen-fix/tools/r7_glucose/boat/r7_boat_recall.py    # 需要 R7_DATA_DIR
python3 -I docs/confgen-fix/tools/r7_glucose/boat/r7_boat_sample.py
cd <R7_BOAT_RUN_OUT> && run_opt.sh ./inputs_boat200.list ./opt_boat200
python3 -I docs/confgen-fix/tools/r7_glucose/boat/r7_boat_postopt.py   # 需要 R7_D_OUT 与 R7_JOINT_RUN_OUT
```

以上命令均以仓库根为工作目录；各脚本输出目录缺省为当前目录下的
`r7diag-out/`、`r7d-out/`、`r7joint-run/`、`r7boat-run/`（可用上表环境变量改向）。

## 外部依赖

- xTB 优化全部在仓库外跑：本机 ORCA（`! XTB2 Opt` + `%pal nprocs 1 end`，
  无约束，`xargs -P4` 并行）。G10 禁止在仓内安装 xTB，
  故优化步骤不在 CI 内、不进仓库；`run_opt.sh` 通过 `ORCA_BIN`
  环境变量指定求解器路径（缺省 `/opt/orca611/orca`）。
- Python 侧只依赖标准库＋numpy＋仓库内 `confflow`（脚本自插 `REPO_ROOT`）。
- `R7_DATA_DIR` 指向未发布的 root 全体系输入
 （含 `reference_match.csv`、`cp_table.csv`、`seeds.xyz`、`run_meta.json`）；
  仓库夹具（`tests/fixtures/confgen/ring/beta_d_glucopyranose/`）由脚本
  按 `REPO_ROOT` 自动定位，无需设置。
- 归档版相对原脚本另有两处无害差异：输出目录不存在时自动创建；
  部分未使用的中间变量加下划线前缀、长 from-import 折行（均为 ruff 合规所需，
  不改变计算结果）。
