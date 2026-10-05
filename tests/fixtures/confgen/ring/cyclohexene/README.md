# cyclohexene ring fixture（F1-Q9 增补预演，test-only）

来源（只读，未修改源目录；原始大日志/轨迹/rotamers 不进仓，保留在外部证据目录）：

- 输入：`/tmp/crest-q9-batch-monitor-output/raw_chexene/input.xyz`
  sha256 `10c99c53110d955d8155bd6b1baaf2e5cf5fd728c8ceab18d0bbb30d592cc68d`，571 字节，
  16 原子，注释行 `chexene cyclohexene SMILES=C1CCC=CC1 seed=42 INPUT-ONLY-NOT-REFERENCE`。
- 构象：`/tmp/crest-q9-batch-monitor-output/raw_chexene/crest_conformers.xyz`
  sha256 `acc51581d5b2be58a96987144ac5ec17d69bdfeba6dc2f93423710f277696c11`，2132 字节，2 帧。
- 同目录外部证据（不进仓）：`crest.out`（711 行，`CREST terminated normally.` 第 711 行）、
  `crest.err`（0 B）、`crest.energies`（2 行相对能量）、`ensemble_energies.log`、
  `crest_best.xyz`、`crest_rotamers.xyz`、`crest_input_copy.xyz`、`coord`、`coord.original`、
  `cmd.txt`、`task_meta.txt`、`crest.version`、`xtb.version`、`.CHRG`/`.UHF`（内容均为 `0`）、
  `HASHMANIFEST_chexene.sha256`、`MONITOR_REPORT.md`、`ROOT-LOCAL-VERIFY.json`（上层目录）。

计算口径（按外部证据原文，不声称本机复算）：

- 命令（`cmd.txt`/`task_meta.txt` 原文）：`OMP_NUM_THREADS=2 /opt/crest input.xyz -gfn1 -chrg 0 -uhf 0 -xnam /opt/xtb671/bin/xtb -T 2`；
  默认 iMTD-GC（无 runtype 旗标、无 `--quick/--squick/--mquick`），线程 ≤2。
- `crest.out`：`Version 3.0.1, Mon May  6 18:43:33 UTC 2024`，`commit (1782d7d)`；
  `--gfn1 : Use of GFN1-xTB requested.` / `GFN1-xTB level`；`Molecular charge : 0`。
- `crest.version` 与 `xtb.version` 文件齐全：CREST 3.0.1，xtb 6.7.1 (`edcfbbe`)。
- 电荷/多重度：中性单重态（`-chrg 0 -uhf 0`；`.CHRG`=`0`、`.UHF`=`0`；`crest.out Molecular charge : 0`）。
- 能量：`crest_conformers.xyz` 各帧第二行（Hartree），2 帧
  `-18.07236444` / `-18.06730360`（原文逐字保留，含前导空格）。
  `crest.energies` 为相对能量（kcal/mol 窗口），未进仓，不混淆。
- 计时（`task_meta.txt` 原文）：`START_UTC=2026-10-05T15:29:42Z`，
  `END_UTC=2026-10-05T15:34:11Z`，`ELAPSED_S=269`，`EXIT=0`，`DRY_EXIT=0`。

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
- 环己烯输入 SMILES `C1CCC=CC1` 未指定立体；元素序列输入与 2 帧一致。
- 根验证：上层 `ROOT-LOCAL-VERIFY.json`（chexene `root verified`，2 帧，原子顺序与感知拓扑不变，
  正常结束）；环探针见 `/tmp/fix1r-r7-root-reference-precheck.json`（chexene 2 帧环 0-based 0-5）。
