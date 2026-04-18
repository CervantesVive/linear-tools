"""Export Linear issues with a flexible JQL-like query language."""
import sys
import csv
import json
from typing import Optional
from typing_extensions import Annotated

import typer

from linear_tools import utils as linear_utils
from linear_tools.query_parser import parse_query

# ---------------------------------------------------------------------------
# GraphQL query — static; filter is passed as a variable
# ---------------------------------------------------------------------------

ISSUES_QUERY = """
query ExportIssues($filter: IssueFilter!, $first: Int!, $after: String) {
  issues(filter: $filter, first: $first, after: $after, orderBy: createdAt) {
    nodes {
      id
      identifier
      title
      description
      priority
      priorityLabel
      estimate
      createdAt
      updatedAt
      state {
        name
        type
      }
      assignee {
        displayName
        name
        email
      }
      labels {
        nodes {
          name
        }
      }
      cycle {
        name
        number
      }
      project {
        name
      }
      parent {
        identifier
        title
      }
      attachments(filter: { sourceType: { eq: "github" } }) {
        nodes {
          url
          title
          metadata
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# All fields available in normalized output
ALL_FIELDS = [
    'identifier', 'title', 'description',
    'state', 'stateType',
    'priority', 'priorityLabel',
    'assignee', 'assigneeEmail',
    'labels',
    'estimate',
    'cycle', 'cycleNumber',
    'project',
    'parent', 'parentTitle',
    'createdAt', 'updatedAt',
    'url',
    'pullRequests',
]

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_issues(graphql_filter):
    """Fetch all issues matching the filter, paginating through all results.

    Args:
        graphql_filter: Linear IssueFilter dict (from parse_query)

    Returns:
        list: All raw GraphQL issue nodes (dicts with nested relations).
    """
    all_issues = []
    cursor = None
    page = 0

    while True:
        variables = {
            'filter': graphql_filter,
            'first': 100,
            'after': cursor,
        }
        data = linear_utils.graphql_request(ISSUES_QUERY, variables=variables)
        connection = data.get('issues', {})
        nodes = connection.get('nodes', [])
        all_issues.extend(nodes)
        page += 1

        if linear_utils.VERBOSE:
            print(
                f"Page {page}: {len(nodes)} issues fetched "
                f"(running total: {len(all_issues)})",
                file=sys.stderr,
            )

        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']

    return all_issues


# ---------------------------------------------------------------------------
# Normalization — flatten nested GraphQL nodes into simple dicts
# ---------------------------------------------------------------------------

def normalize_issue(node):
    """Flatten a raw GraphQL issue node into a flat dict for output.

    Args:
        node: Raw GraphQL issue dict with nested objects (state, assignee, etc.)

    Returns:
        dict: Flat dict with all ALL_FIELDS keys present (None if not available).
    """
    state = node.get('state') or {}
    assignee = node.get('assignee') or {}
    cycle = node.get('cycle') or {}
    project = node.get('project') or {}
    parent = node.get('parent') or {}
    label_nodes = (node.get('labels') or {}).get('nodes', [])

    return {
        'identifier':   node.get('identifier'),
        'title':        node.get('title'),
        'description':  node.get('description'),
        'state':        state.get('name'),
        'stateType':    state.get('type'),
        'priority':     node.get('priority'),
        'priorityLabel': node.get('priorityLabel'),
        'assignee':     assignee.get('displayName') or assignee.get('name'),
        'assigneeEmail': assignee.get('email'),
        'labels':       ', '.join(l['name'] for l in label_nodes),
        'estimate':     node.get('estimate'),
        'cycle':        cycle.get('name'),
        'cycleNumber':  cycle.get('number'),
        'project':      project.get('name'),
        'parent':       parent.get('identifier'),
        'parentTitle':  parent.get('title'),
        'createdAt':    node.get('createdAt'),
        'updatedAt':    node.get('updatedAt'),
        'url':          linear_utils.issue_url(node.get('identifier', '')),
        'pullRequests': ', '.join(
            f"{a['url']} ({(a.get('metadata') or {}).get('status', 'unknown')})"
            for a in (node.get('attachments') or {}).get('nodes', [])
        ),
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _apply_field_selection(issues, fields):
    """Return issues with only the selected fields, in that order."""
    return [{k: issue.get(k) for k in fields} for issue in issues]


def output_json(issues, fields):
    """Write issues as a JSON array to stdout."""
    data = _apply_field_selection(issues, fields) if fields else issues
    print(json.dumps(data, indent=2, default=str))


def output_csv(issues, fields):
    """Write issues as CSV to stdout."""
    fieldnames = fields if fields else ALL_FIELDS
    data = _apply_field_selection(issues, fieldnames) if fields else issues

    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=fieldnames,
        extrasaction='ignore',
        lineterminator='\n',
    )
    writer.writeheader()
    writer.writerows(data)


def export_issues(
    query: Annotated[str, typer.Option("--query", "-q", help="JQL-like filter query string (required)")],
    csv_output: Annotated[bool, typer.Option("--csv", help="Output CSV instead of JSON")] = False,
    fields: Annotated[Optional[str], typer.Option("--fields", help=f"Comma-separated fields. Available: {', '.join(ALL_FIELDS)}")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    if verbose:
        linear_utils.VERBOSE = True

    selected_fields = None
    if fields:
        selected_fields = [f.strip() for f in fields.split(",")]
        unknown = [f for f in selected_fields if f not in ALL_FIELDS]
        if unknown:
            typer.echo(f"Error: unknown field(s): {', '.join(unknown)}. Available: {', '.join(ALL_FIELDS)}", err=True)
            raise typer.Exit(1)

    try:
        graphql_filter = parse_query(query)
    except (SyntaxError, ValueError) as e:
        typer.echo(f"Query error: {e}", err=True)
        raise typer.Exit(1)

    if linear_utils.VERBOSE:
        typer.echo(f"Compiled filter:\n{json.dumps(graphql_filter, indent=2)}", err=True)

    try:
        raw_nodes = fetch_issues(graphql_filter)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    issues = [normalize_issue(n) for n in raw_nodes]

    if csv_output:
        output_csv(issues, selected_fields)
    else:
        output_json(issues, selected_fields)

    typer.echo(f"Exported {len(issues)} issue(s).", err=True)
