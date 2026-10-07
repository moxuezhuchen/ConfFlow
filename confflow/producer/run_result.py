#!/usr/bin/env python3

"""Producer-owned runtime run-result projection (V4 repair, worker J).

Builds the :data:`confflow.producer.contract.RESULT_MANIFEST_SCHEMA`
manifest from the REAL runtime objects only -- :class:`StepResult` /
``ArtifactSet`` outputs -- never from hand-built shapes:

* per-step ``id``/``status``/published ``digest`` plus the compiler
  ``semantic_digest``; counts and diagnostics come from the real
  ``StepResult`` (never ``"completed"``-as-digest, never filename-as-truth);
* top-level ``results`` with ``ResultRef`` identity (``result_id``/``kind``/
  ``subject``/``source`` provenance plus ``value_digest``/``identity_digest``);
* artifacts with ``role``/``subject``/``checksum``/safe run-relative
  ``locator`` plus a ``fetch`` handle (``run-relative:<locator>``).

R2.3a: the reaction-profile group projection (``confflow.analysis``
consumer) is retired with the analysis package; ``analyses`` is always
empty for retained steps.

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
from ..persistence.fsatomic import publish_bytes
from .contract import (
    RESULT_MANIFEST_SCHEMA,
    build_run_result_manifest,
    run_result_json_schema,
)

#: Durable manifest filename at the run root (mirrors the application).
RUN_RESULT_FILENAME = "run_result.json"


def result_ref_entry(record: Any, *, origin: str | None = None) -> dict[str, Any]:
    """Project one real ``ScientificResult`` onto its ResultRef wire entry.

    ``origin`` distinguishes the current generation's produced results
    (``"produced"``) from run-input references the analysis consumed
    (``"run_input"``), so the manifest's reference universe is explicit and
    every analysis citation can be resolved by a consumer.
    """
    if origin is not None and origin not in ("produced", "run_input"):
        raise ValueError(f"unknown result-ref origin {origin!r}")
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
    if origin is not None:
        entry["origin"] = origin
    return entry


def build_result_reference_index(
    step_results: tuple[Any, ...],
    *,
    run_input_results: Any = (),
) -> dict[str, dict[str, Any]]:
    """Build the authoritative ResultRef universe for manifest publication.

    The universe contains exactly two populations:

    - the current generation's actually published ``ScientificResults``
      (``origin="produced"``), so a stale or superseded attempt's id can
      never resolve; and
    - legitimate run-input ``ResultRefs`` (``origin="run_input"``) that an
      analysis may cite.

    Every entry carries the identity a consumer needs (kind, subject,
    source step/work item, digests).  Duplicate or identity-less ids fail
    closed: a reference index with an ambiguous identity must never be
    published.
    """
    index: dict[str, dict[str, Any]] = {}
    for step_result in step_results:
        for record in step_result.results:
            entry = result_ref_entry(record, origin="produced")
            result_id = entry["result_id"]
            if result_id in index:
                raise ValueError(
                    f"duplicate result_id {result_id!r} in the reference universe "
                    f"(step {step_result.step_id!r})"
                )
            index[result_id] = entry
    for record in _iter_run_input_results(run_input_results):
        entry = result_ref_entry(record, origin="run_input")
        result_id = entry["result_id"]
        if result_id in index:
            raise ValueError(
                f"run-input result_id {result_id!r} collides with an already "
                "indexed result; refusing an ambiguous reference"
            )
        index[result_id] = entry
    return index


def result_ref_entries(
    step_results: tuple[Any, ...],
    *,
    run_input_results: Any = (),
) -> list[dict[str, Any]]:
    """Ordered ResultRef wire entries: produced results, then run-input refs.

    The ordering is deterministic (step order, then result order, then the
    named run-input collections' own order), so manifest bytes never depend
    on dict iteration order.
    """
    entries = [
        result_ref_entry(record, origin="produced")
        for step_result in step_results
        for record in step_result.results
    ]
    entries.extend(
        result_ref_entry(record, origin="run_input")
        for record in _iter_run_input_results(run_input_results)
    )
    return entries


def _iter_run_input_results(run_input_results: Any) -> tuple[Any, ...]:
    """Flatten named run-input result collections into one tuple."""
    if run_input_results is None:
        return ()
    records: list[Any] = []
    if isinstance(run_input_results, Mapping):
        collections = run_input_results.values()
    else:
        collections = (run_input_results,)
    for collection in collections:
        if collection is None:
            continue
        try:
            records.extend(tuple(collection))
        except TypeError as exc:
            raise ValueError(f"run-input results are not iterable: {exc}") from exc
    return tuple(records)


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
    return entry


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
) -> dict[str, Any]:
    """Build a manifest from real ``StepResult`` objects and verify its schema.

    R2.3a: ``analyses`` is always empty (the ``analysis`` executor and its
    reaction-profile grouping are retired); retained steps never produced
    group entries, so their manifest bytes are unchanged.
    """
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
    target = os.path.join(run_root, RUN_RESULT_FILENAME)
    publish_bytes(target, canonical_json_bytes(manifest))
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
    "RUN_RESULT_FILENAME",
    "artifact_entry",
    "build_result_reference_index",
    "build_runtime_manifest",
    "check_manifest_against_contract",
    "manifest_digest",
    "publish_manifest_atomically",
    "result_ref_entries",
    "result_ref_entry",
    "step_entry",
    "verify_artifact_bytes",
    "verify_manifest_on_disk",
]
