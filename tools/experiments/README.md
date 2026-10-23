# ConfGen 闭环移动实验

这组代码供搜索算子研究使用。实验入口沿用 main 的搜索和审计，运行时显式选择
`base` 或 `ring`。现有工作流的配置、生产搜索代码和默认行为没有改动。

## 原型

`confgen_ring_moves.py` 提供两个纯 NumPy 坐标生成器：

- `ring_arcs` / `rotate_arc`：通过两个环原子的图切割找到一段环及其完整支链，
  绕两个端点的连线旋转。保持键长和闭环，但端点键角会变。akugod 的首轮
  xTB 诊断未显示收益，保留实现用于复核。
- `pendant_rings` / `invert_pendant_ring`：识别 5–8 元、环内单键的简单环，
  要求外部组件各自只连接一个环原子，固定主体也只能通过一个环原子连接。
  以该原子及其两个环邻居定义平面，反射环骨架；每条外部支链按新旧局部坐标架
  之间的正旋转搬运。保持全图键长、键角及局部四面体手性，主体坐标保持不动。

坐标变换只提出候选。调用方仍需过滤碰撞、双键立体和固定二面角；优化后仍通过
原有的配位、反应约束和化学拓扑审计。稠合、桥连、多个固定连接点和环内刚性键
不在本次翻环原型的适用范围。5–8 元是几何适用范围，当前收益实验针对六元环。

`RingInversionExplorer` 先调用原有 `_reembed`，再把至多一半的成功提案替换成
同一父代的翻环提案。每个父代、每个环最多一次；环顺序采用独立的循环计数，
不额外消耗生产 RNG。没有合法翻环时保留原提案，父代规则和总优化名额沿用 main。
不同提案导致的池变化仍会影响后续搜索轨迹，所以整体效果必须做独立对照。

为了使第一轮实验边界明确，被替换的 DG 坐标仍会生成，其 CPU 开销计入实验。
这是保守的原型实现，当前不能据此宣称坐标生成已经提速。

## 重跑

从已有的类型化拓扑和原始输入坐标导出私有 JSON：

```python
from dataclasses import asdict
import json

case = {
    "name": "example",
    "graph": graph.to_mapping(convention="internal0"),
    "coordination": asdict(spec) if spec is not None else None,
    "coords": coordinates.tolist(),
    "shape": "input" if spec is not None else None,
    "charge": 0,  # 使用该体系的真实电荷
    "uhf": 0,  # 使用该体系的真实未配对电子数
    "settings": {"arrangement": "search", "window_kcal": 6.0},
}
with open("/tmp/case.json", "w") as stream:
    json.dump(case, stream)
```

在仓库根目录运行两臂。种子和组合在启动前固定；用于计时的输出目录必须是新的，
入口会拒绝重用已有目录。两臂同负载同时启动，并保证有足够的可用核心。

```bash
.venv/bin/python -m tools.experiments.run_ring_benchmark \
  --case /tmp/case.json --output /tmp/ring-base-s70 \
  --variant base --seed 70 --budget 300 --cores 8 \
  --xtb /opt/xtb-pr/current/bin/xtb

.venv/bin/python -m tools.experiments.run_ring_benchmark \
  --case /tmp/case.json --output /tmp/ring-new-s70 \
  --variant ring --seed 70 --budget 300 --cores 8 \
  --xtb /opt/xtb-pr/current/bin/xtb
```

入口输出 `result.json` 和逐次 xTB 输入、输出。结果包含原始发布坐标、搜索池及来源、
翻环提案记录、搜索与 release 次数、总 CPU、墙钟、代码和二进制哈希。
`inputs_unchanged` 检查运行期间代码、输入和二进制是否变化。
总 CPU 包含搜索、release 和进程池开销；初始案例读取及事后评分另计。
原始结构留在实验输出目录，仓库中只保存不含坐标的结果摘要。

评分使用冻结基准 v1 的 `bench_score.py`：同一个参考集合、生产判同规则，分别报告
1/3/6 kcal 覆盖、Boltzmann 覆盖和 `new3`。参考结构只用于评分，不传入生成器。
本入口保存整次运行结果，没有记录预算前缀快照，不用于宣称前缀 CPU 增长曲线。

## 检查

```bash
.venv/bin/python -m pytest -q \
  tests/v4/test_confgen_ring_moves_experiment.py \
  tests/v4/test_confgen_ring_explorer_experiment.py \
  tests/v4/test_confgen_rotors.py \
  tests/v4/test_confgen_explore.py
.venv/bin/python -m ruff check tools/experiments \
  tests/v4/test_confgen_ring_moves_experiment.py \
  tests/v4/test_confgen_ring_explorer_experiment.py
```

测试覆盖键长、键角、手性、固定坐标、刚体变换等变性、逆变换、桥连拒绝、
退化坐标拒绝，以及配额、父代、RNG 和失败回退。
