#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow Config Schema - 配置参数规范化模块

提供统一的配置参数验证和规范化，减少 YAML → INI → dict 的转换链复杂度
"""

from typing import Dict, Any, Optional, List
import logging
import re

from ..core.utils import parse_index_spec

logger = logging.getLogger("confflow.config")


class ConfigSchema:
    """配置规范化器

    作用：
    1. 验证配置参数的类型和合法性
    2. 提供默认值
    3. 规范化参数名称和值
    """

    # 全局参数默认值
    GLOBAL_DEFAULTS = {
        "cores_per_task": 1,
        "total_memory": "4GB",
        "max_parallel_jobs": 1,
        "charge": 0,
        "multiplicity": 1,
        "rmsd_threshold": 0.25,
        "enable_dynamic_resources": False,
        "ts_rescue_scan": True,
        "scan_coarse_step": 0.1,
        "scan_fine_step": 0.02,
        "scan_uphill_limit": 10,
        "ts_bond_drift_threshold": 0.4,
        "ts_rmsd_threshold": 1.0,
        "resume_from_backups": True,
        "stop_check_interval_seconds": 1,
        "force_consistency": False,
    }

    # 步骤级参数（可覆盖全局配置）
    STEP_OVERRIDES = {
        "cores_per_task",
        "total_memory",
        "max_parallel_jobs",
        "energy_window",
        "keyword",
        "iprog",
        "itask",
        "blocks",
        "solvent_block",
        "custom_block",
    }

    @classmethod
    def normalize_global_config(cls, raw_config: Dict[str, Any]) -> Dict[str, Any]:
        """规范化全局配置

        Args:
            raw_config: 从 YAML 读取的原始配置

        Returns:
            规范化后的配置字典
        """
        normalized = cls.GLOBAL_DEFAULTS.copy()

        # 更新用户提供的值
        for key, value in raw_config.items():
            if key == "freeze":
                # 转换为整数列表
                if isinstance(value, list):
                    normalized[key] = [int(x) for x in value]
                elif isinstance(value, str):
                    normalized[key] = cls._parse_freeze_string(value)
                else:
                    normalized[key] = []
            elif key == "ts_bond_atoms":
                # 转换为整数列表
                if isinstance(value, list):
                    normalized[key] = [int(x) for x in value]
                else:
                    normalized[key] = None
            else:
                normalized[key] = value

        return normalized

    @classmethod
    def normalize_step_config(
        cls, step_config: Dict[str, Any], global_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """规范化步骤配置

        Args:
            step_config: 步骤配置（params 字段）
            global_config: 全局配置

        Returns:
            合并后的配置字典
        """
        # 从全局配置复制
        normalized = global_config.copy()

        # 应用步骤级覆盖
        params = step_config.get("params", {})
        for key, value in params.items():
            normalized[key] = value

        # 步骤类型
        normalized["step_type"] = step_config.get("type", "calc")
        normalized["step_name"] = step_config.get("name", "unnamed")

        return normalized

    @classmethod
    def validate_calc_config(cls, config: Dict[str, Any]) -> None:
        """验证 calc 任务配置

        Args:
            config: 配置字典

        Raises:
            ValueError: 配置不合法
        """
        required = ["iprog", "itask", "keyword"]
        for key in required:
            if key not in config:
                raise ValueError(f"calc config missing required parameter: {key}")

        # 验证 iprog
        valid_iprogs = {"gaussian", "g16", "orca", "1", "2", 1, 2}
        if config["iprog"] not in valid_iprogs:
            raise ValueError(f"invalid iprog: {config['iprog']}, valid: gaussian, g16, orca, 1, 2")

        # 验证 itask
        valid_itasks = {
            "opt",
            "sp",
            "freq",
            "opt_freq",
            "ts",
            "0",
            "1",
            "2",
            "3",
            "4",
            0,
            1,
            2,
            3,
            4,
        }
        if config["itask"] not in valid_itasks:
            raise ValueError(
                f"invalid itask: {config['itask']}, valid: opt, sp, freq, opt_freq, ts, 0-4"
            )

        # 验证 cores_per_task
        cores = config.get("cores_per_task")
        if cores is not None:
            try:
                cores_int = int(cores)
            except (ValueError, TypeError):
                raise ValueError(f"cores_per_task must be an integer, current: {cores}")
            if cores_int < 1:
                raise ValueError(f"cores_per_task must be >= 1, current: {cores}")

        # 验证 total_memory 格式 (如 4GB, 500MB)
        mem = config.get("total_memory")
        if mem is not None:
            mem_str = str(mem).strip().upper()
            if not re.match(r"^\d+(?:\.\d+)?\s*(?:GB|MB|KB|B)$", mem_str):
                raise ValueError(
                    f"total_memory format error: '{mem}', expected format like '4GB' or '500MB'"
                )

        # 验证 max_parallel_jobs
        max_jobs = config.get("max_parallel_jobs")
        if max_jobs is not None:
            try:
                max_jobs_int = int(max_jobs)
            except (ValueError, TypeError):
                raise ValueError(f"max_parallel_jobs must be an integer, current: {max_jobs}")
            if max_jobs_int < 1:
                raise ValueError(f"max_parallel_jobs must be >= 1, current: {max_jobs}")

        # 验证 charge/multiplicity
        charge = config.get("charge")
        if charge is not None:
            try:
                int(charge)
            except (ValueError, TypeError):
                raise ValueError(f"charge must be an integer, current: {charge}")

        mult = config.get("multiplicity")
        if mult is not None:
            try:
                mult_int = int(mult)
            except (ValueError, TypeError):
                raise ValueError(f"multiplicity must be an integer, current: {mult}")
            if mult_int < 1:
                raise ValueError(f"multiplicity must be >= 1, current: {mult}")

        # 验证 ts_bond_atoms 格式
        ts_atoms = config.get("ts_bond_atoms")
        if ts_atoms is not None:
            if isinstance(ts_atoms, str):
                parts = ts_atoms.replace(",", " ").split()
                if len(parts) != 2:
                    raise ValueError(f"ts_bond_atoms format error: {ts_atoms}, expected 'a,b' or [a, b]")
                try:
                    int(parts[0])
                    int(parts[1])
                except (ValueError, TypeError):
                    raise ValueError(f"ts_bond_atoms must be two integers: {ts_atoms}")
            elif isinstance(ts_atoms, (list, tuple)):
                if len(ts_atoms) != 2:
                    raise ValueError(f"ts_bond_atoms must be two atom indices: {ts_atoms}")
                try:
                    int(ts_atoms[0])
                    int(ts_atoms[1])
                except (ValueError, TypeError):
                    raise ValueError(f"ts_bond_atoms must be two integers: {ts_atoms}")

    @staticmethod
    def _parse_freeze_string(freeze_str: str) -> List[int]:
        """解析 freeze 字符串

        Args:
            freeze_str: 如 "1,2,3-5"

        Returns:
            原子索引列表（1-based）
        """
        return parse_index_spec(freeze_str)


def merge_step_params(step_config: Dict[str, Any], global_config: Dict[str, Any]) -> Dict[str, Any]:
    """合并步骤参数和全局配置（快捷函数）

    Args:
        step_config: 步骤配置
        global_config: 全局配置

    Returns:
        合并后的配置字典
    """
    return ConfigSchema.normalize_step_config(step_config, global_config)
