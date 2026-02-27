# tests 目录说明与准入规则

**目标**: 提供清晰的测试组织策略、编码准则与提交流程，保持测试可读、稳定且易于维护。

**快速指南**
- **位置与用途**: 所有单元/集成/回归测试位于 `tests/` 目录。
- **命名**: 测试文件必须以 `test_` 开头，测试函数以 `test_` 开头。
- **共享资源**: 使用 `tests/conftest.py` 中的 fixtures（如 `cd_tmp`, `input_xyz`, `config_yaml`, `sync_executor`）替代每个文件内重复的 setup/teardown。复用 `tests/_helpers.py` 中的 Fake 对象（`FakeResultsDB`, `FakeExecutor` 等）。
- **断言风格**: 偏向具体、可重复的断言，避免对私有实现细节进行断言或 patch；优先使用黑盒接口或小型替身（fakes）来验证行为。
- **参数化**: 对重复的输入→期望对使用 `@pytest.mark.parametrize` 简化。
- **隔离**: 使用 `tmp_path` 代替 `tempfile` + 手动清理；`importlib.reload` 必须放在 `try/finally` 中。

**测试标记**
- `@pytest.mark.integration`: 端到端集成测试，可通过 `pytest -m integration` 单独运行。

**CI 与覆盖**
- 默认 CI 执行 `pytest tests`，所有核心测试应快速且确定性强。
- 覆盖率阈值 `fail_under = 70`，已在 `pyproject.toml` 中配置。

**变更流程**
- 提交影响多个测试文件的重构前，请先在本地运行 `pytest -q tests` 并确保通过。

示例命令
```bash
pytest -q tests              # 运行所有测试
pytest -m integration        # 仅运行集成测试
pytest -m "not integration"  # 跳过集成测试
```
