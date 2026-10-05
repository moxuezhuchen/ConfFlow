# N-acetyl-L-proline methyl ester ring fixture（F1-Q9 增补预演，test-only）

来源（只读，未修改源目录；原始大日志/轨迹/rotamers 不进仓，保留在外部证据目录）：

- 输入：`/tmp/crest-q9-monitor-next-output/raw_nap_me_L_cand/nap_me_L_cand/input.xyz`
  sha256 `9da9983db9862839e66fca616194dce9227cbe3307e31e0923363663b12d3775`，893 字节，
  25 原子，注释行 `nap_me_L_cand N-acetyl-proline-methyl-ester_L-candidate_S SMILES=COC(=O)[C@@H]1CCCN1C(C)=O seed=42 INPUT-ONLY-NOT-REFERENCE`。
- 构象：`/tmp/crest-q9-monitor-next-output/raw_nap_me_L_cand/nap_me_L_cand/crest_conformers.xyz`
  sha256 `01b6ef2c83c05d590432c33573b54dd68fc84c6fcbcec157a9f76c6b3edd3daf`，23114 字节，14 帧。
- 同目录外部证据（不进仓）：`crest.out`（836 行，`CREST terminated normally.` 第 836 行）、
  `crest.err`（0 B）、`crest.energies`（14 行相对能量）、`ensemble_energies.log`、
  `crest_best.xyz`、`crest_rotamers.xyz`、`crest_input_copy.xyz`、`coord`、`coord.original`、
  `cmd.txt`、`task_meta.txt`、`crest.version`、`xtb.version`、`.CHRG`/`.UHF`（内容均为 `0`）、
  `HASHMANIFEST_nap_me_L_cand.sha256`（49 行）、`MONITOR_REPORT_NEXT.md`、
  `ROOT-LOCAL-VERIFY.json`（上层目录）。

计算口径（按外部证据原文，不声称本机复算）：

- 命令（`cmd.txt`/`task_meta.txt` 原文）：`OMP_NUM_THREADS=2 /opt/crest input.xyz -gfn1 -chrg 0 -uhf 0 -xnam /opt/xtb671/bin/xtb -T 2`；
  默认 iMTD-GC（无 runtype 旗标、无 `--quick/--squick/--mquick`），线程 ≤2。
- `crest.out`：`Version 3.0.1, Mon May  6 18:43:33 UTC 2024`，`commit (1782d7d)`；
  `--gfn1 : Use of GFN1-xTB requested.` / `GFN1-xTB level`；`Molecular charge : 0`。
- `crest.version` 与 `xtb.version` 文件齐全：CREST 3.0.1，xtb 6.7.1 (`edcfbbe`)。
- 电荷/多重度：中性单重态（`-chrg 0 -uhf 0`；`.CHRG`=`0`、`.UHF`=`0`；`crest.out Molecular charge : 0`）。
- 能量：`crest_conformers.xyz` 各帧第二行（Hartree），首 3 帧
  `-41.26181811` / `-41.26147522` / `-41.26118843`（原文逐字保留，含前导空格）。
  `crest.energies` 为相对能量（kcal/mol 窗口），未进仓，不混淆。
- 计时（`task_meta.txt` 原文）：`START_UTC=2026-10-05T15:34:11Z`，
  `END_UTC=2026-10-05T16:08:29Z`，`ELAPSED_S=2058`，`EXIT=0`，`DRY_EXIT=0`。

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
- 输入 SMILES `COC(=O)[C@@H]1CCCN1C(C)=O`；原输入标注 S（L 脯氨酸候选）。
  立体结论引用外部证据，不由本卡重算 CIP：
  `ROOT-LOCAL-VERIFY.json` 中 `stereo_validation` 给出 `input_CIP S`（中心 0-based 4，
  邻居 2/5/8/15），`all_14_conformers_same_orientation true`；
  `MONITOR_REPORT_NEXT.md` 记录 input 与 `crest_best.xyz` 经键感知后 CIP 均为 S，
  InChI `/t7-/m0/s1`、Key `WCIXKWOJEMZXMK-ZETCQYMHSA-N` 与 manifest 一致。
- 环探针见 `/tmp/fix1r-r7-root-reference-precheck.json`（nap_L 14 帧环 0-based 4-8）。
