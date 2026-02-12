# ConfFlow 命令行参考

本文件列出项目内主要 CLI 的常用参数与用法示例。

## confflow

```bash
confflow <input.xyz> -c <confflow.yaml> [-w <work_dir>] [--resume] [--verbose]
```

说明：`confflow` 默认不向终端打印日志；所有 stdout/stderr 会写入输入目录下同名文件 `<input_basename>.txt`。

```bash
tail -f input.txt
```

- `-c/--config`：工作流 YAML
- `-w/--work_dir`：工作目录（默认 `<input_basename>_work`）
- `--resume`：从断点继续
- `--verbose`：更详细日志

## confcalc

```bash
confcalc <input.xyz> -s <settings.ini>
```

用于直接对多帧 XYZ 执行量化计算（Gaussian/ORCA）。

## confgen

```bash
confgen <mol.xyz> [<angle_step>] --chain <a-b-c-...> [--steps <...> | --angles "..."] [-y] [--opt]
```

说明：

- 多输入时仅需指定第一份输入的 `--chain`，其余输入会基于拓扑映射自动识别对应柔性链。
- 链上相邻原子必须成键，否则会报错并提示调整 `--add_bond` 或 `bond_threshold`。

## confrefine

```bash
confrefine <input.xyz> [-o <output.xyz>] [-t <rmsd>] [--ewin <kcal/mol>] [--imag <n>] [--noH] [-n <max>] [--dedup-only] [-w <workers>]
```

## confts

`confts` 提供 TS 相关的辅助功能：

- **keyword 改写**：把 TS keyword 改成 scan 用 keyword（移除 `opt(...)` 内的 `calcfc/tight/ts/noeigentest`，移除 `freq`；`nomicro` 保留）。

```bash
confts --rewrite-scan-keyword "opt(nomicro,calcfc,tight,ts,noeigentest) freq b3lyp/6-31g(d)"
```

TS 失败后的 scan 救援由 calc 执行器在运行 TS 任务失败时自动触发，细节见 `docs/USAGE.md`。
