#!/usr/bin/env python3

"""Regression checks for the versioned release workflow gates."""

from __future__ import annotations

from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"
JOBDESK_CONTRACT_WORKFLOW = WORKFLOW.with_name("jobdesk-contract.yml")
PAIRED_COMPATIBILITY_WORKFLOW = WORKFLOW.with_name("paired-jobdesk-compatibility.yml")
RELEASE_DOC = Path(__file__).parents[1] / "docs" / "RELEASE.md"
README = Path(__file__).parents[1] / "README.md"
RELEASE_LOCK = Path(__file__).parents[1] / "release" / "confflow-2.1.5-py312-linux-x86_64.lock"
RELEASE_MANIFEST = RELEASE_LOCK.with_suffix(".SHA256SUMS")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_v215_release_metadata_and_runtime_inputs_are_consistent():
    repository = Path(__file__).parents[1]
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "2.1.5"
    assert RELEASE_LOCK.is_file()
    assert RELEASE_MANIFEST.is_file()
    assert "confflow-2.1.5-py312-linux-x86_64.lock" in RELEASE_LOCK.read_text(encoding="utf-8")
    assert not (repository / "release" / "confflow-2.1.4-py312-linux-x86_64.lock").exists()
    assert not (repository / "release" / "confflow-2.1.4-py312-linux-x86_64.SHA256SUMS").exists()
    assert 'capabilities.version != "2.1.5"' in JOBDESK_CONTRACT_WORKFLOW.read_text(
        encoding="utf-8"
    )
    assert 'capabilities.version != "2.1.5"' in PAIRED_COMPATIBILITY_WORKFLOW.read_text(
        encoding="utf-8"
    )


def test_release_workflow_is_valid_yaml_with_tag_only_trigger_and_ordered_gates():
    definition = yaml.load(_workflow_text(), Loader=yaml.BaseLoader)

    assert definition["on"] == {"push": {"tags": ["v*"]}}
    steps = definition["jobs"]["build"]["steps"]
    names = [step["name"] for step in steps]
    assert names.index("Verify tag identity and release preconditions") < names.index(
        "Attest final wheel build provenance"
    )
    assert names.index("Cryptographically verify attestation identity") < names.index(
        "Publish immutable GitHub release"
    )
    assert names.index("Publish immutable GitHub release") < names.index(
        "Verify immutable release, tag, assets, and downloaded hashes"
    )


def test_release_workflow_derives_runtime_inputs_from_project_version():
    workflow = _workflow_text()

    assert 'EXPECTED_VERSION="${EXPECTED_TAG#v}"' in workflow
    assert 'test "$EXPECTED_VERSION" = "$PROJECT_VERSION"' in workflow
    tag_type_check = 'test "$(git cat-file -t "$EXPECTED_TAG")" = "tag"'
    assert tag_type_check in workflow
    assert workflow.index(tag_type_check) < workflow.index('test -z "$(git status --porcelain)"')
    assert 'RELEASE_LOCK="release/confflow-${PROJECT_VERSION}-py312-linux-x86_64.lock"' in workflow
    assert (
        'RELEASE_MANIFEST="release/confflow-${PROJECT_VERSION}-py312-linux-x86_64.SHA256SUMS"'
        in workflow
    )
    assert 'tags:\n      - "v*"' in workflow
    assert "workflow_dispatch" not in workflow
    assert "confflow-2.1.3" not in workflow


def test_release_workflow_verifies_pip_names_and_publishes_runtime_inputs():
    workflow = _workflow_text()

    assert "python -m pip download" in workflow
    assert "--require-hashes" in workflow
    assert 'cp -- "$RELEASE_MANIFEST" "$WHEELHOUSE/SHA256SUMS"' in workflow
    assert "validate_dependency_inputs" in workflow
    assert 'cp -- "$RELEASE_LOCK" "dist/$(basename "$RELEASE_LOCK")"' in workflow
    assert 'cp -- "$RELEASE_MANIFEST" "dist/$(basename "$RELEASE_MANIFEST")"' in workflow
    assert '"dependency_lock_filename": lock.name' in workflow
    assert '"wheelhouse_manifest_filename": wheelhouse_manifest.name' in workflow
    assert "sha256sum --check SHA256SUMS" in workflow


def test_release_workflow_binds_event_local_and_remote_annotated_tag_identity():
    workflow = _workflow_text()

    assert "workflow_dispatch" not in workflow
    assert "EXPECTED_TAG: ${{ github.ref_name }}" in workflow
    assert 'LOCAL_PEELED_SHA="$(git rev-parse "$EXPECTED_TAG^{commit}")"' in workflow
    assert 'test "$GITHUB_REF" = "refs/tags/$EXPECTED_TAG"' in workflow
    assert 'test "$GITHUB_SHA" = "$LOCAL_PEELED_SHA"' in workflow
    assert "git/ref/tags/${EXPECTED_TAG}" in workflow
    assert "git/tags/${REMOTE_TAG_OBJECT_SHA}" in workflow
    assert 'test "$REMOTE_TAG_TYPE" = "tag"' in workflow
    assert 'test "$REMOTE_PEELED_SHA" = "$LOCAL_PEELED_SHA"' in workflow
    assert "POST_PEELED_SHA" in workflow
    assert 'test "$POST_PEELED_SHA" = "$LOCAL_PEELED_SHA"' in workflow


def test_release_workflow_cryptographically_verifies_attestation_identity():
    workflow = _workflow_text()

    assert "gh attestation verify" in workflow
    assert '--bundle "$ATTESTATION_BUNDLE"' in workflow
    assert '--repo "$GITHUB_REPOSITORY"' in workflow
    assert '--signer-repo "$GITHUB_REPOSITORY"' in workflow
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/release.yml"' in workflow
    assert '--predicate-type "https://slsa.dev/provenance/v1"' in workflow
    assert '--source-ref "refs/tags/$EXPECTED_TAG"' in workflow
    assert '--source-digest "$LOCAL_PEELED_SHA"' in workflow
    assert '--format json >"$ATTESTATION_VERIFICATION"' in workflow
    assert 'test -s "$ATTESTATION_VERIFICATION"' in workflow


def test_release_workflow_fails_closed_on_existing_release_and_binds_owner_preflight():
    workflow = _workflow_text()

    assert "releases/tags/${EXPECTED_TAG}" in workflow
    assert "release already exists" in workflow
    assert "only an explicit HTTP 404 may continue" in workflow
    assert "repos/${GITHUB_REPOSITORY}/immutable-releases" not in workflow
    assert "IMMUTABLE_PREFLIGHT_SHA: ${{ vars.RELEASE_IMMUTABLE_PREFLIGHT_SHA }}" in workflow
    assert workflow.count('test -n "$IMMUTABLE_PREFLIGHT_SHA"') >= 3
    assert workflow.count('test "$IMMUTABLE_PREFLIGHT_SHA" = "$GITHUB_SHA"') >= 3
    assert workflow.count('test "$IMMUTABLE_PREFLIGHT_SHA" = "$LOCAL_PEELED_SHA"') >= 3
    assert workflow.count('"immutable_owner_preflight_commit"') >= 3
    assert 'gh release view "$EXPECTED_TAG"' in workflow
    assert "--json isImmutable" in workflow
    assert 'test "$IMMUTABLE_CLI" = "true"' in workflow


def test_release_workflow_verifies_exact_published_assets_and_downloaded_hashes():
    workflow = _workflow_text()

    assert "release_files=(" in workflow
    assert '"dist/$(basename "$RELEASE_LOCK")"' in workflow
    assert '"dist/$(basename "$RELEASE_MANIFEST")"' in workflow
    assert '"${release_files[@]}"' in workflow
    assert '"assets"' in workflow
    assert "published release asset set does not match" in workflow
    assert 'gh release download "$EXPECTED_TAG"' in workflow
    assert 'cmp -- "dist/$asset" "$DOWNLOAD_DIR/$asset"' in workflow
    assert '(cd "$DOWNLOAD_DIR" && sha256sum --check SHA256SUMS)' in workflow


def test_release_workflow_preserves_failure_evidence_after_immutable_creation():
    workflow = _workflow_text()

    initialized = workflow.index("Initialize post-publication evidence")
    published = workflow.index("Publish immutable GitHub release")
    verified = workflow.index("Verify immutable release, tag, assets, and downloaded hashes")
    uploaded = workflow.index("Upload post-publication verification evidence")
    assert initialized < published < verified < uploaded
    assert '"status": "publication_not_attempted"' in workflow
    assert 'status="release_created_pending_verification"' in workflow
    assert workflow.count("POST_EXIT_CODE") >= 2
    assert "release_created_verification_failed" in workflow
    assert 'POST_STAGE="record_release_created"' in workflow
    assert workflow.count("os.replace") >= 4
    assert '"status": "verified"' in workflow
    assert "if-no-files-found: warn" in workflow


def test_release_lock_scope_resolver_and_regeneration_limits_are_explicit():
    lock = RELEASE_LOCK.read_text(encoding="utf-8")
    release_doc = RELEASE_DOC.read_text(encoding="utf-8")

    assert "Release/install scope only" in lock
    assert "pip==26.0.1" in lock
    assert "Wheelhouse regeneration command:" in lock
    assert "CPython 3.12 / Linux x86_64" in release_doc
    assert "Python 3.10-3.13 development-lock matrix" in " ".join(release_doc.split())
    assert "not completion evidence" in release_doc
    assert "pip==26.0.1" in release_doc
    assert "python -m pip download" in release_doc


def test_release_docs_record_failed_v214_and_owner_preflight_procedure():
    release_doc = RELEASE_DOC.read_text(encoding="utf-8")

    assert "v2.1.4" in release_doc
    assert "tag-only failed release" in release_doc
    assert "no GitHub Release or release assets" in release_doc
    assert "RELEASE_IMMUTABLE_PREFLIGHT_SHA" in release_doc
    assert "gh variable set" in release_doc
    assert "immutable-releases" in release_doc


def test_readme_identifies_v215_candidate_and_v214_failed_tag_only_release():
    readme = README.read_text(encoding="utf-8")

    assert "v2.1.5 fix-forward candidate" in readme
    assert 'prints "2.1.5"' in readme
    assert '"version": "2.1.5"' in readme
    assert "confflow-2.1.5-py3-none-any.whl" in readme
    assert "protected v2.1.4 tag" in readme
    assert "no GitHub Release or assets" in readme
    assert "v2.1.4 fix-forward candidate" not in readme
