#!/usr/bin/env python3
from pathlib import Path
import json,itertools
ROOT=Path(__file__).resolve().parents[1]
top=json.loads((ROOT/'topology/typed_topology.json').read_text())
exp=json.loads((ROOT/'benchmark/expected_coordination_benchmark.json').read_text())
wit=json.loads((ROOT/'benchmark/expected_sigma_witness.json').read_text())
con=json.loads((ROOT/'benchmark/coordination_constraints.json').read_text())
assert top['atom_count']==122
assert len(top['atoms'])==122
assert len(top['edges'])==131
assert sum(e['type']=='COORDINATION' for e in top['edges'])==6
assert sum(e['type']=='FORMING' for e in top['edges'])==1
# fixed atom order in every XYZ
ref_atoms=None
for p in sorted((ROOT/'structures').glob('*.xyz')):
    lines=p.read_text().splitlines(); i=0
    while i < len(lines):
        n=int(lines[i]); atoms=[lines[i+2+j].split()[0] for j in range(n)]
        if ref_atoms is None: ref_atoms=atoms
        assert atoms==ref_atoms, f'atom order differs in {p.name}'
        i += n+2
# witness preserves typed topology
mp={int(k):int(v) for k,v in wit['mapping'].items()}
assert all(mp[mp[i]]==i for i in mp)
elements={a['index_1based']:a['element'] for a in top['atoms']}
assert all(elements[i]==elements[mp[i]] for i in mp)
E={(min(e['a_1based'],e['b_1based']),max(e['a_1based'],e['b_1based']),e['type']) for e in top['edges']}
for a,b,t in E:
    assert (min(mp[a],mp[b]),max(mp[a],mp[b]),t) in E
# independent count check using supplied rotation permutations
sites=exp['site_order']; si={s:i for i,s in enumerate(sites)}
rots=[tuple(r) for r in exp['vertex_convention']['proper_rotations_vertex_permutations']]
opp={0:1,1:0,2:3,3:2,4:5,5:4}
forbidden=[tuple(c['sites']) for c in con['constraints'] if c['kind']=='FORBIDDEN_TRANS']
def ok(f): return all(opp[f[si[a]]]!=f[si[b]] for a,b in forbidden)
def canon(f): return min(tuple(r[f[i]] for i in range(6)) for r in rots)
allp=list(itertools.permutations(range(6)))
valid=[f for f in allp if ok(f)]
shape=set(canon(f) for f in valid)
assert len(allp)==720
assert len(valid)==288
assert len(shape)==12
sig=tuple(exp['sigma_site_permutation_0based'])
inv=[0]*6
for i,j in enumerate(sig): inv[j]=i
def sigma_act(f): return tuple(f[inv[i]] for i in range(6))
seen=set(); orbits=[]
for r in sorted(shape):
    if r in seen: continue
    s=canon(sigma_act(r)); o={r,s}; seen.update(o); orbits.append(o)
assert len(orbits)==6 and all(len(o)==2 for o in orbits)
print('PASS: TS1 topology fixture: 720 -> 288 -> 12 -> 6; atom order fixed; typed sigma witness valid.')
