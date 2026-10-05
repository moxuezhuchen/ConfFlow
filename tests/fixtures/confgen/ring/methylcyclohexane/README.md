# methylcyclohexane ring fixture（F1-Q9 增补预演，test-only）

来源（只读，未修改源目录；原始大日志/轨迹/rotamers 不进仓，保留在外部证据目录）：

- 输入：`/tmp/crest-q9-batch-monitor-output/raw_mch/input.xyz`
  sha256 `c0c695b0e579ae22d002b75f8d709b5e1ebe54365a774c72639f6184789ff211`，716 字节，
  21 原子，注释行 `mch methylcyclohexane SMILES=CC1CCCCC1 seed=42 INPUT-ONLY-NOT-REFERENCE`。
- 构象：`/tmp/crest-q9-batch-monitor-output/raw_mch/crest_conformers.xyz`
  sha256 `5db90a3c1eaf3f07039afd2068de6e39a6827a07deee0187e246868e6a57933d`，8346 字节，6 帧。
- 同目录外部证据（不进仓）：`crest.out`（790 行，`CREST terminated normally.` 第 790 行）、
  `crest.err`（0 B）、`crest.energies`（6 行相对能量）、`ensemble_energies.log`、
  `crest_best.xyz`、`crest_rotamers.xyz`、`crest_input_copy.xyz`、`coord`、`coord.original`、
  `cmd.txt`、`task_meta.txt`、`crest.version`、`xtb.version`、`.CHRG`/`.UHF`（内容均为 `0`）、
  `HASHMANIFEST_mch.sha256`、`MONITOR_REPORT.md`、`ROOT-LOCAL-VERIFY.json`（上层目录）。

计算口径（按外部证据原文，不声称本机复算）：

- 命令（`cmd.txt`/`task_meta.txt` 原文）：`OMP_NUM_THREADS=2 /opt/crest input.xyz -gfn1 -chrg 0 -uhf 0 -xnam /opt/xtb671/bin/xtb -T 2`；
  默认 iMTD-GC（无 runtype 旗标、无 `--quick/--squick/--mquick`），线程 ≤2。
- `crest.out`：`Version 3.0.1, Mon May  6 18:43:33 UTC 2024`，`commit (1782d7d)`；
  `--gfn1 : Use of GFN1-xTB requested.` / `GFN1-xTB level`；`Molecular charge : 0`。
- `crest.version` 与 `xtb.version` 文件齐全：CREST 3.0.1，xtb 6.7.1 (`edcfbbe`)。
- 电荷/多重度：中性单重态（`-chrg 0 -uhf 0`；`.CHRG`=`0`、`.UHF`=`0`；`crest.out Molecular charge : 0`）。
- 能量：`crest_conformers.xyz` 各帧第二行（Hartree），首 3 帧
  `-22.39019388` / `-22.38895169` / `-22.38407640`（原文逐字保留，含前导空格）。
  `crest.energies` 为相对能量（kcal/mol 窗口），未进仓，不混淆。
- 计时（`task_meta.txt` 原文）：`START_UTC=2026-10-05T15:20:11Z`，
  `END_UTC=2026-10-05T15:29:42Z`，`ELAPSED_S=571`，`EXIT=0`，`DRY_EXIT=0`。

文件（本目录仅 4 个）：

- `input.xyz`：源 `input.xyz` 原始字节直接复制（sha 见上）。
- `crest_conformers.xyz`：源 `crest_conformers.xyz` 原始字节直接复制（sha 见上）。不转换、不改写、不重排。
- `README.md`：本文件。
- `MANIFEST.json`：路径/采样模式/sha256/帧数/元素顺序/电荷多重度/SMILES 与立体来源/
  CREST 与 xTB 版本和 GFN1/能量单位/计算 UTC 元数据/原外部证据定位。

科学范围（诚实口径）：

- 该系综来自用户授权服务器计算（授权配置 `814new`，主机/用户/端口/密钥原文一律隐去），
  不是用户直接提供坐标文件，不声称穷尽全部物理构象；
  表述为“默认 iMTD-GC/GFN1 采样在给定输入与阈值下的构象系综”。
- 甲基环己烷输入 SMILES `CC1CCCCC1` 未指定立体；元素序列输入与 6 帧一致。
- 根验证：上层 `ROOT-LOCAL-VERIFY.json`（mch `root verified`，6 帧，原子顺序与感知拓扑不变，
  正常结束）；环探针见 `/tmp/fix1r-r7-root-reference-precheck.json`（mch 6 帧环 0-based 1-6）。
