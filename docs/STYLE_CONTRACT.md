# ConfFlow 风格契约

本文档是 ConfFlow 在代码、注释、文档、输入和输出上的统一风格基准。

## 1) 代码风格

- 格式化：`black`，行宽 `100`（`ruff` 只做 lint，不要用 `ruff format` 整体重排，见 `DEVELOPMENT.md` §2）
- Lint / import 排序：`ruff`（`E,F,I,B,UP,D`）
- 类型检查：`mypy`
- 测试：`pytest`

本地执行：

```bash
black .
ruff check .
mypy confflow
pytest -q
```

### 1.1) 文件头

只有可直接执行的入口脚本必须以 shebang（`#!/usr/bin/env python3`）开头：带有 `if __name__ == "__main__":` 并打算被直接运行的文件，以及 `scripts/` 与 `tools/` 下的命令行脚本。库模块不要求 shebang。

除 shebang 外的文件头要求不变：每个 `.py` 文件必须包含 `from __future__ import annotations`；当文件包含模块 docstring 时，该导入应位于模块 docstring 之后。

入口脚本的文件头示例：

```python
#!/usr/bin/env python3
from __future__ import annotations
```

### 1.2) Docstring

- 风格：NumPy 风格，使用 `Parameters`、`Returns`、`Raises`、`Examples` 等分节。
- 语言：英文。
- 范围：所有公开模块、类和函数都应提供 docstring。
- 模块 docstring 应保持简洁，优先使用单段或短段落。

示例：

```python
def load_xyz(path: str) -> list[list[str]]:
    """Load an XYZ file and return atom blocks.

    Parameters
    ----------
    path : str
        Path to the XYZ file.

    Returns
    -------
    list[list[str]]
        Parsed atom blocks.

    Raises
    ------
    FileNotFoundError
        Raised when *path* does not exist.
    """
```

### 1.3) 行内注释与日志

- 代码内行内注释、块注释和日志消息一律使用英文。
- 注释应使用完整短句，说明“为什么”或“约束”，避免重复代码字面含义。
- 注释优先使用句式写法，例如 `Fall back to ...`、`Keep ... consistent.`，避免零散标签式写法。
- 用户可见输出继续遵循现有 CLI 约定，级别前缀统一为 `INFO`、`WARNING`、`ERROR`、`SUCCESS`。
- CLI help 文案统一使用简短说明式短语，优先写成 `Path to ...`、`Enable ...`、`Stop ...` 这类可扫描句式。
- 参数校验和用户输入错误优先使用 `must ...`、`cannot ...`、`not found` 这类直接表达，避免口语化或拟人化写法。
- 运行时失败统一使用 `Failed to ...` 句式；成功与状态日志统一使用 `Loaded ...`、`Wrote ...`、`Started ...`、`Skipped ...` 等事件式表达。

### 1.4) 类型标注

- 使用 PEP 604 语法：`X | None`、`X | Y`。
- 不导入 `Optional` 或 `Union`。
- 优先使用内建泛型：`list[int]`、`dict[str, Any]`、`tuple[int, ...]`。

### 1.5) 导出

- 对外提供公开接口的包入口（各包的 `__init__.py`）和被其它子系统导入的公开模块应当声明 `__all__`。
- 仅在子系统内部使用的模块建议声明 `__all__`，不强制。
- `__all__` 使用多行列表格式，并保留尾逗号。
- 子包 `__init__.py` 应负责关键符号重导出，并同步维护 `__all__`。

### 1.6) 异常

- 优先使用 `confflow.core.exceptions` 中的自定义异常。
- 禁止在应用层直接抛裸 `RuntimeError` 或 `ValueError`；需要时应映射到更具体的异常类型。
- 例外：pydantic 校验函数（`field_validator` / `model_validator`）内必须抛 `ValueError`（或 pydantic 自身的错误类型），因为框架只把这类异常转换为校验错误。

## 2) 文档风格

- 仓库内部的开发/架构/流程文档使用中文；面向外部读者的文档使用英文。当前属于后一类的文件：`README.md`、`docs/SECURITY_MODEL.md`、`docs/PUBLIC_ALPHA.md`、`docs/RELEASE.md`、`docs/CONTROL_PROTOCOL_RFC.md`。
- 标题层级、列表和代码块保持稳定、简洁，不依赖 Markdown 尾随双空格实现换行。
- 文档中的代码风格、覆盖率阈值、测试数量、支持版本等事实信息应与仓库当前状态同步。
- 文档若描述代码注释或 docstring 风格，必须与本文件和 `pyproject.toml` 保持一致。

## 3) 输入契约

- TS 键原子配置键名只允许 `ts_bond_atoms`。
- 工作流 YAML 中拒绝遗留键 `ts_bond`。
- CLI 内部始终使用绝对路径解析输入文件。

## 4) 输出契约

- 顶层 plain-CLI 入口将运行日志写入输入目录下的 `<input_basename>.txt`；`confflow v4 run`
  的产物为受管运行根目录下的 `run_result.json` 系（见 `COMMAND_REFERENCE.md`）。
- CLI 退出码统一为：
  - `0`：成功
  - `1`：用法 / 输入 / 配置错误
  - `2`：运行时失败
- 共享控制台输出助手的文本宽度固定为 `80` 列（`confflow/core/console.py::FIXED_WIDTH`）。

## 5) 变更规则

- 任何用户可见消息、返回码、输出文件命名的变化都必须补测试。
- 新增 CLI 入口必须遵循同一套输出与退出码契约。
