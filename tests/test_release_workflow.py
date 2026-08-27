#!/usr/bin/env python3

"""Regression checks for the versioned release workflow gates."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


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
    assert "default: v2.1.4" in workflow
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
