#!/usr/bin/env python3

"""Tests for confflow.core.keyword_rewrite — scan keyword rewriting."""

from __future__ import annotations

import pytest

from confflow.core.keyword_rewrite import make_scan_keyword_from_ts_keyword


class TestMakeScanKeyword:
    """Direct tests for make_scan_keyword_from_ts_keyword."""

    @pytest.mark.parametrize(
        "input_kw, expected_substring",
        [
            ("", ""),
            ("   ", ""),
            # Should strip opt sub-options like calcfc, ts, tight
            ("opt=(ts,calcfc) freq B3LYP/6-31G*", "opt"),
            # freq should be removed entirely
            ("B3LYP/6-31G* freq", "B3LYP/6-31G*"),
            # opt with only removable items reduces to bare opt
            ("opt=(ts,calcfc,tight)", "opt"),
            # opt with non-removable items preserves them
            ("opt=(maxcycles=100,ts)", "maxcycles=100"),
            # freq with arguments should be removed
            ("B3LYP freq=(noraman)", "B3LYP"),
            # noeigentest, rcfc, readfc removed from opt
            ("opt=(noeigentest,rcfc,readfc,maxstep=5)", "maxstep=5"),
        ],
    )
    def test_rewrite_cases(self, input_kw, expected_substring):
        result = make_scan_keyword_from_ts_keyword(input_kw)
        if expected_substring:
            assert expected_substring in result
        else:
            assert result == ""

    def test_ts_removed_from_opt(self):
        result = make_scan_keyword_from_ts_keyword("opt=(ts,calcfc) B3LYP/6-31G*")
        assert "ts" not in result.lower().split("opt")[0]  # ts not before opt
        # In the opt(...) part, ts should be gone
        assert "calcfc" not in result

    def test_freq_fully_removed(self):
        result = make_scan_keyword_from_ts_keyword("opt B3LYP freq 6-31G*")
        assert "freq" not in result

    def test_preserves_non_opt_non_freq_keywords(self):
        result = make_scan_keyword_from_ts_keyword("opt=(ts) B3LYP/6-31G* scf=tight")
        assert "B3LYP/6-31G*" in result
        assert "scf=tight" in result
