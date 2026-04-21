"""Tests for export_projects command — parser, normalization, and fetch."""
import os
import pytest

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.project_query_parser import build_project_condition, parse_project_query


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
