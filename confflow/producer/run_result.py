#!/usr/bin/env python3

"""Producer-owned runtime run-result projection (V4 repair, worker J).

Builds the :data:`confflow.producer.contract.RESULT_MANIFEST_SCHEMA`
manifest from the REAL runtime objects only -- :class:`StepResult` /
``ArtifactSet`` / ``Analysis`` outputs -- never from hand-built shapes:

* per-step ``id``/``status``/published ``digest`` plus the compiler
  ``semantic_digest`` and the per-step ``result_digest`` over emitted
  result identity digests; counts and diagnostics come from the real
  ``StepResult`` (never ``"completed"``-as-digest, never filename-as-truth);
* top-level ``results`` with ``ResultRef`` identity (``result_id``/``kind``/
  ``subject``/``source`` provenance plus ``value_digest``/``identity_digest``);
* artifacts with ``role``/``subject``/``checksum``/safe run-relative
  ``locator`` plus a ``fetch`` handle (``run-relative:<locator>``);
* analysis entries as capability refs for analysis steps plus
  reaction-profile group entries (``group_key``/TS/endpoints/selected
  ``ResultRef`` ids/energies+Gibbs/barriers/assignment/provenance) when
  reaction analysis outputs are supplied.

Publication is atomic and durable: canonical JSON bytes are written to a
temp file, fsynced, ``os.replace``-d onto ``run_result.json``, the directory
is fsynced, and the bytes are re-read from disk and verified (schema +
digest equality) before returning.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

from ..domain.canonical import canonical_json_bytes, canonical_sha256
from .contract import (
    ANALYSIS_REACTION_PROFILE_CONTRACT,
    RESULT_MANIFEST_SCHEMA,
    build_run_result_manifest,
    run_result_json_schema,
)

#: Durable manifest filename at the run root (mirrors the application).
RUN_RESULT_FILENAME = "run_result.json"

_TMP_COUNTER = 0


def _next_tmp_suffix() -> str:
    global _TMP_COUNTER
    _TMP_COUNTER += 1
    return f"{os.getpid()}.{_TMP_COUNTER}"


def result_ref_entry(record: Any) -> dict[str, Any]:
    """Project one real ``ScientificResult`` onto its ResultRef wire entry."""
    entry: dict[str, Any] = {
        "result_id": record.result_id,
        "kind": record.kind,
        "subject_structure_id": record.subject_structure_id,
        "source_step_id": record.source_step_id,
        "source_work_item_id": record.source_work_item_id,
        "producer_digest": getattr(record, "producer_digest", None),
        "value_digest": record.value_digest,
        "identity_digest": record.identity_digest,
    }
    if not entry["result_id"]:
        raise ValueError("refusing to project a result without result_id (legacy record)")
    return entry


def artifact_entry(record: Any) -> dict[str, Any]:
    """Project one real ``ArtifactRef`` onto its manifest wire entry."""
    locator = record.locator
    path = locator.path if locator is not None else None
    checksum = record.checksum
    if not path:
        raise ValueError(f"refusing artifact {record.id!r} without a run-relative locator")
    if not checksum or not str(checksum).startswith("sha256:"):
        raise ValueError(f"refusing artifact {record.id!r} without a sha256 checksum")
    if "/" in str(checksum) or ".." in str(path).split("/"):
        raise ValueError(f"refusing artifact {record.id!r} with an unsafe locator")
    entry: dict[str, Any] = {
        "role": record.role,
        "checksum": str(checksum).lower(),
        "locator": path,
    }
    if record.subject_structure_id:
        entry["subject"] = record.subject_structure_id
    entry["fetch"] = f"run-relative:{path}"
    return entry


def step_entry(
    result: Any,
    *,
    published_digest: str,
    semantic_digest: str | None = None,
) -> dict[str, Any]:
    """Project one real ``StepResult`` onto its manifest step entry."""
    if not published_digest.startswith("sha256:"):
        raise ValueError(f"step {result.step_id!r} published digest is not a sha256 digest")
    if published_digest.split(":", 1)[1] in ("completed", "partial", "failed", "cancelled"):
        raise ValueError(f"step {result.step_id!r} digest must never be a status string")
    summary = result.summary.thaw() if hasattr(result.summary, "thaw") else dict(result.summary)
    entry: dict[str, Any] = {
        "id": result.step_id,
        "status": result.status.value,
        "digest": published_digest.lower(),
        "counts": {
            "completed": int(summary.get("completed", 0)),
            "failed": int(summary.get("failed", 0)),
            "cancelled": int(summary.get("cancelled", 0)),
        },
        "diagnostics": [
            {
                "code": item.code,
                "severity": item.severity.value,
                "message": item.message,
                "step_id": item.step_id,
                "field_path": item.field_path,
            }
            for item in result.diagnostics
        ],
    }
    provenance_digest = None
    provenance = getattr(result, "provenance", None)
    if provenance is not None:
        provenance_digest = provenance.step_semantic_digest
    resolved_semantic = semantic_digest or provenance_digest
    if resolved_semantic is not None:
        if not str(resolved_semantic).startswith("sha256:"):
            raise ValueError(f"step {result.step_id!r} semantic digest is not a sha256 digest")
        entry["semantic_digest"] = str(resolved_semantic).lower()
    identity_digests = sorted(
        {
            record.identity_digest
            for record in result.results
            if getattr(record, "identity_digest", None)
        }
    )
    if identity_digests:
        entry["result_digest"] = (
            "sha256:" + hashlib.sha256(canonical_json_bytes(identity_digests)).hexdigest()
        )
    return entry


def reaction_group_dict(
    *,
    group_key: str,
    ts_structure_id: str | None,
    forward_endpoint_id: str | None,
    reverse_endpoint_id: str | None,
    source_result_ids: list[str],
    energies: dict[str, Any],
    barriers: dict[str, Any],
    assignment: Any,
    step_id: str,
    capability: str = "reaction_profile",
    provenance: dict[str, Any] | None = None,
    endpoint_assignment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one reaction-profile group entry from validated analysis data."""
    if not group_key or not group_key.strip():
        raise ValueError("reaction group entry requires a group_key")
    cited = [str(item) for item in source_result_ids]
    if not cited or any(not item.strip() for item in cited):
        raise ValueError(f"group {group_key!r} cites no source result ids")
    entry: dict[str, Any] = {
        "capability": capability,
        "step_id": step_id,
        "group_key": group_key,
        "ts_structure_id": ts_structure_id,
        "forward_endpoint_id": forward_endpoint_id,
        "reverse_endpoint_id": reverse_endpoint_id,
        "source_result_ids": sorted(cited),
        "energies": dict(energies),
        "barriers": dict(barriers),
        "assignment": assignment,
        "provenance": dict(provenance or {}),
    }
    if endpoint_assignment is not None:
        entry["endpoint_assignment"] = dict(endpoint_assignment)
    return entry


def reaction_group_entry(
    *,
    group_key: str,
    ts_structure_id: str | None,
    forward_endpoint_id: str | None,
    reverse_endpoint_id: str | None,
    source_results: list[Any],
    energies: dict[str, Any],
    barriers: dict[str, Any],
    assignment: Any,
    step_id: str,
    capability: str = "reaction_profile",
    provenance: dict[str, Any] | None = None,
    endpoint_assignment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one reaction-profile group entry from real analysis outputs.

    Source identity is the producer-scoped ``ResultRef`` id (``result_id``),
    never a value digest: two sources with equal values stay
    distinguishable.  ``assignment`` carries the producer's marker verbatim;
    when the analysis ran under an explicit endpoint assignment,
    *endpoint_assignment* repeats that mapping verbatim (the default stays
    pure path direction).
    """
    source_ids = []
    for record in source_results:
        rid = getattr(record, "result_id", None)
        if not rid:
            raise ValueError(
                f"group {group_key!r} cites a result without ResultRef identity "
                "(result_id); refusing to project unstamped analysis output"
            )
        source_ids.append(rid)
    return reaction_group_dict(
        group_key=group_key,
        ts_structure_id=ts_structure_id,
        forward_endpoint_id=forward_endpoint_id,
        reverse_endpoint_id=reverse_endpoint_id,
        source_result_ids=source_ids,
        energies=energies,
        barriers=barriers,
        assignment=assignment,
        step_id=step_id,
        capability=capability,
        provenance=provenance,
        endpoint_assignment=endpoint_assignment,
    )


#: Analysis result kind carrying the per-group PES payload.  The literal
#: mirrors ``confflow.analysis.reaction.KIND_REACTION_PROFILE`` without
#: importing the analysis package (producer never depends on it).
KIND_REACTION_PROFILE: str = "reaction_profile"


def project_analysis_groups(step_results: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Project rich reaction-group entries from real analysis StepResults.

    Scans every ``StepResult`` for ``reaction_profile`` scientific results
    and projects one manifest group entry per payload, solely from the
    actual ``Analysis`` outputs: ``group_key``, the TS/forward/reverse
    subject ids from the payload ``nodes`` (TS falling back to the
    result's own subject), the verbatim ``electronic_energy``/``gibbs_energy``
    entries plus ``barriers``, the assignment marker with the explicit
    endpoint mapping repeated verbatim when present, and the analysis's own
    ``source_result_ids`` carried through verbatim.  No science is
    recomputed here: energies, barriers, assignments, and citations are
    copied verbatim from the profile payload, and a profile without usable
    citations fails closed instead of inventing identity.  In full
    step-sourced flows the cited ids coincide with published manifest
    results; analyses over run-input reference results cite those inputs'
    ``ResultRef`` ids.
    """
    groups: list[dict[str, Any]] = []
    for step_result in step_results:
        for record in step_result.results:
            if getattr(record, "kind", None) != KIND_REACTION_PROFILE:
                continue
            value = getattr(record, "value", None)
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"step {step_result.step_id!r} carries a non-mapping "
                    "reaction_profile value; refusing to project"
                )
            payload = dict(value)
            group_key = payload.get("group_key")
            if not isinstance(group_key, str) or not group_key.strip():
                raise ValueError(
                    f"step {step_result.step_id!r} carries a reaction_profile "
                    "value without a group_key; refusing to project"
                )
            nodes = payload.get("nodes")
            nodes = dict(nodes) if isinstance(nodes, Mapping) else {}
            ts_id = nodes.get("ts") or getattr(record, "subject_structure_id", None)
            forward_id = nodes.get("forward")
            reverse_id = nodes.get("reverse")
            energies: dict[str, Any] = {}
            for key in ("electronic_energy", "gibbs_energy", "relative"):
                section = payload.get(key)
                if isinstance(section, Mapping):
                    energies[key] = dict(section)
            if "electronic_energy" not in energies or "gibbs_energy" not in energies:
                raise ValueError(
                    f"group {group_key!r} carries no electronic/Gibbs entries; "
                    "refusing to project an energy-less profile"
                )
            barriers = payload.get("barriers")
            barriers = dict(barriers) if isinstance(barriers, Mapping) else {}
            if not barriers:
                raise ValueError(f"group {group_key!r} carries no barriers; refusing to project")
            raw_sources = payload.get("source_result_ids") or []
            if not isinstance(raw_sources, list) or not raw_sources:
                raise ValueError(f"group {group_key!r} cites no source result ids")
            cited = [str(item) for item in raw_sources]
            if any(not item.strip() for item in cited):
                raise ValueError(
                    f"group {group_key!r} cites an unusable source result id; "
                    "refusing to project"
                )
            endpoint_assignment = payload.get("endpoint_assignment")
            provenance = {
                "contract": ANALYSIS_REACTION_PROFILE_CONTRACT,
                "step_id": step_result.step_id,
                "group_key": group_key,
                "subject_structure_id": ts_id,
                "energy_model": (
                    dict(payload["energy_model"])
                    if isinstance(payload.get("energy_model"), Mapping)
                    else payload.get("energy_model")
                ),
                "fallback_used": (
                    dict(payload["fallback_used"])
                    if isinstance(payload.get("fallback_used"), Mapping)
                    else payload.get("fallback_used", {})
                ),
                "source_step_id": getattr(record, "source_step_id", None),
            }
            groups.append(
                reaction_group_dict(
                    group_key=group_key,
                    ts_structure_id=ts_id,
                    forward_endpoint_id=forward_id,
                    reverse_endpoint_id=reverse_id,
                    source_result_ids=cited,
                    energies=energies,
                    barriers=barriers,
                    assignment=payload.get("assignment", "unassigned"),
                    step_id=step_result.step_id,
                    provenance=provenance,
                    endpoint_assignment=(
                        dict(endpoint_assignment)
                        if isinstance(endpoint_assignment, Mapping)
                        else None
                    ),
                )
            )
    groups.sort(key=lambda entry: str(entry["group_key"]))
    return groups


def build_runtime_manifest(
    *,
    run_id: str,
    status: str,
    definition_digest: str,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
    step_results: tuple[Any, ...],
    published_digests: dict[str, str],
    semantic_digests: dict[str, str] | None = None,
    plan: Any | None = None,
    group_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a manifest from real ``StepResult`` objects and verify its schema."""
    import jsonschema

    semantic_digests = semantic_digests or {}
    steps: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    seen_result_ids: set[str] = set()
    for result in step_results:
        published = published_digests.get(result.step_id)
        if published is None:
            raise ValueError(
                f"step {result.step_id!r} has no durable publication; "
                "manifest publication requires every step result to be published"
            )
        steps.append(
            step_entry(
                result,
                published_digest=published,
                semantic_digest=semantic_digests.get(result.step_id),
            )
        )
        for record in result.results:
            entry = result_ref_entry(record)
            if entry["result_id"] in seen_result_ids:
                raise ValueError(f"duplicate result_id {entry['result_id']!r} across steps")
            seen_result_ids.add(entry["result_id"])
            results.append(entry)
        for artifact in result.artifacts:
            artifacts.append(artifact_entry(artifact))
    analyses: list[dict[str, Any]] = []
    if plan is not None:
        for planned in plan.steps:
            if getattr(planned.executor, "value", planned.executor) == "analysis":
                analyses.append(
                    {
                        "capability": str(getattr(planned.executor, "value", planned.executor)),
                        "step_id": planned.step_id,
                    }
                )
    for group in group_entries or []:
        if "group_key" not in group:
            raise ValueError("analysis group entry without group_key")
        analyses.append(dict(group))
    manifest = build_run_result_manifest(
        run_id=run_id,
        status=status,
        definition_digest=definition_digest,
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
        steps=steps,
        analyses=analyses,
        artifacts=artifacts,
        results=results,
    )
    try:
        jsonschema.validate(instance=manifest, schema=run_result_json_schema())
    except Exception as exc:
        raise ValueError(f"run-result manifest fails its schema: {exc}") from exc
    if manifest["content_schema"] != RESULT_MANIFEST_SCHEMA:
        raise ValueError("manifest carries the wrong content_schema")
    if not artifacts and any(len(tuple(result.results)) for result in step_results):
        raise ValueError("manifest with results but no artifacts is refused")
    return manifest


def verify_manifest_on_disk(manifest: dict[str, Any], run_root: str) -> dict[str, Any]:
    """Re-read the durable manifest and verify it matches *manifest*."""
    import jsonschema

    target = os.path.join(run_root, RUN_RESULT_FILENAME)
    with open(target, "rb") as handle:
        raw = handle.read()
    reread = json.loads(raw.decode("utf-8"))
    if canonical_json_bytes(reread) != canonical_json_bytes(manifest):
        raise ValueError("durable manifest bytes differ from the published manifest")
    try:
        jsonschema.validate(instance=reread, schema=run_result_json_schema())
    except Exception as exc:
        raise ValueError(f"durable manifest fails its schema: {exc}") from exc
    if not isinstance(reread, dict):
        raise ValueError("durable manifest is not a mapping")
    return dict(reread)


def publish_manifest_atomically(manifest: dict[str, Any], run_root: str) -> dict[str, Any]:
    """Atomically publish *manifest* and re-read it back from disk."""
    import jsonschema

    try:
        jsonschema.validate(instance=manifest, schema=run_result_json_schema())
    except Exception as exc:
        raise ValueError(f"run-result manifest fails its schema: {exc}") from exc
    os.makedirs(run_root, exist_ok=True)
    target = os.path.join(run_root, RUN_RESULT_FILENAME)
    tmp_path = f"{target}.tmp.{_next_tmp_suffix()}"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(canonical_json_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    try:
        dir_fd = os.open(run_root, os.O_RDONLY)
    except OSError:
        dir_fd = None
    if dir_fd is not None:
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
    return verify_manifest_on_disk(manifest, run_root)


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Return the canonical SHA-256 of a manifest mapping."""
    return canonical_sha256(manifest)


def verify_artifact_bytes(manifest: dict[str, Any], run_root: str) -> dict[str, str]:
    """Verify every manifest artifact checksum against bytes under *run_root*.

    Returns the verified ``{locator: checksum}`` mapping. Raises
    ``ValueError`` when an artifact has no bytes or its checksum does not
    match (corruption is a structured refusal, never a silent skip).
    """
    verified: dict[str, str] = {}
    for artifact in manifest.get("artifacts", []):
        locator = artifact.get("locator")
        claimed = artifact.get("checksum")
        if not locator or not claimed:
            raise ValueError(f"artifact entry {artifact!r} carries no locator/checksum")
        if ".." in str(locator).split("/") or str(locator).startswith("/"):
            raise ValueError(f"artifact locator {locator!r} is not run-relative")
        target = os.path.join(run_root, str(locator))
        try:
            with open(target, "rb") as handle:
                payload = handle.read()
        except FileNotFoundError as exc:
            raise ValueError(f"artifact {locator!r} has no bytes under {run_root!r}") from exc
        actual = "sha256:" + hashlib.sha256(payload).hexdigest()
        if actual != str(claimed).lower():
            raise ValueError(
                f"artifact {locator!r} checksum does not match its bytes "
                f"(claimed {claimed!r}, computed {actual!r})"
            )
        verified[str(locator)] = actual
    if not verified:
        raise ValueError("manifest carries no verifiable artifacts")
    return verified


def check_manifest_against_contract(
    manifest: dict[str, Any],
    contract_envelope: dict[str, Any],
    definition_digest: str,
) -> dict[str, Any]:
    """Verify manifest provenance and workflow digest against the contract run.

    Raises ``ValueError`` when the manifest provenance (package/version)
    does not match the contract producer, or when either the manifest's
    ``definition_digest`` or the expected *definition_digest* disagrees.
    """
    expected_producer = contract_envelope.get("producer", {})
    actual_producer = manifest.get("provenance", {})
    for key in ("package", "version"):
        expected = expected_producer.get(key)
        observed = actual_producer.get(key)
        if expected is None or observed is None:
            continue
        if str(expected) != str(observed):
            raise ValueError(
                f"manifest provenance {key} {observed!r} does not match "
                f"contract producer {expected!r}"
            )
    if manifest.get("definition_digest") != definition_digest:
        raise ValueError(
            f"manifest definition_digest {manifest.get('definition_digest')!r} "
            f"does not match the validated digest {definition_digest!r}"
        )
    return manifest


__all__ = [
    "KIND_REACTION_PROFILE",
    "RUN_RESULT_FILENAME",
    "artifact_entry",
    "build_runtime_manifest",
    "check_manifest_against_contract",
    "manifest_digest",
    "project_analysis_groups",
    "publish_manifest_atomically",
    "reaction_group_dict",
    "reaction_group_entry",
    "result_ref_entry",
    "step_entry",
    "verify_artifact_bytes",
    "verify_manifest_on_disk",
]
