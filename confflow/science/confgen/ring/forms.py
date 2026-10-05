#!/usr/bin/env python3

"""R4: ring regular-form catalog, selectors, aliases, constrained expansion.

Authority: PLAN R4 + Q1-Q4/Q13 + V18/V20 + R1 canonical_forms (CP table is
authoritative, geometry only a test object). No Cartesian template I/O here;
zero_torsion_bonds comes from puckering.canonical_forms (inverse-CP
construction), never hand-filled.

Naming:
- Precise selector: "<FAMILY>_<INDEX>" e.g. "B_2", "TB_0", "E_5", "C_0",
  "T_3", "P_0", "B+_0", "B-_0". Family is CanonicalForm.family verbatim.
- Family selector: "<FAMILY>" e.g. "B", "TB", "E", "H", "C", "T", "P",
  "B+", "B-" expands to all forms of that family for the ring size.
  For n=4, bare "B" expands to both B+ and B- (convenience; precise
  "B+_0"/"B-_0" remain canonical).
- Old templates field: Q3 aliases. chair_A_6 -> C matching its pole
  (A: theta180 -> C_1; B: theta0 -> C_0); boat_6 -> B_3 (phi180, matches
  old boat CP theta90/phi180); twist_boat_6 -> TB_0 (phi30, nearest TB
  per PLAN S0.3 theta41/phi30); envelope_5 -> E_5 (phi180); twist_5 ->
  E_5 + DeprecationWarning; planar_4 -> P_0; pucker_up_4 -> B-_0
  (measured sign -1); pucker_down_4 -> B+_0 (measured sign +1);
  planar_5 -> explicit P for n=5 (special planar, outside default 20).
  Unknown names fail closed.

Defaults (Q1/Q2): n=6 -> 2C+6TB (8); n=5 -> all 20 (10E+10T);
n=4 -> all 3 (P/B+/B-). Explicit "P" for n=5/6 is a special planar
calling object (outside default catalogs, per R3 root decision #2);
it is only enumerated when explicitly requested.

Constrained forms: given pinned global pairs Z (from R2 units) restricted
to one ring, return forms with Z subset of form.zero_torsion_bonds
(mapped to global pairs via ring_order). If none covers all of Z, return
those covering the most bonds. Empty Z -> empty (use defaults only).
"""

from __future__ import annotations

import warnings

from .puckering import CanonicalForm, canonical_forms

__all__ = [
    "FORM_NAMES_BY_SIZE",
    "alias_templates_to_forms",
    "constrained_forms",
    "default_forms",
    "expand_form_tokens",
    "form_by_name",
    "form_name",
]


def form_name(form: CanonicalForm) -> str:
    """Return the precise selector name for one form."""
    return f"{form.family}_{form.index}"


def _all_forms(n: int) -> tuple[CanonicalForm, ...]:
    return canonical_forms(n)


def _forms_by_family(n: int) -> dict[str, tuple[CanonicalForm, ...]]:
    out: dict[str, list[CanonicalForm]] = {}
    for form in _all_forms(n):
        out.setdefault(form.family, []).append(form)
    return {k: tuple(v) for k, v in out.items()}


def _synthetic_planar(n: int) -> CanonicalForm:
    """Explicit-P special object for n=5/6 (outside default catalogs)."""
    from .puckering import CPCoords

    if n == 5:
        target = CPCoords(n=5, q=0.0, theta=0.0, phi=0.0)
        # Planar 5-ring: every torsion is 0 -> every bond is zero-torsion.
        zeros = tuple(f"{k}-{(k + 1) % 5}" for k in range(5))
        # Canonical middle-bond labels are "a-b" with a=(j+1)%n,b=(j+2)%n;
        # for planar all five middle bonds qualify.
        return CanonicalForm(n=5, family="P", index=0, cp_target=target, zero_torsion_bonds=zeros)
    if n == 6:
        target = CPCoords(n=6, q=0.0, theta=0.0, phi=0.0)
        zeros = tuple(f"{k}-{(k + 1) % 6}" for k in range(6))
        return CanonicalForm(n=6, family="P", index=0, cp_target=target, zero_torsion_bonds=zeros)
    raise ValueError(f"synthetic P only for n=5/6, got {n}")


def form_by_name(name: str, n: int) -> CanonicalForm:
    """Resolve one precise selector for ring size n (fail closed)."""
    token = str(name)
    # Explicit P special-case for n=5/6.
    if token in ("P", "P_0") and n in (5, 6):
        # "P" family selector and "P_0" precise both yield the special.
        return _synthetic_planar(n)
    for form in _all_forms(n):
        if form_name(form) == token:
            return form
    raise KeyError(f"unknown form {token!r} for ring size {n}")


def expand_form_tokens(tokens: list[str], n: int) -> tuple[CanonicalForm, ...]:
    """Expand forms tokens (family or precise) for size n, stable order.

    Family token expands to all forms of that family (canonical order);
    precise token yields one form. Duplicates are removed, order follows
    canonical_forms. Unknown tokens raise KeyError (caller maps to
    RingUnsupported fail-closed).
    """
    if not tokens:
        return ()
    by_fam = _forms_by_family(n)
    # n=4 convenience: bare "B" means both B+ and B-.
    out: list[CanonicalForm] = []
    seen: set[str] = set()
    for raw in tokens:
        token = str(raw)
        if token in by_fam:
            for form in by_fam[token]:
                key = form_name(form)
                if key not in seen:
                    seen.add(key)
                    out.append(form)
            continue
        if token == "B" and n == 4:
            for fam in ("B+", "B-"):
                for form in by_fam.get(fam, ()):
                    key = form_name(form)
                    if key not in seen:
                        seen.add(key)
                        out.append(form)
            if any(k.startswith("B") for k in seen):
                continue
            raise KeyError(f"unknown form {token!r} for ring size {n}")
        if token in ("P",) and n in (5, 6):
            key = "P_0"
            if key not in seen:
                seen.add(key)
                out.append(_synthetic_planar(n))
            continue
        # Precise selector.
        form = form_by_name(token, n)
        key = form_name(form) if form.family != "P" or n == 4 else "P_0"
        if key not in seen:
            seen.add(key)
            out.append(form)
    # Stable canonical order.
    order = {
        form_name(f) if not (f.family == "P" and n in (5, 6)) else "P_0": i
        for i, f in enumerate(_all_forms(n))
    }

    # Synthetic P has no canonical position; keep it first when present.
    def _key(f: CanonicalForm) -> tuple[int, str]:
        nm = form_name(f) if not (f.family == "P" and n in (5, 6)) else "P_0"
        return (order.get(nm, -1), nm)

    return tuple(sorted(out, key=_key))


def default_forms(n: int) -> tuple[CanonicalForm, ...]:
    """Default enumeration per Q1/Q2 (n=6: 2C+6TB; n=5: all 20; n=4: all 3)."""
    if n == 6:
        return tuple(f for f in _all_forms(6) if f.family in ("C", "TB"))
    if n == 5:
        return _all_forms(5)
    if n == 4:
        return _all_forms(4)
    raise ValueError(f"unsupported ring size {n}")


# Q3 alias table: old template name -> list of precise form names.
# Anchor-relative: each alias resolves to the form(s) whose ideal CP matches
# the old Cartesian template at anchor j0 (measured in proto:
# chair_A theta180 -> C_1; chair_B theta0 -> C_0; boat phi180 -> B_3;
# envelope_5/twist_5 phi180 -> E_5; planar_4 -> P_0; pucker_up sign-1 -> B-_0;
# pucker_down sign+1 -> B+_0; twist_boat_6 -> TB_0 nearest per S0.3).
_ALIAS_TO_FORMS: dict[str, tuple[str, ...]] = {
    "chair_A_6": ("C_1",),
    "chair_B_6": ("C_0",),
    "boat_6": ("B_3",),
    "twist_boat_6": ("TB_0",),
    "envelope_5": ("E_5",),
    "twist_5": ("E_5",),
    "planar_4": ("P_0",),
    "pucker_up_4": ("B-_0",),
    "pucker_down_4": ("B+_0",),
    "planar_5": ("P",),
}


def alias_templates_to_forms(templates: list[str], n: int) -> tuple[CanonicalForm, ...]:
    """Map old templates tokens to forms (Q3); twist_5 warns deprecated."""
    out: list[CanonicalForm] = []
    seen: set[str] = set()
    for raw in templates:
        name = str(raw)
        if name not in _ALIAS_TO_FORMS:
            raise KeyError(f"unknown template {name!r} for ring size {n}")
        if name == "twist_5":
            warnings.warn(
                "twist_5 is a deprecated alias for envelope E (it was always an envelope)",
                DeprecationWarning,
                stacklevel=3,
            )
        for precise in _ALIAS_TO_FORMS[name]:
            form = form_by_name(precise, n)
            key = form_name(form) if not (form.family == "P" and n in (5, 6)) else "P_0"
            # Size check: alias must fit this ring size.
            if form.n != n:
                raise KeyError(f"template_size_mismatch:{name}")
            if key not in seen:
                seen.add(key)
                out.append(form)
    order = {form_name(f): i for i, f in enumerate(_all_forms(n))}
    order["P_0"] = -1

    def _key(f: CanonicalForm) -> tuple[int, str]:
        nm = form_name(f) if not (f.family == "P" and n in (5, 6)) else "P_0"
        return (order.get(nm, -1), nm)

    return tuple(sorted(out, key=_key))


def _global_pairs_of_form(form: CanonicalForm, ring_order: list[int]) -> set[tuple[int, int]]:
    """Map form.zero_torsion_bonds (ring-position labels) to global pairs."""
    n = len(ring_order)
    out: set[tuple[int, int]] = set()
    for label in form.zero_torsion_bonds:
        try:
            a_s, b_s = str(label).split("-")
            a, b = int(a_s), int(b_s)
        except ValueError:
            continue
        if 0 <= a < n and 0 <= b < n:
            ga, gb = int(ring_order[a]), int(ring_order[b])
            out.add((ga, gb) if ga < gb else (gb, ga))
    # Synthetic P for n=5/6: zero list above uses consecutive labels which are
    # not middle-bond labels; instead treat every ring bond as zero.
    if form.family == "P" and form.n in (5, 6):
        for k in range(n):
            ga, gb = int(ring_order[k]), int(ring_order[(k + 1) % n])
            out.add((ga, gb) if ga < gb else (gb, ga))
    return out


def constrained_forms(
    n: int, pinned_global: set[tuple[int, int]], ring_order: list[int]
) -> tuple[CanonicalForm, ...]:
    """Z-coverage rule (R4): forms with Z subset of zero-torsion bonds.

    pinned_global are canonical (min,max) global pairs restricted to this
    ring. Empty Z -> (). If none covers all of Z, return those covering the
    most bonds (ties included). Stable canonical order.
    """
    if not pinned_global:
        return ()
    scored: list[tuple[CanonicalForm, int]] = []
    for form in _all_forms(n):
        covered = _global_pairs_of_form(form, ring_order)
        if pinned_global <= covered:
            scored.append((form, len(pinned_global)))
    if scored:
        return tuple(f for f, _ in sorted(scored, key=lambda t: (t[0].family, t[0].index)))
    # Degenerate: maximal coverage.
    best = -1
    cands: list[CanonicalForm] = []
    for form in _all_forms(n):
        covered = _global_pairs_of_form(form, ring_order)
        hit = len(pinned_global & covered)
        if hit > best:
            best = hit
            cands = [form]
        elif hit == best:
            cands.append(form)
    if best <= 0:
        return ()
    return tuple(sorted(cands, key=lambda f: (f.family, f.index)))


#: Contract-facing authoritative tables (precise names, stable order).
FORM_NAMES_BY_SIZE: dict[int, tuple[str, ...]] = {
    4: tuple(form_name(f) for f in canonical_forms(4)),
    5: tuple(form_name(f) for f in canonical_forms(5)),
    6: tuple(form_name(f) for f in canonical_forms(6)),
}
