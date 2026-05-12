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


def _fetch_history(issue_uuid):
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


def issue_history(
    ctx: typer.Context,
    query: Annotated[Optional[str], typer.Option("--query", "-q", help="JQL-like filter query string")] = None,
    issue_id: Annotated[Optional[list[str]], typer.Option("--id", help="Issue identifier(s), e.g. WEB-123. Can be repeated.")] = None,
    csv_output: Annotated[bool, typer.Option("--csv", help="Output CSV instead of JSON")] = False,
    fields: Annotated[Optional[str], typer.Option("--fields", help=f"Comma-separated fields. Available: {', '.join(ALL_FIELDS)}")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    """Retrieve field-level change history for Linear issues."""
    if verbose:
        linear_utils.VERBOSE = True

    if not query and not issue_id:
        typer.echo(ctx.get_help())
        raise typer.Exit(1)

    selected_fields = None
    if fields:
        selected_fields = [f.strip() for f in fields.split(",")]
        unknown = [f for f in selected_fields if f not in ALL_FIELDS]
        if unknown:
            typer.echo(f"Error: unknown field(s): {', '.join(unknown)}. Available: {', '.join(ALL_FIELDS)}", err=True)
            raise typer.Exit(1)

    filters = []
    if issue_id:
        id_query = (
            f'identifier in [{", ".join(issue_id)}]'
            if len(issue_id) > 1
            else f'identifier = {issue_id[0]}'
        )
        try:
            filters.append(parse_query(id_query))
        except (SyntaxError, ValueError) as e:
            typer.echo(f"ID error: {e}", err=True)
            raise typer.Exit(1)

    if query:
        try:
            filters.append(parse_query(query))
        except (SyntaxError, ValueError) as e:
            typer.echo(f"Query error: {e}", err=True)
            raise typer.Exit(1)

    graphql_filter = filters[0] if len(filters) == 1 else {'and': filters}

    if linear_utils.VERBOSE:
        typer.echo(f"Compiled filter:\n{json.dumps(graphql_filter, indent=2)}", err=True)

    try:
        issues = _fetch_issues_for_history(graphql_filter)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    if not issues:
        typer.echo("No issues found.", err=True)
        raise typer.Exit(0)

    rows = []
    for issue in issues:
        try:
            events = _fetch_history(issue['id'])
        except Exception as e:
            typer.echo(f"API error fetching history for {issue['identifier']}: {e}", err=True)
            raise typer.Exit(1)
        for event in events:
            if not _is_noop(event):
                rows.append(normalize_history_event(issue, event))

    if csv_output:
        fieldnames = selected_fields if selected_fields else ALL_FIELDS
        data = [{k: r.get(k) for k in fieldnames} for r in rows] if selected_fields else rows
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=fieldnames,
            extrasaction='ignore',
            lineterminator='\n',
        )
        writer.writeheader()
        writer.writerows(data)
    else:
        output = [{k: r.get(k) for k in selected_fields} for r in rows] if selected_fields else rows
        typer.echo(json.dumps(output, indent=2, default=str))

    typer.echo(f"Exported {len(rows)} event(s).", err=True)
