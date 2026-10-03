# ConfFlow 命令行参考

本文件列出项目内主要 CLI 的常用参数与用法示例。

## confflow

```bash
confflow <input.xyz> [-c <config.yaml>] [-w <work_dir>] [--resume] [--verbose]
```

说明：仓库根目录提供 `confflow.example.yaml` 作为示例配置；工作流 CLI（`confflow`）默认不向终端打印运行日志；stdout/stderr 会写入输入目录下同名文件 `<input_basename>.txt`。

```bash
tail -f input.txt
```

- `-c/--config`：工作流 YAML；省略时默认使用第一个输入文件同目录下的 `confflow.yaml`
- `-w/--work_dir`：工作目录（默认 `<input_basename>_work`）
- `--resume`：从断点继续
- `--verbose`：更详细日志

## 统一返回码

- `0`：成功
- `1`：用法 / 输入 / 配置错误
- `2`：运行时失败
