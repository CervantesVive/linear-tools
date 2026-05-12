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
    return all(
        event.get(k) is None
        for k in ('fromState', 'toState', 'fromAssignee', 'toAssignee', 'fromPriority', 'toPriority')
    )


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
        'fromState':    from_state.get('name'),
        'toState':      to_state.get('name'),
        'fromAssignee': from_assignee.get('displayName') or from_assignee.get('name'),
        'toAssignee':   to_assignee.get('displayName') or to_assignee.get('name'),
        'fromPriority': _priority_label(event.get('fromPriority')),
        'toPriority':   _priority_label(event.get('toPriority')),
    }


_ISSUES_QUERY = """
query FetchIssuesForHistory($filter: IssueFilter!, $first: Int!, $after: String) {
  issues(filter: $filter, first: $first, after: $after) {
    nodes {
      id
      identifier
      title
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

_HISTORY_QUERY = """
query IssueHistory($id: String!, $after: String) {
  issue(id: $id) {
    history(first: 100, after: $after) {
      nodes {
        id
        createdAt
        actor {
          displayName
          name
        }
        fromState { name }
        toState   { name }
        fromAssignee { displayName name }
        toAssignee   { displayName name }
        fromPriority
        toPriority
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""


def _fetch_issues_for_history(graphql_filter):
    all_issues = []
    cursor = None
    while True:
        variables = {'filter': graphql_filter, 'first': 100, 'after': cursor}
        data = linear_utils.graphql_request(_ISSUES_QUERY, variables=variables)
        connection = data.get('issues', {})
        nodes = connection.get('nodes', [])
        all_issues.extend(nodes)
        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']
    if linear_utils.VERBOSE:
        print(f"Found {len(all_issues)} issue(s)", file=sys.stderr)
    return all_issues


def fetch_history(issue_uuid):
    all_events = []
    cursor = None
    while True:
        variables = {'id': issue_uuid, 'after': cursor}
        data = linear_utils.graphql_request(_HISTORY_QUERY, variables=variables)
        history = (data.get('issue') or {}).get('history', {})
        nodes = history.get('nodes', [])
        all_events.extend(nodes)
        page_info = history.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']
    if linear_utils.VERBOSE:
        print(f"  {len(all_events)} history event(s) for {issue_uuid}", file=sys.stderr)
    return all_events
