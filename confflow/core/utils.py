#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ConfFlow 公共工具模块。

保留当前代码路径实际使用的：
- 基础异常（用于输入校验）
- 统一 logger（控制台/文件）
- XYZ/YAML 校验
- 共享解析函数：iprog/itask/memory、索引范围（freeze）
"""

import os
import sys
import re
import logging
from typing import Optional, List, Dict, Any, Tuple

# 模块可用性标志 (供其他模块检测)
UTILS_AVAILABLE = True


# ==============================================================================
# Numba Fallback 支持
# ==============================================================================


def get_numba_jit(logger_name: str = "confflow"):
    """获取 numba.njit 装饰器，如果不可用则返回空装饰器。"""
    try:
        import numba
        return numba
    except ImportError:
        log = logging.getLogger(logger_name)
        log.warning("Numba not found. Performance will be impacted. Consider: pip install numba")

        class FakeNumba:
            __name__ = "FakeNumba"

            def njit(self, *args, **kwargs):
                def decorator(func):
                    return func
                return decorator if not args else args[0]

            def jit(self, *args, **kwargs):
                def decorator(func):
                    return func
                return decorator if not args else args[0]

        return FakeNumba()


# ==============================================================================
# 自定义异常类
# ==============================================================================


class ConfFlowError(Exception):
    """ConfFlow 基础异常类"""

    pass


class InputFileError(ConfFlowError):
    """Input file related error."""

    def __init__(self, message: str, filepath: Optional[str] = None):
        self.filepath = filepath
        super().__init__(f"Input file error: {message}" + (f" (file: {filepath})" if filepath else ""))


class XYZFormatError(InputFileError):
    """XYZ file format error."""

    def __init__(
        self, message: str, filepath: Optional[str] = None, line_num: Optional[int] = None
    ):
        self.line_num = line_num
        line_info = f", line {line_num}" if line_num else ""
        super().__init__(f"XYZ format error: {message}{line_info}", filepath)


# ==============================================================================
# 统一日志系统
# ==============================================================================


class ConfFlowLogger:
    """ConfFlow 统一日志管理器

    支持两种运行模式:
    1. 独立运行: 完整的控制台 + 文件日志
    2. 嵌入运行 (被 GibbsFlow 调用): 只使用父进程的日志系统
    """

    _instance = None
    _initialized = False
    _embedded_mode = False  # 是否被嵌入调用（如被 GibbsFlow 调用）

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if ConfFlowLogger._initialized:
            return
        ConfFlowLogger._initialized = True

        self.logger = logging.getLogger("confflow")
        self.logger.setLevel(logging.DEBUG)
        self.handlers = {}

        # 检测是否被嵌入调用（检查父日志器是否已配置）
        root_logger = logging.getLogger()
        if root_logger.hasHandlers():
            # 父进程已配置日志，使用嵌入模式
            ConfFlowLogger._embedded_mode = True
            # 传播到父日志器
            self.logger.propagate = True
        else:
            # 独立运行，添加自己的处理器
            self.logger.propagate = False
            self._add_console_handler()

    @classmethod
    def set_embedded_mode(cls, enabled: bool = True):
        """设置嵌入模式（由外部调用者如 GibbsFlow 调用）"""
        cls._embedded_mode = enabled
        if cls._instance:
            cls._instance.logger.propagate = enabled
            # 如果启用嵌入模式，移除独立的控制台处理器
            if enabled and "console" in cls._instance.handlers:
                cls._instance.logger.removeHandler(cls._instance.handlers["console"])
                del cls._instance.handlers["console"]

    def _add_console_handler(self):
        """添加控制台日志处理器"""
        if "console" in self.handlers:
            return

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # 使用普通格式化器 (关闭颜色以保持一致性)
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)

        self.logger.addHandler(console_handler)
        self.handlers["console"] = console_handler

    def redirect_console_handler(self, stream=None) -> None:
        """将控制台 handler 的输出流重定向到指定 stream（默认当前 sys.stdout）。

        用途：CLI 在进入 redirect_stdout/redirect_stderr 后调用，确保日志不会继续写到
        import 阶段绑定的原始终端 stdout。
        """
        if stream is None:
            stream = sys.stdout

        handler = self.handlers.get("console")
        if handler is None:
            return

        if isinstance(handler, logging.StreamHandler):
            try:
                handler.setStream(stream)
            except Exception:
                # Fallback for older/custom handlers
                try:
                    handler.stream = stream  # type: ignore[attr-defined]
                except Exception:
                    pass

    def add_file_handler(self, log_file: str, level: int = logging.DEBUG):
        """添加文件日志处理器

        在嵌入模式下跳过添加文件处理器（使用父进程的日志文件）
        """
        # 嵌入模式下跳过
        if ConfFlowLogger._embedded_mode:
            return

        if "file" in self.handlers:
            self.logger.removeHandler(self.handlers["file"])

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)

        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.handlers["file"] = file_handler

    def set_level(self, level: int):
        """设置日志级别"""
        self.logger.setLevel(level)
        for handler in self.handlers.values():
            handler.setLevel(level)

    def close(self):
        """关闭所有处理器"""
        for handler in list(self.handlers.values()):
            handler.close()
            self.logger.removeHandler(handler)
        self.handlers.clear()

    # 便捷方法
    def debug(self, msg, *args, **kwargs):
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self.logger.exception(msg, *args, **kwargs)


def get_logger() -> ConfFlowLogger:
    """获取全局日志实例"""
    return ConfFlowLogger()


def redirect_logging_streams(stream=None, include_root: bool = False) -> None:
    """将已存在的 logging.StreamHandler 输出流统一重定向到指定 stream。

    主要用于 CLI 已将 sys.stdout/stderr 重定向到文件，但 logger handler 仍绑定到原始终端。
    """
    if stream is None:
        stream = sys.stdout

    targets = [logging.getLogger("confflow")]
    if include_root:
        targets.insert(0, logging.getLogger())

    for lg in targets:
        for handler in list(getattr(lg, "handlers", [])):
            if isinstance(handler, logging.StreamHandler):
                try:
                    handler.setStream(stream)
                except Exception:
                    try:
                        handler.stream = stream  # type: ignore[attr-defined]
                    except Exception:
                        pass


# ==============================================================================
# 输入验证函数
# ==============================================================================


def validate_xyz_file(filepath: str, strict: bool = False) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    验证 XYZ 文件格式并返回解析结果

    Args:
        filepath: XYZ 文件路径
        strict: 是否启用严格模式（更严格的格式检查）

    Returns:
        (is_valid, geometries): 验证结果和解析出的几何结构列表

    Raises:
        InputFileError: 文件不存在或无法读取
        XYZFormatError: 格式错误（严格模式下）
    """
    if not os.path.exists(filepath):
        raise InputFileError(f"文件不存在: {filepath}", filepath)

    if not os.path.isfile(filepath):
        raise InputFileError(f"路径不是文件: {filepath}", filepath)

    if os.path.getsize(filepath) == 0:
        raise InputFileError(f"文件为空: {filepath}", filepath)

    geometries = []
    errors = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except IOError as e:
        raise InputFileError(f"无法读取文件: {e}", filepath)

    i = 0
    frame_idx = 0

    while i < len(lines):
        line = lines[i].strip()

        # 跳过空行
        if not line:
            i += 1
            continue

        # 尝试解析原子数
        try:
            num_atoms = int(line)
        except ValueError:
            if strict:
                errors.append(f"行 {i+1}: 期望原子数量（整数），得到 '{line}'")
            i += 1
            continue

        # 验证原子数有效性
        if num_atoms <= 0:
            errors.append(f"行 {i+1}: 无效的原子数量 {num_atoms}")
            i += 1
            continue

        # 检查是否有足够的行
        if i + 2 + num_atoms > len(lines):
            errors.append(f"行 {i+1}: 声明了 {num_atoms} 个原子，但文件行数不足")
            break

        # 读取注释行
        comment = lines[i + 1].strip()

        # 解析坐标
        coords = []
        atoms = []
        coord_errors = []

        for j in range(num_atoms):
            coord_line = lines[i + 2 + j].strip()
            parts = coord_line.split()

            if len(parts) < 4:
                coord_errors.append(f"行 {i + 3 + j}: 坐标数据不完整 '{coord_line}'")
                continue

            atom_symbol = parts[0]

            # 验证原子符号
            if not re.match(r"^[A-Za-z]{1,2}$", atom_symbol):
                coord_errors.append(f"行 {i + 3 + j}: 无效的原子符号 '{atom_symbol}'")
                if strict:
                    continue

            # 解析坐标值
            try:
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                atoms.append(atom_symbol)
                coords.append((x, y, z))
            except ValueError as e:
                coord_errors.append(f"行 {i + 3 + j}: 无法解析坐标 '{coord_line}'")

        if coord_errors:
            errors.extend(coord_errors)

        # 如果成功解析了所有原子，添加到结果
        if len(coords) == num_atoms:
            geometries.append(
                {
                    "num_atoms": num_atoms,
                    "comment": comment,
                    "atoms": atoms,
                    "coords": coords,
                    "frame_index": frame_idx,
                }
            )
            frame_idx += 1
        elif strict:
            errors.append(f"帧 {frame_idx}: 只解析了 {len(coords)}/{num_atoms} 个原子")

        i += 2 + num_atoms

    # 严格模式下，如果有错误则抛出异常
    if strict and errors:
        raise XYZFormatError("\n".join(errors), filepath)

    is_valid = len(geometries) > 0 and len(errors) == 0

    return is_valid, geometries


def validate_yaml_config(
    config: Dict[str, Any], required_sections: Optional[List[str]] = None
) -> List[str]:
    """
    验证 YAML 配置文件结构

    Args:
        config: 解析后的配置字典
        required_sections: 必需的配置节列表

    Returns:
        错误消息列表（空列表表示验证通过）
    """
    errors = []

    if required_sections is None:
        required_sections = ["global", "steps"]

    # Check required sections
    for section in required_sections:
        if section not in config:
            errors.append(f"missing required section: '{section}'")

    # 验证 global 配置
    if "global" in config:
        global_config = config["global"]

        # 检查程序路径
        if "gaussian_path" in global_config:
            path = global_config["gaussian_path"]
            if path and not os.path.exists(path) and "/" in path:
                errors.append(f"Gaussian path not found: {path}")

        if "orca_path" in global_config:
            path = global_config["orca_path"]
            if path and not os.path.exists(path) and "/" in path:
                errors.append(f"ORCA path not found: {path}")

        # 检查资源配置
        cores = global_config.get("cores_per_task", 1)
        if not isinstance(cores, int) or cores <= 0:
            errors.append(f"invalid cores_per_task: {cores}")

        max_jobs = global_config.get("max_parallel_jobs", 1)
        if not isinstance(max_jobs, int) or max_jobs <= 0:
            errors.append(f"invalid max_parallel_jobs: {max_jobs}")

    # 验证 steps 配置
    if "steps" in config:
        steps = config["steps"]

        if not isinstance(steps, list):
            errors.append("'steps' must be a list")
        else:
            for i, step in enumerate(steps):
                step_errors = _validate_step_config(step, i)
                errors.extend(step_errors)

    return errors


def _validate_step_config(step: Dict[str, Any], index: int) -> List[str]:
    """验证单个步骤的配置"""
    errors = []
    step_id = f"step {index + 1}"

    def _pair_list_ok(val: Any) -> bool:
        """验证键对列表的形状：支持 [[a,b], ...] / [a,b] / ['a b', ...] / 'a b' 等。"""
        if val is None:
            return True
        if isinstance(val, str):
            nums = re.findall(r"\d+", val)
            return len(nums) >= 2
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                return True
            # 单对 [a,b]
            if len(val) == 2 and all(isinstance(x, int) for x in val):
                return True
            # 多对 [[a,b], ...]
            if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in val):
                return True
            # 字符串列表 ['a b', 'c,d']
            if all(isinstance(x, str) for x in val):
                return all(len(re.findall(r"\d+", x)) >= 2 for x in val)
        return False

    # 检查必需字段
    if "name" not in step:
        errors.append(f"{step_id}: missing 'name' field")
    else:
        step_id = f"step '{step['name']}'"

    if "type" not in step:
        errors.append(f"{step_id}: missing 'type' field")
    else:
        step_type = step["type"]
        # 支持新旧命名 (向后兼容)
        valid_types = ["confgen", "calc", "gen", "task"]
        if step_type not in valid_types:
            errors.append(
                f"{step_id}: invalid type '{step_type}', must be 'confgen', 'calc', 'gen' or 'task'"
            )

    # 验证 params
    if "params" in step:
        params = step["params"]
        step_type = step.get("type", "")

        if step_type in ["calc", "task"]:
            # 验证 itask
            itask = params.get("itask")
            valid_itasks = ["opt", "sp", "freq", "opt_freq", "ts", 0, 1, 2, 3, 4]
            if itask is not None and itask not in valid_itasks:
                errors.append(f"{step_id}: invalid itask value '{itask}'")

            # 验证 iprog
            iprog = params.get("iprog")
            valid_iprogs = ["gaussian", "g16", "orca", 1, 2]
            if iprog is not None and iprog not in valid_iprogs:
                errors.append(f"{step_id}: invalid iprog value '{iprog}'")

            # 检查 keyword（对于 task 类型，通常需要）
            if "keyword" not in params and iprog in ["orca", 2]:
                errors.append(f"{step_id}: ORCA task missing 'keyword' parameter")

        elif step_type in ["confgen", "gen"]:
            # 链模式：必须提供 chain/chains（自动柔性键判断已移除）
            chains = params.get("chains", None)
            if chains is None:
                chains = params.get("chain", None)
            if not chains:
                errors.append(
                    f"{step_id}: confgen step requires 'chains' (or 'chain'), e.g. chains: ['81-79-78-86-92']"
                )

            # 可选：手动修改键/旋转约束（允许但需要基本格式正确）
            for key in ("add_bond", "del_bond", "no_rotate", "force_rotate"):
                if key in params and not _pair_list_ok(params.get(key)):
                    errors.append(
                        f"{step_id}: confgen parameter '{key}' format error; expected [[a,b], ...] / [a,b] / ['a b', ...] / 'a b' (1-based indices)"
                    )

            # 验证 angle_step
            angle_step = params.get("angle_step")
            if angle_step is not None:
                if not isinstance(angle_step, (int, float)) or angle_step <= 0:
                    errors.append(f"{step_id}: invalid angle_step value '{angle_step}'")

    return errors


def format_duration_hms(seconds: float) -> str:
    """格式化耗时为 H:MM:SS 或 M:SS（适合控制台摘要）。"""
    try:
        s = int(round(float(seconds)))
    except Exception:
        return str(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:d}:{ss:02d}"


def parse_index_spec(value: Any) -> List[int]:
    """解析 1-based 索引集合（支持列表/字符串/范围）。

    用途：freeze / 原子索引类配置。

    支持：
    - 0/None/"0" 表示空
    - "1,2,5-7" / "1 2 5-7" / [1, 2, "5-7"]
    """
    if value is None:
        return []
    if isinstance(value, (int, float)) and int(value) == 0:
        return []
    if isinstance(value, str) and value.strip().lower() in {"", "0", "none", "false"}:
        return []

    tokens: List[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            tokens.extend(str(item).replace(",", " ").split())
    else:
        tokens = str(value).replace(",", " ").split()

    out: List[int] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= 0 or b <= 0:
                continue
            lo, hi = (a, b) if a <= b else (b, a)
            out.extend(list(range(lo, hi + 1)))
            continue
        if tok.isdigit():
            v = int(tok)
            if v > 0:
                out.append(v)
            continue
        for m2 in re.findall(r"\d+", tok):
            v = int(m2)
            if v > 0:
                out.append(v)

    return sorted(set(out))


def format_index_ranges(indices: List[int]) -> str:
    """将索引列表压缩成范围字符串，如 [1,2,3,8,10,11] -> '1-3,8,10-11'。"""
    if not indices:
        return "none"
    sorted_idx = sorted(indices)
    parts: List[str] = []
    start = prev = sorted_idx[0]
    for v in sorted_idx[1:]:
        if v == prev + 1:
            prev = v
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = v
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(parts)


# ==============================================================================
# 共享解析函数（消除重复代码）
# ==============================================================================

# 程序映射常量
PROG_NAME_MAP = {"gaussian": 1, "g16": 1, "orca": 2}
ITASK_NAME_MAP = {
    "opt": 0,  # 结构优化
    "sp": 1,  # 单点能
    "freq": 2,  # 频率
    "opt_freq": 3,  # 优化 + 频率
    "ts": 4,  # 过渡态优化 + 频率
}


def parse_iprog(config_or_value: Any, default: int = 2) -> int:
    """
    统一解析 iprog 参数，支持整数和字符串格式

    Args:
        config_or_value: 可以是配置字典或直接的值
        default: 默认值（2 = ORCA）

    Returns:
        程序 ID（1=Gaussian, 2=ORCA）

    示例:
        >>> parse_iprog({'iprog': 'orca'})
        2
        >>> parse_iprog({'iprog': 1})
        1
        >>> parse_iprog('gaussian')
        1
    """
    # 如果是字典，提取 iprog 值
    if isinstance(config_or_value, dict):
        iprog_val = config_or_value.get("iprog", default)
    else:
        iprog_val = config_or_value

    # 如果是字符串，映射到整数
    if isinstance(iprog_val, str):
        return PROG_NAME_MAP.get(iprog_val.lower(), default)

    # 尝试转换为整数
    try:
        return int(iprog_val)
    except (ValueError, TypeError):
        return default


def parse_itask(config_or_value: Any, default: int = 3) -> int:
    """
    统一解析 itask 参数，支持整数和字符串格式

    Args:
        config_or_value: 可以是配置字典或直接的值
        default: 默认值（3 = opt_freq）

    Returns:
        任务类型 ID（0=opt, 1=sp, 2=freq, 3=opt_freq, 4=ts）

    示例:
        >>> parse_itask({'itask': 'opt'})
        0
        >>> parse_itask('sp')
        1
    """
    # 如果是字典，提取 itask 值
    if isinstance(config_or_value, dict):
        val = config_or_value.get("itask", default)
    else:
        val = config_or_value

    # 如果是整数，直接返回
    if isinstance(val, int):
        return val

    # 如果是数字字符串
    if str(val).isdigit():
        return int(val)

    # 字符串映射
    return ITASK_NAME_MAP.get(str(val).lower(), default)


def parse_memory(mem_str: Any, unit: str = "MB") -> int:
    """
    解析内存字符串为指定单位的整数值（使用二进制转换 1GB = 1024MB）

    Args:
        mem_str: 内存字符串，如 '120GB', '4000MB', '4000'
        unit: 目标单位 ('MB' 或 'GB')

    Returns:
        转换后的整数值

    示例:
        >>> parse_memory('4GB')
        4096  # MB
        >>> parse_memory('4GB', 'GB')
        4
    """
    mem_str = str(mem_str).strip().upper()

    # 提取数值和单位
    if "GB" in mem_str:
        value = float(mem_str.replace("GB", ""))
        value_mb = int(value * 1024)  # 二进制: 1 GB = 1024 MB
    elif "MB" in mem_str:
        value_mb = int(float(mem_str.replace("MB", "")))
    else:
        # 假设没有单位时为 MB
        try:
            value_mb = int(float(mem_str))
        except ValueError:
            value_mb = 4096  # 默认 4GB

    if unit.upper() == "GB":
        return value_mb // 1024
    return value_mb
