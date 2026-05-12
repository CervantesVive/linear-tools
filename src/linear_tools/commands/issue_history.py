"""Retrieve issue history from Linear."""
import sys
import csv
import json
from typing import Optional, Annotated

import typer

from linear_tools import utils as linear_utils
from linear_tools.query_parser import parse_query

PRIORITY_LABELS = {
    0: 'No priority',
    1: 'Urgent',
    2: 'High',
    3: 'Medium',
    4: 'Low',
}

ALL_FIELDS = [
    'identifier', 'issueTitle', 'eventAt', 'actor',
    'fromState', 'toState',
    'fromAssignee', 'toAssignee',
    'fromPriority', 'toPriority',
]


def _priority_label(value):
    if value is None:
        return None
    return PRIORITY_LABELS.get(value, str(value))


def _is_noop(event):
    return all([
        event.get('fromState') is None,
        event.get('toState') is None,
        event.get('fromAssignee') is None,
        event.get('toAssignee') is None,
        event.get('fromPriority') is None,
        event.get('toPriority') is None,
    ])


def normalize_history_event(issue, event):
    actor = event.get('actor') or {}
    from_state = event.get('fromState') or {}
    to_state = event.get('toState') or {}
    from_assignee = event.get('fromAssignee') or {}
    to_assignee = event.get('toAssignee') or {}
    return {
        'identifier':   issue.get('identifier'),
        'issueTitle':   issue.get('title'),
        'eventAt':      event.get('createdAt'),
        'actor':        actor.get('displayName') or actor.get('name'),
        'fromState':    from_state.get('name') if from_state else None,
        'toState':      to_state.get('name') if to_state else None,
        'fromAssignee': from_assignee.get('displayName') or from_assignee.get('name') if from_assignee else None,
        'toAssignee':   to_assignee.get('displayName') or to_assignee.get('name') if to_assignee else None,
        'fromPriority': _priority_label(event.get('fromPriority')),
        'toPriority':   _priority_label(event.get('toPriority')),
    }
