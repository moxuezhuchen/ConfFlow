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
    "CAP_DYNAMICS CAP_FIXED_ATOMS CAP_HESSIAN CAP_RESTRAINED_RELAX Calculator CalculatorInfo "
    "DistanceRestraint FixedAtom RelaxOutcome RelaxRequest Restraint FIXED_ATOM_TOLERANCE_ANGSTROM "
    "FIXED_REFERENCE_TOLERANCE_ANGSTROM"
).split()

#: Relaxation that honours the request's restraints (required by the search).
CAP_RESTRAINED_RELAX = "restrained_relax"
#: Exact Cartesian pinning to request-bound reference coordinates.
CAP_FIXED_ATOMS = "fixed_atoms"
#: Second derivatives at a geometry (enables Hessian-based moves).
CAP_HESSIAN = "hessian"
#: Molecular dynamics trajectories (enables dynamics-based moves).
CAP_DYNAMICS = "dynamics"
#: Maximum accepted backend displacement of a pinned coordinate (xTB output is written to 1e-6 A).
FIXED_ATOM_TOLERANCE_ANGSTROM = 1.0e-5
#: Request coordinates must match their declared immutable targets to this input tolerance.
FIXED_REFERENCE_TOLERANCE_ANGSTROM = 1.0e-8


@dataclass(frozen=True, slots=True)
class DistanceRestraint:
    """Hold the distance between atoms ``i`` and ``j`` (0-based) near ``target`` angstrom."""

    i: int
    j: int
    target: float


@dataclass(frozen=True, slots=True)
class FixedAtom:
    """One exactly pinned atom and its immutable 0-based reference coordinate."""

    index: int
    coordinates: tuple[float, float, float]

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0:
            raise ValueError("fixed atom index must be a non-negative integer")
        if len(self.coordinates) != 3:
            raise ValueError("fixed atom reference coordinate must contain three values")
        point = tuple(float(value) for value in self.coordinates)
        if not all(np.isfinite(value) for value in point):
            raise ValueError("fixed atom reference coordinate must be finite")
        object.__setattr__(self, "coordinates", point)


#: Union of the restraint kinds an engine may be asked to honour; extend it here when the
#: search needs angles, dihedrals or frozen atoms, and teach each engine to render them.
Restraint = DistanceRestraint


@dataclass(frozen=True, slots=True)
class RelaxRequest:
    """One relaxation: a stable ``key`` (unique within a batch), start coordinates, restraints."""

    key: str
    coords: np.ndarray
    restraints: tuple[Restraint, ...] = ()
    fixed_atoms: tuple[FixedAtom, ...] = ()

    def __post_init__(self) -> None:
        coords = np.asarray(self.coords, dtype=float)
        if coords.ndim != 2 or coords.shape[1] != 3 or not bool(np.all(np.isfinite(coords))):
            raise ValueError("relaxation coordinates must be a finite Nx3 array")
        object.__setattr__(self, "coords", coords)
        fixed = tuple(self.fixed_atoms)
        indices = [atom.index for atom in fixed]
        if len(set(indices)) != len(indices):
            raise ValueError("relaxation request contains duplicate fixed atom indices")
        for atom in fixed:
            if atom.index >= len(coords):
                raise ValueError(f"fixed atom index {atom.index} is out of range")
            if not np.allclose(
                coords[atom.index],
                atom.coordinates,
                rtol=0.0,
                atol=FIXED_REFERENCE_TOLERANCE_ANGSTROM,
            ):
                raise ValueError(
                    f"fixed atom {atom.index} request coordinate differs from its reference"
                )
        object.__setattr__(self, "fixed_atoms", fixed)


@dataclass(frozen=True, slots=True)
class RelaxOutcome:
    """Result of one relaxation; ``coords`` is ``None`` unless the engine converged."""

    coords: np.ndarray | None = None
    energy_eh: float | None = None
    converged: bool = False
    wall_s: float = 0.0
    detail: str = ""
    failure_kind: str | None = None


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
