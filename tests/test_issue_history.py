"""Tests for linear issue-history command."""
import os

os.environ.setdefault('LINEAR_API_KEY', 'test')

import pytest

from linear_tools.commands.issue_history import (
    _priority_label,
    _is_noop,
    normalize_history_event,
)


def _event(**overrides):
    base = {
        'id': 'e1',
        'createdAt': '2026-01-01T00:00:00.000Z',
        'actor': {'displayName': 'Alice', 'name': 'alice'},
        'fromState': None,
        'toState': None,
        'fromAssignee': None,
        'toAssignee': None,
        'fromPriority': None,
        'toPriority': None,
    }
    base.update(overrides)
    return base


def _issue(identifier='WEB-1', title='My issue', uid='uuid-1'):
    return {'id': uid, 'identifier': identifier, 'title': title}


class TestPriorityLabel:
    @pytest.mark.parametrize('value,expected', [
        (0, 'No priority'),
        (1, 'Urgent'),
        (2, 'High'),
        (3, 'Medium'),
        (4, 'Low'),
        (None, None),
    ])
    def test_maps_integer_to_label(self, value, expected):
        assert _priority_label(value) == expected


class TestIsNoop:
    def test_all_null_is_noop(self):
        assert _is_noop(_event()) is True

    def test_state_change_is_not_noop(self):
        assert _is_noop(_event(toState={'name': 'Done'})) is False

    def test_assignee_change_is_not_noop(self):
        assert _is_noop(_event(toAssignee={'displayName': 'Bob', 'name': 'bob'})) is False

    def test_priority_change_is_not_noop(self):
        assert _is_noop(_event(toPriority=2)) is False


class TestNormalizeHistoryEvent:
    def test_flattens_state_change(self):
        event = _event(
            fromState={'name': 'Backlog'},
            toState={'name': 'In Progress'},
        )
        row = normalize_history_event(_issue(), event)
        assert row['identifier'] == 'WEB-1'
        assert row['issueTitle'] == 'My issue'
        assert row['eventAt'] == '2026-01-01T00:00:00.000Z'
        assert row['actor'] == 'Alice'
        assert row['fromState'] == 'Backlog'
        assert row['toState'] == 'In Progress'
        assert row['fromAssignee'] is None
        assert row['toAssignee'] is None
        assert row['fromPriority'] is None
        assert row['toPriority'] is None

    def test_flattens_priority_change(self):
        event = _event(fromPriority=3, toPriority=1)
        row = normalize_history_event(_issue(), event)
        assert row['fromPriority'] == 'Medium'
        assert row['toPriority'] == 'Urgent'

    def test_flattens_assignee_change(self):
        event = _event(
            fromAssignee={'displayName': 'Alice', 'name': 'alice'},
            toAssignee={'displayName': 'Bob', 'name': 'bob'},
        )
        row = normalize_history_event(_issue(), event)
        assert row['fromAssignee'] == 'Alice'
        assert row['toAssignee'] == 'Bob'

    def test_falls_back_to_name_when_no_display_name(self):
        event = _event(actor={'displayName': '', 'name': 'svc-bot'})
        row = normalize_history_event(_issue(), event)
        assert row['actor'] == 'svc-bot'

    def test_null_actor_produces_none(self):
        event = _event(actor=None)
        row = normalize_history_event(_issue(), event)
        assert row['actor'] is None


from unittest.mock import patch

from linear_tools.commands.issue_history import (
    _fetch_issues_for_history,
    fetch_history,
)


def _issues_page(nodes, has_next=False, cursor=None):
    return {
        'issues': {
            'nodes': nodes,
            'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        }
    }


def _history_page(nodes, has_next=False, cursor=None):
    return {
        'issue': {
            'history': {
                'nodes': nodes,
                'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
            }
        }
    }


class TestFetchIssuesForHistory:
    def test_returns_issues_from_single_page(self):
        nodes = [{'id': 'u1', 'identifier': 'WEB-1', 'title': 'T'}]
        with patch('linear_tools.utils.graphql_request', return_value=_issues_page(nodes)) as gql:
            result = _fetch_issues_for_history({'team': {'key': {'eq': 'WEB'}}})
        assert len(result) == 1
        assert result[0]['identifier'] == 'WEB-1'
        gql.assert_called_once()

    def test_paginates_through_all_pages(self):
        page1 = _issues_page([{'id': 'u1', 'identifier': 'WEB-1', 'title': 'T'}], has_next=True, cursor='c1')
        page2 = _issues_page([{'id': 'u2', 'identifier': 'WEB-2', 'title': 'T'}])
        with patch('linear_tools.utils.graphql_request', side_effect=[page1, page2]) as gql:
            result = _fetch_issues_for_history({})
        assert len(result) == 2
        assert gql.call_count == 2
        second_vars = gql.call_args_list[1][1]['variables']
        assert second_vars['after'] == 'c1'

    def test_returns_empty_list_when_no_issues(self):
        with patch('linear_tools.utils.graphql_request', return_value=_issues_page([])):
            result = _fetch_issues_for_history({})
        assert result == []


class TestFetchHistory:
    def test_returns_events_from_single_page(self):
        event = {'id': 'e1', 'createdAt': '2026-01-01T00:00:00.000Z'}
        with patch('linear_tools.utils.graphql_request', return_value=_history_page([event])) as gql:
            result = fetch_history('uuid-1')
        assert len(result) == 1
        assert result[0]['id'] == 'e1'
        gql.assert_called_once()

    def test_paginates_through_all_pages(self):
        page1 = _history_page([{'id': 'e1'}], has_next=True, cursor='c1')
        page2 = _history_page([{'id': 'e2'}])
        with patch('linear_tools.utils.graphql_request', side_effect=[page1, page2]) as gql:
            result = fetch_history('uuid-1')
        assert len(result) == 2
        assert gql.call_count == 2
        second_vars = gql.call_args_list[1][1]['variables']
        assert second_vars['after'] == 'c1'

    def test_returns_empty_list_when_no_history(self):
        with patch('linear_tools.utils.graphql_request', return_value=_history_page([])):
            result = fetch_history('uuid-1')
        assert result == []


import json as json_mod

from typer.testing import CliRunner
from typer import Typer

from linear_tools.commands.issue_history import issue_history

runner = CliRunner()
app = Typer()
app.command()(issue_history)


def _make_issue(identifier='WEB-1', title='My issue', uid='uuid-1'):
    return {'id': uid, 'identifier': identifier, 'title': title}


def _make_event(from_state='Backlog', to_state='In Progress'):
    return {
        'id': 'e1',
        'createdAt': '2026-01-01T00:00:00.000Z',
        'actor': {'displayName': 'Alice', 'name': 'alice'},
        'fromState': {'name': from_state} if from_state else None,
        'toState': {'name': to_state} if to_state else None,
        'fromAssignee': None,
        'toAssignee': None,
        'fromPriority': None,
        'toPriority': None,
    }


class TestIssueHistoryCommand:
    def test_no_args_prints_usage_and_exits_1(self):
        result = runner.invoke(app, [])
        assert result.exit_code == 1
        assert 'Options' in result.stdout or '--id' in result.stdout

    def test_unknown_field_exits_1(self):
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=[_make_issue()]), \
             patch('linear_tools.commands.issue_history.fetch_history', return_value=[_make_event()]):
            result = runner.invoke(app, ['--id', 'WEB-1', '--fields', 'badfield'])
        assert result.exit_code == 1
        assert 'badfield' in result.stderr or 'unknown' in result.stderr.lower()

    def test_no_issues_found_exits_0(self):
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=[]):
            result = runner.invoke(app, ['--id', 'WEB-9999'])
        assert result.exit_code == 0

    def test_outputs_json_by_default(self):
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=[_make_issue()]), \
             patch('linear_tools.commands.issue_history.fetch_history', return_value=[_make_event()]):
            result = runner.invoke(app, ['--id', 'WEB-1'])
        assert result.exit_code == 0
        data = json_mod.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]['identifier'] == 'WEB-1'
        assert data[0]['fromState'] == 'Backlog'
        assert data[0]['toState'] == 'In Progress'
        assert data[0]['actor'] == 'Alice'

    def test_noop_events_filtered_out(self):
        noop = {
            'id': 'e_noop', 'createdAt': '2026-01-01T00:00:00.000Z',
            'actor': {'displayName': 'Alice', 'name': 'alice'},
            'fromState': None, 'toState': None,
            'fromAssignee': None, 'toAssignee': None,
            'fromPriority': None, 'toPriority': None,
        }
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=[_make_issue()]), \
             patch('linear_tools.commands.issue_history.fetch_history', return_value=[noop, _make_event()]):
            result = runner.invoke(app, ['--id', 'WEB-1'])
        assert result.exit_code == 0
        data = json_mod.loads(result.stdout)
        assert len(data) == 1

    def test_csv_output_has_header_and_row(self):
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=[_make_issue()]), \
             patch('linear_tools.commands.issue_history.fetch_history', return_value=[_make_event()]):
            result = runner.invoke(app, ['--id', 'WEB-1', '--csv'])
        assert result.exit_code == 0
        lines = result.stdout.strip().splitlines()
        assert lines[0].startswith('identifier')
        assert 'WEB-1' in lines[1]

    def test_fields_flag_restricts_output(self):
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=[_make_issue()]), \
             patch('linear_tools.commands.issue_history.fetch_history', return_value=[_make_event()]):
            result = runner.invoke(app, ['--id', 'WEB-1', '--fields', 'identifier,fromState,toState'])
        assert result.exit_code == 0
        data = json_mod.loads(result.stdout)
        assert set(data[0].keys()) == {'identifier', 'fromState', 'toState'}

    def test_multiple_issues_aggregates_all_events(self):
        issues = [_make_issue('WEB-1', uid='u1'), _make_issue('WEB-2', uid='u2')]
        with patch('linear_tools.commands.issue_history._fetch_issues_for_history', return_value=issues), \
             patch('linear_tools.commands.issue_history.fetch_history', return_value=[_make_event()]):
            result = runner.invoke(app, ['--query', 'team = WEB'])
        assert result.exit_code == 0
        data = json_mod.loads(result.stdout)
        assert len(data) == 2
