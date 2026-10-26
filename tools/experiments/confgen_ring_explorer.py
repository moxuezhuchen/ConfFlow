"""Opt-in search prototype for saturated pendant-ring inversion.

This deliberately uses the production parent's selection, random stream and DG
generation unchanged, replacing at most half the successful local re-embedding
proposals. Generation of the discarded DG proposals remains charged as overhead.
No extra optimizations, parent priorities or production defaults are introduced.
"""

from __future__ import annotations

import time
from collections import Counter

import numpy as np

from confflow.science.confgen.explore import _Explorer
from confflow.science.confgen.stereo import (
    preserves_double_bonds,
    preserves_tetrahedra,
    preserves_torsions,
)
from tools.experiments.confgen_ring_moves import invert_pendant_ring, pendant_rings


class RingInversionExplorer(_Explorer):
    """Replace up to half the local DG proposals with exact ring inversions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pinned = set(self.donors) | {atom.index for atom in self.fixed_atoms}
        if self.metal is not None:
            pinned.add(self.metal)
        self.ring_parts = ()
        if self.stable_molecule is not None:
            rigid_edges = frozenset(
                tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))
                for bond in self.stable_molecule.GetBonds()
                if bond.GetIsAromatic() or bond.GetBondTypeAsDouble() != 1.0
            )
            self.ring_parts = pendant_rings(
                self.id.full, self.topo.rings, frozenset(pinned), rigid_edges
            )
        self.ring_cursor = self.st.seed % max(1, len(self.ring_parts))
        self.ring_counts = Counter()
        self.ring_proposals = []

    def _reembed(self, quota: int) -> list[tuple[str, np.ndarray, str]]:
        items = super()._reembed(quota)
        if not self.ring_parts or len(items) < 2:
            return items
        began, cpu = time.monotonic(), time.process_time()
        parents = {m.id: m for m in self.pool.minima}
        used = set()
        replaced = 0
        for k, (_, _, origin) in enumerate(items):
            if replaced >= len(items) // 2:
                break
            pid = int(origin.split(":")[1])
            parent = parents[pid]
            for _ in self.ring_parts:
                ri = self.ring_cursor % len(self.ring_parts)
                self.ring_cursor += 1
                if (pid, ri) in used:
                    continue
                used.add((pid, ri))
                self.ring_counts["attempts"] += 1
                try:
                    x = invert_pendant_ring(parent.coords, self.ring_parts[ri])
                except ValueError:
                    self.ring_counts["degenerate"] += 1
                    continue
                if not (
                    preserves_tetrahedra(x, self.tetrahedral_states)
                    and preserves_double_bonds(x, self.double_bond_states)
                    and preserves_torsions(x, self.torsion_states)
                    and not self.topo.clashes(x)
                ):
                    self.ring_counts["precheck_rejected"] += 1
                    continue
                if np.max(np.abs(x - parent.coords)) < 1e-6:
                    self.ring_counts["unchanged"] += 1
                    continue
                key = f"reembed/m{pid:05d}_{k:02d}_ring{ri:02d}"
                items[k] = (key, x, f"reembed:{pid}:ring:{ri}")
                self.ring_proposals.append(
                    dict(key=key, parent=pid, ring=ri, round=self.round_index, nopt=self.nopt)
                )
                replaced += 1
                break
        self.ring_counts["proposed"] += replaced
        record = self.generation["reembed"]
        record["cpu_s"] += time.process_time() - cpu
        record["map_wall_s"] += time.monotonic() - began
        return items
