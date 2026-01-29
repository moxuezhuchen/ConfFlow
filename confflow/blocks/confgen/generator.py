# -*- coding: utf-8 -*-

"""
ConfGen - 构象生成器 (v1.0)
功能: 基于 RDKit 的系统性构象搜索
架构: 双重模式 (可作为库导入，也可作为脚本运行)
"""

import sys
import os
import logging
import itertools
import multiprocessing
import re
import numpy as np
from typing import Any, List, Optional, Tuple

# --- Library Imports ---
try:
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem
    from rdkit.Chem import rdMolTransforms
    # 禁用 RDKit 警告（如价键异常），避免在 TS/金属体系中产生大量干扰输出
    RDLogger.DisableLog("rdApp.*")
except ImportError as e:
    raise ImportError("RDKit not found. Please install it (e.g. conda install rdkit).") from e


from ...core.console import create_progress


logger = logging.getLogger("confflow.confgen")

try:
    import numba
except ImportError:
    logger.warning("Numba not found. Execution will be slow. Consider: pip install numba")

    class FakeNumba:
        def njit(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    numba = FakeNumba()

# --- 导入共价半径数据（统一从 core.data 导入）---
try:
    from ...core.data import GV_COVALENT_RADII
except ImportError:
    try:
        from confflow.core.data import GV_COVALENT_RADII
    except ImportError:
        # 最后回退：直接从 core 导入
        from ...core import GV_COVALENT_RADII

# 构建 numpy 数组用于高性能计算
GV_RADII_ARRAY = np.zeros(120, dtype=np.float64)
for i, r in enumerate(GV_COVALENT_RADII):
    GV_RADII_ARRAY[i] = r
for i in range(112, 120):
    GV_RADII_ARRAY[i] = 1.50

# 链映射（用于多输入时根据第一个文件迁移链定义）
from .mapping import transfer_chain_indices


# ------------------------------------------------------------------------------
# 核心逻辑：几何碰撞检测
# ------------------------------------------------------------------------------


@numba.njit(cache=True)
def check_clash_core(atom_numbers, coords, clash_threshold, topo_dist_matrix, radii_array):
    """
    检查严重碰撞。
    Logic: 如果 distance < (R1 + R2) * 0.65，则认为发生核重叠。
    """
    num_atoms = len(atom_numbers)
    radii = np.empty(num_atoms, dtype=np.float64)
    for i in range(num_atoms):
        radii[i] = radii_array[atom_numbers[i]]

    # 拓扑距离过滤：忽略 1-4 及以内 (Non-bonded interaction 标准)
    ignore_hops = 3

    for i in range(num_atoms):
        for j in range(i + 1, num_atoms):
            # 1. 拓扑过滤
            if topo_dist_matrix[i, j] <= ignore_hops:
                continue

            # 2. 距离计算
            dist_sq = (
                (coords[i, 0] - coords[j, 0]) ** 2
                + (coords[i, 1] - coords[j, 1]) ** 2
                + (coords[i, 2] - coords[j, 2]) ** 2
            )

            # 3. 软球碰撞判据
            sum_radii = radii[i] + radii[j]
            limit = sum_radii * clash_threshold

            if dist_sq < (limit * limit):
                return True  # 发生碰撞，丢弃

    return False  # 合格


# ------------------------------------------------------------------------------
# 多进程 worker
# ------------------------------------------------------------------------------

w_mol: Any = None
w_conf: Any = None
w_bonds: Any = None
w_clash: Any = None
w_topo: Any = None
w_atoms: Any = None
w_opt: Any = None


def init_worker(mol, conf, bonds, clash, topo, atoms, opt):
    global w_mol, w_conf, w_bonds, w_clash, w_topo, w_atoms, w_opt
    w_mol, w_conf, w_bonds = mol, conf, bonds
    w_clash, w_topo, w_atoms, w_opt = clash, topo, atoms, opt


def _rotate_atoms_around_bond(
    coords: np.ndarray, i: int, j: int, atom_indices: np.ndarray, angle_deg: float
) -> None:
    """绕 i-j 轴旋转指定原子集合（不改变 i/j）。

    采用 Rodrigues 旋转公式，对 coords 就地修改。
    coords: (N, 3)
    atom_indices: 需要旋转的原子索引（0-based）
    """
    if atom_indices.size == 0:
        return
    p1 = coords[i]
    p2 = coords[j]
    axis = p2 - p1
    norm = float(np.linalg.norm(axis))
    if norm < 1e-12:
        return
    u = axis / norm

    theta = float(angle_deg) * np.pi / 180.0
    c = float(np.cos(theta))
    s = float(np.sin(theta))

    v = coords[atom_indices] - p1
    ux, uy, uz = u
    # u x v
    cross = np.column_stack(
        [
            uy * v[:, 2] - uz * v[:, 1],
            uz * v[:, 0] - ux * v[:, 2],
            ux * v[:, 1] - uy * v[:, 0],
        ]
    )
    dot = (v[:, 0] * ux + v[:, 1] * uy + v[:, 2] * uz).reshape(-1, 1)
    v_rot = v * c + cross * s + (u.reshape(1, 3) * dot) * (1.0 - c)
    coords[atom_indices] = p1 + v_rot


def process_task(angle_combo):
    # 1. 生成构象（坐标数组）
    temp_conf = Chem.Conformer(w_conf)
    coords = np.array(temp_conf.GetPositions(), dtype=np.float64)

    # w_bonds 支持两种模式：
    # - 自动模式: (n1, a1, a2, n2) -> SetDihedralDeg
    # - 手动链模式: (a1, a2, atoms_to_rotate_array) -> 几何旋转坐标
    for bond_spec, angle in zip(w_bonds, angle_combo):
        try:
            if len(bond_spec) == 4:
                n1, a1, a2, n2 = bond_spec
                rdMolTransforms.SetDihedralDeg(
                    temp_conf, int(n1), int(a1), int(a2), int(n2), float(angle)
                )
                coords = np.array(temp_conf.GetPositions(), dtype=np.float64)
            else:
                a1, a2, atom_indices = bond_spec
                _rotate_atoms_around_bond(coords, int(a1), int(a2), atom_indices, float(angle))
        except Exception:
            return None

    # 写回 conformer
    for idx in range(coords.shape[0]):
        temp_conf.SetAtomPosition(idx, coords[idx])

    # 2. 预优化 (可选)
    if w_opt:
        try:
            m_opt = Chem.Mol(w_mol)
            m_opt.RemoveAllConformers()
            m_opt.AddConformer(temp_conf)
            AllChem.MMFFOptimizeMolecule(m_opt, maxIters=200, mmffVariant="MMFF94s")  # type: ignore[attr-defined]
            temp_conf = m_opt.GetConformer(0)
        except Exception as e:
            logger = logging.getLogger("confflow.confgen")
            logger.debug(f"MMFF优化失败: {e}")

    new_coords = temp_conf.GetPositions()

    # 3. 碰撞筛选
    is_clash = check_clash_core(w_atoms, new_coords, w_clash, w_topo, GV_RADII_ARRAY)

    if is_clash:
        return None
    return new_coords


# ------------------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------------------


def load_mol_from_xyz(filename, bond_coeff):
    """从 XYZ 文件加载分子结构"""
    # 验证文件存在性
    if not os.path.exists(filename):
        raise FileNotFoundError(f"输入文件不存在: {filename}")
    if not os.path.isfile(filename):
        raise ValueError(f"路径不是文件: {filename}")
    if os.path.getsize(filename) == 0:
        raise ValueError(f"文件为空: {filename}")

    # Read XYZ
    symbols, positions = [], []
    with open(filename, "r") as f:
        lines = f.readlines()

    if len(lines) < 3:
        raise ValueError(f"XYZ 文件格式错误，行数不足: {filename}")

    try:
        num_atoms = int(lines[0].strip())
    except ValueError:
        raise ValueError(f"无法解析原子数量: {lines[0].strip()}")

    if len(lines) < num_atoms + 2:
        raise ValueError(f"文件声明 {num_atoms} 个原子，但行数不足")

    for line in lines[2 : 2 + num_atoms]:
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"坐标行格式错误: {line.strip()}")
        symbols.append(parts[0])
        positions.append((float(parts[1]), float(parts[2]), float(parts[3])))

    # Build RDKit Mol
    rw_mol = Chem.RWMol()
    for s in symbols:
        rw_mol.AddAtom(Chem.Atom(s))
    atom_nums = [atom.GetAtomicNum() for atom in rw_mol.GetAtoms()]

    conf = Chem.Conformer(num_atoms)
    for i in range(num_atoms):
        conf.SetAtomPosition(i, positions[i])
    rw_mol.AddConformer(conf)

    # 拓扑识别逻辑
    radii = np.array([GV_RADII_ARRAY[z] if z < 120 else 1.5 for z in atom_nums])
    pos_array = np.array(positions)

    d = pos_array[:, np.newaxis, :] - pos_array[np.newaxis, :, :]
    dist_matrix = np.sqrt(np.sum(d**2, axis=-1))
    threshold_matrix = (radii[:, np.newaxis] + radii[np.newaxis, :]) * bond_coeff

    mask = (dist_matrix < threshold_matrix) & (dist_matrix > 0.4)
    i_indices, j_indices = np.where(np.triu(mask, k=1))

    for i, j in zip(i_indices, j_indices):
        rw_mol.AddBond(int(i), int(j), Chem.BondType.SINGLE)

    mol = rw_mol.GetMol()
    try:
        Chem.SanitizeMol(mol)
    except:
        mol.UpdatePropertyCache(strict=False)

    from ...core.console import console
    from rich.columns import Columns
    from rich.panel import Panel
    from rich import box

    console.print(f"[info]INFO:[/info] 成功载入 {filename}, 识别到 {mol.GetNumBonds()} 个键。")

    # --- 打印连接表 ---
    bonds_list = []
    for b in mol.GetBonds():
        a1 = b.GetBeginAtom()
        a2 = b.GetEndAtom()
        b_str = f"{a1.GetIdx()+1:>3}({a1.GetSymbol()}) - {a2.GetIdx()+1:<3}({a2.GetSymbol()})"
        bonds_list.append(b_str)

    # 使用 Rich Panel 和 Columns 展示，美观且自动换行
    console.print(Panel(Columns(bonds_list, equal=True, expand=True), title="当前拓扑连接表 (Index: 1-based)", border_style="dim", expand=False, box=box.ASCII))

    return mol


def get_rotatable_bonds(mol, no_rot, force_rot):
    # 兼容旧接口：参数保留但功能已移除。
    del mol, no_rot, force_rot
    raise RuntimeError("自动柔性键判断已移除：请使用 --chain/--steps/--angles 手动指定旋转链与角度")


def _parse_chain(chain_str: str) -> List[int]:
    """解析链，例如 '81-69-78-86-92' -> [80, 68, 77, 85, 91] (0-based)。"""
    parts = [p.strip() for p in chain_str.replace(",", "-").split("-") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"链格式错误: {chain_str}")
    try:
        atoms_1based = [int(x) for x in parts]
    except ValueError:
        raise ValueError(f"链必须是整数序列: {chain_str}")
    if any(x <= 0 for x in atoms_1based):
        raise ValueError(f"链原子编号必须为 1-based 正整数: {chain_str}")
    atoms = [x - 1 for x in atoms_1based]
    if len(set(atoms)) != len(atoms):
        raise ValueError(f"链中存在重复原子: {chain_str}")
    return atoms


def _parse_steps(steps_str: str, n_bonds: int) -> List[int]:
    parts = [p.strip() for p in steps_str.split(",") if p.strip()]
    if len(parts) != n_bonds:
        raise ValueError(
            f"steps 需要 {n_bonds} 个值(对应链上 {n_bonds} 根键)，实际 {len(parts)}: {steps_str}"
        )
    steps = [int(x) for x in parts]
    if any(s <= 0 or s > 360 for s in steps):
        raise ValueError(f"steps 必须在 1..360: {steps_str}")
    return steps


def _parse_angles(angles_str: str, n_bonds: int) -> List[List[float]]:
    """解析每根键的角度列表，例如 '0,120,240;0,60,120,180;180;0,120'。"""
    segs = [s.strip() for s in angles_str.split(";") if s.strip()]
    if len(segs) != n_bonds:
        raise ValueError(f"angles 需要 {n_bonds} 段(用 ';' 分隔)，实际 {len(segs)}: {angles_str}")
    out: List[List[float]] = []
    for seg in segs:
        vals = [v.strip() for v in seg.split(",") if v.strip()]
        if not vals:
            raise ValueError(f"angles 段不能为空: {angles_str}")
        out.append([float(v) for v in vals])
    return out


def _build_adjacency(mol):
    n_atoms = mol.GetNumAtoms()
    adjacency = [set() for _ in range(n_atoms)]
    for b in mol.GetBonds():
        i = b.GetBeginAtomIdx()
        j = b.GetEndAtomIdx()
        adjacency[i].add(j)
        adjacency[j].add(i)
    return adjacency


def _bfs_distances(adjacency, source: int) -> List[int]:
    """在无权图上计算 source 到所有点的最短路径距离（不可达为很大值）。"""
    n = len(adjacency)
    INF = 10**9
    dist = [INF] * n
    dist[source] = 0
    q = [source]
    head = 0
    while head < len(q):
        cur = q[head]
        head += 1
        nd = dist[cur] + 1
        for nxt in adjacency[cur]:
            if dist[nxt] != INF:
                continue
            dist[nxt] = nd
            q.append(nxt)
    return dist


def _bfs_distances_multi(adjacency, sources: List[int]) -> List[int]:
    """多源最短路：dist[x] = min_{s in sources} dist(s, x)。"""
    n = len(adjacency)
    INF = 10**9
    dist = [INF] * n
    q: List[int] = []
    for s in sources:
        if 0 <= s < n and dist[s] != 0:
            dist[s] = 0
            q.append(s)
    head = 0
    while head < len(q):
        cur = q[head]
        head += 1
        nd = dist[cur] + 1
        for nxt in adjacency[cur]:
            if dist[nxt] <= nd:
                continue
            dist[nxt] = nd
            q.append(nxt)
    return dist


def _component_nodes(adjacency, start: int, blocked: int) -> set:
    visited = set([start])
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adjacency[cur]:
            if (cur == start and nxt == blocked) or (cur == blocked and nxt == start):
                continue
            if nxt in visited:
                continue
            visited.add(nxt)
            stack.append(nxt)
    return visited


def _edge_in_cycle(adjacency, u: int, v: int) -> bool:
    """若移除 u-v 后，u 仍可到达 v，则 u-v 在某个闭环中（图论意义）。"""
    if u == v:
        return False
    visited = set([u])
    stack = [u]
    while stack:
        cur = stack.pop()
        for nxt in adjacency[cur]:
            if (cur == u and nxt == v) or (cur == v and nxt == u):
                continue
            if nxt == v:
                return True
            if nxt in visited:
                continue
            visited.add(nxt)
            stack.append(nxt)
    return False


def _validate_chain_bonds(mol, parsed_chains: List[List[int]], filename: str) -> None:
    """验证链上相邻原子是否成键。"""
    missing = []
    for ch in parsed_chains:
        for i in range(len(ch) - 1):
            a = int(ch[i])
            b = int(ch[i + 1])
            if mol.GetBondBetweenAtoms(a, b) is None:
                missing.append((a, b))
    if missing:
        pairs = ", ".join([f"{a+1}-{b+1}" for a, b in missing[:5]])
        extra = "" if len(missing) <= 5 else f" ... 共 {len(missing)} 处"
        raise ValueError(
            f"链上相邻原子未成键: {pairs}{extra} (文件: {filename})。"
            "请使用 --add_bond 或调整 bond_threshold。"
        )


def write_xyz(mol, conformers, filename):
    with open(filename, "w") as f:
        syms = [a.GetSymbol() for a in mol.GetAtoms()]
        natoms = len(syms)
        for i, coords in enumerate(conformers):
            # 为后续工作流溯源提供稳定 ID
            f.write(f"{natoms}\nConformer {i+1} | CID=cf_{i+1:06d}\n")
            for j, s in enumerate(syms):
                x, y, z = coords[j]
                f.write(f"{s:<4s} {x:12.6f} {y:12.6f} {z:12.6f}\n")


# ------------------------------------------------------------------------------
# 库接口（API）
# ------------------------------------------------------------------------------


def run_generation(
    input_files,
    angle_step=120,
    bond_threshold=1.15,
    clash_threshold=0.65,
    add_bond=None,
    del_bond=None,
    no_rotate=None,
    force_rotate=None,
    optimize=False,
    confirm=False,
    chains: Optional[List[str]] = None,
    chain_steps: Optional[List[str]] = None,
    chain_angles: Optional[List[str]] = None,
    rotate_side: str = "left",
):
    """
    执行构象生成的入口函数

    Args:
        input_files: XYZ 文件路径列表或单个字符串
        angle_step: 旋转角度步长 (int)
        bond_threshold: 成键判定系数 (float, default 1.15)
        clash_threshold: 碰撞判定系数 (float, default 0.65)
        add_bond: 强制添加键列表 [[idx1, idx2], ...] (1-based)
        del_bond: 强制删除键列表 [[idx1, idx2], ...] (1-based)
        no_rotate: 禁止旋转键列表 [[idx1, idx2], ...] (1-based)
        force_rotate: 强制旋转键列表 [[idx1, idx2], ...] (1-based)
        optimize: 是否预优化 (bool)
        confirm: 是否跳过用户确认 (bool)
    """
    from ...core.console import console
    from rich import box

    def _normalize_pair_list_local(value):
        if value is None:
            return None
        if isinstance(value, list):
            if len(value) == 0:
                return []
            if len(value) == 2 and all(isinstance(x, int) for x in value):
                return [value]
            if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in value):
                return [[int(a), int(b)] for a, b in value]
            if all(isinstance(x, str) for x in value):
                out = []
                for item in value:
                    parts = re.split(r"[\s,\-]+", item.strip())
                    parts = [p for p in parts if p]
                    if len(parts) != 2:
                        raise ValueError(f"键对格式错误: {item}，应为 'a b' 或 'a,b' 或 'a-b'")
                    out.append([int(parts[0]), int(parts[1])])
                return out
        if isinstance(value, str):
            parts = re.split(r"[\s,\-]+", value.strip())
            parts = [p for p in parts if p]
            if len(parts) != 2:
                raise ValueError(f"键对格式错误: {value}，应为 'a b' 或 'a,b' 或 'a-b'")
            return [[int(parts[0]), int(parts[1])]]
        raise ValueError(f"不支持的键对格式: {type(value)}")

    # 规范化键对输入（允许 '1-2' 等格式）
    add_bond = _normalize_pair_list_local(add_bond)
    del_bond = _normalize_pair_list_local(del_bond)
    no_rotate = _normalize_pair_list_local(no_rotate)
    force_rotate = _normalize_pair_list_local(force_rotate)

    # 确保输入是列表
    if isinstance(input_files, str):
        input_files = [input_files]

    master_mol = None
    ref_mol = None
    ref_parsed_chains = None
    all_confs_data = []

    for file_idx, xyz_file in enumerate(input_files):
        console.print()
        console.rule(f"处理文件: {os.path.basename(xyz_file)}", characters="-")
        
        try:
            mol = load_mol_from_xyz(xyz_file, bond_threshold)
            if master_mol is None:
                master_mol = Chem.Mol(mol)

            # 预解析链（仅解析参考链），映射在拓扑修正后进行
            parsed_chains = None
            if chains and ref_parsed_chains is None:
                ref_parsed_chains = [_parse_chain(c) for c in chains]

            # 允许手动修改键
            rw_mol = Chem.RWMol(mol)
            is_mod = False

            if del_bond:
                for p in del_bond:
                    if len(p) == 2 and rw_mol.GetBondBetweenAtoms(p[0] - 1, p[1] - 1):
                        rw_mol.RemoveBond(p[0] - 1, p[1] - 1)
                        is_mod = True

            if add_bond:
                for p in add_bond:
                    if len(p) == 2 and not rw_mol.GetBondBetweenAtoms(p[0] - 1, p[1] - 1):
                        rw_mol.AddBond(p[0] - 1, p[1] - 1, Chem.BondType.SINGLE)
                        is_mod = True

            if is_mod:
                mol = rw_mol.GetMol()
                try:
                    Chem.SanitizeMol(mol)
                except:
                    mol.UpdatePropertyCache(strict=False)
                print(f"INFO: 手动修正后，现有 {mol.GetNumBonds()} 个键。")

            # 记录参考分子（使用修改后的拓扑）
            if file_idx == 0 and ref_mol is None:
                ref_mol = Chem.Mol(mol)

            # 链映射（第一个输入为参考，其它输入通过拓扑映射）
            if chains:
                if file_idx == 0:
                    parsed_chains = ref_parsed_chains
                else:
                    if ref_mol is None or ref_parsed_chains is None:
                        raise ValueError("无法建立参考链定义，请检查输入顺序")
                    parsed_chains = [
                        transfer_chain_indices(ref_mol, mol, ch) for ch in ref_parsed_chains
                    ]

            # 链模式：确认链上相邻原子成键
            if parsed_chains:
                _validate_chain_bonds(mol, parsed_chains, xyz_file)

            # --- 强制刷新 RDKit 环感知 ---
            # 说明：XYZ 先猜键，再通过 --add_bond/--del_bond 修改拓扑。
            # 如果不显式触发 ring perception，bond.IsInRing() 可能仍基于旧 ring info，
            # 从而把新形成的（例如 4 元）环上的键误判为可旋转键。
            try:
                mol.UpdatePropertyCache(strict=False)
            except Exception:
                pass
            try:
                Chem.GetSymmSSSR(mol)
            except Exception:
                pass

            # --------------------------
            # 旋转键确定：仅手动链模式
            # --------------------------
            rot_bonds = []
            angle_lists: List[List[float]] = []

            if not chains:
                raise ValueError("请使用 --chain 指定需要旋转的链（已移除自动柔性键判断）")

            # 若上方已预解析，则复用
            if parsed_chains is None:
                parsed_chains = [_parse_chain(c) for c in chains]
            adjacency = _build_adjacency(mol)
            n_atoms = mol.GetNumAtoms()

            # 解析每条链的角度配置
            per_chain_angle_lists: List[List[List[float]]] = []
            if chain_angles:
                if len(chain_angles) not in (1, len(parsed_chains)):
                    raise ValueError("--angles 数量需为 1（应用于所有链）或与 --chain 数量一致")
                angles_specs = (
                    chain_angles
                    if len(chain_angles) == len(parsed_chains)
                    else [chain_angles[0]] * len(parsed_chains)
                )
                for ch, ang in zip(parsed_chains, angles_specs):
                    per_chain_angle_lists.append(_parse_angles(ang, len(ch) - 1))
            else:
                # steps：每根键一个 step；如果没给，就用全局 angle_step（默认 120）
                if chain_steps:
                    if len(chain_steps) not in (1, len(parsed_chains)):
                        raise ValueError("--steps 数量需为 1（应用于所有链）或与 --chain 数量一致")
                    step_specs = (
                        chain_steps
                        if len(chain_steps) == len(parsed_chains)
                        else [chain_steps[0]] * len(parsed_chains)
                    )
                    for ch, st in zip(parsed_chains, step_specs):
                        steps = _parse_steps(st, len(ch) - 1)
                        per_chain_angle_lists.append([list(range(0, 360, int(s))) for s in steps])
                else:
                    # 默认所有键使用 angle_step
                    per_chain_angle_lists = [
                        [list(range(0, 360, int(angle_step))) for _ in range(len(ch) - 1)]
                        for ch in parsed_chains
                    ]

            # 构建旋转键（按链顺序），并展开 angle_lists 与之对应。
            # 规则：旋转链“左侧”片段（默认即链方向上靠前那端）。
            # 为了在闭环/金属配位闭环中也能稳定划分左右，这里使用图最短路距离进行左右划分：
            # - rotate_side=left: 旋转 dist_to_left < dist_to_right 的原子
            # - rotate_side=right: 旋转 dist_to_right < dist_to_left 的原子
            if rotate_side not in ("left", "right"):
                raise ValueError("rotate_side 只能为 left 或 right")
            for ch, bond_angles in zip(parsed_chains, per_chain_angle_lists):
                for bi in range(len(ch) - 1):
                    a_left = ch[bi]
                    a_right = ch[bi + 1]

                    # 键必须存在（链模式已自动补键；这里做最终校验）
                    if mol.GetBondBetweenAtoms(a_left, a_right) is None:
                        raise ValueError(
                            f"链上相邻原子之间不存在键: {a_left+1}-{a_right+1}（可用 --add_bond 或检查链编号）"
                        )

                    # no_rotate 优先：从链中剔除
                    if no_rotate:
                        pair = tuple(sorted((a_left, a_right)))
                        if any(tuple(sorted((p[0] - 1, p[1] - 1))) == pair for p in no_rotate):
                            continue

                    # 严格按“输入链”定义左右：
                    # - rotate_side=left：旋转链前缀(包含 a_left)一侧
                    # - rotate_side=right：旋转链后缀(包含 a_right)一侧
                    if rotate_side == "left":
                        left_sources = ch[: bi + 1]
                        right_sources = ch[bi + 1 :]
                    else:
                        left_sources = ch[bi + 1 :]
                        right_sources = ch[: bi + 1]

                    dist_left = _bfs_distances_multi(adjacency, left_sources)
                    dist_right = _bfs_distances_multi(adjacency, right_sources)

                    right_source_set = set(right_sources)
                    # 关键：
                    # 1) 平局(dist_left==dist_right)也归到左侧，避免“方向乱”
                    # 2) 明确排除链后缀原子，保证链本身始终按输入方向分割
                    rotate_atoms = [
                        idx
                        for idx in range(n_atoms)
                        if idx not in (a_left, a_right)
                        and idx not in right_source_set
                        and dist_left[idx] <= dist_right[idx]
                    ]

                    # 若用户显式要求旋转该键（force_rotate），但分区为空，仍然保留（只是不会动原子）
                    if not rotate_atoms and force_rotate:
                        pair = tuple(sorted((a_left, a_right)))
                        if any(tuple(sorted((p[0] - 1, p[1] - 1))) == pair for p in force_rotate):
                            rotate_atoms = []

                    rot_bonds.append(
                        (int(a_left), int(a_right), np.array(rotate_atoms, dtype=np.int64))
                    )
                    angle_lists.append([float(x) for x in bond_angles[bi]])

            # 准备数据
            from rich.table import Table
            from rich import box
            topo_mat = Chem.GetDistanceMatrix(mol).astype(np.int64)
            atom_nums = np.array([a.GetAtomicNum() for a in mol.GetAtoms()], dtype=np.int64)

            console.print(f"可旋转键数: {len(rot_bonds)}")
            if len(rot_bonds) > 0:
                table = Table(show_header=False, box=box.ASCII, padding=(0, 2))
                for i, b in enumerate(rot_bonds):
                    a1, a2, _ = b
                    aa1, aa2 = mol.GetAtomWithIdx(a1), mol.GetAtomWithIdx(a2)
                    table.add_row(f"{i+1}:", f"{a1+1}({aa1.GetSymbol()}) - {a2+1}({aa2.GetSymbol()})")
                console.print(table)

            console.print(f"筛选策略: 软球碰撞系数 {clash_threshold} (理论推荐值)")

            if not rot_bonds:
                console.print("无旋转键，跳过。")
                all_confs_data.append(mol.GetConformer(0).GetPositions())
                continue

            if confirm:
                if input("开始生成? (y/n): ").lower() != "y":
                    continue

            per_bond_angles = angle_lists
            combos = list(itertools.product(*per_bond_angles))
            total_tasks = len(combos)
            print(f"任务总数: {total_tasks}")

            cpu_count = multiprocessing.cpu_count()
            init_args = (
                mol,
                mol.GetConformer(0),
                rot_bonds,
                clash_threshold,
                topo_mat,
                atom_nums,
                optimize,
            )

            with multiprocessing.Pool(
                cpu_count, initializer=init_worker, initargs=init_args
            ) as pool:
                chunk = max(1, total_tasks // (cpu_count * 10))
                
                results = []
                with create_progress() as progress:
                    task_id = progress.add_task("ConfGen", total=total_tasks)
                    for res in pool.imap(process_task, combos, chunksize=chunk):
                        results.append(res)
                        progress.advance(task_id)

            valid_count = 0
            for res in results:
                if res is not None:
                    all_confs_data.append(res)
                    valid_count += 1

            print(f"有效构象: {valid_count} / {total_tasks} ({valid_count/total_tasks*100:.1f}%)")

        except Exception as e:
            print(f"处理文件 {xyz_file} 时出错: {e}")
            import traceback

            traceback.print_exc()

    if all_confs_data and master_mol:
        out_name = "traj.xyz"
        print(f"\n正在写入 {len(all_confs_data)} 个构象到 {out_name}...")
        write_xyz(master_mol, all_confs_data, out_name)
        print("完成!")
    else:
        print("\n未生成任何构象。")

    return all_confs_data


# ------------------------------------------------------------------------------
# 命令行入口
# ------------------------------------------------------------------------------


def main():
    multiprocessing.freeze_support()
    import argparse

    parser = argparse.ArgumentParser(
        description="ConfGen v1.0 - Conformer Generator",
        epilog=(
            "链模式示例(默认角度步长=120): confgen mol.xyz --chain 81-69-78-86-92 --steps 120,60,120,120 -y\n"
            "链模式(显式角度列表示例): confgen mol.xyz --chain 81-69-78-86-92 --angles '0,120,240;0,60,120,180;180;0,120' -y\n"
            "可选：在末尾附加 angle_step 覆盖默认值，例如: confgen mol.xyz 60 --chain 81-69-78-86-92 -y\n"
            "注: 已移除自动柔性键判断，必须使用 --chain"
        ),
    )
    # 兼容位置参数：
    # - 旧用法: confgen mol.xyz 120
    # - 多文件: confgen a.xyz b.xyz 120
    # 解析策略：收集为 inputs；若最后一个 token 是整数且不是文件，则当作 angle_step。
    parser.add_argument(
        "inputs", nargs="+", help="Input XYZ files (+ optional trailing angle_step, default=120)"
    )

    # 增加 -m 别名，兼容旧习惯
    parser.add_argument(
        "-b", "-m", "--bond_threshold", type=float, default=1.15, help="成键判定系数 (默认 1.15)"
    )
    parser.add_argument(
        "-c", "--clash_threshold", type=float, default=0.65, help="软球碰撞系数 (默认 0.65)。"
    )

    parser.add_argument("--add_bond", nargs=2, type=int, action="append")
    parser.add_argument("--del_bond", nargs=2, type=int, action="append")
    parser.add_argument("--no_rotate", nargs=2, type=int, action="append")
    parser.add_argument("--force_rotate", nargs=2, type=int, action="append")

    # 新：手动链模式（不再自动判断柔性键）
    parser.add_argument(
        "--chain",
        action="append",
        default=None,
        help="指定一条链(1-based，用 '-' 连接)，例如 81-69-78-86-92；可重复多次",
    )
    parser.add_argument(
        "--steps",
        action="append",
        default=None,
        help="每条链每根键的角度步长列表(逗号分隔)，例如 120,60,120,120；可重复多次并与 --chain 一一对应",
    )
    parser.add_argument(
        "--angles",
        action="append",
        default=None,
        help="每条链每根键的角度列表示例: '0,120,240;0,60,120,180;180;0,120' (用 ';' 分隔每根键，用 ',' 分隔角度)",
    )
    parser.add_argument(
        "--rotate_side",
        choices=["left", "right"],
        default="left",
        help="围绕链方向旋转哪一侧片段：left=包含链首原子的一侧(默认)，right=包含链末原子的一侧",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Auto confirm")
    parser.add_argument("--optimize", "--opt", action="store_true", help="MMFF94s pre-optimization")

    args = parser.parse_args()

    # 解析 inputs -> input_files + angle_step
    angle_step = 120
    input_files = list(args.inputs)
    if len(input_files) >= 2:
        last = input_files[-1]
        if last.isdigit() and not os.path.exists(last):
            angle_step = int(last)
            input_files = input_files[:-1]
    if not input_files:
        parser.error("缺少输入 XYZ 文件")

    # 调用核心逻辑
    run_generation(
        input_files=input_files,
        angle_step=angle_step,
        bond_threshold=args.bond_threshold,
        clash_threshold=args.clash_threshold,
        add_bond=args.add_bond,
        del_bond=args.del_bond,
        no_rotate=args.no_rotate,
        force_rotate=args.force_rotate,
        optimize=args.optimize,
        confirm=args.yes,
        chains=args.chain,
        chain_steps=args.steps,
        chain_angles=args.angles,
        rotate_side=args.rotate_side,
    )


if __name__ == "__main__":
    main()
