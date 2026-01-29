# tests/

本目录包含 ConfFlow 的单元测试与回归测试。

## 目录结构

- `coverage_push/`
  - 覆盖率推进/补漏测试（迭代文件，历史保留）。
  - 特点：大量 mock、覆盖边界/异常分支；优先保证稳定与可复现。
- 其他 `test_*.py`
  - 常规单元测试、集成测试、回归测试。

## 常用命令

- 运行全部测试：`pytest tests/ -q`
- 查看覆盖率：`pytest tests/ --cov=confflow --cov-report=term-missing`
- 只跑覆盖率推进组：`pytest tests/coverage_push/ -q`
