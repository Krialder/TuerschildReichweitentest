"""Tests fuer die Statistik-Helfer."""
from __future__ import annotations

import pytest

from dashboard.stats import (
    iso_duration_s, longest_fail_streak, percentile, wilson,
)


def test_percentile_basic():
    vals = [10, 20, 30, 40, 50]
    assert percentile(vals, 0) == 10
    assert percentile(vals, 100) == 50
    assert percentile(vals, 50) == 30


def test_percentile_empty_returns_none():
    assert percentile([], 50) is None


def test_wilson_handles_zero_n():
    assert wilson(0, 0) == (0.0, 0.0)


def test_wilson_perfect_score_close_to_one():
    lo, hi = wilson(100, 100)
    assert hi == pytest.approx(1.0)
    assert lo > 0.95


def test_longest_fail_streak_counts_max_run_of_zeroes():
    assert longest_fail_streak([1, 1, 0, 0, 0, 1, 0, 0]) == 3
    assert longest_fail_streak([1, 1, 1]) == 0
    assert longest_fail_streak([0]) == 1


def test_iso_duration_s_correct_seconds():
    assert iso_duration_s("2025-01-01T00:00:00Z",
                          "2025-01-01T00:01:30Z") == 90


def test_iso_duration_s_invalid_returns_none():
    assert iso_duration_s("nope", "2025-01-01T00:00:00Z") is None
