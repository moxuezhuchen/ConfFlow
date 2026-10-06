#!/usr/bin/env python3

"""Producer checkpoint/Hessian reuse helper (Phase 6 input simplification).

:func:`wire_checkpoint_reuse` adds one semantic checkpoint edge to a strict
V4 document: the target step gains a ``checkpoint`` input binding fed by the
source step's ``artifacts`` output with the ``checkpoint`` role selector,
promised with binding cardinality ``one``.  It is a pure authoring helper:
every rule it applies comes from an existing authority, and it creates no
second runtime.

Authorities consulted (imported, never copied):

- :mod:`confflow.execution.registry` -- executor contracts, the ``standard``
  adapter's ``checkpoint`` input port (``optional``/``by_subject`` with the
  ``checkpoint`` role) and the calculation ``artifacts`` output port roles;
- :func:`confflow.programs.gaussian.rendering.resolve_write_chk` -- whether
  the source natively writes a checkpoint file;
- :func:`confflow.programs.gaussian.path.parse_irc_route` -- IRC option
  validation, direction-vote conflicts, and unknown-option refusal;
- :func:`confflow.programs.gaussian.energy_semantics.unsupported_method_finding`
  -- method families whose final energy ConfFlow cannot publish;
- :mod:`confflow.workflow.v4.graph` -- binding cardinality strengthening
  (``optional`` ports accept a required ``one`` binding), selector/role
  checks, and cycle detection;
- :func:`confflow.workflow.v4.compiler.compile_workflow` -- the full
  document is compiled through the existing compiler, so cycles, unknown
  steps, ORCA/QST misuse, and every semantic rule reject exactly as
  hand-written documents do;
- :class:`confflow.programs.gaussian.adapter.GaussianProgramAdapter` (in
  tests) -- the current renderer proving ``%Chk``/``%OldChk`` output and
  the scope cardinality of the checkpoint edge.

Consumption promise
-------------------
The emitted binding declares cardinality ``one`` (which the graph
authority permits on the ``optional`` checkpoint port).  At assembly the
``one`` contract requires exactly one subject-matched artifact, so a
missing checkpoint fails through the existing ``artifact_flow`` guard
(``artifact_subject_missing``) instead of silently running without restart
data.  Atom matching stays with the existing ``by_subject`` pairing: the
helper never sets a pairing, so no path or order guessing is introduced.

Method compatibility (no chemistry guessing)
--------------------------------------------
The helper retains each calculation's native method verbatim and records
the source relationship in the digest-covered binding (bindings are part
of the definition digest).  Two checks are decidable at authoring time
without guessing chemistry and therefore refuse loudly:

- charge/spin compatibility: explicit step overrides and declared bound
  structure state propagate along structure bindings. Known unequal values
  refuse. Unknown values are compatible only when the target inherits the
  checkpoint source's state without a field override. Run defaults remain
  fallbacks and never replace inherited state. An explicit target override
  against an unknown source fails closed;
- effective route method: the route keywords, after removing only the
  helper-managed job-type items (``Opt(...)``, ``IRC(...)``, ``Freq``,
  standalone ``SP``), must agree token-for-token, and the remaining native scientific
  payload (basis/ECP/extra sections, ``modredundant``, atom mapping --
  everything except the helper-managed ``keyword``/``write_chk``/
  ``link0`` keys) must agree exactly; any other difference is a known
  differing method for Hessian reuse and is refused by default.  The
  comparison is conservative exact equality: no chemistry equivalence is
  ever guessed.

Everything else is deferred to runtime and documented as such: structure
atom counts (structures are runtime data; ``by_subject`` pairing plus the
executor's atom-sequence gate own them), basis/method equivalence beyond
token identity, and checkpoint file presence (owned by staging and the
``one`` cardinality guard).  A user-intended method change (for example a
deliberate Hessian transfer across levels of theory) is accepted only
with the explicit advanced override ``allow_method_change=True``, which
is recorded in the authoring-layer provenance annotation on the target
step (annotations are digest-excluded by design; the binding itself stays
digest-covered).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ..domain.binding import PortKind
from ..domain.errors import DomainError, InvalidBindingError
from ..execution.contracts import ExecutorCapability
from ..execution.registry import ExecutionRegistry, RegistryLookupError, default_registry

# G1a: 11 canonical route constants + trio moved to
# confflow.programs.gaussian.checkpoint_policy (same objects, bidirectional mirror).
# G1b: Gaussian stages delegate to the same policy module (data values only).
from ..programs.gaussian.checkpoint_policy import (  # noqa: F401
    _FREQ_TOKEN_RE,
    _IRC_ITEM_RE,
    _IRC_MANAGED_RE,
    _LINK0_CHECKPOINT_RE,
    _OPT_ASSIGN_RE,
    _OPT_BARE_RE,
    _OPT_PAREN_RE,
    _QST_TOKEN_RE,
    _RCFC_CONFLICTS,
    _READFC_CONFLICTS,
    _SP_MANAGED_RE,
    _add_irc_rcfc,
    _add_opt_option,
    _strip_managed_items,
    check_native_payload_cores,
    check_route_cores,
    check_target_link0_core,
    check_unsupported_method,
    ensure_source_write_chk,
    require_artifact_checkpoint_role,
    require_gaussian_program,
    require_no_qst,
    require_standard_adapter,
    require_standard_checkpoint_role,
)
from ..programs.gaussian.checkpoint_policy import (
    native_scientific_core as _policy_native_scientific_core,
)
from ..workflow.v4.compiler import compile_workflow
from ..workflow.v4.schema import CalculationModel

__all__ = [
    "CHECKPOINT_REUSE_VERSION",
    "REUSE_MODES",
    "wire_checkpoint_reuse",
]

#: Version stamp recorded on every checkpoint-reuse provenance annotation.
CHECKPOINT_REUSE_VERSION = "confflow.producer.checkpoints.v1"

#: Supported reuse modes.
REUSE_MODES: tuple[str, ...] = ("checkpoint", "readfc", "rcfc")

#: Annotation key carrying the authoring-layer reuse provenance on the
#: target step.  Annotations are digest-excluded by design; the
#: digest-covered binding carries the scientific relationship.
_REUSE_ANNOTATION_KEY = "confflow.checkpoint_reuse"

# G1a mirror: 11 canonical route constants live in
# confflow.programs.gaussian.checkpoint_policy; re-exported above (same objects).


def _refuse(message: str) -> DomainError:
    """Build the refusal error for a checkpoint-reuse violation."""
    return InvalidBindingError(message)


def _raw_steps(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the mutable step mappings of *document*, or refuse."""
    steps = document.get("steps")
    if not isinstance(steps, list) or not steps:
        raise _refuse("the document declares no steps")
    for step in steps:
        if not isinstance(step, Mapping):
            raise _refuse("every step must be a mapping")
    return [step for step in steps if isinstance(step, dict)]


def _find_step(steps: list[dict[str, Any]], step_id: str, *, role: str) -> dict[str, Any]:
    """Return the step mapping with *step_id*, or refuse."""
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise _refuse(f"unknown {role} step {step_id!r}")


def _calculation_block(step: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ``calculation`` block of *step*, or refuse."""
    block = step.get("calculation")
    if not isinstance(block, dict):
        raise _refuse(
            f"step {step.get('id')!r} is not a calculation step; "
            "checkpoint reuse needs calculation steps on both ends"
        )
    return block


def _program_of(block: Mapping[str, Any], step_id: str) -> str:
    """Return the declared program of a calculation block, or refuse."""
    program = block.get("program")
    if not isinstance(program, str) or not program.strip():
        raise _refuse(f"step {step_id!r} declares no calculation program")
    return program


def _native_of(block: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    """Return the mutable native mapping of a calculation block, or refuse."""
    native = block.get("native")
    if not isinstance(native, dict):
        raise _refuse(f"step {step_id!r} declares no native mapping")
    return native


def _keyword_of(native: Mapping[str, Any], step_id: str) -> str:
    """Return the native route keyword of *native*, or refuse."""
    keyword = native.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise _refuse(f"step {step_id!r} declares no native route keyword")
    return keyword


def _check_gaussian_sides(
    registry: ExecutionRegistry,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> tuple[str, str]:
    """Enforce the Gaussian/standard-adapter/IRC requirements, or refuse.

    Returns the ``(source_keyword, target_keyword)`` pair for the route
    layer.  Both ends must resolve through the program registry to
    Gaussian, both must use the ``standard`` execution adapter (which owns
    the ``checkpoint`` input port), and neither route may carry a QST
    item: the Gaussian adapter declares no checkpoint vocabulary for QST
    rendering and refuses staged-but-unused success at render time.

    Generic shape/registry I/O stays here in original row order; Gaussian
    decisions delegate to ``checkpoint_policy`` stages with data values.
    """
    for step in (source, target):
        step_id = str(step.get("id"))
        block = _calculation_block(step)
        program = _program_of(block, step_id)
        try:
            adapter = registry.resolve_program(program)
        except RegistryLookupError as exc:
            raise _refuse(f"step {step_id!r} uses an unknown program: {exc}") from exc
        require_gaussian_program(
            step_id=step_id, program=program, program_name=adapter.program_name
        )
        require_standard_adapter(
            step_id=step_id, execution_adapter=block.get("execution_adapter", "standard")
        )
    source_block = _calculation_block(source)
    target_block = _calculation_block(target)
    source_keyword = _keyword_of(
        _native_of(source_block, str(source.get("id"))), str(source.get("id"))
    )
    target_keyword = _keyword_of(
        _native_of(target_block, str(target.get("id"))), str(target.get("id"))
    )
    for step_id, keyword in (
        (str(source.get("id")), source_keyword),
        (str(target.get("id")), target_keyword),
    ):
        require_no_qst(step_id=step_id, keyword=keyword)
    try:
        standard_adapter = registry.resolve_adapter("standard")
    except RegistryLookupError as exc:
        raise _refuse(f"the 'standard' execution adapter is not registered: {exc}") from exc
    checkpoint_port = standard_adapter.input_port("checkpoint")
    require_standard_checkpoint_role(
        port_present=checkpoint_port is not None,
        has_checkpoint_role=(
            "checkpoint" in tuple(checkpoint_port.roles) if checkpoint_port is not None else False
        ),
    )
    try:
        contract = registry.resolve_executor(source_block.get("executor", "calculation"))
    except (RegistryLookupError, ValueError) as exc:
        raise _refuse(f"cannot resolve the calculation executor contract: {exc}") from exc
    artifacts_port = contract.output_port("artifacts")
    require_artifact_checkpoint_role(
        port_present=artifacts_port is not None,
        has_checkpoint_role=(
            "checkpoint" in tuple(artifacts_port.roles) if artifacts_port is not None else False
        ),
    )
    return source_keyword, target_keyword


def _check_write_chk(source: Mapping[str, Any]) -> None:
    """Require the source to write a checkpoint file, then make it explicit."""
    step_id = str(source.get("id"))
    native = _native_of(_calculation_block(source), step_id)
    ensure_source_write_chk(step_id=step_id, native=native)
    native["write_chk"] = True


def _check_target_link0(target: Mapping[str, Any]) -> None:
    """Refuse a target that hand-manages checkpoint Link0 directives."""
    step_id = str(target.get("id"))
    native = _native_of(_calculation_block(target), step_id)
    check_target_link0_core(step_id=step_id, link0_value=native.get("link0"))


# G1a mirror: _add_opt_option lives in
# confflow.programs.gaussian.checkpoint_policy; re-exported above (same object).


# G1a mirror: _add_irc_rcfc lives in
# confflow.programs.gaussian.checkpoint_policy; re-exported above (same object).


# G1a mirror: _strip_managed_items lives in
# confflow.programs.gaussian.checkpoint_policy; re-exported above (same object).


#: Native keys managed by this helper and therefore excluded from the
#: scientific-payload comparison: ``keyword`` is compared through the
#: managed job-type strip, ``write_chk`` is set explicitly on the source
#: by this helper, and ``link0`` carries renderer-owned checkpoint paths
#: plus user Link0 lines that never change the science.  Every other
#: native key (basis/ECP/extra sections, ``modredundant``, atom mapping)
#: is scientific payload and must agree exactly.
_NATIVE_MANAGED_KEYS = frozenset({"keyword", "write_chk", "link0"})


def _native_scientific_core(native: Mapping[str, Any]) -> dict[str, Any]:
    """Return the scientific payload of *native* minus helper-managed keys."""
    return _policy_native_scientific_core(native, managed_keys=_NATIVE_MANAGED_KEYS)


def _step_override_value(step: Mapping[str, Any], field: str) -> Any:
    """Return the explicit step scientific override for *field*, if declared.

    Only ``calculation.overrides`` and ``confgen.overrides`` count.
    Transform steps carry no scientific overrides.  ``None`` means no
    explicit declaration (unknown at this layer).
    """
    for block_name in ("calculation", "confgen"):
        block = step.get(block_name)
        if isinstance(block, Mapping):
            overrides = block.get("overrides")
            if isinstance(overrides, Mapping) and overrides.get(field) is not None:
                return overrides.get(field)
    return None


def _raw_inputs_by_name(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return the raw run-input declarations keyed by name."""
    inputs = document.get("inputs")
    if not isinstance(inputs, Mapping):
        return {}
    return {str(name): value for name, value in inputs.items() if isinstance(value, Mapping)}


def _structure_input_ports_raw(
    step: Mapping[str, Any], registry: ExecutionRegistry
) -> set[str] | None:
    """Return STRUCTURE-kind input port names for *step*, or ``None`` if unknown.

    Port facts come from the existing execution registry only (executor
    contract plus the resolved execution adapter when one is required).
    ``None`` means the ports cannot be proven (unknown executor/adapter),
    so callers fail closed instead of guessing.
    """
    try:
        executor_name = step.get("executor")
        if not isinstance(executor_name, str) or not executor_name:
            return None
        capability = ExecutorCapability(executor_name)
        contract = registry.resolve_executor(capability)
    except Exception:
        return None
    try:
        if contract.requires_adapter:
            adapter_name = None
            calculation = step.get("calculation")
            if isinstance(calculation, Mapping):
                adapter_name = calculation.get(
                    "execution_adapter",
                    CalculationModel.model_fields["execution_adapter"].default,
                )
            if not isinstance(adapter_name, str) or not adapter_name:
                return None
            adapter = registry.resolve_adapter(adapter_name)
            ports = adapter.input_ports
        else:
            ports = contract.input_ports
    except Exception:
        return None
    try:
        return {port.name for port in ports if getattr(port, "kind", None) is PortKind.STRUCTURE}
    except Exception:
        return None


def _producer_output_is_structure_raw(
    producer: Mapping[str, Any], port_name: Any, registry: ExecutionRegistry
) -> bool | None:
    """Return whether *producer*'s output *port_name* carries structures.

    ``True`` means definitely a structure output (contract output port is
    STRUCTURE-kind and the producer's result profile provides structures).
    ``False`` means definitely not (unknown port, non-structure kind, or a
    profile without structures) so callers skip the edge.  ``None`` means
    the fact cannot be proven (unresolvable contract/profile), so callers
    fail closed instead of guessing.  Mirrors the registry port authority
    used by semantic validation without duplicating the runtime resolver.
    """
    if not isinstance(port_name, str) or not port_name:
        return False
    try:
        executor_name = producer.get("executor")
        if not isinstance(executor_name, str) or not executor_name:
            return None
        contract = registry.resolve_executor(ExecutorCapability(executor_name))
    except Exception:
        return None
    try:
        output_ports = contract.output_ports
    except Exception:
        return None
    matched: bool | None = None
    try:
        for port in output_ports:
            if port.name == port_name:
                matched = port.kind is PortKind.STRUCTURE
                break
        else:
            return False
    except Exception:
        return None
    if matched is not True:
        return False
    profile_name = "standard"
    try:
        calculation = producer.get("calculation")
        if isinstance(calculation, Mapping):
            candidate = calculation.get("result_profile")
            if isinstance(candidate, str) and candidate:
                profile_name = candidate
        profile = registry.resolve_profile(profile_name)
    except Exception:
        return None
    try:
        return bool(getattr(profile, "provides_structures", False))
    except Exception:
        return None


def _declared_lineage_value(
    document: Mapping[str, Any],
    steps_by_id: Mapping[str, Mapping[str, Any]],
    inputs_by_name: Mapping[str, Mapping[str, Any]],
    step_id: str,
    field: str,
    registry: ExecutionRegistry,
    seen: frozenset[str],
) -> tuple[bool, Any]:
    """Return the bound-lineage DECLARED VALUE for *field* on *step_id*.

    Conservative proof only (no chemistry guessing, no second runtime):

    - an explicit step scientific override wins when present;
    - otherwise every bound STRUCTURE input root must prove the same
      declared value: run-input declarations carrying the field explicitly,
      or producer steps proven recursively through their own override or
      structure lineage (transform/ConfGen passthrough propagates when the
      registry declares structure ports on both ends);
    - only STRUCTURE input bindings and STRUCTURE output ports are
      followed (artifact/checkpoint edges are skipped, never chosen);
    - cycles, disabled producers, unknown producers/ports/contracts, and
      ambiguous differing values across roots are unknown (not picked).

    Run ``global.scientific_defaults`` are deliberately NOT consulted
    here: a default is a fallback for rendering, never proof of the
    absolute actual charge when a runtime-imported structure may carry its
    own value.  ``(False, None)`` means unknown/deferred-or-refused by the
    caller; ``(True, value)`` is a proven declared value.
    """
    step = steps_by_id.get(step_id)
    if step is None:
        return False, None
    if step.get("enabled") is False:
        return False, None
    override = _step_override_value(step, field)
    if override is not None:
        return True, override
    if step_id in seen:
        return False, None
    extended = seen | {step_id}
    ports = _structure_input_ports_raw(step, registry)
    if ports is None:
        return False, None
    bindings = step.get("bindings")
    if not isinstance(bindings, Mapping):
        return False, None
    relevant = False
    candidates: list[Any] = []
    for port_name, binding in bindings.items():
        if port_name not in ports:
            continue
        if not isinstance(binding, Mapping):
            return False, None
        source = binding.get("source")
        if not isinstance(source, Mapping):
            return False, None
        if isinstance(source.get("run"), str) and source.get("run"):
            declaration = inputs_by_name.get(str(source.get("run")))
            if declaration is None:
                return False, None
            if str(declaration.get("kind")) != PortKind.STRUCTURE.value:
                return False, None
            relevant = True
            value = declaration.get(field)
            if value is None:
                return False, None
            candidates.append(value)
        elif isinstance(source.get("step"), str) and source.get("step"):
            producer_id = str(source.get("step"))
            producer = steps_by_id.get(producer_id)
            if producer is None or producer.get("enabled") is False:
                return False, None
            flag = _producer_output_is_structure_raw(producer, source.get("port"), registry)
            if flag is False:
                continue
            if flag is None:
                return False, None
            relevant = True
            known, value = _declared_lineage_value(
                document, steps_by_id, inputs_by_name, producer_id, field, registry, extended
            )
            if not known:
                return False, None
            candidates.append(value)
        else:
            return False, None
    if not relevant:
        return False, None
    first = candidates[0]
    for candidate in candidates[1:]:
        if candidate != first:
            return False, None
    return True, first


def _effective_declared_state(
    document: Mapping[str, Any],
    steps_by_id: Mapping[str, Mapping[str, Any]],
    inputs_by_name: Mapping[str, Mapping[str, Any]],
    step: Mapping[str, Any],
    field: str,
    registry: ExecutionRegistry,
) -> tuple[bool, Any]:
    """Return the effective declared state (override > lineage, no globals)."""
    step_id = str(step.get("id"))
    return _declared_lineage_value(
        document, steps_by_id, inputs_by_name, step_id, field, registry, frozenset()
    )


def _all_structure_roots_reach_source(
    steps_by_id: Mapping[str, Mapping[str, Any]],
    inputs_by_name: Mapping[str, Mapping[str, Any]],
    consumer_id: str,
    source_id: str,
    field: str,
    registry: ExecutionRegistry,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Return whether every STRUCTURE root of *consumer* reaches *source*.

    Symbolic same-lineage identity proof: the target shares the exact
    produced records only when all of its bound STRUCTURE inputs derive
    transitively from the checkpoint source through STRUCTURE ports, with
    no overriding scientific field on the path and no independent run-input
    or foreign-step root.  Artifact/checkpoint edges are never followed.
    Unknown ports, disabled/unknown producers, cycles, overrides on the
    path, and any independent root all fail closed (``False``).
    """
    if consumer_id == source_id:
        return True
    if consumer_id in seen:
        return False
    consumer = steps_by_id.get(consumer_id)
    if consumer is None or consumer.get("enabled") is False:
        return False
    if _step_override_value(consumer, field) is not None:
        return False
    extended = seen | {consumer_id}
    ports = _structure_input_ports_raw(consumer, registry)
    if ports is None:
        return False
    bindings = consumer.get("bindings")
    if not isinstance(bindings, Mapping):
        return False
    relevant = False
    for port_name, binding in bindings.items():
        if port_name not in ports:
            continue
        if not isinstance(binding, Mapping):
            return False
        source = binding.get("source")
        if not isinstance(source, Mapping):
            return False
        if isinstance(source.get("run"), str) and source.get("run"):
            return False
        if isinstance(source.get("step"), str) and source.get("step"):
            producer_id = str(source.get("step"))
            producer = steps_by_id.get(producer_id)
            if producer is None or producer.get("enabled") is False:
                return False
            flag = _producer_output_is_structure_raw(producer, source.get("port"), registry)
            if flag is False:
                continue
            if flag is None:
                return False
            relevant = True
            if not _all_structure_roots_reach_source(
                steps_by_id, inputs_by_name, producer_id, source_id, field, registry, extended
            ):
                return False
        else:
            return False
    return relevant


def _check_charge_spin_compatibility(
    document: Mapping[str, Any],
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    registry: ExecutionRegistry,
) -> None:
    """Refuse unverified Hessian charge/spin differences (fail closed).

    Conservative policy, identical for every checkpoint mode; the advanced
    ``allow_method_change`` override never bypasses charge/spin proof:

    - known unequal declared values refuse (explicit overrides, run-input
      declarations, or propagated upstream overrides);
    - a source override inherited verbatim by the target accepts;
    - an unknown concrete state accepts ONLY when symbolic
      bound-STRUCTURE lineage proves the same inherited state (the target
      transitively consumes the source structures with no overriding
      field, so ``by_subject`` pairs the exact produced records);
    - a target explicit override against an unknown source fails closed;
    - mixed/ambiguous roots never pick one state (fail closed);
    - run globals are fallback only and never infer the absolute actual
      charge when a runtime structure may carry its own value.
    """
    source_id = str(source.get("id"))
    target_id = str(target.get("id"))
    steps_by_id: dict[str, Mapping[str, Any]] = {}
    raw_steps = document.get("steps")
    if isinstance(raw_steps, list):
        for entry in raw_steps:
            if isinstance(entry, Mapping) and isinstance(entry.get("id"), str):
                steps_by_id[str(entry.get("id"))] = entry
    inputs_by_name = _raw_inputs_by_name(document)
    for field, label in (("charge", "charge"), ("multiplicity", "multiplicity")):
        source_override = _step_override_value(source, field)
        target_override = _step_override_value(target, field)
        source_known, source_value = _effective_declared_state(
            document, steps_by_id, inputs_by_name, source, field, registry
        )
        target_known, target_value = _effective_declared_state(
            document, steps_by_id, inputs_by_name, target, field, registry
        )
        if source_override is not None and target_override is not None:
            if source_override != target_override:
                raise _refuse(
                    f"source step {source_id!r} declares {label} {source_override!r} but "
                    f"target step {target_id!r} declares {label} {target_override!r}: "
                    "checkpoint reuse across charge states is refused"
                    if label == "charge"
                    else f"source step {source_id!r} declares {label} {source_override!r} but "
                    f"target step {target_id!r} declares {label} {target_override!r}: "
                    "checkpoint reuse across spin states is refused"
                )
            continue
        if source_override is not None and target_override is None:
            if target_known and target_value == source_override:
                continue
            raise _refuse(
                f"source step {source_id!r} declares {label} {source_override!r} but "
                f"target step {target_id!r} does not prove the same inherited state: "
                "checkpoint reuse across charge states is refused"
                if label == "charge"
                else f"source step {source_id!r} declares {label} {source_override!r} but "
                f"target step {target_id!r} does not prove the same inherited state: "
                "checkpoint reuse across spin states is refused"
            )
        if source_override is None and target_override is not None:
            if source_known and source_value == target_override:
                continue
            raise _refuse(
                f"source step {source_id!r} does not prove {label} {target_override!r} "
                f"declared by target step {target_id!r}: "
                "checkpoint reuse across charge states is refused"
                if label == "charge"
                else f"source step {source_id!r} does not prove {label} {target_override!r} "
                f"declared by target step {target_id!r}: "
                "checkpoint reuse across spin states is refused"
            )
        if source_known and target_known:
            if source_value != target_value:
                raise _refuse(
                    f"source step {source_id!r} declares {label} {source_value!r} but "
                    f"target step {target_id!r} declares {label} {target_value!r}: "
                    "checkpoint reuse across charge states is refused"
                    if label == "charge"
                    else f"source step {source_id!r} declares {label} {source_value!r} but "
                    f"target step {target_id!r} declares {label} {target_value!r}: "
                    "checkpoint reuse across spin states is refused"
                )
            continue
        if _all_structure_roots_reach_source(
            steps_by_id, inputs_by_name, target_id, source_id, field, registry
        ):
            continue
        raise _refuse(
            f"source step {source_id!r} and target step {target_id!r} do not prove "
            f"the same inherited {label} state: "
            "checkpoint reuse across charge states is refused"
            if label == "charge"
            else f"source step {source_id!r} and target step {target_id!r} do not prove "
            f"the same inherited {label} state: "
            "checkpoint reuse across spin states is refused"
        )


def _check_method_compatibility(
    document: Mapping[str, Any],
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    source_keyword: str,
    target_keyword: str,
    *,
    allow_method_change: bool,
    registry: ExecutionRegistry,
) -> None:
    """Refuse known charge/spin/method incompatibilities (fail closed)."""
    _check_charge_spin_compatibility(document, source, target, registry)
    source_id = str(source.get("id"))
    target_id = str(target.get("id"))
    check_unsupported_method(target_id=target_id, target_keyword=target_keyword)
    check_route_cores(
        source_id=source_id,
        target_id=target_id,
        source_keyword=source_keyword,
        target_keyword=target_keyword,
        allow_method_change=allow_method_change,
    )
    source_native = _native_of(_calculation_block(source), source_id)
    target_native = _native_of(_calculation_block(target), target_id)
    check_native_payload_cores(
        source_id=source_id,
        target_id=target_id,
        source_native=source_native,
        target_native=target_native,
        allow_method_change=allow_method_change,
        managed_keys=frozenset(_NATIVE_MANAGED_KEYS),
    )


def _compile_diagnostics_text(compiled: Any) -> str:
    """Render compiler diagnostics for a refusal message."""
    parts: list[str] = []
    for item in compiled.diagnostics:
        reason = item.details.get("reason") if hasattr(item, "details") else None
        parts.append(f"{item.code}[{reason}]: {item.message}")
    return "; ".join(parts)


def wire_checkpoint_reuse(
    document: Mapping[str, Any],
    target_step_id: str,
    source_step_id: str,
    mode: str = "checkpoint",
    *,
    allow_method_change: bool = False,
    registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    """Add one semantic checkpoint edge to a strict V4 document.

    Parameters
    ----------
    document :
        Raw V4 document mapping (the strict wire shape the compiler
        accepts).  It is never mutated; a deep copy is wired and returned.
    target_step_id :
        Step consuming the checkpoint (gains the ``checkpoint`` binding).
    source_step_id :
        Step producing the checkpoint (its ``artifacts`` output with the
        ``checkpoint`` role selector feeds the edge).
    mode :
        ``"checkpoint"`` wires restart data only; ``"readfc"`` additionally
        inserts the ``ReadFC`` option into the target's ``Opt`` route item;
        ``"rcfc"`` additionally inserts the ``RCFC`` vote into the target's
        ``IRC`` route item.  The mode is user scientific intent and must be
        passed explicitly by the caller.
    allow_method_change :
        Advanced explicit override.  When ``False`` (default), a known
        differing effective method between source and target is refused.
        When ``True``, the intended method change is accepted and recorded
        in the authoring-layer provenance annotation on the target step.
    registry :
        Execution registry every contract check resolves against.  Defaults
        to the shared default registry.

    Returns
    -------
    dict
        The copied strict V4 document with the ``checkpoint`` binding
        (cardinality ``one``), the explicit source ``write_chk`` record,
        the mode route edit, and the reuse provenance annotation.

    Raises
    ------
    InvalidBindingError
        On unknown steps, self-links, pre-existing checkpoint bindings,
        non-Gaussian programs, non-``standard`` adapters, QST routes,
        disabled ``write_chk`` sources, user-managed checkpoint Link0,
        route conflicts, charge/spin/method incompatibilities, or a
        compiler rejection (cycles, cardinality, and every other semantic
        rule) of the wired document.
    """
    if not isinstance(document, Mapping):
        raise _refuse("the document must be a mapping")
    if not isinstance(target_step_id, str) or not target_step_id:
        raise _refuse("the target step id must be a non-empty string")
    if not isinstance(source_step_id, str) or not source_step_id:
        raise _refuse("the source step id must be a non-empty string")
    if mode not in REUSE_MODES:
        raise _refuse(f"unknown reuse mode {mode!r}; expected one of {list(REUSE_MODES)}")
    if not isinstance(allow_method_change, bool):
        raise _refuse("'allow_method_change' must be a boolean")

    active = registry if registry is not None else default_registry()
    wired = copy.deepcopy(dict(document))
    steps = _raw_steps(wired)
    if target_step_id == source_step_id:
        raise _refuse("a step must not consume its own checkpoint")
    target = _find_step(steps, target_step_id, role="target")
    source = _find_step(steps, source_step_id, role="source")
    for step in (target, source):
        if step.get("enabled") is False:
            raise _refuse(
                f"step {step.get('id')!r} is disabled: checkpoint reuse needs "
                "enabled steps on both ends"
            )
    target_bindings = target.get("bindings")
    if not isinstance(target_bindings, dict):
        raise _refuse(f"target step {target_step_id!r} declares no bindings mapping")
    if "checkpoint" in target_bindings:
        raise _refuse(
            f"target step {target_step_id!r} already declares a 'checkpoint' "
            "binding; existing bindings are never silently overwritten"
        )

    source_keyword, target_keyword = _check_gaussian_sides(active, source, target)
    _check_write_chk(source)
    _check_target_link0(source)
    _check_target_link0(target)
    _check_method_compatibility(
        wired,
        source,
        target,
        source_keyword,
        target_keyword,
        allow_method_change=allow_method_change,
        registry=active,
    )

    target_native = _native_of(_calculation_block(target), target_step_id)
    if mode == "readfc":
        target_native["keyword"] = _add_opt_option(target_keyword, "ReadFC", step_id=target_step_id)
    elif mode == "rcfc":
        target_native["keyword"] = _add_irc_rcfc(target_keyword, step_id=target_step_id)

    target_bindings["checkpoint"] = {
        "source": {
            "step": source_step_id,
            "port": "artifacts",
            "select": {"role": "checkpoint"},
        },
        "cardinality": "one",
    }
    annotations = target.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
        target["annotations"] = annotations
    annotations[_REUSE_ANNOTATION_KEY] = {
        "source_step": source_step_id,
        "mode": mode,
        "allow_method_change": allow_method_change,
        "version": CHECKPOINT_REUSE_VERSION,
    }

    compiled = compile_workflow(wired, registry=active)
    if not compiled.ok:
        raise _refuse(
            "the wired document is rejected by the compiler: " + _compile_diagnostics_text(compiled)
        )
    return wired
