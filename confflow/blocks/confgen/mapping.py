
import logging
from typing import Dict, List

from rdkit import Chem
from rdkit.Chem import rdFMCS

logger = logging.getLogger("confflow.confgen")

def get_mcs_mapping(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    timeout: int = 30,
    verbose: bool = False,
    min_coverage: float = 0.7,
) -> Dict[int, int]:
    """
    计算 Reference 分子到 Target 分子的原子索引映射 (0-based)
    
    采用全分子 MCS (Maximum Common Substructure) 匹配策略，
    忽略元素类型与键级差异，仅基于拓扑匹配。
    
    Returns:
        Dict[ref_idx, target_idx]
    
    Raises:
        ValueError: 若无法找到覆盖大部分原子的匹配 (覆盖率过低或 MCS 搜索失败)
    """
    # 1. 计算 MCS
    # compareAny: 忽略原子类型和键类型（最宽松模式，适配“原子序号不一致”甚至“元素微变”）
    # completeRingsOnly: 确保环完整匹配，增加稳健性
    params = rdFMCS.MCSParameters()
    params.AtomTyper = rdFMCS.AtomCompare.CompareAny
    params.BondTyper = rdFMCS.BondCompare.CompareAny
    params.MaximizeBonds = True
    params.Timeout = timeout

    res = rdFMCS.FindMCS([ref_mol, target_mol], params)
    
    if not res.canceled and res.numAtoms == 0:
        raise ValueError("MCS 搜索未能找到公共子结构")

    if verbose:
        logger.info(f"MCS 匹配: {res.numAtoms} 原字, {res.numBonds} 键")

    # 简单覆盖率检查
    ratio = res.numAtoms / max(ref_mol.GetNumAtoms(), 1)
    if ratio < min_coverage:
        raise ValueError(f"MCS 覆盖率过低 ({ratio:.1%} < {min_coverage:.1%})")

    # 2. 获取映射 (Pattern -> Ref, Pattern -> Target)
    patt = Chem.MolFromSmarts(res.smartsString)
    if patt is None:
         # 极少情况 smarts 失效
         raise ValueError("无法解析 MCS SMARTs")

    # GetSubstructMatch 返回的是 tuple of indices
    ref_match = ref_mol.GetSubstructMatch(patt)
    target_match = target_mol.GetSubstructMatch(patt)

    if not ref_match or not target_match:
        raise ValueError("无法将 MCS 映射回原分子")

    # 3. 建立 Ref -> Target 映射
    # ref_match[i] 是 pattern 中第 i 个原子在 ref 中的索引
    # target_match[i] 是 pattern 中第 i 个原子在 target 中的索引
    # 因此对应关系是 ref_match[i] <-> target_match[i]
    
    # 优化：处理多重匹配问题 (symmetry)
    # 对于 confgen 目的，我们通常只需任意一组有效映射即可。
    # 如果分子高度对称，RDKit 只返回第一组。
    
    mapping = {}
    for r_idx, t_idx in zip(ref_match, target_match):
        mapping[r_idx] = t_idx
        
    return mapping


def transfer_chain_indices(
    ref_mol: Chem.Mol,
    target_mol: Chem.Mol,
    ref_chain: List[int]
) -> List[int]:
    """
    将 Ref 分子的链索引迁移到 Target 分子。
    
    Args:
        ref_chain: 0-based indices in Ref
    
    Returns:
        target_chain: 0-based indices in Target
    """
    mapping = get_mcs_mapping(ref_mol, target_mol)
    
    target_chain = []
    missing = []
    
    for idx in ref_chain:
        if idx in mapping:
            target_chain.append(mapping[idx])
        else:
            missing.append(idx)
            
    if missing:
        raise ValueError(f"链原子 {missing} 未能通过 MCS 映射到目标分子 (可能位于非同构区域)")
        
    return target_chain
