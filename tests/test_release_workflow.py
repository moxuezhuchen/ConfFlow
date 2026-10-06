#!/usr/bin/env python3

"""Regression checks for the remaining workflow gates."""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

WORKFLOWS_DIR = Path(__file__).parents[1] / ".github" / "workflows"
JOBDESK_CONTRACT_WORKFLOW = WORKFLOWS_DIR / "jobdesk-contract.yml"
RELEASE_DOC = Path(__file__).parents[1] / "docs" / "RELEASE.md"
README = Path(__file__).parents[1] / "README.md"


def test_v216_release_metadata_and_runtime_inputs_are_consistent():
    repository = Path(__file__).parents[1]
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "2.1.6"
    # The paired CI now targets the current JobDesk-v2 consumer; the release
    # consistency requirement is that both workflows pin the same audited
    # consumer revision the cross-repo tests enforce, and reference no
    # retired consumer repository.
    pin_match = re.search(
        r'EXPECTED_JOBDESK_SHA = "([0-9a-f]{40})"',
        (repository / "tests" / "v4" / "jobdesk_integration.py").read_text(encoding="utf-8"),
    )
    assert pin_match is not None, "the cross-repo pin constant must exist"
    pinned = pin_match.group(1)
    for workflow in (JOBDESK_CONTRACT_WORKFLOW,):
        text = workflow.read_text(encoding="utf-8")
        assert "moxuezhuchen/jobdesk-v2" in text, workflow.name
        assert pinned in text, workflow.name
        assert "moxuezhuchen/jobdesk\n" not in text, workflow.name
        assert "jobdesk_app" not in text, workflow.name


def test_release_lock_scope_resolver_and_regeneration_limits_are_explicit():
    release_doc = RELEASE_DOC.read_text(encoding="utf-8")

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


def test_release_docs_record_failed_v215_attestation_verification():
    release_doc = RELEASE_DOC.read_text(encoding="utf-8")

    assert "v2.1.5" in release_doc
    assert "tag-only failed release" in release_doc
    assert "cryptographic attestation verification" in release_doc
    assert "no GitHub Release or release assets" in release_doc


def test_readme_identifies_v216_candidate_and_v215_failed_tag_only_release():
    readme = README.read_text(encoding="utf-8")

    assert "v2.1.6 fix-forward candidate" in readme
    assert 'prints "2.1.6"' in readme
    assert '"version": "2.1.6"' in readme
    assert "confflow-2.1.6-py3-none-any.whl" in readme
    assert "protected v2.1.4 and v2.1.5 tags are failed tag-only" in readme
    assert "no GitHub Release or assets" in readme
    assert "v2.1.5 fix-forward candidate" not in readme
