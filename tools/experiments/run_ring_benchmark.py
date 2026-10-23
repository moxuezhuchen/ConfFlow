"""Run one isolated ring-inversion comparison arm from a private JSON case.

Use ``python -m tools.experiments.run_ring_benchmark --help``. The case contains
``graph`` (TypedGraph.to_mapping, internal0), ``coords``, optional ``coordination``
(dataclasses.asdict(CoordinationSpec)), ``shape``, ``charge``, ``uhf`` and optional
ExploreSettings fields under ``settings``. No molecule-specific data is bundled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import resource
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def load_case(path):
    import numpy as np

    from confflow.science.confgen.graph import (
        BindingSite,
        CoordinationSpec,
        ForbiddenTrans,
        TypedGraph,
    )

    data = json.loads(path.read_text())
    topology = data["graph"]
    if topology.get("indexing") != "internal0":
        raise ValueError("benchmark cases must explicitly use internal0 indexing")
    graph = TypedGraph.from_mapping(
        topology,
        [atom["element"] for atom in topology["atoms"]],
        convention="internal0",
        source=topology.get("source", "benchmark"),
    )
    spec = None
    if data.get("coordination") is not None:
        fields = dict(data["coordination"])
        fields["binding_sites"] = tuple(BindingSite(**s) for s in fields["binding_sites"])
        constraints = fields.get("constraints", [])
        if any(c.get("proof") is not None for c in constraints):
            raise ValueError("proof-bearing constraints need the production workflow loader")
        fields["constraints"] = tuple(ForbiddenTrans(**c) for c in constraints)
        fields["witnesses"] = tuple(tuple(w) for w in fields.get("witnesses", []))
        spec = CoordinationSpec(**fields)
    return data, graph, spec, np.asarray(data["coords"], dtype=float)


def _cpu():
    rows = [resource.getrusage(kind) for kind in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    return sum(row.ru_utime + row.ru_stime for row in rows)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _main(args):
    # Both xTB and RDKit use process-level parallelism in the production worker.
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    import numpy as np

    import confflow
    from confflow.execution.confgen_xtb import XtbCalculator
    from confflow.science.confgen.explore import ExploreSettings, _Explorer
    from tools.experiments.confgen_ring_explorer import RingInversionExplorer

    data, graph, spec, coords = load_case(args.case)
    fields = dict(data.get("settings", {}))
    for name in ("fragment_charges", "fixed_states"):
        if name in fields:
            fields[name] = tuple(tuple(row) for row in fields[name])
    if "freeze_core" in fields:
        fields["freeze_core"] = tuple(fields["freeze_core"])
    fields.update(
        seed=args.seed,
        budget=args.budget,
        batch=8 * args.cores,
        relaxation_batch=max(4, args.cores),
        round_batches=4,
        release=True,
        adaptive=False,
    )
    settings = ExploreSettings(**fields)
    exe = Path(shutil.which(args.xtb) or args.xtb).resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)  # never reuse caches in a timing comparison
    calc = XtbCalculator(
        str(exe),
        args.output / "xtb",
        [a.element for a in graph.atoms],
        int(data["charge"]),
        int(data["uhf"]),
        300,
        args.cores,
    )
    root = Path(confflow.__file__).resolve().parents[1]
    sources = sorted((root / "confflow/science/confgen").rglob("*.py")) + [
        root / "confflow/execution/confgen_xtb.py",
        Path(__file__),
        Path(__file__).with_name("confgen_ring_moves.py"),
        Path(__file__).with_name("confgen_ring_explorer.py"),
    ]
    source_hashes = {str(p.relative_to(root)): _sha(p) for p in sources}
    case_hash, exe_hash = _sha(args.case), _sha(exe)
    cls = _Explorer if args.variant == "base" else RingInversionExplorer
    start, cpu = time.monotonic(), _cpu()
    try:
        with ProcessPoolExecutor(
            max_workers=args.cores, mp_context=multiprocessing.get_context("spawn")
        ) as workers:
            engine = cls(
                graph,
                spec,
                coords,
                data.get("shape"),
                settings,
                calc,
                lambda fn, items: workers.map(fn, items, chunksize=1),
                None,
            )
            final, stats = engine.run()
            run = engine._result(final, stats)
    except BaseException:
        calc.cancel()
        raise
    wall_s, cpu_s = time.monotonic() - start, _cpu() - cpu
    final_by_id = {m.id: m for m in final.minima}
    records = [
        dict(
            energy=r.energy_eh,
            coords=np.asarray(r.coords).tolist(),
            passing=True,
            id=r.start,
            origin=final_by_id[r.start].origin,
        )
        for r in run.passing
    ]
    result = dict(
        system=data.get("name", args.case.stem),
        seed=args.seed,
        variant=args.variant,
        budget=args.budget,
        cores=args.cores,
        wall_s=wall_s,
        cpu_s=cpu_s,
        code=str(root),
        commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        case_sha256=case_hash,
        source_sha256=source_hashes,
        inputs_unchanged=(
            case_hash == _sha(args.case)
            and exe_hash == _sha(exe)
            and source_hashes == {str(p.relative_to(root)): _sha(p) for p in sources}
        ),
        xtb=str(exe),
        xtb_sha256=exe_hash,
        summary=run.summary(),
        records=records,
        pool=[
            dict(id=m.id, energy=m.energy, origin=m.origin, cell=m.cell, coords=m.coords.tolist())
            for m in engine.pool.minima
        ],
        ring_counts=dict(getattr(engine, "ring_counts", {})),
        ring_proposals=getattr(engine, "ring_proposals", []),
    )
    (args.output / "result.json").write_text(json.dumps(result))
    print(
        json.dumps(
            dict(
                system=result["system"],
                variant=args.variant,
                seed=args.seed,
                best_eh=min((r["energy"] for r in records), default=None),
                wall_s=wall_s,
                cpu_s=cpu_s,
                search_optimizations=engine.nopt,
                release_optimizations=engine.released,
                ring_counts=result["ring_counts"],
            )
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=("base", "ring"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--cores", type=int, default=8)
    parser.add_argument("--xtb", default="xtb")
    args = parser.parse_args()
    if args.budget < 1 or args.cores < 1:
        parser.error("budget and cores must be positive")
    _main(args)


if __name__ == "__main__":
    main()
