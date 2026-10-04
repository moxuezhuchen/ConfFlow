#!/usr/bin/env python3

"""ConfFlow shared data module.

Centralises constant data shared across all modules to avoid duplicate definitions.
"""

from __future__ import annotations

# ==============================================================================
# GaussView official covalent radii (used uniformly across all tools)
# ==============================================================================
# Covalent radii for elements 0-119 (unit: Ångström), sourced from GaussView.
# Used for:
# - Bond detection in the conformer generator
# - Atom identification during RMSD deduplication
# - Geometric clash detection
GV_COVALENT_RADII: tuple[float, ...] = (
    0.00,  # 0 - placeholder
    0.30,  # 1 - H
    1.16,  # 2 - He
    1.23,  # 3 - Li
    0.89,  # 4 - Be
    0.88,  # 5 - B
    0.77,  # 6 - C
    0.70,  # 7 - N
    0.66,  # 8 - O
    0.58,  # 9 - F
    0.55,  # 10 - Ne
    1.40,  # 11 - Na
    1.36,  # 12 - Mg
    1.25,  # 13 - Al
    1.17,  # 14 - Si
    1.05,  # 15 - P
    1.01,  # 16 - S
    0.99,  # 17 - Cl
    1.58,  # 18 - Ar
    2.03,  # 19 - K
    1.74,  # 20 - Ca
    1.44,  # 21 - Sc
    1.32,  # 22 - Ti
    1.20,  # 23 - V
    1.13,  # 24 - Cr
    1.17,  # 25 - Mn
    1.16,  # 26 - Fe
    1.16,  # 27 - Co
    1.15,  # 28 - Ni
    1.17,  # 29 - Cu
    1.25,  # 30 - Zn
    1.25,  # 31 - Ga
    1.22,  # 32 - Ge
    1.21,  # 33 - As
    1.17,  # 34 - Se
    1.14,  # 35 - Br
    1.89,  # 36 - Kr
    2.25,  # 37 - Rb
    1.92,  # 38 - Sr
    1.62,  # 39 - Y
    1.45,  # 40 - Zr
    1.34,  # 41 - Nb
    1.29,  # 42 - Mo
    1.23,  # 43 - Tc
    1.24,  # 44 - Ru
    1.25,  # 45 - Rh
    1.28,  # 46 - Pd
    1.34,  # 47 - Ag
    1.41,  # 48 - Cd
    1.50,  # 49 - In
    1.40,  # 50 - Sn
    1.41,  # 51 - Sb
    1.37,  # 52 - Te
    1.33,  # 53 - I
    2.09,  # 54 - Xe
    2.35,  # 55 - Cs
    1.98,  # 56 - Ba
    1.69,  # 57 - La
    1.65,  # 58 - Ce
    1.65,  # 59 - Pr
    1.64,  # 60 - Nd
    1.64,  # 61 - Pm
    1.66,  # 62 - Sm
    1.85,  # 63 - Eu
    1.61,  # 64 - Gd
    1.59,  # 65 - Tb
    1.59,  # 66 - Dy
    1.58,  # 67 - Ho
    1.57,  # 68 - Er
    1.56,  # 69 - Tm
    1.70,  # 70 - Yb
    1.56,  # 71 - Lu
    1.44,  # 72 - Hf
    1.34,  # 73 - Ta
    1.30,  # 74 - W
    1.28,  # 75 - Re
    1.26,  # 76 - Os
    1.26,  # 77 - Ir
    1.29,  # 78 - Pt
    1.34,  # 79 - Au
    1.44,  # 80 - Hg
    1.55,  # 81 - Tl
    1.54,  # 82 - Pb
    1.52,  # 83 - Bi
    1.53,  # 84 - Po
    1.52,  # 85 - At
    1.53,  # 86 - Rn
    2.45,  # 87 - Fr
    2.02,  # 88 - Ra
    1.70,  # 89 - Ac
    1.63,  # 90 - Th
    1.46,  # 91 - Pa
    1.40,  # 92 - U
    1.36,  # 93 - Np
    1.25,  # 94 - Pu
    1.57,  # 95 - Am
    1.58,  # 96 - Cm
    1.54,  # 97 - Bk
    1.53,  # 98 - Cf
    1.84,  # 99 - Es
    1.61,  # 100 - Fm
    1.50,  # 101 - Md
    1.49,  # 102 - No
    1.38,  # 103 - Lr
    1.36,  # 104 - Rf
    1.26,  # 105 - Db
    1.20,  # 106 - Sg
    1.16,  # 107 - Bh
    1.14,  # 108 - Hs
    1.06,  # 109 - Mt
    1.28,  # 110 - Ds
    1.21,  # 111 - Rg
)
GV_RADII_ARRAY = GV_COVALENT_RADII


# Periodic table symbols (1-based; index 0 is an empty string)
PERIODIC_SYMBOLS: tuple[str, ...] = (
    "",
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
    "Po",
    "At",
    "Rn",
    "Fr",
    "Ra",
    "Ac",
    "Th",
    "Pa",
    "U",
    "Np",
    "Pu",
    "Am",
    "Cm",
    "Bk",
    "Cf",
    "Es",
    "Fm",
    "Md",
    "No",
    "Lr",
    "Rf",
    "Db",
    "Sg",
    "Bh",
    "Hs",
    "Mt",
    "Ds",
    "Rg",
    "Cn",
    "Nh",
    "Fl",
    "Mc",
    "Lv",
    "Ts",
    "Og",
)

# Element symbol -> atomic number mapping (upper-case)
SYMBOL_TO_ATOMIC_NUMBER = {sym.upper(): i for i, sym in enumerate(PERIODIC_SYMBOLS) if sym}


def get_covalent_radius(atomic_number: int) -> float:
    """Return the covalent radius for a given element.

    Parameters
    ----------
    atomic_number : int
        Atomic number (1-based).

    Returns
    -------
    float
        Covalent radius in Ångström.  Returns 1.50 for unknown elements.
    """
    if 0 < atomic_number < len(GV_COVALENT_RADII):
        return GV_COVALENT_RADII[atomic_number]
    return 1.50  # Default radius for unknown elements


def get_element_symbol(atomic_number: int) -> str:
    """Return the element symbol for a given atomic number.

    Parameters
    ----------
    atomic_number : int
        Atomic number (1-based).

    Returns
    -------
    str
        Element symbol (upper-case).  Returns ``"X"`` for unknown elements.
    """
    if 0 < atomic_number < len(PERIODIC_SYMBOLS):
        return PERIODIC_SYMBOLS[atomic_number]
    return "X"


def get_atomic_number(symbol: str) -> int:
    """Return the atomic number for a given element symbol.

    Parameters
    ----------
    symbol : str
        Element symbol (case-insensitive).

    Returns
    -------
    int
        Atomic number.  Returns 0 for unknown elements.
    """
    return SYMBOL_TO_ATOMIC_NUMBER.get(symbol.upper(), 0)


__all__ = [
    "GV_COVALENT_RADII",
    "GV_RADII_ARRAY",
    "PERIODIC_SYMBOLS",
    "SYMBOL_TO_ATOMIC_NUMBER",
    "get_covalent_radius",
    "get_element_symbol",
    "get_atomic_number",
]
