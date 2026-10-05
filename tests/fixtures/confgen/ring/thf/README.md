# thf ring fixture（F1-Q9 增补预演，test-only）

来源（只读，未修改源目录；原始大日志/轨迹/rotamers 不进仓，保留在外部证据目录）：

- 输入：`/tmp/crest-q9-pilot-monitor-output/remote_thf/input.xyz`
  sha256 `559fb0911f3d4a2bcf4ee0f604dd78acd60bde330ab855e1061f515d1923f4fe`，461 字节，
  13 原子，注释行 `thf tetrahydrofuran SMILES=C1CCOC1 seed=42 INPUT-ONLY-NOT-REFERENCE`。
- 构象：`/tmp/crest-q9-pilot-monitor-output/remote_thf/crest_conformers.xyz`
  sha256 `c9b1e561315b49ef933bd6eddccd696c32a9ef9cfa1221e1dc07c27862aa556c`，871 字节，1 帧。
- 同目录外部证据（不进仓）：`crest.out`（743 行，`CREST terminated normally.` 第 743 行）、
  `crest.err`（0 B）、`crest.energies`（相对能量）、`ensemble_energies.log`、
  `crest_best.xyz`、`crest_rotamers.xyz`、`cmd.txt`、`dry.txt`、
  `ROOT-LOCAL-VERIFY.json`、`ROOT-THF-CP.json`、`EVIDENCE.md`、`VALIDATION.md`。

计算口径（按外部证据原文，不声称本机复算）：

- 命令（`cmd.txt` 原文）：`OMP_NUM_THREADS=2 /opt/crest input.xyz -gfn1 -chrg 0 -uhf 0 -xnam /opt/xtb671/bin/xtb -T 2`；
  默认 iMTD-GC（无 runtype 旗标、无 `--quick/--squick/--mquick`），线程 ≤2。
- `crest.out`：`Version 3.0.1, Mon May  6 18:43:33 UTC 2024`，`commit (1782d7d)`；
  `--gfn1 : Use of GFN1-xTB requested.` / `GFN1-xTB level`；`Molecular charge : 0`。
- xtb 版本：THF 回收集内无 `xtb.version` 文件；`VALIDATION.md` 交叉记录为
  xtb 6.7.1 (`edcfbbe`)（`crest.out` 头与 dry 一致）。此处如实注记，不冒称已见文件。
- 电荷/多重度：中性单重态（`-chrg 0 -uhf 0`；`crest.out Molecular charge : 0`）。
  THF 回收集内无 `.CHRG`/`.UHF` 文件，已如实注记。
- 能量：`crest_conformers.xyz` 第二行 `-17.48309498`，单位 Hartree（Eh）。
  `crest.energies` 为相对能量（kcal/mol 窗口，单构象故 0.000），未进仓，不混淆。
- 计时：THF 回收集内无 `task_meta.txt`；`crest.out` 记录 `CREST runtime (total) 0 d, 0 h, 2 min, 43.790 sec`，
  `cpu-time 5 min 23.983 sec`；远端 `date -u` 见 `EVIDENCE.md`（2026-10-05 15:11 UTC 左右）。未编造 START/END。

文件（本目录仅 4 个）：

- `input.xyz`：源 `input.xyz` 原始字节直接复制（sha 见上）。
- `crest_conformers.xyz`：源 `crest_conformers.xyz` 原始字节直接复制（sha 见上）；
  注释原文逐字保留（含前导空格）：`        -17.48309498`。不转换、不改写、不重排。
- `README.md`：本文件。
- `MANIFEST.json`：路径/采样模式/sha256/帧数/元素顺序/电荷多重度/SMILES 与立体来源/
  CREST 与 xTB 版本和 GFN1/能量单位/计算 UTC 元数据/原外部证据定位。无法核实字段已标明。

科学范围（诚实口径）：

- 该系综来自用户授权服务器计算（授权配置 `814new`，主机/用户/端口/密钥原文一律隐去），
  不是用户直接提供坐标文件，不声称穷尽全部物理构象；
  表述为“默认 iMTD-GC/GFN1 采样在给定输入与阈值下的构象系综”。
- THF 无手性中心（SMILES `C1CCOC1`）；元素序列输入与 1 帧一致。
- 根验证：`ROOT-LOCAL-VERIFY.json`（`root_verified true`，1 帧，能量 `-17.48309498`，
  limitation `not a proof of exhaustive conformer sampling`）；
  `ROOT-THF-CP.json` 环 puckering 探针记录（`q_over_rbar 0.20059027`）。
