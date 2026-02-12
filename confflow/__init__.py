#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow - 自动化计算化学构象搜索工作流引擎

模块导出:
  - main: 工作流主程序入口
  - run_generation: 构象生成
  - ChemTaskManager: 量子计算管理器
  - RefineOptions: 筛选配置
  - ConfFlowLogger: 日志系统
"""

__version__ = "1.0"
__author__ = "ConfFlow Team"

# ============================================================================
# 可选依赖的集中管理
# ============================================================================

# RDKit - 构象生成必需
try:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False
    Chem = None  # type: ignore
    AllChem = None  # type: ignore

# psutil - 资源监控（可选）
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None  # type: ignore

# numba - JIT 加速（可选）
try:
    from numba import njit

    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False

    # 回退：返回原函数的装饰器
    def njit(*args, **kwargs):
        def decorator(func):
            return func

        return decorator if not args else args[0]


# ============================================================================
# 核心模块导出
# ============================================================================

try:
    from .main import main
    from .blocks.confgen import generate_conformers as run_generation
    from .calc import ChemTaskManager
    from .blocks.refine import RefineOptions, process_xyz
    from .blocks.viz import parse_xyz_file
    from .core.utils import ConfFlowLogger, get_logger
    from .core.io import read_xyz_file, write_xyz_file, parse_comment_metadata
    from .config.schema import ConfigSchema, merge_step_params

    __all__ = [
        # 工作流入口
        "main",
        # 构象生成
        "run_generation",
        # 量子计算
        "ChemTaskManager",
        # 构象筛选
        "RefineOptions",
        "process_xyz",
        # 可视化
        "parse_xyz_file",
        # 日志
        "ConfFlowLogger",
        "get_logger",
        # I/O
        "read_xyz_file",
        "write_xyz_file",
        "parse_comment_metadata",
        # 配置
        "ConfigSchema",
        "merge_step_params",
        # 版本
        "__version__",
        # 可选依赖状态
        "RDKIT_AVAILABLE",
        "PSUTIL_AVAILABLE",
        "NUMBA_AVAILABLE",
    ]
except ImportError as e:
    # 调试模式：如果导入失败，打印错误但不中断
    import warnings

    warnings.warn(f"ConfFlow 模块导入警告: {e}")
    __all__ = ["__version__", "RDKIT_AVAILABLE", "PSUTIL_AVAILABLE", "NUMBA_AVAILABLE"]
