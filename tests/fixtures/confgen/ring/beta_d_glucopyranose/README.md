# beta-D-glucopyranose ring fixture（F1-Q9 增补预演，test-only）

来源（只读，未修改源目录；原始大日志/轨迹/rotamers 不进仓，保留在外部证据目录）：

- 输入：`/tmp/crest-q9-glc-monitor-output/raw_glc_pyr_anomerB_cand/input.xyz`
  sha256 `7c1be01108586efcb51c1d8c2f7d9662d134fd0a89d8eacd7fe649125a3d7bac`，873 字节，
  24 原子，注释行 `glc_pyr_anomerB_cand glucopyranose_anomer-B-candidate SMILES=C([C@@H]1[C@H]([C@@H]([C@H]([C@@H](O1)O)O)O)O)O seed=42 INPUT-ONLY-NOT-REFERENCE`。
  注意输入注释行 SMILES 文本缺少闭环数字配对的末尾 `1`（外部源原文如此，此处逐字引用，不修正、不补写）。
- 构象：`/tmp/crest-q9-glc-monitor-output/raw_glc_pyr_anomerB_cand/crest_conformers.xyz`
  sha256 `1d5f8bef52c34989e50fa358c3b72d16507e529a67e5338f23831b849b719b62`，245830 字节，155 帧。
- 同目录外部证据（不进仓）：`crest.out`（924 行，`CREST terminated normally.` 第 924 行）、
  `crest.err`（0 B）、`crest.energies`（155 行相对能量）、`crest_rotamers.xyz`、
  `cmd.txt`、`task_meta.txt`、`crest.version`、`xtb.version`、
  `HASHMANIFEST_glc_pyr_anomerB_cand.sha256`（11 行）、`MONITOR_REPORT_GLC.md`、
  `ROOT-LOCAL-VERIFY.json`。
  未回收项（按最小清单口径，未进仓）：`MDFILES/`、`crest_dynamics.trj`、`confcross.xyz`、
  `crest.restart` 等轨迹/中间文件；`ensemble_energies.log` 不在 glc 回收集内，已如实注记。

计算口径（按外部证据原文，不声称本机复算）：

- 命令（`cmd.txt`/`task_meta.txt` 原文）：`OMP_NUM_THREADS=2 /opt/crest input.xyz -gfn1 -chrg 0 -uhf 0 -xnam /opt/xtb671/bin/xtb -T 2`；
  默认 iMTD-GC（无 runtype 旗标、无 `--quick/--squick/--mquick`），线程 ≤2。
- `crest.out`：`Version 3.0.1, Mon May  6 18:43:33 UTC 2024`，`commit (1782d7d)`；
  `--gfn1 : Use of GFN1-xTB requested.` / `GFN1-xTB level`；能量偏置单位 `Eh`（Hartree）。
- `crest.version` 与 `xtb.version` 文件齐全：CREST 3.0.1，xtb 6.7.1 (`edcfbbe`)。
- 电荷/多重度：中性单重态（`-chrg 0 -uhf 0`）。
  glc 回收集内无 `.CHRG`/`.UHF` 文件，已如实注记（mch/chexene/nap 同命令集有 `0` 实证，此处不跨组冒充）。
- 能量：`crest_conformers.xyz` 各帧第二行（Hartree），首 3 帧
  `-47.38937514` / `-47.38871649` / `-47.38834612`（原文逐字保留，含前导空格）。
  `crest.energies` 为相对能量（kcal/mol 窗口），未进仓，不混淆。
- 计时（`task_meta.txt` 原文）：`START_UTC=2026-10-05T16:08:29Z`，
  `END_UTC=2026-10-05T16:33:15Z`，`ELAPSED_S=1486`，`EXIT=0`，`DRY_EXIT=0`。

文件（本目录仅 4 个）：

- `input.xyz`：源 `input.xyz` 原始字节直接复制（sha 见上）。
- `crest_conformers.xyz`：源 `crest_conformers.xyz` 原始字节直接复制（sha 见上）。不转换、不改写、不重排。
- `README.md`：本文件。
- `MANIFEST.json`：路径/采样模式/sha256/帧数/元素顺序/电荷多重度/SMILES 与立体来源/
  CREST 与 xTB 版本和 GFN1/能量单位/计算 UTC 元数据/原外部证据定位。无法核实字段已标明。

科学范围（诚实口径）：

- 该系综来自用户授权服务器计算（授权配置 `814new`，主机/用户/端口/密钥原文一律隐去），
  不是用户直接提供坐标文件，不声称穷尽全部物理构象；
  表述为“默认 iMTD-GC/GFN1 采样在给定输入与阈值下的构象系综”。
- 输入身份引用外部证据，不由本卡重算立体：
  `ROOT-LOCAL-VERIFY.json` 给出 `frozen_input_identity beta-D-glucose; WQZGKKKJIJFFOK-VFUOTHLCSA-N`，
  `all_5_input_stereocenters all155 same signed orientation`，155 帧原子顺序与感知拓扑不变；
  `MONITOR_REPORT_GLC.md` 记录 input InChI `/t2-,3-,4+,5-,6-/m1/s1`、Key 与 manifest 一致（beta-D），
  全部 155 帧 5 中心与输入逐帧一致（mismatches=0），首帧 InChI/Key 与输入逐字相同。
