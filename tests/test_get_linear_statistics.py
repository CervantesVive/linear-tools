"""Tests for linear get_statistics command."""
import os
import pytest

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.commands.get_statistics import (
    DEFAULT_ESTIMATE,
    COMPLETED_STATE_TYPES,
    calculate_estimates_for_issues,
)


class TestConstants:
    def test_default_estimate(self):
        assert DEFAULT_ESTIMATE == 3

    def test_completed_state_types(self):
        assert COMPLETED_STATE_TYPES == {'completed', 'cancelled'}


class TestCalculateEstimatesForIssues:
    """Tests for calculate_estimates_for_issues — pure function, no I/O."""

    def _issue(self, state_type, estimate):
        return {'stateType': state_type, 'estimate': estimate}

    def test_all_explicit_estimates(self):
        issues = [
            self._issue('completed', 3),
            self._issue('started', 5),
            self._issue('completed', 8),
        ]
        total, defaulted, explicit = calculate_estimates_for_issues(issues)
        assert total == 16
        assert defaulted == 0
        assert explicit == 3

    def test_all_defaulted_estimates(self):
        issues = [
            self._issue('completed', None),
            self._issue('unstarted', None),
        ]
        total, defaulted, explicit = calculate_estimates_for_issues(issues)
        assert total == 6  # 2 * DEFAULT_ESTIMATE (3)
        assert defaulted == 2
        assert explicit == 0

    def test_mixed_estimates(self):
        issues = [
            self._issue('completed', 5),
            self._issue('started', None),
            self._issue('backlog', 8),
        ]
        total, defaulted, explicit = calculate_estimates_for_issues(issues)
        assert total == 16  # 5 + 3 (default) + 8
        assert defaulted == 1
        assert explicit == 2

    def test_zero_estimate_is_explicit(self):
        issues = [self._issue('completed', 0)]
        total, defaulted, explicit = calculate_estimates_for_issues(issues)
        assert total == 0
        assert defaulted == 0
        assert explicit == 1

    def test_empty_list(self):
        total, defaulted, explicit = calculate_estimates_for_issues([])
        assert total == 0
        assert defaulted == 0
        assert explicit == 0
