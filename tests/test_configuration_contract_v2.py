#!/usr/bin/env python3

"""Tests for the published configuration contracts (v1 and v2).

``v1`` is the document that already exists in the wild, so the assertions about it
are *regression* assertions: the same key set, the same schema, the same workflow
digest, no extra member.

``v2`` is ``v1`` plus four members, and the tests assert exactly that: the shared
members are identical to v1's, the additions are the editor manifest and the
recipe catalog with their digests, and every embedded digest recomputes from the
artifact it claims to describe.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.config.canonical.contract import (
    CONFIGURATION_CONTRACT_BUILDERS,
    CONFIGURATION_CONTRACT_SCHEMA,
    CONFIGURATION_CONTRACT_V1_SCHEMA,
    CONFIGURATION_CONTRACT_V2_SCHEMA,
    CONFIGURATION_VALIDATION_SCHEMA,
    build_configuration_contract,
    build_configuration_contract_for_version,
    build_configuration_contract_v1,
    build_configuration_contract_v2,
)
from confflow.config.canonical.editor_manifest import (
    EDITOR_MANIFEST_SCHEMA,
    editor_manifest_sha256,
)
from confflow.config.canonical.recipes import RECIPE_CATALOG_SCHEMA, recipe_catalog_sha256
from confflow.config.canonical.schema import (
    WORKFLOW_SCHEMA_VERSION,
    workflow_json_schema,
    workflow_schema_sha256,
)
from confflow.config.canonical.serialization import canonical_json, canonical_sha256

#: The v1 member set, frozen.  A v1 document that gained a member would be a
#: contract change whether or not it is additive, so it is asserted literally.
EXPECTED_V1_KEYS = {
    "producer",
    "schema",
    "validation_response_schema",
    "workflow_schema",
    "workflow_schema_sha256",
    "workflow_schema_version",
}

#: The members v2 adds.  Nothing else may differ.
EXPECTED_V2_ONLY_KEYS = {
    "editor_manifest",
    "editor_manifest_sha256",
    "recipe_catalog",
    "recipe_catalog_sha256",
}

#: Observed from ``confflow config contract --json`` and published to consumers.
EXPECTED_WORKFLOW_SCHEMA_SHA256 = "38c45669ff9f3be0d352ab8d6bb2a64be9cd67bb2327dd89cd72d804aee2e7ca"

#: Key names that would make the output environment-dependent.
VOLATILE_KEY_TOKENS = {
    "timestamp",
    "time",
    "host",
    "hostname",
    "cwd",
    "pid",
    "random",
    "uuid",
    "seed",
}


def _v1() -> dict[str, Any]:
    return build_configuration_contract_v1(
        producer_version="2.1.6", producer_commit=None, producer_dirty=None
    )


def _v2() -> dict[str, Any]:
    return build_configuration_contract_v2(
        producer_version="2.1.6", producer_commit=None, producer_dirty=None
    )


def _all_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            found |= _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _all_keys(item)
    return found


class TestV1Regression:
    """v1 is frozen: this is the document consumers already parse."""

    def test_key_set_is_unchanged(self) -> None:
        assert set(_v1()) == EXPECTED_V1_KEYS

    def test_schema_and_validation_schema_are_unchanged(self) -> None:
        document = _v1()

        assert document["schema"] == "confflow.configuration-contract.v1"
        assert document["validation_response_schema"] == "confflow.configuration-validation.v1"

    def test_the_workflow_schema_and_its_digest_are_unchanged(self) -> None:
        document = _v1()

        assert document["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION
        assert document["workflow_schema"] == workflow_json_schema()
        assert document["workflow_schema_sha256"] == EXPECTED_WORKFLOW_SCHEMA_SHA256
        assert document["workflow_schema_sha256"] == workflow_schema_sha256()

    def test_the_producer_block_is_unchanged(self) -> None:
        document = _v1()

        assert document["producer"] == {
            "package": "confflow",
            "version": "2.1.6",
            "commit": None,
            "dirty": None,
        }
        assert set(document["producer"]) == {"package", "version", "commit", "dirty"}

    def test_the_default_builder_is_v1(self) -> None:
        assert build_configuration_contract(producer_version="2.1.6") == _v1()

    def test_the_historical_schema_constant_still_means_v1(self) -> None:
        assert CONFIGURATION_CONTRACT_SCHEMA == CONFIGURATION_CONTRACT_V1_SCHEMA
        assert CONFIGURATION_CONTRACT_V1_SCHEMA == "confflow.configuration-contract.v1"
        assert CONFIGURATION_CONTRACT_V2_SCHEMA == "confflow.configuration-contract.v2"
        assert CONFIGURATION_VALIDATION_SCHEMA == "confflow.configuration-validation.v1"


class TestV2Shape:
    def test_it_is_v1_plus_exactly_the_four_added_members(self) -> None:
        document = _v2()

        assert set(document) == EXPECTED_V1_KEYS | EXPECTED_V2_ONLY_KEYS

    def test_every_shared_member_is_identical_to_v1(self) -> None:
        v1, v2 = _v1(), _v2()

        for key in EXPECTED_V1_KEYS - {"schema"}:
            assert v2[key] == v1[key], key
        assert v2["schema"] == CONFIGURATION_CONTRACT_V2_SCHEMA

    def test_the_embedded_artifacts_declare_their_own_schema(self) -> None:
        document = _v2()

        assert document["editor_manifest"]["schema"] == EDITOR_MANIFEST_SCHEMA
        assert document["recipe_catalog"]["schema"] == RECIPE_CATALOG_SCHEMA

    def test_no_level_is_published(self) -> None:
        """The consumer derives the level from artifact provenance.

        A published level would be a second, disputable answer.
        """
        assert "level" not in _v2()

    def test_no_provenance_inside_the_artifacts(self) -> None:
        document = _v2()

        for artifact in ("editor_manifest", "recipe_catalog"):
            assert "producer" not in document[artifact], artifact
            assert "contract_key" not in document[artifact], artifact

    def test_no_editor_or_catalog_version_member_is_published(self) -> None:
        """The artifact's own ``schema`` is the version.

        A duplicate top-level member could disagree with it.
        """
        document = _v2()

        assert "editor_manifest_version" not in document
        assert "recipe_catalog_version" not in document


class TestEmbeddedDigests:
    def test_the_manifest_digest_recomputes_from_the_embedded_manifest(self) -> None:
        document = _v2()

        assert document["editor_manifest_sha256"] == canonical_sha256(document["editor_manifest"])
        assert document["editor_manifest_sha256"] == editor_manifest_sha256()

    def test_the_catalog_digest_recomputes_from_the_embedded_catalog(self) -> None:
        document = _v2()

        assert document["recipe_catalog_sha256"] == canonical_sha256(document["recipe_catalog"])
        assert document["recipe_catalog_sha256"] == recipe_catalog_sha256()

    def test_every_digest_is_a_hex_sha256(self) -> None:
        document = _v2()

        for key in ("workflow_schema_sha256", "editor_manifest_sha256", "recipe_catalog_sha256"):
            digest = document[key]
            assert isinstance(digest, str) and len(digest) == 64
            assert digest == digest.lower()
            int(digest, 16)


class TestDeterminism:
    def test_two_builds_are_identical(self) -> None:
        assert _v2() == _v2()
        assert canonical_json(_v2()) == canonical_json(_v2())

    def test_the_same_arguments_produce_the_same_bytes(self) -> None:
        first = canonical_json(
            build_configuration_contract_v2(
                producer_version="2.1.6", producer_commit="abc", producer_dirty=False
            )
        )
        second = canonical_json(
            build_configuration_contract_v2(
                producer_version="2.1.6", producer_commit="abc", producer_dirty=False
            )
        )
        assert first == second

    def test_the_document_carries_no_volatile_key(self) -> None:
        for key in _all_keys(_v2()):
            assert key.lower() not in VOLATILE_KEY_TOKENS, key

    def test_provenance_is_taken_from_the_arguments(self) -> None:
        document = build_configuration_contract_v2(
            producer_version="9.9.9", producer_commit="deadbeef", producer_dirty=True
        )
        assert document["producer"] == {
            "package": "confflow",
            "version": "9.9.9",
            "commit": "deadbeef",
            "dirty": True,
        }


class TestVersionDispatch:
    def test_the_builder_map_defines_the_supported_versions(self) -> None:
        # R7 publishes the additive v3 producer contract alongside v1/v2.
        assert sorted(CONFIGURATION_CONTRACT_BUILDERS) == [1, 2, 3]

    @pytest.mark.parametrize("version", [1, 2])
    def test_each_version_dispatches_to_its_builder(self, version: int) -> None:
        document = build_configuration_contract_for_version(version, producer_version="2.1.6")
        expected = (
            CONFIGURATION_CONTRACT_V1_SCHEMA if version == 1 else CONFIGURATION_CONTRACT_V2_SCHEMA
        )
        assert document["schema"] == expected

    def test_the_dispatched_v1_matches_the_v1_builder(self) -> None:
        assert build_configuration_contract_for_version(1, producer_version="2.1.6") == _v1()

    def test_the_dispatched_v2_matches_the_v2_builder(self) -> None:
        assert build_configuration_contract_for_version(2, producer_version="2.1.6") == _v2()

    @pytest.mark.parametrize("version", [0, 99, -1])
    def test_an_unsupported_version_raises_with_the_supported_set(self, version: int) -> None:
        with pytest.raises(ValueError, match="unsupported configuration contract version"):
            build_configuration_contract_for_version(version, producer_version="2.1.6")
