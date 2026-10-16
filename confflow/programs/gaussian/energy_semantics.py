#!/usr/bin/env python3

"""Authoritative Gaussian energy-semantics capability model.

ConfFlow publishes a Gaussian ``energy`` result only when it can prove that
the published number is the final energy of the *requested* method.  The
Gaussian file-format authority owns that capability here, in one place, and
both halves of the guarantee use this module:

1. **Static route classification** (:func:`unsupported_method_finding`),
   evaluated at compile/validation time and again before rendering.  Method
   families whose final energy is *not* the SCF energy — post-SCF wavefunction
   methods, double hybrids, composite methods, and response methods — are
   refused with an explicit diagnostic before anything is submitted.

2. **Runtime publication proof** (:func:`final_energy_semantics_problem`),
   evaluated while parsing the native output.  The parser reads the last
   ``SCF Done`` line as the electronic energy; this proof accepts that value
   only when the log contains no later method-final-energy marker (``E2(``,
   ``E(Method)=``, ``MP2=``, ``CCSD=``, composite summaries, ...).  For a
   Gaussian ``External`` job (no ``SCF Done`` line) the parser instead reads
   the last ``Recovered energy=`` value; the proof accepts it only when the log
   announces ``External calculation of energy``, contains no ``SCF Done`` and
   no ``ONIOM`` layer (an ONIOM energy is an extrapolation), and has no
   method-final-energy marker after the last ``Recovered energy=`` line.  A
   failure yields an ERROR parser diagnostic, which the executor already treats
   as authoritative, so the item fails and no scientific result is published.

The two halves share one vocabulary.  Gaussian accepting a keyword is not the
same claim as ConfFlow being able to interpret the keyword's final energy;
only the capability model above decides what ConfFlow may publish.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "MethodFamily",
    "MethodFamilyFinding",
    "SCF_DONE_MARKER",
    "final_energy_semantics_problem",
    "unsupported_method_finding",
]

#: Marker whose final occurrence anchors the published electronic energy.
SCF_DONE_MARKER: Final[str] = "SCF Done"

#: Per-evaluation energy echo of a Gaussian ``External`` calculation.
EXTERNAL_RECOVERED_MARKER: Final[str] = "Recovered energy="

#: Log line announcing that Gaussian delegated energy to an external program.
EXTERNAL_CALCULATION_MARKER: Final[str] = "External calculation of energy"

#: Parser source name for an electronic energy taken from ``Recovered energy=``.
EXTERNAL_RECOVERED_SOURCE: Final[str] = "external_recovered"


class MethodFamily(str, Enum):
    """Method families whose final energy ConfFlow cannot extract."""

    POST_SCF = "post_scf"
    DOUBLE_HYBRID = "double_hybrid"
    COMPOSITE = "composite"
    RESPONSE = "response"


@dataclass(frozen=True, slots=True)
class MethodFamilyFinding:
    """One route token positively classified into an unsupported family."""

    token: str
    family: MethodFamily
    reason: str


#: Route tokens are split on whitespace and route separators; ``#``/``#p``
#: prefixes and empty fragments are ignored.
_ROUTE_SPLIT: Final[re.Pattern[str]] = re.compile(r"[\s/]+")

#: Post-SCF wavefunction methods: MP2-MP5, coupled cluster, QCI/CI, CASSCF,
#: MCSCF, MRCI/FCI, BD, SAC-CI, EOM-CCSD and their explicit-correlation
#: variants, with the usual R/U/RO prefixes and option suffixes.
_POST_SCF: Final[re.Pattern[str]] = re.compile(
    r"^(?:ro|r|u)?(?:scs-|sos-)?(?:"
    r"mp[2-5](?:\([^)]*\)|=[a-z0-9]+)?"
    r"|ccsd(?:\([^)]*\)|=[a-z0-9]+)?|ccsdt(?:\([^)]*\)|=[a-z0-9]+)?|ccd"
    r"|qcisd(?:\([^)]*\)|=[a-z0-9]+)?|cisd(?:\([^)]*\)|=[a-z0-9]+)?|cid"
    r"|casscf(?:\([^)]*\)|=[a-z0-9]+)?|cas(?:\([^)]*\)|=[a-z0-9]+)?|mcscf"
    r"|mrci|mrcc|fci|bd(?:\([^)]*\)|=[a-z0-9]+)?|sac-?ci"
    r"|eom-?ccsd(?:\([^)]*\)|=[a-z0-9]+)?"
    r"|(?:mp[2-5]|ccsd|ccsd\(t\)|ccsdt)-?f12(?:\([^)]*\)|=[a-z0-9]+)?"
    r")$"
)

#: Double hybrids: the ``*plyp`` family (B2PLYP, mPW2PLYP, B2GP-PLYP, ...),
#: DSD functionals in both literature (``DSD-``) and Gaussian (``DSDPBEP86``)
#: spellings, and the other named double hybrids Gaussian accepts.
_DOUBLE_HYBRID: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r".*plyp.*"
    r"|dsd-?[a-z0-9-]*"
    r"|pbe0-?dh|pbe-?qidh"
    r"|xyg[0-9a-z-]*"
    r"|wb97x-?2"
    r")$"
)

#: Composite methods: G1-G4 and their variants (G3B3, G4MP2, G3MP2B3, ...),
#: CBS methods, and W1 variants.
_COMPOSITE: Final[re.Pattern[str]] = re.compile(
    r"^(?:" r"g[1-4][a-z0-9()]*" r"|cbs-?[a-z0-9-]+" r"|w1[a-z0-9-]*" r")$"
)

#: Response / excited-state methods whose final energy is not the SCF energy:
#: CIS and its option forms, the TD/TDA job keywords (any option form),
#: EOM-CCSD, SAC-CI, and the semi-empirical ZINDO whose final energy is the
#: CI/TDA total energy, not the SCF value.
_RESPONSE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"(?:cis|td|tda)(?:\([^)]*\)|=[a-z0-9()=,+-]+)?"
    r"|eom-?ccsd|sac-?ci|td-?dft|td-?hf|zindo"
    r")$"
)

_FAMILIES: Final[tuple[tuple[MethodFamily, re.Pattern[str]], ...]] = (
    (MethodFamily.POST_SCF, _POST_SCF),
    (MethodFamily.DOUBLE_HYBRID, _DOUBLE_HYBRID),
    (MethodFamily.COMPOSITE, _COMPOSITE),
    (MethodFamily.RESPONSE, _RESPONSE),
)

_FAMILY_LABELS: Final[dict[MethodFamily, str]] = {
    MethodFamily.POST_SCF: "post-SCF wavefunction",
    MethodFamily.DOUBLE_HYBRID: "double-hybrid",
    MethodFamily.COMPOSITE: "composite",
    MethodFamily.RESPONSE: "response/excited-state",
}

#: Method-final-energy markers that must not appear after the last
#: ``SCF Done`` line for a publishable SCF final energy.
_METHOD_FINAL_ENERGY_MARKERS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("double_hybrid_e2", re.compile(r"(?<![A-Za-z0-9])E2\s*\(")),
    (
        "method_energy_assignment",
        re.compile(r"(?<![A-Za-z0-9])E\([A-Za-z][A-Za-z0-9()+/=-]*\)\s*="),
    ),
    ("second_order_energy", re.compile(r"(?<![A-Za-z0-9])E2\s*=")),
    ("post_scf_mp", re.compile(r"(?<![A-Za-z0-9])MP[2-5]\s*=")),
    ("post_scf_cc", re.compile(r"(?<![A-Za-z0-9])CCSD(?:\(T\))?\s*=")),
    ("post_scf_qci", re.compile(r"(?<![A-Za-z0-9])QCISD(?:\(T\))?\s*=")),
    ("post_scf_ci", re.compile(r"(?<![A-Za-z0-9])(?:CISD|CID|CCD|BD)\s*=")),
    ("response_sac_ci", re.compile(r"(?<![A-Za-z0-9])SAC-CI\b")),
    (
        "composite_summary",
        re.compile(
            r"(?<![A-Za-z0-9])(?:G[1-4](?:MP2)?|CBS-[A-Za-z0-9-]+|W1[A-Za-z0-9-]*)"
            r"\s*(?:\(0 K\))?\s*(?:Energy|Enthalpy|Free Energy)?\s*="
        ),
    ),
    ("mcscf_energy", re.compile(r"(?<![A-Za-z0-9])MCSCF\s*=")),
)


def unsupported_method_finding(keyword: str) -> MethodFamilyFinding | None:
    """Classify *keyword* against the unsupported method families.

    Parameters
    ----------
    keyword : str
        Gaussian route keyword (the same string validation and rendering
        resolve; ``#``/``#p`` prefixes and job/basis tokens are allowed).

    Returns
    -------
    MethodFamilyFinding | None
        The first positively classified unsupported token in route order,
        or ``None`` when no unsupported family is identified.
    """
    for token in _ROUTE_SPLIT.split(keyword):
        candidate = token.strip().lower()
        if not candidate or candidate.startswith("#"):
            continue
        for family, pattern in _FAMILIES:
            if pattern.fullmatch(candidate):
                return MethodFamilyFinding(
                    token=token.strip(),
                    family=family,
                    reason=(
                        f"a {_FAMILY_LABELS[family]} method whose final energy "
                        "ConfFlow cannot extract"
                    ),
                )
    return None


def _external_recovered_problem(text: str) -> str | None:
    """Prove that the last ``Recovered energy=`` value is a final External energy.

    A Gaussian ``External`` job (``external="..."``) prints no ``SCF Done``
    line; each energy evaluation of the external program is echoed as
    ``Recovered energy=``.  The proof requires the log to announce an External
    calculation, to contain no ``SCF Done`` line, to contain no ONIOM layer
    (an ONIOM energy is an extrapolation, not one layer's energy), and to have
    no method-final-energy marker after the last ``Recovered energy=`` line.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    str | None
        ``None`` when the proof holds; otherwise a stable reason string.
    """
    if EXTERNAL_CALCULATION_MARKER not in text:
        return "external_energy_without_external_calculation"
    if SCF_DONE_MARKER in text:
        return "electronic_energy_source_not_scf_final"
    if "ONIOM" in text:
        return "external_energy_in_oniom"
    last = text.rfind(EXTERNAL_RECOVERED_MARKER)
    if last < 0:
        return "electronic_energy_source_not_scf_final"
    line_end = text.find("\n", last)
    tail = text[line_end:] if line_end >= 0 else ""
    for name, pattern in _METHOD_FINAL_ENERGY_MARKERS:
        if pattern.search(tail):
            return f"post_external_final_energy_marker:{name}"
    return None


def final_energy_semantics_problem(text: str, sources: Mapping[str, str]) -> str | None:
    """Prove that the parsed electronic energy is a publishable final energy.

    Two proofs are accepted, selected by the source of the electronic energy:

    * ``scf_done`` (a Gaussian SCF job): the electronic energy comes from an
      ``SCF Done`` line and the remainder of the log after the *last* ``SCF
      Done`` line contains no method-final-energy marker.  Any marker means the
      log carries a different method's final energy (for example a double
      hybrid's ``E2(Method)/E(Method)`` pair or a composite summary), so the SCF
      value must not be published as the requested method's energy.
    * ``external_recovered`` (a Gaussian ``External`` calculation): the log
      contains the ``External calculation of energy`` marker and no ``SCF
      Done`` line, no ``ONIOM`` layer, and no method-final-energy marker after
      the last ``Recovered energy=`` line.  See
      :func:`_external_recovered_problem`.

    Parameters
    ----------
    text : str
        Full log file content.
    sources : Mapping[str, str]
        Per-key source markers returned by
        :func:`confflow.programs.gaussian.parsing.parse_energies`.

    Returns
    -------
    str | None
        ``None`` when the published electronic energy is proven to be a final
        energy of the requested method; otherwise a stable reason string.
    """
    if sources.get("electronic") == EXTERNAL_RECOVERED_SOURCE:
        return _external_recovered_problem(text)
    if sources.get("electronic") != "scf_done":
        return "electronic_energy_source_not_scf_final"
    last = text.rfind(SCF_DONE_MARKER)
    if last < 0:
        return "electronic_energy_source_not_scf_final"
    line_end = text.find("\n", last)
    tail = text[line_end:] if line_end >= 0 else ""
    for name, pattern in _METHOD_FINAL_ENERGY_MARKERS:
        if pattern.search(tail):
            return f"post_scf_final_energy_marker:{name}"
    return None
