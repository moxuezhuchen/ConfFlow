"""工作流执行引擎。

该包负责：
- 解析并规范化配置
- 执行 steps（confgen/calc/refine/viz）
- 断点续跑与统计信息

CLI 入口在 `confflow.cli`。
"""

from .engine import run_workflow, load_workflow_config
from .helpers import (
    pushd, as_list, normalize_pair_list,
    count_conformers_any, count_conformers_in_xyz,
)
from .validation import validate_inputs_compatible
from .config_builder import build_task_config, create_runtask_config
from .stats import (
    CheckpointManager, WorkflowStatsTracker, TaskStatsCollector,
    FailureTracker, Tracer,
)

__all__ = [
    "run_workflow", "load_workflow_config",
    "pushd", "as_list", "normalize_pair_list",
    "count_conformers_any", "count_conformers_in_xyz",
    "validate_inputs_compatible",
    "build_task_config", "create_runtask_config",
    "CheckpointManager", "WorkflowStatsTracker", "TaskStatsCollector",
    "FailureTracker", "Tracer",
]
