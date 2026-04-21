"""Export Linear projects with a flexible JQL-like query language."""
import sys
import csv
import json
from typing import Optional
from typing import Annotated

import typer

from linear_tools import utils as linear_utils
from linear_tools.project_query_parser import parse_project_query

PRIORITY_LABELS = {0: 'No Priority', 1: 'Urgent', 2: 'High', 3: 'Medium', 4: 'Low'}

PROJECTS_QUERY = """
query ExportProjects($filter: ProjectFilter!, $first: Int!, $after: String) {
  projects(filter: $filter, first: $first, after: $after, orderBy: createdAt) {
    nodes {
      id
      name
      description
      url
      priority
      startDate
      targetDate
      createdAt
      updatedAt
      status { name type }
      labels { nodes { name } }
      lead { displayName name }
      teams { nodes { name key } }
      initiatives { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

DEFAULT_FIELDS = ['name', 'url', 'state']

ALL_FIELDS = [
    'name', 'url',
    'state', 'stateType',
    'priority', 'priorityLabel',
    'labels',
    'lead',
    'teams',
    'initiative',
    'startDate', 'targetDate',
    'createdAt', 'updatedAt',
    'description',
]


def fetch_projects(graphql_filter):
    """Fetch all projects matching the filter, paginating through all results.

    Args:
        graphql_filter: Linear ProjectFilter dict (from parse_project_query)

    Returns:
        list: All raw GraphQL project nodes.
    """
    all_projects = []
    cursor = None
    page = 0

    while True:
        variables = {
            'filter': graphql_filter,
            'first': 50,
            'after': cursor,
        }
        data = linear_utils.graphql_request(PROJECTS_QUERY, variables=variables)
        connection = data.get('projects', {})
        nodes = connection.get('nodes', [])
        all_projects.extend(nodes)
        page += 1

        if linear_utils.VERBOSE:
            print(
                f"Page {page}: {len(nodes)} projects fetched "
                f"(running total: {len(all_projects)})",
                file=sys.stderr,
            )

        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']

    return all_projects


def normalize_project(node):
    """Flatten a raw GraphQL project node into a flat dict for output.

    Args:
        node: Raw GraphQL project dict with nested objects.

    Returns:
        dict: Flat dict with all ALL_FIELDS keys present (None if not available).
    """
    status = node.get('status') or {}
    lead = node.get('lead') or {}
    label_nodes = (node.get('labels') or {}).get('nodes', [])
    team_nodes = (node.get('teams') or {}).get('nodes', [])
    initiative_nodes = (node.get('initiatives') or {}).get('nodes', [])
    priority = node.get('priority')

    return {
        'name':          node.get('name'),
        'url':           node.get('url'),
        'state':         status.get('name'),
        'stateType':     status.get('type'),
        'priority':      priority,
        'priorityLabel': PRIORITY_LABELS.get(priority) if priority is not None else None,
        'labels':        ', '.join(l['name'] for l in label_nodes),
        'lead':          lead.get('displayName') or lead.get('name'),
        'teams':         ', '.join(t['key'] for t in team_nodes),
        'initiative':    ', '.join(i['name'] for i in initiative_nodes),
        'startDate':     node.get('startDate'),
        'targetDate':    node.get('targetDate'),
        'createdAt':     node.get('createdAt'),
        'updatedAt':     node.get('updatedAt'),
        'description':   node.get('description'),
    }


def _apply_field_selection(projects, fields):
    """Return projects with only the selected fields, in that order."""
    return [{k: p.get(k) for k in fields} for p in projects]


def output_json(projects, fields):
    """Write projects as a JSON array to stdout."""
    print(json.dumps(_apply_field_selection(projects, fields), indent=2, default=str))


def output_csv(projects, fields):
    """Write projects as CSV to stdout."""
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=fields,
        extrasaction='ignore',
        lineterminator='\n',
    )
    writer.writeheader()
    writer.writerows(_apply_field_selection(projects, fields))


def export_projects(
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
        graphql_filter = parse_project_query(query)
    except (SyntaxError, ValueError) as e:
        typer.echo(f"Query error: {e}", err=True)
        raise typer.Exit(1)

    if linear_utils.VERBOSE:
        typer.echo(f"Compiled filter:\n{json.dumps(graphql_filter, indent=2)}", err=True)

    try:
        raw_nodes = fetch_projects(graphql_filter)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    projects = [normalize_project(n) for n in raw_nodes]
    output_fields = selected_fields if selected_fields else DEFAULT_FIELDS

    if csv_output:
        output_csv(projects, output_fields)
    else:
        output_json(projects, output_fields)

    typer.echo(f"Exported {len(projects)} project(s).", err=True)
