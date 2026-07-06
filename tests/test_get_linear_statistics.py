"""Tests for linear get_statistics command."""
import os
import pytest

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.commands.get_statistics import (
    DEFAULT_ESTIMATE,
    COMPLETED_STATE_TYPES,
    calculate_estimates_for_issues,
    extract_slug_id,
    get_filter_statistics,
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
            'https://linear.app/sinchi/project/sprint-planning-doc-a1b2c3d4e5f6'
            '/issues?layout=list&ordering=priority'
        )
        assert extract_slug_id(url) == 'a1b2c3d4e5f6'

    def test_full_url_no_trailing_path(self):
        url = 'https://linear.app/sinchi/project/sprint-planning-doc-a1b2c3d4e5f6'
        assert extract_slug_id(url) == 'a1b2c3d4e5f6'

    def test_full_slug(self):
        assert extract_slug_id('sprint-planning-doc-a1b2c3d4e5f6') == 'a1b2c3d4e5f6'

    def test_short_id_only(self):
        assert extract_slug_id('a1b2c3d4e5f6') == 'a1b2c3d4e5f6'

    def test_strips_whitespace(self):
        assert extract_slug_id('  a1b2c3d4e5f6  ') == 'a1b2c3d4e5f6'

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            extract_slug_id('')

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            extract_slug_id('   ')

    def test_url_with_no_slug_raises(self):
        with pytest.raises(ValueError):
            extract_slug_id('https://linear.app/sinchi/project/')

    def test_url_with_only_query_params_raises(self):
        with pytest.raises(ValueError):
            extract_slug_id('https://linear.app/sinchi/project/?param=value')


class TestGetFilterStatistics:
    """Tests for get_filter_statistics — mocks fetch_issues directly."""

    def _node(self, state_type, estimate):
        return {'state': {'type': state_type}, 'estimate': estimate}

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    def test_passes_filter_to_fetch(self, mock_fetch):
        mock_fetch.return_value = []
        gql_filter = {'project': {'slugId': {'eq': 'a1b2c3d4e5f6'}}}
        get_filter_statistics(gql_filter)
        mock_fetch.assert_called_once_with(gql_filter)

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    def test_combined_and_filter(self, mock_fetch):
        mock_fetch.return_value = [self._node('completed', 5)]
        combined = {'and': [
            {'project': {'slugId': {'eq': 'a1b2c3d4e5f6'}}},
            {'team': {'key': {'eq': 'WEB'}}},
        ]}
        stats = get_filter_statistics(combined)
        mock_fetch.assert_called_once_with(combined)
        assert stats['total_issues'] == 1
        assert stats['resolved_points'] == 5
        assert stats['resolved_issues'] == 1

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    def test_empty_result(self, mock_fetch):
        mock_fetch.return_value = []
        stats = get_filter_statistics({'project': {'slugId': {'eq': 'abc'}}})
        assert stats['total_issues'] == 0
        assert stats['total_points'] == 0


class TestGetStatisticsCli:
    """Tests for the get_statistics CLI — uses typer.testing.CliRunner."""

    def setup_method(self):
        from typer.testing import CliRunner
        from linear_tools.cli import app
        self.runner = CliRunner()
        self.app = app

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_query_only(self, mock_parse, mock_fetch):
        mock_parse.return_value = {'team': {'key': {'eq': 'WEB'}}}
        mock_fetch.return_value = []
        result = self.runner.invoke(self.app, ['get-statistics', 'team = WEB'])
        assert result.exit_code == 0
        import json
        output = json.loads(result.output)
        assert output['query'] == 'team = WEB'
        assert 'project' not in output

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    def test_project_only(self, mock_fetch):
        mock_fetch.return_value = []
        result = self.runner.invoke(self.app, ['get-statistics', '--project', 'a1b2c3d4e5f6'])
        assert result.exit_code == 0
        import json
        output = json.loads(result.output)
        assert output['project'] == 'a1b2c3d4e5f6'
        assert 'query' not in output
        mock_fetch.assert_called_once_with({'project': {'slugId': {'eq': 'a1b2c3d4e5f6'}}})

    @patch('linear_tools.commands.get_statistics.fetch_issues')
    @patch('linear_tools.commands.get_statistics.parse_query')
    def test_project_and_query_combined(self, mock_parse, mock_fetch):
        mock_parse.return_value = {'state': {'name': {'neq': 'Completed'}}}
        mock_fetch.return_value = []
        result = self.runner.invoke(
            self.app,
            ['get-statistics', '--project', 'a1b2c3d4e5f6', 'state != Completed'],
        )
        assert result.exit_code == 0
        called_filter = mock_fetch.call_args[0][0]
        assert called_filter == {'and': [
            {'project': {'slugId': {'eq': 'a1b2c3d4e5f6'}}},
            {'state': {'name': {'neq': 'Completed'}}},
        ]}

    def test_no_args_exits_with_error(self):
        result = self.runner.invoke(self.app, ['get-statistics'])
        assert result.exit_code == 1
