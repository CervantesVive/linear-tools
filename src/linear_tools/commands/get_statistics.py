"""Calculate estimate statistics for Linear issues matching a query."""
import json
from typing import Annotated
import typer

from linear_tools import utils as linear_utils
from linear_tools.commands.export_issues import fetch_issues
from linear_tools.query_parser import parse_query

DEFAULT_ESTIMATE = 3
COMPLETED_STATE_TYPES = {'completed', 'cancelled'}


def extract_slug_id(project_input):
    """Extract the short hex slug ID from a project URL, full slug, or bare ID.

    Accepts:
      - Full URL:   'https://linear.app/bitgo/project/navbar-revamp-offsite-2d728a27e93e/issues?...'
      - Full slug:  'navbar-revamp-offsite-2d728a27e93e'
      - Short ID:   '2d728a27e93e'

    Returns:
        str: The last hyphen-delimited token of the slug (the short hex ID).

    Raises:
        ValueError: If the input is empty or a URL cannot be parsed.
    """
    value = project_input.strip()
    if not value:
        raise ValueError("Project input cannot be empty.")
    if value.startswith('http'):
        parts = value.split('/project/')
        if len(parts) < 2:
            raise ValueError(f"Could not parse project slug from URL: {project_input!r}")
        value = parts[1].split('/')[0].split('?')[0]
    return value.split('-')[-1]


def calculate_estimates_for_issues(issues):
    """Calculate estimate totals for a list of normalized issue dicts.

    Args:
        issues: List of dicts with 'stateType' and 'estimate' keys.

    Returns:
        tuple: (total_points, defaulted_count, explicit_count)
    """
    total_points = 0
    defaulted_count = 0
    explicit_count = 0

    for issue in issues:
        estimate = issue.get('estimate')
        if estimate is None:
            estimate = DEFAULT_ESTIMATE
            defaulted_count += 1
        else:
            explicit_count += 1
        total_points += estimate

    return total_points, defaulted_count, explicit_count


def get_query_statistics(query):
    """Get statistics for Linear issues matching a query string.

    Args:
        query: Linear JQL-like query string (same syntax as export-issues)

    Returns:
        dict with keys: total_points, resolved_points, unresolved_points,
        resolved_points_percentage, total_issues, resolved_issues,
        unresolved_issues, resolved_issues_percentage
    """
    graphql_filter = parse_query(query)
    raw_nodes = fetch_issues(graphql_filter)

    # Each raw node has state.type — flatten just what we need
    issues = [
        {
            'stateType': (node.get('state') or {}).get('type'),
            'estimate': node.get('estimate'),
        }
        for node in raw_nodes
    ]

    completed = [i for i in issues if i['stateType'] in COMPLETED_STATE_TYPES]
    unresolved = [i for i in issues if i['stateType'] not in COMPLETED_STATE_TYPES]

    resolved_points, _, _ = calculate_estimates_for_issues(completed)
    unresolved_points, _, _ = calculate_estimates_for_issues(unresolved)
    total_points = resolved_points + unresolved_points

    total_issues = len(issues)
    total_resolved = len(completed)
    total_unresolved = len(unresolved)

    resolved_points_pct = round(resolved_points / total_points * 100) if total_points > 0 else 0
    resolved_issues_pct = round(total_resolved / total_issues * 100) if total_issues > 0 else 0

    return {
        'total_points': total_points,
        'resolved_points': resolved_points,
        'unresolved_points': unresolved_points,
        'resolved_points_percentage': resolved_points_pct,
        'total_issues': total_issues,
        'resolved_issues': total_resolved,
        'unresolved_issues': total_unresolved,
        'resolved_issues_percentage': resolved_issues_pct,
    }


def get_statistics(
    query: Annotated[str, typer.Argument(help="Linear query string")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    if verbose:
        linear_utils.VERBOSE = True

    if not query.strip():
        typer.echo("Error: query cannot be empty", err=True)
        raise typer.Exit(1)

    if verbose:
        typer.echo(f"Processing query...", err=True)
        typer.echo(f"  Query: {query}", err=True)

    try:
        stats = get_query_statistics(query)
    except (SyntaxError, ValueError) as e:
        typer.echo(f"Query error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    if verbose:
        typer.echo(f"  Found {stats['total_issues']} issues, {stats['total_points']} total points", err=True)
        if stats['total_issues'] > 0:
            typer.echo(
                f"    Completed: {stats['resolved_issues']} issues, "
                f"{stats['resolved_points']} points ({stats['resolved_points_percentage']}%)",
                err=True,
            )
            typer.echo(
                f"    Unresolved: {stats['unresolved_issues']} issues, "
                f"{stats['unresolved_points']} points",
                err=True,
            )
        typer.echo("", err=True)

    output = {'query': query, **stats}
    typer.echo(json.dumps(output, indent=2))
