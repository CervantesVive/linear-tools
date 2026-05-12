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
