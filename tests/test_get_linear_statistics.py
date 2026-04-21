"""Tests for linear get_statistics command."""
import os
import pytest

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.commands.get_statistics import (
    DEFAULT_ESTIMATE,
    COMPLETED_STATE_TYPES,
    calculate_estimates_for_issues,
    extract_slug_id,
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


from unittest.mock import patch
from linear_tools.commands.get_statistics import get_query_statistics


class TestGetQueryStatistics:
    """Tests for get_query_statistics — mocks fetch_issues."""

    def _node(self, state_type, estimate):
        """Build a minimal raw GraphQL node (as returned by fetch_issues)."""
        return {
            'state': {'type': state_type},
            'estimate': estimate,
        }

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_all_completed(self, mock_parse, mock_fetch):
        mock_parse.return_value = {}
        mock_fetch.return_value = [
            self._node('completed', 5),
            self._node('cancelled', 8),
        ]
        stats = get_query_statistics('team = WEB')
        assert stats['total_points'] == 13
        assert stats['resolved_points'] == 13
        assert stats['unresolved_points'] == 0
        assert stats['resolved_points_percentage'] == 100
        assert stats['total_issues'] == 2
        assert stats['resolved_issues'] == 2
        assert stats['unresolved_issues'] == 0
        assert stats['resolved_issues_percentage'] == 100

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_all_unresolved(self, mock_parse, mock_fetch):
        mock_parse.return_value = {}
        mock_fetch.return_value = [
            self._node('started', 5),
            self._node('unstarted', 8),
        ]
        stats = get_query_statistics('team = WEB')
        assert stats['total_points'] == 13
        assert stats['resolved_points'] == 0
        assert stats['unresolved_points'] == 13
        assert stats['resolved_points_percentage'] == 0
        assert stats['total_issues'] == 2
        assert stats['resolved_issues'] == 0
        assert stats['unresolved_issues'] == 2
        assert stats['resolved_issues_percentage'] == 0

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_mixed(self, mock_parse, mock_fetch):
        mock_parse.return_value = {}
        mock_fetch.return_value = [
            self._node('completed', 5),
            self._node('started', 8),
            self._node('cancelled', 3),
            self._node('backlog', 2),
        ]
        stats = get_query_statistics('team = WEB')
        assert stats['total_points'] == 18
        assert stats['resolved_points'] == 8   # 5 + 3
        assert stats['unresolved_points'] == 10  # 8 + 2
        assert stats['resolved_points_percentage'] == 44
        assert stats['total_issues'] == 4
        assert stats['resolved_issues'] == 2
        assert stats['unresolved_issues'] == 2
        assert stats['resolved_issues_percentage'] == 50

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_defaulted_estimates(self, mock_parse, mock_fetch):
        mock_parse.return_value = {}
        mock_fetch.return_value = [
            self._node('completed', None),
            self._node('started', None),
        ]
        stats = get_query_statistics('team = WEB')
        assert stats['total_points'] == 6   # 2 * 3
        assert stats['resolved_points'] == 3
        assert stats['unresolved_points'] == 3
        assert stats['resolved_points_percentage'] == 50
        assert stats['total_issues'] == 2
        assert stats['resolved_issues'] == 1
        assert stats['unresolved_issues'] == 1
        assert stats['resolved_issues_percentage'] == 50

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_empty(self, mock_parse, mock_fetch):
        mock_parse.return_value = {}
        mock_fetch.return_value = []
        stats = get_query_statistics('team = NONE')
        assert stats['total_points'] == 0
        assert stats['resolved_points'] == 0
        assert stats['unresolved_points'] == 0
        assert stats['resolved_points_percentage'] == 0
        assert stats['total_issues'] == 0
        assert stats['resolved_issues'] == 0
        assert stats['unresolved_issues'] == 0
        assert stats['resolved_issues_percentage'] == 0


class TestExtractSlugId:
    """Tests for extract_slug_id — pure function, no I/O."""

    def test_full_url_with_query_params(self):
        url = (
            'https://linear.app/bitgo/project/navbar-revamp-offsite-2d728a27e93e'
            '/issues?layout=list&ordering=priority'
        )
        assert extract_slug_id(url) == '2d728a27e93e'

    def test_full_url_no_trailing_path(self):
        url = 'https://linear.app/bitgo/project/navbar-revamp-offsite-2d728a27e93e'
        assert extract_slug_id(url) == '2d728a27e93e'

    def test_full_slug(self):
        assert extract_slug_id('navbar-revamp-offsite-2d728a27e93e') == '2d728a27e93e'

    def test_short_id_only(self):
        assert extract_slug_id('2d728a27e93e') == '2d728a27e93e'

    def test_strips_whitespace(self):
        assert extract_slug_id('  2d728a27e93e  ') == '2d728a27e93e'

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            extract_slug_id('')

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            extract_slug_id('   ')
