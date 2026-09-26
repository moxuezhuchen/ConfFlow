#!/usr/bin/env python3

"""V4 named-structure resolution and compatibility (V4-5).

This module resolves the ``named_structures`` execution adapter inputs of one
work item into a typed reactant/product/optional-guess triple.  It performs
structural resolution plus semantic validation only:

- cardinality: exactly one structure per required slot, zero-or-one guess;
- group-key agreement: every present slot shares one non-empty group key;
- charge/multiplicity compatibility across the resolved slots.

It never renders native input, never maps atoms (see
:mod:`confflow.execution.atom_mapping`), and never guesses a pairing: any
ambiguity or mismatch fails closed with :class:`NamedStructureError`.

Slot names and ordering are owned by
:mod:`confflow.execution.execution_adapters`; TS-output lineage delegates to
:mod:`confflow.execution.output_identity`.  Nothing is redefined here.

Dependency rule: ``confflow.domain`` plus ``confflow.execution`` plus stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..domain.errors import DomainError
from ..domain.structure import StructureRecord
from ..domain.work_item import WorkItem
from .execution_adapters import GUESS_SLOT, NAMED_SLOT_ORDER, PRODUCT_SLOT, REACTANT_SLOT
from .output_identity import multi_parent_lineage

__all__ = [
    "NAMED_COMPATIBILITY_ERROR",
    "NAMED_GROUP_MISMATCH",
    "NAMED_STRUCTURE_AMBIGUOUS",
    "NAMED_STRUCTURE_MISSING",
    "NamedReactionInputs",
    "NamedStructureError",
    "qst_logical_key",
    "resolve_named_inputs",
    "ts_output_lineage",
    "validate_named_compatibility",
]

#: Cardinality failure: a required named slot carries zero structures.
NAMED_STRUCTURE_MISSING: Final[str] = "named_structure_missing"

#: Cardinality failure: a named slot carries more than one structure.
NAMED_STRUCTURE_AMBIGUOUS: Final[str] = "named_structure_ambiguous"

#: Pairing failure: present slots disagree on (or lack) the group key.
NAMED_GROUP_MISMATCH: Final[str] = "named_group_mismatch"

#: Semantic failure: charge/multiplicity disagree across resolved slots.
NAMED_COMPATIBILITY_ERROR: Final[str] = "named_compatibility_error"


class NamedStructureError(DomainError):
    """A named-structure slot cannot be resolved into reaction inputs.

    Parameters
    ----------
    message : str
        Human-readable failure description.
    code : str
        Machine-readable failure code (one of the ``NAMED_*`` constants).

    Attributes
    ----------
    code : str
        Machine-readable failure code carried for executor mapping.
    """

    def __init__(self, message: str, *, code: str) -> None:
        """Store *message* and the machine-readable *code*."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NamedReactionInputs:
    """Resolved named structures for one reaction work item.

    Parameters
    ----------
    reactant : StructureRecord
        The single reactant structure.
    product : StructureRecord
        The single product structure.
    guess : StructureRecord | None
        The single transition-state guess (QST3) or ``None`` (QST2).
    group_key : str
        The shared non-empty pairing key of every present slot.
    """

    reactant: StructureRecord
    product: StructureRecord
    guess: StructureRecord | None
    group_key: str


def _slot_records(work_item: WorkItem, slot: str) -> tuple[StructureRecord, ...]:
    """Return the structure records bound to *slot* (empty when unbound)."""
    if slot not in work_item.named_inputs.structures:
        return ()
    return tuple(work_item.named_inputs.structures[slot])


def _require_single(work_item: WorkItem, slot: str) -> StructureRecord:
    """Return the single structure on *slot* or raise a cardinality error.

    Parameters
    ----------
    work_item : WorkItem
        Item whose named structure ports are inspected.
    slot : str
        Named slot (``reactant``, ``product``, or ``guess``).

    Returns
    -------
    StructureRecord
        The single structure bound to *slot*.

    Raises
    ------
    NamedStructureError
        With ``named_structure_missing`` when the slot is empty and
        ``named_structure_ambiguous`` when it holds more than one record.
    """
    records = _slot_records(work_item, slot)
    if not records:
        raise NamedStructureError(
            f"work item {work_item.logical_key!r} has no structure on {slot!r} port",
            code=NAMED_STRUCTURE_MISSING,
        )
    if len(records) > 1:
        raise NamedStructureError(
            f"work item {work_item.logical_key!r} has {len(records)} structures "
            f"on {slot!r} port; exactly one is required",
            code=NAMED_STRUCTURE_AMBIGUOUS,
        )
    return records[0]


def resolve_named_inputs(work_item: WorkItem, *, require_guess: bool) -> NamedReactionInputs:
    """Resolve the named reaction inputs of *work_item*.

    Parameters
    ----------
    work_item : WorkItem
        Item carrying ``reactant``/``product``/optional ``guess`` ports.
    require_guess : bool
        When ``True`` (QST3) the ``guess`` slot must hold exactly one
        structure; when ``False`` (QST2) it must hold zero (``None``) or one.

    Returns
    -------
    NamedReactionInputs
        Reactant, product, guess, and their shared group key.

    Raises
    ------
    NamedStructureError
        ``named_structure_missing`` for an empty required slot,
        ``named_structure_ambiguous`` for a multi-record slot, or
        ``named_group_mismatch`` when the present slots do not share one
        non-empty group key.  A missing group on any slot mismatches; the
        key is never inferred.
    """
    reactant = _require_single(work_item, REACTANT_SLOT)
    product = _require_single(work_item, PRODUCT_SLOT)
    guess: StructureRecord | None
    if require_guess:
        guess = _require_single(work_item, GUESS_SLOT)
    else:
        guess_records = _slot_records(work_item, GUESS_SLOT)
        if len(guess_records) > 1:
            raise NamedStructureError(
                f"work item {work_item.logical_key!r} has {len(guess_records)} "
                f"structures on {GUESS_SLOT!r} port; at most one is allowed",
                code=NAMED_STRUCTURE_AMBIGUOUS,
            )
        guess = guess_records[0] if guess_records else None
    present = [reactant, product] if guess is None else [reactant, product, guess]
    keys = [record.group_key for record in present]
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != 1:
        raise NamedStructureError(
            f"work item {work_item.logical_key!r} slots do not share one group key",
            code=NAMED_GROUP_MISMATCH,
        )
    group_key = keys[0]
    assert isinstance(group_key, str) and group_key
    return NamedReactionInputs(reactant=reactant, product=product, guess=guess, group_key=group_key)


def validate_named_compatibility(
    resolved: NamedReactionInputs,
) -> tuple[int | None, int | None]:
    """Check charge/multiplicity agreement across the resolved slots.

    Parameters
    ----------
    resolved : NamedReactionInputs
        Reactant/product/guess triple from :func:`resolve_named_inputs`.

    Returns
    -------
    tuple[int | None, int | None]
        ``(charge, multiplicity)`` shared by every slot; ``(None, None)``
        only when every slot leaves the value unknown (the executor's
        charge gate then fires later).

    Raises
    ------
    NamedStructureError
        ``named_compatibility_error`` on mixed known/unknown values or on
        disagreement between known values.  Fails closed: a partially
        known charge or multiplicity is never defaulted.
    """
    present = (
        (resolved.reactant, resolved.product)
        if resolved.guess is None
        else (resolved.reactant, resolved.product, resolved.guess)
    )
    charges = [record.charge for record in present]
    if all(charge is None for charge in charges):
        charge: int | None = None
    elif any(charge is None for charge in charges):
        raise NamedStructureError(
            "named slots disagree on charge: partially unknown values are rejected",
            code=NAMED_COMPATIBILITY_ERROR,
        )
    elif len(set(charges)) != 1:
        raise NamedStructureError(
            "named slots disagree on charge",
            code=NAMED_COMPATIBILITY_ERROR,
        )
    else:
        charge = charges[0]
    multiplicities = [record.multiplicity for record in present]
    if all(value is None for value in multiplicities):
        multiplicity: int | None = None
    elif any(value is None for value in multiplicities):
        raise NamedStructureError(
            "named slots disagree on multiplicity: partially unknown values are rejected",
            code=NAMED_COMPATIBILITY_ERROR,
        )
    elif len(set(multiplicities)) != 1:
        raise NamedStructureError(
            "named slots disagree on multiplicity",
            code=NAMED_COMPATIBILITY_ERROR,
        )
    else:
        multiplicity = multiplicities[0]
    return charge, multiplicity


def qst_logical_key(step_id: str, group_key: str) -> str:
    """Return the logical key of one QST work item.

    Parameters
    ----------
    step_id : str
        Owning step id (non-empty).
    group_key : str
        Shared pairing key of the item's slots (non-empty).

    Returns
    -------
    str
        ``"<step id>:<group>"``, matching the assembly ``BY_GROUP_KEY``
        logical-key rule.

    Raises
    ------
    ValueError
        Raised when either argument is not a non-empty string.
    """
    if not isinstance(step_id, str) or not step_id:
        raise ValueError("step_id must be a non-empty string")
    if not isinstance(group_key, str) or not group_key:
        raise ValueError("group_key must be a non-empty string")
    return f"{step_id}:{group_key}"


def ts_output_lineage(
    resolved: NamedReactionInputs,
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Return ``(parent_ids, lineage_root_id, group_key)`` for a TS output.

    Parameters
    ----------
    resolved : NamedReactionInputs
        Reactant/product/guess triple from :func:`resolve_named_inputs`.

    Returns
    -------
    tuple
        Lineage from :func:`multi_parent_lineage` with parents in semantic
        slot order (reactant, product, guess when present).
    """
    by_slot = {
        REACTANT_SLOT: resolved.reactant,
        PRODUCT_SLOT: resolved.product,
        GUESS_SLOT: resolved.guess,
    }
    parents = tuple(by_slot[slot] for slot in NAMED_SLOT_ORDER if by_slot[slot] is not None)
    return multi_parent_lineage(parents)
