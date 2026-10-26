# ConfFlow 命令行参考

只有一个公开命令 `confflow`（另有两个安装即带的辅助入口，见末尾）。正式的 V4 命令都在 `confflow v4` 下。

## confflow v4

```text
confflow v4 {contract,boundary,canonical,validate,authoring,run} ...
```

### run — 运行整份 V4 工作流

```bash
confflow v4 run --workflow <文件> --run-root <目录> [--inputs NAME=FILE ...] \
                [--executable PROG=PATH ...] [--owner-token TOKEN] [--json]
```

| 参数 | 说明 |
| --- | --- |
| `--workflow` | V4 工作流文档（YAML 或 JSON） |
| `--inputs NAME=FILE` | 把 XYZ 文件 `FILE` 作为运行输入 `NAME`（可重复） |
| `--run-root` | 受管的运行根目录（持久化状态、已发布结果、产物） |
| `--executable PROG=PATH` | 程序 `PROG`（如 `orca`、`g16`）的可执行文件（可重复） |
| `--owner-token` | 运行的所有权令牌（默认 `v4-cli`） |
| `--json` | 在标准输出打印机器可读报告（`run_id`、`status`、`definition_digest`、各步骤状态、`manifest`） |

运行 `completed` 返回 0，否则返回 1。重新执行同一条命令即续跑。V1/V2/V3 文档以 `legacy_workflow_not_executable` 失败关闭。

### validate — 校验工作流字节

```bash
confflow v4 validate [--json] (--workflow <文件> | --stdin)
```

`--json` 为可选项。带 `--json` 时输出 `confflow.configuration-validation.v1` 报告：`ok`、`diagnostics`（`code`/`reason`/`field_path`/`step_id`）、
`definition_digest`、`step_ids`，格式与之前相同。

不带 `--json` 时输出人类可读的文本：通过则在标准输出打印 `OK: workflow is valid`（若有警告，每条一行 `Warning: <code>: <message>`）；
未通过则每个错误一行 `Error: <code>: <message>` 输出到标准错误。退出码与 `--json` 相同：通过返回 0，未通过返回非 0。

### contract / boundary — 发布契约与边界协议

```bash
confflow v4 contract --json      # confflow.configuration-contract.v4
confflow v4 boundary --json      # confflow.boundary.v4
```

### authoring — authoring 接口

```bash
confflow v4 authoring --json --stdin < request.json
```

请求/响应遵循 `confflow.authoring.v4`；操作：`describe_step`、`binding_candidates`、`instantiate_card`、
`validate_document`、`check_compatibility`、`compile_intent`。

### canonical — RFC 8785（JCS）规范化

```bash
confflow v4 canonical --json --stdin < input.json
```

## confflow（顶层）

```bash
confflow <input.xyz> ... -c <V4 工作流> [-w <运行根目录>] [--resume] [--verbose]
confflow --version
confflow --capabilities [--json]
confflow --stop
```

| 参数 | 说明 |
| --- | --- |
| `-c/--config` | V4 工作流文档（旧版文档会失败关闭） |
| `-w/--work_dir` | 运行根目录（默认 `<输入文件名>_work`） |
| `--resume` | 从已有状态续跑 |
| `--verbose` | 更详细的日志；用户错误的 traceback 也只在此时输出 |
| `--capabilities [--json]` | 打印能力握手 JSON 后退出 |
| `--version` | 打印版本后退出 |
| `--stop` | 停止本用户在这台机器上的所有 ConfFlow 进程树（含子进程；只看同 uid 的进程，能认出 `confflow` 与 `confflow-control-worker` 入口；需要 `psutil`） |

顶层调用是同一个 V4 应用的薄入口；运行日志写入输入目录下的 `<输入文件名>.txt`。
用户错误（无效的工作流或输入，`ConfFlowError` / `DomainError`）只在终端打印一行 `Error: <消息>`
（`.txt` 里保留 `[ERROR]` 记录），完整 traceback 只在 `--verbose` 时输出；非用户错误的未预期异常
保持原有的 traceback 行为。退出码不变。
`--rerun-failed` / `--step` / `-o/--output` 已退役：仍可解析（`--help` 标注 `retired; fails closed`），
但一经传入即以 `legacy_workflow_not_executable` 失败关闭。

## confflow compare / dedupe（结构比较与去重）

判断任意多帧 XYZ 中的结构是否为同一个（对称等价、镜像默认视为同一个）。细节见 [`USAGE.md`](USAGE.md) 第 7 节。

```bash
confflow compare A.xyz[@k] B.xyz[@k] [选项] [--json]
confflow dedupe IN.xyz [IN2.xyz ...] [-o OUT.xyz] [选项] [--json]
```

- `PATH@k`：0 基帧号，默认 `@0`。
- `compare` 以 A 的图（第一个选中帧）为准，打印对称 RMSD、普通重原子 RMSD、ΔE（两者能量都已知时）和判定 `same` / `different`。
- `dedupe` 以第一个输入文件的第一帧为准建图；有能量时按能量升序，每组保留最低者；打印 `N frames -> M unique` 和每帧归属。
- 两个命令都只要不出错就返回 0（判同结果不影响退出码）。

| 参数 | 说明 |
| --- | --- |
| `--rmsd 0.2` | 对称 RMSD 阈值（Å，严格小于才算同一结构；同 ConfGen） |
| `--e-tol 0.5` | 能量容差（kcal/mol，`|ΔE| <= e_tol` 才算同一结构；同 ConfGen） |
| `--no-mirror` | 不把镜像像视为同一结构（默认视为同一个） |
| `--bond-scale 1.15` | 由距离判定成键时的系数（建图用第一帧） |
| `--metal N` | 金属原子的 1 基序号（默认自动识别；只有一个金属时其边记为配位） |
| `--pair I J` | 1 基反应对（声明为成键中的边，可重复） |
| `--no-energy` | 忽略能量，只按几何判同 |
| `--json` | 打印 JSON 报告，而不是文本 |
| `-o/--output`（仅 dedupe） | 写出每组代表帧；注释保留原文并追加 ` members=<组内帧数>` |

能量从帧注释行读取：`energy_eh=<Eh>` 优先，其次 ORCA 风格的 `E <数>`；都读不到则只按几何判同。

`compare --json` 报告键：`a`、`b`（`file`、`frame`）、`symmetry_rmsd`、`heavy_atom_rmsd`、`delta_kcal`（未知为 `null`）、`rmsd_threshold`、`e_tol`、`verdict`。

`dedupe --json` 报告键：`total`、`unique`、`output`（未给 `-o` 时为 `null`）、`frames`（每帧一项：`file`、`frame`、`representative_file`、`representative_frame`、`rmsd`、`delta_kcal`；代表帧的 `rmsd`/`delta_kcal` 为 `null`）。

输入错误（XYZ 截断、坐标非数值、帧之间元素序列不一致、索引越界、选项非法、文件不可读）打印 `Error: <消息>` 并返回 `2`。

示例输出（数字仅示意格式）：

```text
$ confflow dedupe ts1_pilot.xyz -o ts1_unique.xyz
37 frames -> 15 unique
ts1_pilot.xyz@0: unique
ts1_pilot.xyz@1: duplicate of ts1_pilot.xyz@0 (rmsd 0.05 A, dE 0.01 kcal/mol)
...
wrote ts1_unique.xyz (15 frames)
```

## 返回码

- `0`：成功
- `1`：用法 / 输入 / 配置错误
- `2`：运行时失败

## 辅助入口

- `confflow-control-worker`：控制协议 v1 的外部 worker（排队的启动意图），见 `docs/CONTROL_PROTOCOL_RFC.md`。
- `confflow-fixture-agent`：显式启用的、不做计算的生命周期夹具，仅用于测试（不随包 `project.scripts` 安装；本机 `/usr/local/bin` 的副本为遗留安装产物）。
