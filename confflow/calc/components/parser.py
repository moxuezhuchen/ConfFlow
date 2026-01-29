#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""输出解析（兼容层）。

历史上这里实现了一份通用解析逻辑；当前运行期以 policy.parse_output 为准。
为了减少重复代码，这里改为按 prog_id 选择 policy 并委托解析。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from ..policies.gaussian import GaussianPolicy
from ..policies.orca import OrcaPolicy


def parse_output(
    log_file: str, config: Dict[str, Any], prog_id: int, is_sp_task: bool = False
) -> Dict[str, Any]:
    if not os.path.exists(log_file):
        return {}

    if int(prog_id) == 1:
        return GaussianPolicy().parse_output(log_file, config, is_sp_task=is_sp_task) or {}
    if int(prog_id) == 2:
        return OrcaPolicy().parse_output(log_file, config, is_sp_task=is_sp_task) or {}
    return {}
