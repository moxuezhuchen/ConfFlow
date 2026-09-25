"""Pure producer-owned configuration contract documents.

Three contract versions exist and all stay valid:

``v1``
    the workflow schema, its digest, and the producer provenance block. This is
    what ``confflow config contract --json`` has always emitted and it keeps
    emitting unless a caller explicitly asks otherwise.
``v2``
    ``v1`` plus the editor manifest and the recipe catalog, each with its own
    digest. Adding members is backward compatible for every existing consumer,
    because an unknown top-level member is simply not read.
``v3`` (additive, opt-in via ``--version 3``)
    advertises Workflow V3 (``confflow.workflow.v3``, parse + execute), the V3
    document/fragment digests, the single ``workflow`` block (step identity and
    ID generator policy, ID step selector, explicit-input DAG, stable-ID
    checkpoint reference, nonsemantic annotations, extension policy), the
    structured-theory vocabulary and program/task mapping, the recipe-fragment
    policy, and the V3 editor manifest and V3 recipe catalog. v1/v2 documents
    are byte-identical to before.

The v1/v2 builders share one implementation of the common members, so a v2
document can never disagree with the v1 document about the workflow schema or
the producer block -- the difference between them is exactly the four added
members. v3 is built separately for the V3 workflow line and never alters v1/v2.

v3 key history: the prior closure published the identity/DAG surface as three
overlapping members (``step_grammar`` / ``step_identity`` / ``dag_semantics``)
plus duplicated extension keys (``extensions`` vs ``known_semantic_extensions``
/ ``unknown_extension_runnable_policy``) and two capability blocks
(``capabilities`` vs ``workflow_capabilities``). Those are collapsed into the
single authoritative ``workflow`` block; there is exactly one spelling of each
fact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .editor_manifest import (
    build_editor_manifest,
    build_editor_manifest_v3,
    editor_manifest_sha256,
    editor_manifest_sha256_v3,
    program_choices,
    task_choices,
    theory_dispersion_choices,
    theory_solvent_models,
)
from .execution_versions import CAPABILITIES
from .extensions import DEFAULT_EXTENSION_REGISTRY, EXTENSION_NAMESPACE_PATTERN
from .recipes import (
    RECIPE_STEP_ID_PATTERN,
    STEP_ID_ALPHABET,
    STEP_ID_MAX_ATTEMPTS,
    STEP_ID_SUFFIX_LENGTH,
    build_recipe_catalog,
    build_recipe_catalog_v3,
    recipe_catalog_sha256,
    recipe_catalog_sha256_v3,
)
from .schema import (
    WORKFLOW_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION_V3,
    WORKFLOW_V3_ID_PATTERN,
    SchemaProfile,
    workflow_fragment_schema_sha256_v3,
    workflow_json_schema,
    workflow_json_schema_v3,
    workflow_schema_sha256,
    workflow_schema_sha256_v3,
)
from .structured import STRUCTURED_THEORY_FIELDS

CONFIGURATION_CONTRACT_V1_SCHEMA = "confflow.configuration-contract.v1"
CONFIGURATION_CONTRACT_V2_SCHEMA = "confflow.configuration-contract.v2"
CONFIGURATION_CONTRACT_V3_SCHEMA = "confflow.configuration-contract.v3"

#: Historical alias, kept so existing imports and callers do not have to move.
#: It deliberately keeps meaning **v1**: that is what the name has always
#: described, and the default CLI output is still v1.
CONFIGURATION_CONTRACT_SCHEMA = CONFIGURATION_CONTRACT_V1_SCHEMA

CONFIGURATION_VALIDATION_SCHEMA = "confflow.configuration-validation.v1"

ContractBuilder = Callable[..., dict[str, Any]]


def build_configuration_contract_v1(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a deterministic v1 contract without runtime or workload probing."""
    return {
        "schema": CONFIGURATION_CONTRACT_V1_SCHEMA,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_schema_sha256": workflow_schema_sha256(),
        "workflow_schema": workflow_json_schema(),
        "producer": {
            "package": "confflow",
            "version": producer_version,
            "commit": producer_commit,
            "dirty": producer_dirty,
        },
        "validation_response_schema": CONFIGURATION_VALIDATION_SCHEMA,
    }


def build_configuration_contract_v2(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a v2 contract: v1 plus the editor manifest and the recipe catalog.

    Each artifact's digest is taken over the artifact alone, so an artifact can be
    published, cached or verified on its own without reproducing the envelope.
    """
    document = build_configuration_contract_v1(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )
    document["schema"] = CONFIGURATION_CONTRACT_V2_SCHEMA
    document["editor_manifest"] = build_editor_manifest()
    document["editor_manifest_sha256"] = editor_manifest_sha256()
    document["recipe_catalog"] = build_recipe_catalog()
    document["recipe_catalog_sha256"] = recipe_catalog_sha256()
    return document


def build_configuration_contract_v3(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a v3 contract: Workflow V3, the single workflow block, and V3 catalogs.

    The ``workflow`` member is the one authoritative workflow/identity/DAG/
    extensions structure: step-id grammar plus the opaque generator policy, the
    ID step selector, explicit-input DAG semantics, the stable-ID checkpoint
    reference, nonsemantic annotations, and the extension policy. Structured
    theory (fields, the single program/task mapping, the raw keyword escape
    hatch, registry-derived choices) lives under ``structured_theory``; the
    recipe-fragment policy (fragment profile, required fields, the instantiate
    entry point and rebase rule) under ``recipes``.
    """
    v3_capability = CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3]
    known_extensions = sorted(DEFAULT_EXTENSION_REGISTRY.namespaces())
    doc_schema = workflow_json_schema_v3(SchemaProfile.DOCUMENT)
    frag_schema = workflow_json_schema_v3(SchemaProfile.FRAGMENT)
    doc_sha256 = workflow_schema_sha256_v3()
    frag_sha256 = workflow_fragment_schema_sha256_v3()
    manifest = build_editor_manifest_v3()
    manifest_sha256 = editor_manifest_sha256_v3()
    catalog = build_recipe_catalog_v3()
    catalog_sha256 = recipe_catalog_sha256_v3()

    return {
        "schema": CONFIGURATION_CONTRACT_V3_SCHEMA,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION_V3,
        "workflow_schema_sha256": doc_sha256,
        "workflow_schema": doc_schema,
        "workflow_fragment_schema_sha256": frag_sha256,
        "workflow_fragment_schema": frag_schema,
        "producer": {
            "package": "confflow",
            "version": producer_version,
            "commit": producer_commit,
            "dirty": producer_dirty,
        },
        "validation_response_schema": CONFIGURATION_VALIDATION_SCHEMA,
        "workflow": {
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "parse": v3_capability.parse,
            "execute": v3_capability.execute,
            "document_schema_sha256": doc_sha256,
            "fragment_schema_sha256": frag_sha256,
            "step_selector": "id",
            "identity": {
                "grammar": WORKFLOW_V3_ID_PATTERN,
                "labels_display_only": True,
                "generator": {
                    "form": "opaque",
                    "pattern": RECIPE_STEP_ID_PATTERN,
                    "alphabet": STEP_ID_ALPHABET,
                    "length": STEP_ID_SUFFIX_LENGTH,
                    "max_attempts": STEP_ID_MAX_ATTEMPTS,
                    "sequential_ids_never_issued": True,
                },
            },
            "dag": {"inputs": "explicit_array", "inputs_required": True},
            "checkpoint": {
                "from_step": "stable_step_id_ref",
                "calc_only": True,
                "strict_ancestor_required": True,
            },
            "annotations": {"semantic": False},
            "extensions": {
                "known_namespaces": known_extensions,
                "namespace_pattern": EXTENSION_NAMESPACE_PATTERN,
                "unknown_runnable_policy": "error",
            },
        },
        "structured_theory": {
            "wire_member": "params.theory",
            "fields": list(STRUCTURED_THEORY_FIELDS),
            "program_task_mapping": (
                "calc.program and calc.task are the single program/task editors. "
                "The producer compiler writes the edited value to both the wire "
                "key (iprog/itask) and theory.program/theory.task, so the pair "
                "can never disagree; an override contradicting a pinned theory "
                "value fails instead of silently winning."
            ),
            "compiler": ("confflow.config.canonical.structured.compile_structured_calc"),
            "keyword": {
                "member": "params.keyword",
                "role": "verbatim_escape_hatch",
                "consistency": "theory_and_keyword_must_agree",
                "note": ("ConfFlow requires a non-empty keyword for a runnable calc step."),
            },
            "program_choices": program_choices(),
            "task_choices": task_choices(),
            "dispersion_choices": theory_dispersion_choices(),
            "solvent_models": theory_solvent_models(),
        },
        "recipes": {
            "document_profile": "fragment",
            "required_fields": (
                "Editor field ids the user must still fill (calc.program, "
                "calc.keyword, confgen.chains); always a subset of the recipe's "
                "exposed_fields. A fragment with unfilled required fields is "
                "not runnable."
            ),
            "instantiate": ("confflow.config.canonical.recipes.instantiate_recipe_v3"),
            "rebase_rule": (
                "instantiate allocates a fresh opaque id per template step, "
                "rewrites inputs and checkpoint.from_step through the old-to-new "
                "map, and sets every template root's inputs to attach_roots_to "
                "only when attach_roots_to is given."
            ),
        },
        "editor_manifest": manifest,
        "editor_manifest_sha256": manifest_sha256,
        "recipe_catalog": catalog,
        "recipe_catalog_sha256": catalog_sha256,
    }


def build_configuration_contract(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build the contract emitted by default, which is v1."""
    return build_configuration_contract_v1(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )


#: The builder for each contract version a caller may ask for. Single definition
#: of "which versions exist", so the CLI's accepted choices cannot drift from the
#: dispatch below.
CONFIGURATION_CONTRACT_BUILDERS: dict[int, ContractBuilder] = {
    1: build_configuration_contract_v1,
    2: build_configuration_contract_v2,
    3: build_configuration_contract_v3,
}

#: Private alias kept for backward compatibility; the public surface is
#: CONFIGURATION_CONTRACT_BUILDERS (``_BUILDERS`` is not exported).
_BUILDERS: dict[int, ContractBuilder] = CONFIGURATION_CONTRACT_BUILDERS


def build_configuration_contract_for_version(
    version: int,
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build the contract for an explicit version, or raise for an unknown one."""
    builder = CONFIGURATION_CONTRACT_BUILDERS.get(version)
    if builder is None:
        supported = ", ".join(str(item) for item in sorted(CONFIGURATION_CONTRACT_BUILDERS))
        raise ValueError(
            f"unsupported configuration contract version {version!r}; supported: {supported}"
        )
    return builder(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )


__all__ = [
    "CONFIGURATION_CONTRACT_BUILDERS",
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_CONTRACT_V1_SCHEMA",
    "CONFIGURATION_CONTRACT_V2_SCHEMA",
    "CONFIGURATION_CONTRACT_V3_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "ContractBuilder",
    "build_configuration_contract",
    "build_configuration_contract_for_version",
    "build_configuration_contract_v1",
    "build_configuration_contract_v2",
    "build_configuration_contract_v3",
]
