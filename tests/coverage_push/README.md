# Coverage Push Tests

本目录包含为了将项目总覆盖率提升至 90% 而专门编写的补充测试。

## 目录结构

- `_helpers.py`: 包含测试共用的辅助函数（如模拟导入失败的 reload 工具）。
- `test_analysis_and_viz_paths.py`: 覆盖 `calc/analysis.py` 和 `blocks/viz/report.py` 的边缘分支。
- `test_calc_manager_paths.py`: 覆盖 `calc/manager.py` 中的停止信号、清理逻辑及异常恢复。
- `test_cli_and_confts_paths.py`: 覆盖 `cli.py` 和 `confts.py` 中的命令行交互与进程管理。
- `test_confgen_refine_fallbacks.py`: 覆盖 `confgen` 和 `refine` 模块在缺少 `numba` 或 `tqdm` 时的 fallback 路径。
- `test_rescue_ts_scan_paths.py`: 覆盖 `calc/rescue.py` 中复杂的 TS 搜索与扫描逻辑。
- `test_task_runner_and_input_helpers_paths.py`: 覆盖 `task_runner.py` 的执行逻辑与 `input_helpers.py` 的配置解析。
- `test_workflow_engine_paths.py`: 覆盖 `workflow/engine.py` 的断点续传、Trace 逻辑及配置加载错误。

## 运行方式

在项目根目录下运行：

```bash
pytest tests/coverage_push/
```

或者运行全量测试并查看覆盖率：

```bash
pytest --cov=confflow --cov-report=term-missing tests/
```
