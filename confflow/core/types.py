#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ConfFlow 类型定义模块。

提供统一的类型别名和 TypedDict 定义，增强类型安全性。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union
from typing_extensions import TypedDict, NotRequired

# ==============================================================================
# 基础类型别名
# ==============================================================================

CoordLine = str
CoordLines = List[CoordLine]

# 坐标类型：[[x, y, z], ...]
Coords3D = List[List[float]]

# 原子列表：["C", "H", "H", ...]
AtomList = List[str]


# ==============================================================================
# 状态常量与枚举
# ==============================================================================

class TaskStatus:
    """任务与步骤状态常量"""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    SKIPPED_MULTI = "skipped_multi_frame"
    COMPLETED = "completed"
    RUNNING = "running"
    PENDING = "pending"


# ==============================================================================
# 配置相关 TypedDict
# ==============================================================================


class GlobalConfig(TypedDict, total=False):
    """全局配置参数类型定义"""

    # 程序路径
    gaussian_path: str
    orca_path: str

    # 资源配置
    cores_per_task: int
    total_memory: str
    max_parallel_jobs: int

    # 分子属性
    charge: int
    multiplicity: int

    # 计算参数
    rmsd_threshold: float
    energy_window: float
    freeze: Union[str, List[int]]

    # TS 相关
    ts_bond_atoms: Union[str, List[int]]
    ts_rescue_scan: bool
    scan_coarse_step: float
    scan_fine_step: float
    scan_uphill_limit: float
    ts_bond_drift_threshold: float
    ts_rmsd_threshold: float

    # 资源管理
    enable_dynamic_resources: bool
    resume_from_backups: bool
    stop_check_interval_seconds: int

    # ORCA 特定
    orca_maxcore: int


class StepParams(TypedDict, total=False):
    """工作流步骤参数类型定义"""

    # 通用
    name: str
    type: str
    enabled: bool

    # 计算参数（可覆盖全局配置）
    iprog: Union[str, int]
    itask: Union[str, int]
    keyword: str

    # 资源配置（可覆盖全局配置）
    cores_per_task: int
    total_memory: str
    max_parallel_jobs: int

    # 分子属性（可覆盖全局配置）
    charge: int
    multiplicity: int

    # 去重参数
    rmsd_threshold: float
    energy_window: float
    noH: bool
    dedup_only: bool
    keep_all_topos: bool

    # 约束参数
    freeze: Union[str, List[int]]
    ts_bond_atoms: Union[str, List[int]]

    # 构象生成参数
    angle_step: int
    bond_multiplier: float
    add_bond: List[List[int]]
    del_bond: List[List[int]]
    no_rotate: List[List[int]]
    force_rotate: List[List[int]]
    optimize: bool
    chains: Union[str, List[str]]
    chain_steps: Union[str, List[str]]
    chain_angles: Union[str, List[str]]
    rotate_side: str

    # chk 文件相关
    chk_from_step: Union[str, int]
    input_chk_dir: str
    gaussian_write_chk: bool


class ConformerData(TypedDict, total=False):
    """构象数据类型定义"""

    natoms: int
    comment: str
    atoms: AtomList
    coords: Coords3D
    frame_index: int
    metadata: Dict[str, Any]


class TaskResult(TypedDict, total=False):
    """计算任务结果类型定义"""

    job_name: str
    status: str  # "success", "failed", "skipped"
    error: str
    error_details: str

    # 能量
    energy: float
    final_gibbs_energy: float
    final_sp_energy: float
    g_corr: float

    # 几何结构
    final_coords: CoordLines

    # 频率信息
    num_imag_freqs: int
    lowest_freq: float

    # TS 特定
    ts_bond_atoms: str
    ts_bond_length: float


class WorkflowStats(TypedDict, total=False):
    """工作流统计信息类型定义"""

    start_time: str
    end_time: str
    input_files: List[str]
    initial_conformers: int
    final_conformers: int
    is_multi_frame_input: bool
    total_duration_seconds: float
    final_output: str
    steps: List[Dict[str, Any]]


class StepStats(TypedDict, total=False):
    """步骤统计信息类型定义"""

    name: str
    type: str
    index: int
    status: str
    input_conformers: int
    output_conformers: int
    failed_conformers: int
    duration_seconds: float
    output_xyz: str
    error: str
    end_time: str


# ==============================================================================
# 解析结果类型
# ==============================================================================


class ParsedOutput(TypedDict, total=False):
    """计算输出解析结果类型定义"""

    e_low: float  # 低层能量
    e_high: float  # 高层能量（ONIOM）
    g_low: float  # Gibbs 自由能
    g_corr: float  # Gibbs 校正
    final_coords: CoordLines
    num_imag_freqs: int
    lowest_freq: float
    frequencies: List[float]


# ==============================================================================
# 验证相关类型
# ==============================================================================


class ValidationResult(TypedDict):
    """验证结果类型定义"""

    valid: bool
    errors: List[str]
    warnings: List[str]


__all__ = [
    # 基础类型
    "CoordLine",
    "CoordLines",
    "Coords3D",
    "AtomList",
    # 配置类型
    "GlobalConfig",
    "StepParams",
    # 数据类型
    "ConformerData",
    "TaskResult",
    "WorkflowStats",
    "StepStats",
    "ParsedOutput",
    "ValidationResult",
]
