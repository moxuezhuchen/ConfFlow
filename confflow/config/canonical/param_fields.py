#!/usr/bin/env python3

"""Param descriptor registry: the structural truth for core step parameters.

This module owns **structure**: which parameter keys exist for each step type,
their basic JSON shape, enum membership and which workflow versions accept them.
It does *not* own semantics — coercion, defaults, cross-field rules and runnable
invariants stay with the typed resolvers (the ``CalcStepParams`` factory and
``resolve_confgen_params``). The split is deliberate:

* descriptor registry -> the key set + structural JSON Schema,
* resolver            -> semantic meaning,
* validator           -> combines both.

Deriving the V3 parameter schema and the V2 "known key" sets from one registry
means the JSON Schema and the validator can never keep two drifting allow-lists.
The confgen vocabulary is derived from :mod:`confflow.shared.confgen_params`
(the resolver's own alias groups and simple keys) so there is still one source.

V2 keeps its open-bag tolerance: this registry only *defines* the known keys; it
does not make V2 reject unknown parameters. Only the V3 schema is strict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

from ...shared.confgen_params import CONFGEN_ALIAS_GROUPS, CONFGEN_SIMPLE_KEYS
from .types import ProgramName, TaskName

__all__ = [
    "ParamFieldDescriptor",
    "calc_param_fields",
    "confgen_keys",
    "confgen_param_fields",
    "param_properties",
    "v2_calc_keys",
    "v3_calc_keys",
]

JsonValueKind = Literal["string", "integer", "number", "boolean", "array", "object", "any"]

#: A descriptor's value may accept several JSON shapes; ``any`` accepts all .
KindSpec = JsonValueKind | tuple[JsonValueKind, ...]

_CALC = "calc"
_CONFGEN = "confgen"

_PROGRAM_VALUES: tuple[str, ...] = tuple(str(value) for value in get_args(ProgramName))
_TASK_VALUES: tuple[str, ...] = tuple(str(value) for value in get_args(TaskName))


@dataclass(frozen=True)
class ParamFieldDescriptor:
    """One core step parameter.

    ``value_kind`` is the accepted JSON shape (a single kind or a union). It is
    intentionally permissive where the resolvers accept several wire forms
    (e.g. ``"0.5"`` and ``0.5``); the point of the V3 schema is to make the *key*
    set strict, not to reject every alternate spelling. ``v3`` marks membership in
    the V3 runnable vocabulary — ``chk_from_step`` is V2-only because V3 promotes
    it to ``checkpoint.from_step``. ``required`` documents runnable-required
    fields (e.g. confgen ``chains``); no core parameter is *structurally*
    required, so it is ``False`` here and the runnable rule stays semantic.
    """

    key: str
    value_kind: KindSpec = "any"
    item_kind: JsonValueKind | None = None
    enum_values: tuple[str, ...] | None = None
    aliases: tuple[str, ...] = ()
    v3: bool = True
    required: bool = False


def _calc(
    key: str,
    kind: KindSpec,
    *,
    item: JsonValueKind | None = None,
    enum: tuple[str, ...] | None = None,
    v3: bool = True,
) -> ParamFieldDescriptor:
    return ParamFieldDescriptor(key=key, value_kind=kind, item_kind=item, enum_values=enum, v3=v3)


_FLAG = ("boolean", "integer", "string")
_NUMBER = ("number", "string")

#: The V2 calc parameter vocabulary, in the historical order. ``chk_from_step`` is
#: V2-only (V3 migrates it to ``checkpoint.from_step``).
_CALC_FIELDS: tuple[ParamFieldDescriptor, ...] = (
    _calc("iprog", "string", enum=_PROGRAM_VALUES),
    _calc("itask", "string", enum=_TASK_VALUES),
    _calc("keyword", "string"),
    _calc("gaussian_path", "string"),
    _calc("orca_path", "string"),
    _calc("cores_per_task", "integer"),
    _calc("total_memory", "string"),
    _calc("max_parallel_jobs", "integer"),
    _calc("charge", "integer"),
    _calc("multiplicity", "integer"),
    _calc("freeze", ("array", "string"), item="integer"),
    _calc("auto_clean", _FLAG),
    _calc("dedup_only", _FLAG),
    _calc("keep_all_topos", _FLAG),
    _calc("noH", _FLAG),
    _calc("rmsd_threshold", _NUMBER),
    _calc("energy_window", _NUMBER),
    _calc("energy_tolerance", _NUMBER),
    _calc("clean_params", ("object", "string")),
    _calc("clean_opts", ("object", "string")),
    _calc("imag", "integer"),
    _calc("max_conformers", "integer"),
    _calc("enable_dynamic_resources", _FLAG),
    _calc("resume_from_backups", _FLAG),
    _calc("max_wall_time_seconds", _NUMBER),
    _calc("delete_work_dir", _FLAG),
    _calc("sandbox_root", "string"),
    _calc("input_chk_dir", "string"),
    _calc("allowed_executables", ("string", "array"), item="string"),
    _calc("gaussian_write_chk", _FLAG),
    _calc("stop_check_interval_seconds", _NUMBER),
    _calc("ts_bond_atoms", ("array", "string"), item="integer"),
    _calc("ts_rescue_scan", _FLAG),
    _calc("ts_bond_drift_threshold", _NUMBER),
    _calc("ts_rmsd_threshold", _NUMBER),
    _calc("scan_coarse_step", _NUMBER),
    _calc("scan_fine_step", _NUMBER),
    _calc("scan_uphill_limit", "integer"),
    _calc("scan_max_steps", "integer"),
    _calc("scan_fine_half_window", _NUMBER),
    _calc("ts_rescue_keep_scan_dirs", _FLAG),
    _calc("ts_rescue_scan_backup", _FLAG),
    _calc("blocks", ("string", "object")),
    _calc("orca_maxcore", ("integer", "string")),
    _calc("maxcore", ("integer", "string")),
    _calc("gaussian_modredundant", ("string", "array"), item="string"),
    _calc("gaussian_link0", ("string", "array"), item="string"),
    _calc("ibkout", "integer"),
    _calc("chk_from_step", "string", v3=False),
)

#: Confgen kind per canonical key. Aliases share their canonical key's kind.
_CONFGEN_KINDS: dict[str, ParamFieldDescriptor] = {
    "angle_step": _calc("angle_step", ("integer", "string")),
    "clash_threshold": _calc("clash_threshold", _NUMBER),
    "add_bond": _calc("add_bond", ("string", "array")),
    "del_bond": _calc("del_bond", ("string", "array")),
    "no_rotate": _calc("no_rotate", ("string", "array")),
    "force_rotate": _calc("force_rotate", ("string", "array")),
    "optimize": _calc("optimize", _FLAG),
    "rotate_side": _calc("rotate_side", "string"),
    "bond_threshold": _calc("bond_threshold", _NUMBER),
    "chains": _calc("chains", ("array", "string"), item="string"),
    "chain_steps": _calc("chain_steps", ("array", "string")),
    "chain_angles": _calc("chain_angles", ("array", "string")),
    "workers": _calc("workers", ("integer", "string")),
}


def _confgen_fields() -> tuple[ParamFieldDescriptor, ...]:
    """Expand the resolver's alias groups into one descriptor per concrete key."""
    descriptors: list[ParamFieldDescriptor] = []
    for key in CONFGEN_SIMPLE_KEYS:
        template = _CONFGEN_KINDS[key]
        descriptors.append(
            ParamFieldDescriptor(
                key=key, value_kind=template.value_kind, item_kind=template.item_kind
            )
        )
    for canonical, aliases in CONFGEN_ALIAS_GROUPS.items():
        template = _CONFGEN_KINDS[canonical]
        for alias in aliases:
            descriptors.append(
                ParamFieldDescriptor(
                    key=alias,
                    value_kind=template.value_kind,
                    item_kind=template.item_kind,
                    aliases=tuple(name for name in aliases if name != alias),
                )
            )
    return tuple(descriptors)


_CONFGEN_FIELDS: tuple[ParamFieldDescriptor, ...] = _confgen_fields()


def _assert_vocabulary_is_complete() -> None:
    """Fail loudly if the resolver's confgen vocabulary grows without a descriptor."""
    declared = {descriptor.key for descriptor in _CONFGEN_FIELDS}
    expected = set(CONFGEN_SIMPLE_KEYS)
    for aliases in CONFGEN_ALIAS_GROUPS.values():
        expected.update(aliases)
    missing = expected - declared
    if missing:
        raise RuntimeError(f"confgen param descriptors are missing keys: {sorted(missing)}")


_assert_vocabulary_is_complete()


def calc_param_fields() -> tuple[ParamFieldDescriptor, ...]:
    """Return every calc parameter descriptor (V2 vocabulary, ``v3`` marks V3 membership)."""
    return _CALC_FIELDS


def confgen_param_fields() -> tuple[ParamFieldDescriptor, ...]:
    """Return every confgen parameter descriptor (aliases expanded)."""
    return _CONFGEN_FIELDS


def v2_calc_keys() -> frozenset[str]:
    """Return the V2 calc parameter key set (including ``chk_from_step``)."""
    return frozenset(descriptor.key for descriptor in _CALC_FIELDS)


def v3_calc_keys() -> frozenset[str]:
    """Return the V3 calc parameter key set (``chk_from_step`` promoted to checkpoint)."""
    return frozenset(descriptor.key for descriptor in _CALC_FIELDS if descriptor.v3)


def confgen_keys() -> frozenset[str]:
    """Return every confgen parameter key recognized by the resolver."""
    return frozenset(descriptor.key for descriptor in _CONFGEN_FIELDS)


def _render(descriptor: ParamFieldDescriptor) -> dict[str, Any]:
    if descriptor.enum_values is not None:
        return {"enum": list(descriptor.enum_values)}
    kinds = descriptor.value_kind
    if kinds == "any":
        return {}
    schema: dict[str, Any] = {"type": list(kinds) if isinstance(kinds, tuple) else kinds}
    if kinds == "array" or (isinstance(kinds, tuple) and "array" in kinds):
        if descriptor.item_kind is not None:
            schema["items"] = (
                {} if descriptor.item_kind == "any" else {"type": descriptor.item_kind}
            )
    return schema


def param_properties(step_type: str) -> dict[str, Any]:
    """Return the strict V3 ``params`` properties for one step type.

    The result is the JSON Schema ``properties`` mapping (not the full object
    schema); :mod:`confflow.config.canonical.schema` wraps it with
    ``additionalProperties: false``. Every key is a concrete accepted name, so
    legacy aliases (``chain``, ``max_workers`` …) remain legal while an unknown
    or misspelled key is rejected.
    """
    if step_type == _CALC:
        return {descriptor.key: _render(descriptor) for descriptor in _CALC_FIELDS if descriptor.v3}
    if step_type == _CONFGEN:
        return {descriptor.key: _render(descriptor) for descriptor in _CONFGEN_FIELDS}
    raise ValueError(f"unknown step type for param properties: {step_type!r}")
