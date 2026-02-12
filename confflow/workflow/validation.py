#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Workflow 输入校验模块"""

from typing import Any, Dict, List, Optional
from ..core.utils import validate_xyz_file
from ..blocks.confgen.generator import load_mol_from_xyz
from ..blocks.confgen.validator import ChainValidator
from .helpers import as_list


def validate_inputs_compatible(
    input_files: List[str],
    confgen_params: Optional[Dict[str, Any]] = None,
    force_consistency: bool = False,
) -> None:
    """确保多输入可被 confgen 合并：单帧、原子数与元素序列一致。

    Args:
        input_files: 输入文件列表
        confgen_params: (Optional) confgen 步骤参数，用于柔性链对齐检查
        force_consistency: 即使不一致也不抛出异常（用于 --yes / --force 模式绕过检查）
    """
    if not input_files:
        raise ValueError("no input files provided")

    allow_chain_mapping = bool(confgen_params and confgen_params.get("chains"))

    ref_atoms = None
    ref_natoms = None
    for fp in input_files:
        ok, geoms = validate_xyz_file(fp)
        if not ok or not geoms:
            raise ValueError(f"cannot parse input XYZ: {fp}")
        if len(geoms) != 1:
            raise ValueError(
                f"multi-input mode requires single-frame XYZ per input (current {fp} has {len(geoms)} frames)."
            )
        atoms = list(geoms[0].get("atoms") or [])
        natoms = len(atoms)
        if ref_atoms is None:
            ref_atoms = atoms
            ref_natoms = natoms
            continue
        if natoms != ref_natoms:
            raise ValueError(
                f"atom count mismatch: {fp} ({natoms}) vs reference ({ref_natoms})"
            )

        if allow_chain_mapping:
            # 允许原子顺序不同，但要求元素计数一致
            if sorted(atoms) != sorted(ref_atoms):
                raise ValueError(
                    "element composition mismatch (chains mode requires equal element counts):\n"
                    f"File: {fp}"
                )
        else:
            # 默认严格要求原子顺序一致
            if atoms != ref_atoms:
                diffs = []
                for i, (a1, a2) in enumerate(zip(atoms, ref_atoms)):
                    if a1 != a2:
                        diffs.append(f"#{i+1} {a1} vs {a2}")
                        if len(diffs) >= 3:
                            break
                raise ValueError(
                    "all inputs must have the same atom count and element order.\n"
                    "element order mismatch (multi-input mode requires full match):\n"
                    f"File: {fp}\nDifference: {', '.join(diffs)}..."
                )

    # -------------------------------------------------------------------------
    # 柔性链一致性检查（如果有 confgen 参数）
    # -------------------------------------------------------------------------
    if confgen_params and "chains" in confgen_params:
        chains = as_list(confgen_params.get("chains"))
        if chains:
            try:
                if not bool(confgen_params.get("validate_chain_bonds", False)):
                    return
                validator = ChainValidator(chains)
                bond_threshold = float(confgen_params.get("bond_threshold", 1.15))

                # 仅检查第一个输入文件中的链合法性与成键性
                ref_fp = input_files[0]
                mol = load_mol_from_xyz(ref_fp, bond_threshold)
                ref_data = validator.validate_mol(mol, ref_fp)
                invalid = [d for d in ref_data if not d.get("valid")]
                if invalid:
                    messages = [f"{d.get('raw_chain')}: {d.get('error')}" for d in invalid]
                    raise ValueError(
                        "柔性链在参考输入文件中无效：\n" + "\n".join(messages)
                    )
            except Exception as e:
                if isinstance(e, ValueError):
                    raise
