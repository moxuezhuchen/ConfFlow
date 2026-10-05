# rpdd ring 基准 fixture（F1，test-only）

## 来源（只读，未写入源目录）

- `/mnt/c/dft/wcm/rpdd.gjf`：sha256 `4929b48cbc4c3a2fa39788f3b68e792471b34c8b36b40f4441b2692c14e5b8a3`，1439 字节。
  route `# opt freq b3lyp/6-31g(d) em=gd3bj`；title `re-rr-salanal2-r-maend-rpddb-ts1-irc-end1`；charge 0 mult 1。
  坐标为该文件第 8–29 行（共 22 行 `el x y z`；卡片 L7–L28 为行号笔误，L7 实为 `0 1`，此处以实测为准）。
- `/mnt/c/dft/wcm/rpdd.xyz`：sha256 `2078aca84ff293021553e3046920b9ceb787f7d04193d1f9efdd433705a1c7b1`，4368 字节。
  72 行 = 3 帧 ×（header + comment + 22 原子），header 全为 `22`。

用户确认（`/tmp/fix1r-f1-card-prep/USER-DATA-CONFIRMATION.md`）：`rpdd.xyz` 是以 `rpdd.gjf` 为输入的
CREST xTB1 输出；CREST 默认 XYZ 能量注释单位为 Hartree；保持原文件字节及原始能量注释，不转换。

## 文件

- `input_ts_fragment.xyz`：取 gjf 上述 22 行坐标逐行转写。header `22`；comment 注明来源 title、
  charge 0 mult 1 与源 sha256；`% / # / title / 0 1` 头不进 xyz。
  转写规范化：去除 gjf 行尾 CR，前后空白按生产解析器 `strip()` 规范化为 LF 行，行内 `el x y z`
  数值文本逐字不变。经生产解析器 `confflow/core/gaussian_input.py:43 parse_gaussian_input`
 （文本版 `:53 parse_gaussian_input_text`）核对：22 原子，charge 0 mult 1。
- `crest_conformers.xyz`：`rpdd.xyz` 全 72 行逐字节复制（sha256 与源一致）。
  comment 三行原文逐字保留（含前导空格）：`        -44.25350192` /
  `        -44.25032157` / `        -44.24850601`，单位按 Hartree 理解，原字节不转换、不改写。
  经 `confflow/core/io.py:181 read_xyz_file`（`iter_xyz_frames :71`）核对：
  3 帧 × 22 原子，元素序列 `C C O O C C O O H H H C C C C H C H C H H H`，坐标有限。
- 两目标元素序列一致（C10/O4/H8）；3 帧 × 22 原子；有限坐标。

## 原子编号（1-based，占位，不推断拓扑/手性）

环原子为编号 1-2-4-5-6-8；C1 为手性中心占位；C12 起为苯基占位。拓扑与手性不推断。

## 期望结论（PLAN 科学预期，F1 不断言已验证）

PLAN §0.3 实测引用：2 个环盆，3 个构象（第 3 个来自苯基转子）。本卡仅提供基准数据，
不宣称已做 CP 召回；科学断言留给 R1–R7。

## 能量

comment 原始数值逐字保留，单位按用户确认理解为 Hartree，原字节不转换。

## Q9 与计算环境

- Q9 参考集（甲基环己烷、环己烯、THF、脯氨酸衍生物、吡喃糖）缺失；缺 Q9 仅影响 R7，不阻塞 F1/R1。
- 脯氨酸/吡喃糖身份待指定。
- CREST 仅服务器可用；本机不安装、不计算、不做大规模计算。
- 环己烷、甲基环己烷理想几何由测试辅助函数生成，不需外部数据。
