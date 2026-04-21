"""Tests for export_projects command — parser, normalization, and fetch."""
import os
import pytest
from unittest.mock import patch

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.project_query_parser import build_project_condition, parse_project_query
from linear_tools.commands.export_projects import normalize_project, fetch_projects, PRIORITY_LABELS


class TestBuildProjectCondition:
    """Tests for build_project_condition — pure function, no I/O."""

    # --- team ---
    def test_team_eq(self):
        assert build_project_condition('team', '=', 'WEB') == {
            'accessibleTeams': {'some': {'key': {'eq': 'WEB'}}}
        }

    def test_team_unsupported_operator(self):
        with pytest.raises(ValueError, match="'team' only supports"):
            build_project_condition('team', '!=', 'WEB')

    # --- label ---
    def test_label_eq(self):
        assert build_project_condition('label', '=', "Q2'26") == {
            'labels': {'some': {'name': {'eq': "Q2'26"}}}
        }

    def test_label_neq(self):
        assert build_project_condition('label', '!=', 'Bug') == {
            'labels': {'every': {'name': {'neq': 'Bug'}}}
        }

    def test_label_in(self):
        assert build_project_condition('label', 'in', ["Q2'26", "Q3'26"]) == {
            'labels': {'some': {'name': {'in': ["Q2'26", "Q3'26"]}}}
        }

    # --- state ---
    def test_state_eq(self):
        assert build_project_condition('state', '=', 'In Progress') == {
            'status': {'name': {'eq': 'In Progress'}}
        }

    def test_state_neq(self):
        assert build_project_condition('state', '!=', 'Completed') == {
            'status': {'name': {'neq': 'Completed'}}
        }

    def test_state_in(self):
        assert build_project_condition('state', 'in', ['Blocked', 'In Progress']) == {
            'status': {'name': {'in': ['Blocked', 'In Progress']}}}

    def test_state_unsupported_operator(self):
        with pytest.raises(ValueError, match="'state' supports"):
            build_project_condition('state', '>', 'Done')

    # --- lead ---
    def test_lead_eq(self):
        assert build_project_condition('lead', '=', 'Alice') == {
            'lead': {'displayName': {'containsIgnoreCase': 'Alice'}}
        }

    def test_lead_contains(self):
        assert build_project_condition('lead', 'contains', 'Alice') == {
            'lead': {'displayName': {'containsIgnoreCase': 'Alice'}}
        }

    def test_lead_unsupported_operator(self):
        with pytest.raises(ValueError, match="'lead' supports"):
            build_project_condition('lead', '!=', 'Alice')

    # --- priority ---
    def test_priority_eq(self):
        assert build_project_condition('priority', '=', 'High') == {
            'priority': {'eq': 2}
        }

    def test_priority_gte(self):
        result = build_project_condition('priority', '>=', 'High')
        assert result == {'priority': {'in': [1, 2]}}

    def test_priority_in(self):
        result = build_project_condition('priority', 'in', ['Urgent', 'High'])
        assert result == {'priority': {'in': [1, 2]}}

    # --- name ---
    def test_name_eq(self):
        assert build_project_condition('name', '=', 'auth') == {
            'name': {'containsIgnoreCase': 'auth'}
        }

    def test_name_contains(self):
        assert build_project_condition('name', 'contains', 'auth') == {
            'name': {'containsIgnoreCase': 'auth'}
        }

    def test_name_unsupported_operator(self):
        with pytest.raises(ValueError, match="'name' supports"):
            build_project_condition('name', '!=', 'auth')

    # --- date fields ---
    def test_created_alias(self):
        assert build_project_condition('created', '>', '2025-01-01') == {
            'createdAt': {'gt': '2025-01-01'}
        }

    def test_updated_alias(self):
        assert build_project_condition('updated', '>=', '2025-01-01') == {
            'updatedAt': {'gte': '2025-01-01'}
        }

    def test_start_date(self):
        assert build_project_condition('startDate', '<', '2026-06-30') == {
            'startDate': {'lt': '2026-06-30'}
        }

    def test_target_date(self):
        assert build_project_condition('targetDate', '<=', '2026-12-31') == {
            'targetDate': {'lte': '2026-12-31'}
        }

    # --- unknown field ---
    def test_unknown_field(self):
        with pytest.raises(ValueError, match="Unknown filter field"):
            build_project_condition('assignee', '=', 'Alice')


class TestParseProjectQuery:
    """Integration tests for parse_project_query — tests boolean composition."""

    def test_single_team(self):
        result = parse_project_query('team = WEB')
        assert result == {'accessibleTeams': {'some': {'key': {'eq': 'WEB'}}}}

    def test_team_and_label(self):
        result = parse_project_query("team = WEB AND label = \"Q2'26\"")
        assert result == {
            'and': [
                {'accessibleTeams': {'some': {'key': {'eq': 'WEB'}}}},
                {'labels': {'some': {'name': {'eq': "Q2'26"}}}},
            ]
        }

    def test_state_in_list(self):
        result = parse_project_query('state in ["Blocked", "In Progress"]')
        assert result == {'status': {'name': {'in': ['Blocked', 'In Progress']}}}

    def test_or_expression(self):
        result = parse_project_query('state = Completed OR state = Cancelled')
        assert result == {
            'or': [
                {'status': {'name': {'eq': 'Completed'}}},
                {'status': {'name': {'eq': 'Cancelled'}}},
            ]
        }

    def test_invalid_field_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown filter field"):
            parse_project_query('cycle = "Sprint 1"')

    def test_syntax_error(self):
        with pytest.raises(SyntaxError):
            parse_project_query('team =')




class TestNormalizeProject:
    """Tests for normalize_project — pure function, no I/O."""

    def _node(self, **overrides):
        base = {
            'name': 'Auth Revamp',
            'description': 'Revamp authentication',
            'url': 'https://linear.app/bitgo/project/auth-revamp',
            'priority': 2,
            'startDate': '2026-01-01',
            'targetDate': '2026-06-30',
            'createdAt': '2026-01-01T00:00:00Z',
            'updatedAt': '2026-02-01T00:00:00Z',
            'status': {'name': 'In Progress', 'type': 'started'},
            'labels': {'nodes': [{'name': "Q2'26"}, {'name': 'Security'}]},
            'lead': {'displayName': 'Alice', 'name': 'alice'},
            'teams': {'nodes': [{'name': 'Web', 'key': 'WEB'}]},
            'initiatives': {'nodes': [{'name': 'Platform Modernization'}]},
        }
        base.update(overrides)
        return base

    def test_full_node(self):
        result = normalize_project(self._node())
        assert result['name'] == 'Auth Revamp'
        assert result['url'] == 'https://linear.app/bitgo/project/auth-revamp'
        assert result['state'] == 'In Progress'
        assert result['stateType'] == 'started'
        assert result['priority'] == 2
        assert result['priorityLabel'] == 'High'
        assert result['labels'] == "Q2'26, Security"
        assert result['lead'] == 'Alice'
        assert result['teams'] == 'WEB'
        assert result['initiative'] == 'Platform Modernization'
        assert result['startDate'] == '2026-01-01'
        assert result['targetDate'] == '2026-06-30'
        assert result['description'] == 'Revamp authentication'

    def test_missing_status(self):
        result = normalize_project(self._node(status=None))
        assert result['state'] is None
        assert result['stateType'] is None

    def test_missing_lead(self):
        result = normalize_project(self._node(lead=None))
        assert result['lead'] is None

    def test_missing_labels(self):
        result = normalize_project(self._node(labels={'nodes': []}))
        assert result['labels'] == ''

    def test_missing_teams(self):
        result = normalize_project(self._node(teams={'nodes': []}))
        assert result['teams'] == ''

    def test_missing_initiatives(self):
        result = normalize_project(self._node(initiatives={'nodes': []}))
        assert result['initiative'] == ''

    def test_priority_none(self):
        result = normalize_project(self._node(priority=None))
        assert result['priority'] is None
        assert result['priorityLabel'] is None

    def test_priority_labels_map(self):
        assert PRIORITY_LABELS == {0: 'No Priority', 1: 'Urgent', 2: 'High', 3: 'Medium', 4: 'Low'}


class TestFetchProjects:
    """Tests for fetch_projects — mocks graphql_request."""

    def _make_page(self, names, has_next=False, cursor=None):
        return {
            'projects': {
                'nodes': [{'name': n} for n in names],
                'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
            }
        }

    @patch('linear_tools.commands.export_projects.linear_utils.graphql_request')
    def test_single_page(self, mock_gql):
        mock_gql.return_value = self._make_page(['Alpha', 'Beta'])
        result = fetch_projects({'name': {'containsIgnoreCase': 'a'}})
        assert [p['name'] for p in result] == ['Alpha', 'Beta']
        assert mock_gql.call_count == 1

    @patch('linear_tools.commands.export_projects.linear_utils.graphql_request')
    def test_two_pages(self, mock_gql):
        mock_gql.side_effect = [
            self._make_page(['Alpha', 'Beta'], has_next=True, cursor='cur1'),
            self._make_page(['Gamma'], has_next=False),
        ]
        result = fetch_projects({})
        assert [p['name'] for p in result] == ['Alpha', 'Beta', 'Gamma']
        assert mock_gql.call_count == 2
        assert mock_gql.call_args_list[1].kwargs['variables']['after'] == 'cur1'

    @patch('linear_tools.commands.export_projects.linear_utils.graphql_request')
    def test_empty_result(self, mock_gql):
        mock_gql.return_value = self._make_page([])
        result = fetch_projects({})
        assert result == []
