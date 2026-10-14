"""Backend-neutral energy/geometry engine interface for the ConfGen search.

Owns the records the search exchanges with whatever engine computes energies and relaxes
geometries: restraints, relaxation requests and outcomes, the engine's identity and declared
capabilities, and the ``Calculator`` protocol.  The search depends only on this module, so the
engine (GFN2-xTB today) can be replaced or upgraded without touching the search algorithm.

Does not launch processes, read or write files, or know any program's input format; concrete
engines live in ``confflow.execution`` and translate these records into their own inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

__all__ = (
    "CAP_DYNAMICS CAP_HESSIAN CAP_RESTRAINED_RELAX Calculator CalculatorInfo DistanceRestraint "
    "RelaxOutcome RelaxRequest Restraint"
).split()

#: Relaxation that honours the request's restraints (required by the search).
CAP_RESTRAINED_RELAX = "restrained_relax"
#: Second derivatives at a geometry (enables Hessian-based moves).
CAP_HESSIAN = "hessian"
#: Molecular dynamics trajectories (enables dynamics-based moves).
CAP_DYNAMICS = "dynamics"


@dataclass(frozen=True, slots=True)
class DistanceRestraint:
    """Hold the distance between atoms ``i`` and ``j`` (0-based) near ``target`` angstrom."""

    i: int
    j: int
    target: float


#: Union of the restraint kinds an engine may be asked to honour; extend it here when the
#: search needs angles, dihedrals or frozen atoms, and teach each engine to render them.
Restraint = DistanceRestraint


@dataclass(frozen=True, slots=True)
class RelaxRequest:
    """One relaxation: a stable ``key`` (unique within a batch), start coordinates, restraints."""

    key: str
    coords: np.ndarray
    restraints: tuple[Restraint, ...] = ()


@dataclass(frozen=True, slots=True)
class RelaxOutcome:
    """Result of one relaxation; ``coords`` is ``None`` unless the engine converged."""

    coords: np.ndarray | None = None
    energy_eh: float | None = None
    converged: bool = False
    wall_s: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CalculatorInfo:
    """Identity of an engine for provenance: program, level of theory, version, capabilities."""

    program: str
    method: str
    version: str = ""
    capabilities: frozenset[str] = frozenset({CAP_RESTRAINED_RELAX})

    def supports(self, capability: str) -> bool:
        """Return whether the engine declares ``capability``."""
        return capability in self.capabilities


class Calculator(Protocol):
    """Energy/geometry engine used by the search.

    ``relax_many`` returns exactly one outcome per request, in request order.  Engines own
    their parallelism, caching and cancellation; a cancelled batch raises instead of
    returning partial results.
    """

    @property
    def info(self) -> CalculatorInfo:
        """Return the engine identity and capabilities."""
        ...

    def relax_many(self, requests: Sequence[RelaxRequest]) -> list[RelaxOutcome]:
        """Relax every request and return the outcomes in request order."""
        ...

    def hessian(self, request: RelaxRequest) -> np.ndarray | None:
        """Return the Cartesian Hessian (3N x 3N, Eh/bohr^2) at ``request.coords``.

        The request's restraints act as during relaxation.  Called only when the engine
        declares ``CAP_HESSIAN``; ``None`` reports a failed evaluation.
        """
        ...
