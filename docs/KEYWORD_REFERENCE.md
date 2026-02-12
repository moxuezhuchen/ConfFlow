# ConfFlow 关键词快速参考

## confgen - 构象生成工具

### 基本用法
```bash
confgen <input.xyz> <angle_step> [选项]
```

### 关键词列表

| 关键词 | 简写 | 类型 | 说明 | 示例 |
|--------|------|------|------|------|
| `--add_bond` | - | 键 | 添加新键 | `--add_bond 1 2` |
| `--del_bond` | - | 键 | 删除现有键 | `--del_bond 2 3` |
| `--no_rotate` | - | 旋转 | 禁止某键旋转 | `--no_rotate 1 2` |
| `--force_rotate` | - | 旋转 | 强制某键旋转 | `--force_rotate 3 4` |
| `--yes` | `-y` | 控制 | 自动确认所有提示 | `-y` |
| `--optimize` | `--opt` | 优化 | 启用MMFF94s预优化 | `--opt` |
| `--bond_threshold` | `-b, -m` | 系数 | 成键判定系数(默认1.15) | `-b 1.0` |
| `--clash_threshold` | `-c` | 系数 | 软球碰撞系数(默认0.65) | `-c 0.5` |

### 常用示例

```bash
# 基础构象生成
confgen molecule.xyz 120

# 自动确认 + 预优化
confgen molecule.xyz 120 -y --opt

# 修改拓扑
confgen molecule.xyz 120 --add_bond 1 2 --del_bond 3 4

# 控制旋转
confgen molecule.xyz 120 --force_rotate 2 3 --no_rotate 3 4

# 调整参数
confgen molecule.xyz 60 -b 1.1 -c 0.6 -y

# 组合使用
confgen molecule.xyz 120 \
  -y --opt \
  --force_rotate 2 3 \
  --no_rotate 3 4 \
  -b 1.1 -c 0.6
```

---

## confrefine - 构象后处理工具

### 基本用法
```bash
confrefine <input.xyz> [选项]
```

### 关键词列表

| 关键词 | 简写 | 类型 | 说明 | 默认值 | 示例 |
|--------|------|------|------|--------|------|
| `--output` | `-o` | 输出 | 输出文件路径 | 自动 | `-o refined.xyz` |
| `--threshold` | `-t` | 去重 | RMSD阈值(Å) | 0.25 | `-t 0.5` |
| `--max-conformers` | `-n` | 限制 | 最大输出构象数 | 无 | `-n 20` |
| `--ewin` | - | 筛选 | 能量窗口(kcal/mol) | 无 | `--ewin 5` |
| `--imag` | - | 筛选 | 保留的虚频数 | 无 | `--imag 0` |
| `--dedup-only` | - | 模式 | 仅去重,跳过其他分析 | 否 | `--dedup-only` |
| `--keep-all-topos` | - | 模式 | 保留所有拓扑异构体 | 否 | `--keep-all-topos` |
| `--workers` | `-w` | 并行 | 并行核心数 | CPU-2 | `-w 8` |
| `--noH` | - | 标志 | RMSD计算忽略氢原子 | 否 | `--noH` |

### 常用示例

```bash
# 基础去重
confrefine search.xyz

# 修改RMSD阈值
confrefine search.xyz -t 0.3 -o stricter.xyz

# 能量筛选
confrefine search.xyz --ewin 5 -o filtered.xyz

# 限制输出数量
confrefine search.xyz -n 20 -o top20.xyz

# 忽略氢原子
confrefine search.xyz --noH -o noH.xyz

# 仅去重
confrefine search.xyz --dedup-only -o dedup.xyz

# 组合多个条件
confrefine search.xyz \
  -t 0.25 \
  --ewin 3.0 \
  -n 15 \
  --noH \
  -w 12 \
  -o results.xyz
```

---

## confcalc - 量子化学计算工具

### 基本用法
```bash
confcalc <input.xyz> -s <settings.ini>
```

### 必需参数

| 参数 | 简写 | 说明 | 格式 | 示例 |
|------|------|------|------|------|
| `input_xyz` | - | 输入轨迹文件 | 文件路径 | `search.xyz` |
| `--settings` | `-s` | 配置文件 | INI文件 | `-s gaussian.ini` |

### 常用示例

```bash
# 使用Gaussian计算
confcalc search.xyz -s gaussian.ini

# 使用ORCA计算
confcalc search.xyz -s orca.ini

# 仅优化
confcalc structures.xyz -s opt_settings.ini

# 频率分析
confcalc optimized.xyz -s freq_settings.ini
```

### 配置文件示例

```ini
[gaussian]
path = /opt/g16/g16
method = B3LYP
basis = 6-31G(d)

[orca]
path = /opt/orca601/orca
method = r2SCAN-3c
basis = def2-SVP

[calculation]
cores_per_task = 12
total_memory = 240GB
max_parallel_jobs = 4
charge = 0
multiplicity = 1
```

---

## confflow - 完整工作流

### 基本用法
```bash
confflow <input.xyz> -c <confflow.yaml> [选项]
```

### 关键词列表

| 关键词 | 简写 | 说明 | 示例 |
|--------|------|------|------|
| `--config` | `-c` | 配置文件(必需) | `-c confflow.yaml` |
| `--resume` | - | 从断点恢复 | `--resume` |
| `--verbose` | - | DEBUG日志 | `--verbose` |
| `--work_dir` | `-w` | 工作目录(可选) | `-w custom_dir` |

### 工作目录自动生成

- 输入: `hexane.xyz` → 工作目录: `hexane_work/`
- 输入: `molecule_123.xyz` → 工作目录: `molecule_123_work/`
- 可用 `-w custom` 覆盖自动生成的目录

### 常用示例

```bash
# 基础工作流
confflow molecule.xyz -c confflow.yaml

# 自动生成的工作目录
# molecule.xyz → molecule_work/

# 自定义工作目录
confflow molecule.xyz -c confflow.yaml -w my_workflow

# 从断点恢复
confflow molecule.xyz -c confflow.yaml --resume

# 启用调试
confflow molecule.xyz -c confflow.yaml --verbose

# 组合选项
confflow molecule.xyz \
  -c confflow.yaml \
  --resume \
  --verbose
```

---

## YAML 常用字段速记（confflow.yaml）

### `ts_rescue_scan`

控制 TS 失败后的自动救援功能。

- `true` (默认)：开启救援。当 TS 搜索失败时，自动尝试通过 Scan 寻找更好的起始点。
- `false`：关闭救援。

输出：当触发救援扫描时，会在终端打印“键长-能量”关系表，并在 `<work_dir>/scan/scan_table.txt` 写入同样内容（标记能量最高点 `MAX`）。如果配置了 `backup_dir`，该文件会随 scan 目录一起出现在 `<work_dir>/<step>/backups/<job>_scan/scan_table.txt`。

### `ts_bond_drift_threshold`

控制 TS 任务的关键键长漂移判据（仅当 TS keyword 不包含 `freq` 时生效）。

- 默认：`0.4` Å
- 含义：TS 优化后关键键长相对初始结构的偏移 $|\Delta R|$ 超过阈值则判定失败。

### `freeze`

冻结原子坐标（仅对 `opt/opt_freq` 生效；`sp/freq/ts` 会强制关闭）。原子编号均为 1-based。

支持的写法：

- 列表：`freeze: [1, 5]`
- 索引字符串：`freeze: "1,5"` 或 `freeze: "1 5"`
- 范围字符串：`freeze: "1-5"` 或 `freeze: "1,2,5-7"`

## 速查表

### 去重参数组合

```bash
# 严格去重 (0.1 Å)
confrefine search.xyz -t 0.1

# 中等去重 (0.25 Å) - 默认
confrefine search.xyz

# 宽松去重 (0.5 Å)
confrefine search.xyz -t 0.5

# 能量+RMSD去重
confrefine search.xyz -t 0.25 -ewin 5 -n 50

# 仅保留最优的N个
confrefine search.xyz -n 10 --dedup-only
```

### 构象生成参数组合

```bash
# 快速搜索 (大步进)
confgen molecule.xyz 180 -y

# 细致搜索 (小步进)
confgen molecule.xyz 60 -y

# 预优化搜索
confgen molecule.xyz 120 -y -opt

# 修改拓扑后搜索
confgen molecule.xyz 120 -y --add_bond 1 2

# 控制旋转搜索
confgen molecule.xyz 120 -y --force_rotate 2 3
```

### 工作流参数组合

```bash
# 标准流程
confflow input.xyz -c confflow.yaml

# 完整流程+调试
confflow input.xyz -c confflow.yaml --resume --verbose

# 自定义路径
confflow input.xyz -c ./configs/setup.yaml -w ./results/exp1
```

---

## 文件扩展名说明

| 扩展名 | 说明 | 用途 |
|--------|------|------|
| `.xyz` | XYZ坐标文件 | 分子结构,输入/输出 |
| `.yaml` | YAML配置文件 | confflow工作流配置 |
| `.ini` | INI配置文件 | confcalc计算配置 |
| `.log` | 日志文件 | 执行日志 |
| `.db` | 数据库文件 | 结果数据库 |

---

## 常见问题 (FAQ)

### Q: 如何快速生成构象?
```bash
confgen molecule.xyz 120 -y -opt
```

### Q: 如何去除相似构象?
```bash
confrefine search.xyz -t 0.25 -n 50
```

### Q: 工作目录在哪里?
```bash
# 自动在当前目录生成: <input_name>_work/
confflow molecule.xyz -c confflow.yaml
# → 创建 molecule_work/ 目录
```

### Q: 如何从中间步骤恢复?
```bash
confflow molecule.xyz -c confflow.yaml --resume
```

### Q: 如何查看详细日志?
```bash
confflow molecule.xyz -c confflow.yaml --verbose
```

---

## 更多帮助

```bash
# 查看各工具帮助
confgen --help
confrefine --help
confcalc --help
confflow --help

# 文档位置
cat README.md               # 快速开始
cat QUICK_START.txt        # 快速入门
cat DEVELOPMENT.md         # 详细开发文档
```

