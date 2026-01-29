#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置加载与校验的统一入口。

目标：让 CLI/engine/tests 都复用同一套逻辑：
- 读取 YAML
- validate_yaml_config 结构校验
- ConfigSchema.normalize_global_config 标准化

返回结构保持简单的 dict，避免引入新的复杂类型。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import yaml

from .schema import ConfigSchema
from ..core.utils import validate_yaml_config

logger = logging.getLogger("confflow.config")


class ConfigurationError(ValueError):
    """配置错误异常"""

    def __init__(self, message: str, errors: List[str] = None):
        self.errors = errors or []
        if errors:
            full_msg = f"{message}:\n" + "\n".join(f"  - {e}" for e in errors)
        else:
            full_msg = message
        super().__init__(full_msg)


def load_workflow_config_file(config_file: str) -> Dict[str, Any]:
    """读取并校验工作流配置文件。

    Args:
        config_file: 配置文件路径

    Returns:
        包含 global、steps、raw 的配置字典

    Raises:
        FileNotFoundError: 配置文件不存在
        ConfigurationError: 配置验证失败
        yaml.YAMLError: YAML 解析失败
    """
    if not config_file:
        raise ConfigurationError("配置文件路径不能为空")

    if not os.path.exists(config_file):
        raise FileNotFoundError(f"配置文件不存在: {config_file}")

    if not os.path.isfile(config_file):
        raise ConfigurationError(f"配置路径不是文件: {config_file}")

    logger.info(f"加载配置文件: {config_file}")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            full_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"YAML 解析失败: {e}")
        raise ConfigurationError(f"YAML 解析失败: {e}") from e
    except (IOError, OSError) as e:
        logger.error(f"读取配置文件失败: {e}")
        raise ConfigurationError(f"读取配置文件失败: {e}") from e

    # 基础类型检查
    if not isinstance(full_config, dict):
        raise ConfigurationError(f"配置文件根节点必须是字典类型，当前: {type(full_config).__name__}")

    # 结构验证
    errors = validate_yaml_config(full_config)
    if errors:
        logger.error(f"配置验证失败，共 {len(errors)} 个错误")
        raise ConfigurationError("配置文件验证失败", errors)

    # 标准化全局配置
    global_raw = full_config.get("global", {})
    if global_raw is None:
        global_raw = {}
    if not isinstance(global_raw, dict):
        raise ConfigurationError(f"global 配置必须是字典类型，当前: {type(global_raw).__name__}")

    global_config = ConfigSchema.normalize_global_config(global_raw)

    # 验证步骤配置
    steps = full_config.get("steps", [])
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        raise ConfigurationError(f"steps 配置必须是列表类型，当前: {type(steps).__name__}")

    # 验证每个步骤的基本结构
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ConfigurationError(f"步骤 {i+1} 必须是字典类型，当前: {type(step).__name__}")
        if "name" not in step:
            raise ConfigurationError(f"步骤 {i+1} 缺少必要的 'name' 字段")
        if "type" not in step:
            raise ConfigurationError(f"步骤 {i+1} ({step.get('name', 'unnamed')}) 缺少必要的 'type' 字段")

    logger.info(f"配置加载成功: {len(steps)} 个步骤")

    return {
        "global": global_config,
        "steps": steps,
        "raw": full_config,
    }

