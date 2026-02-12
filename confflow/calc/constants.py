#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常量定义模块

包含程序路径、任务类型映射、周期表元素符号等常量。
"""

from typing import Dict, Any

# =============================================================================
# 物理常量
# =============================================================================

HARTREE_TO_KCALMOL = 627.5094740631  # Hartree to kcal/mol

# =============================================================================
# 任务类型映射
# =============================================================================

ITASK_MAP: Dict[str, int] = {
    "opt": 0,  # 结构优化
    "sp": 1,  # 单点能
    "freq": 2,  # 频率计算
    "opt_freq": 3,  # 优化+频率
    "ts": 4,  # 过渡态搜索
}

# =============================================================================
# 周期表
# =============================================================================

PERIODIC_SYMBOLS = (
    "",
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
    "Po",
    "At",
    "Rn",
    "Fr",
    "Ra",
    "Ac",
    "Th",
)

# =============================================================================
# 输入文件模板
# =============================================================================

GAUSSIAN_TEMPLATE = """{link0}%nproc={cores}
%mem={memory}
#p {keyword_line}

{job_name}

{charge} {multiplicity}
{coordinates}

{extra_section}


"""

ORCA_TEMPLATE = """! {keyword}
%pal nprocs {cores} end
%maxcore {memory}
{generated_blocks}* xyz {charge} {multiplicity}
{coordinates}
*
"""

BUILTIN_TEMPLATES: Dict[str, str] = {"gaussian": GAUSSIAN_TEMPLATE, "orca": ORCA_TEMPLATE}


# =============================================================================
# 辅助函数
# =============================================================================


def get_element_symbol(atomic_number: int) -> str:
    """根据原子序数获取元素符号。

    Args:
        atomic_number: 原子序数 (1-based)

    Returns:
        元素符号，未知则返回 'X'
    """
    if 0 <= atomic_number < len(PERIODIC_SYMBOLS):
        return PERIODIC_SYMBOLS[atomic_number]
    return "X"
