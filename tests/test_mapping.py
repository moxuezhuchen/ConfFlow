#!/usr/bin/env python3
"""Tests for confgen.mapping MCS and chain-transfer edge cases."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from rdkit import Chem

import confflow.blocks.confgen.mapping as mapping


class _FakeConformer:
    def __init__(self, positions: list[list[float]]):
        self._positions = np.array(positions, dtype=np.float64)

    def GetPositions(self) -> np.ndarray:
        return self._positions


class _FakeMol:
    def __init__(
        self,
        matches: list[tuple[int, ...]],
        positions: list[list[float]] | None = None,
        *,
        conformer_error: Exception | None = None,
    ):
        self._matches = tuple(matches)
        self._positions = positions
        self._conformer_error = conformer_error

    def GetSubstructMatches(self, patt) -> tuple[tuple[int, ...], ...]:
        del patt
        return self._matches

    def GetConformer(self) -> _FakeConformer:
        if self._conformer_error is not None:
            raise self._conformer_error
        assert self._positions is not None
        return _FakeConformer(self._positions)


def test_run_mcs_timeout_without_match_raises():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    fake_res = SimpleNamespace(canceled=True, numAtoms=0, numBonds=0, smartsString="")

    with patch.object(mapping.rdFMCS, "FindMCS", return_value=fake_res):
        with pytest.raises(ValueError, match="timed out"):
            mapping._run_mcs(ref, target, timeout=1, min_coverage=0.7, verbose=False)


def test_run_mcs_partial_timeout_warns(caplog: pytest.LogCaptureFixture):
    ref = Chem.MolFromSmiles("CCC")
    target = Chem.MolFromSmiles("CCC")
    fake_res = SimpleNamespace(canceled=True, numAtoms=2, numBonds=1, smartsString="[#6]-[#6]")

    with (
        patch.object(mapping.rdFMCS, "FindMCS", return_value=fake_res),
        patch.object(mapping.logger, "warning") as mock_warning,
    ):
        with caplog.at_level("WARNING", logger="confflow.confgen"):
            patt = mapping._run_mcs(ref, target, timeout=1, min_coverage=0.5, verbose=False)

    assert patt is not None
    mock_warning.assert_called_once()


def test_run_mcs_low_coverage_raises():
    ref = Chem.MolFromSmiles("CCCC")
    target = Chem.MolFromSmiles("CC")
    fake_res = SimpleNamespace(canceled=False, numAtoms=2, numBonds=1, smartsString="[#6]-[#6]")

    with patch.object(mapping.rdFMCS, "FindMCS", return_value=fake_res):
        with pytest.raises(ValueError, match="coverage too low"):
            mapping._run_mcs(ref, target, timeout=1, min_coverage=0.9, verbose=False)


def test_get_mcs_mapping_success():
    ref = Chem.MolFromSmiles("CCO")
    target = Chem.MolFromSmiles("CCO")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    with patch.object(mapping, "_run_mcs", return_value=patt):
        got = mapping.get_mcs_mapping(ref, target)

    assert got == {0: 0, 1: 1}


def test_get_mcs_mapping_missing_match_raises():
    patt = Chem.MolFromSmarts("[#6]")
    assert patt is not None
    ref = MagicMock()
    target = MagicMock()
    ref.GetSubstructMatch.return_value = ()
    target.GetSubstructMatch.return_value = (0,)

    with patch.object(mapping, "_run_mcs", return_value=patt):
        with pytest.raises(ValueError, match="cannot map MCS back"):
            mapping.get_mcs_mapping(ref, target)


def test_best_mapping_for_chain_prefers_smallest_displacement():
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None
    ref = _FakeMol(
        matches=[(0, 1), (1, 0)],
        positions=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
    )
    target = _FakeMol(
        matches=[(0, 1), (1, 0)],
        positions=[[0.1, 0.0, 0.0], [100.0, 0.0, 0.0]],
    )

    got = mapping._best_mapping_for_chain(ref, target, patt, [0])
    assert got[0] == 0


def test_best_mapping_for_chain_without_coords_uses_first_mapping():
    patt = Chem.MolFromSmarts("[#6]")
    assert patt is not None
    ref = _FakeMol(matches=[(0,), (1,)], conformer_error=RuntimeError("no conformer"))
    target = _FakeMol(matches=[(5,), (6,)], conformer_error=RuntimeError("no conformer"))

    got = mapping._best_mapping_for_chain(ref, target, patt, [0])
    assert got == {0: 5}


def test_transfer_chain_indices_missing_mapping_raises():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    with (
        patch.object(mapping, "_run_mcs", return_value=patt),
        patch.object(mapping, "_best_mapping_for_chain", return_value={0: 9}),
    ):
        with pytest.raises(ValueError, match="could not be mapped"):
            mapping.transfer_chain_indices(ref, target, [0, 1])


def test_transfer_chain_indices_success():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    with (
        patch.object(mapping, "_run_mcs", return_value=patt),
        patch.object(mapping, "_best_mapping_for_chain", return_value={0: 5, 1: 6}),
    ):
        got = mapping.transfer_chain_indices(ref, target, [0, 1])

    assert got == [5, 6]
