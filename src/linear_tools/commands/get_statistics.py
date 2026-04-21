"""Calculate estimate statistics for Linear issues matching a query."""
import json
from typing import Annotated
import typer

from linear_tools import utils as linear_utils
from linear_tools.commands.export_issues import fetch_issues
from linear_tools.query_parser import parse_query

DEFAULT_ESTIMATE = 3
COMPLETED_STATE_TYPES = {'completed', 'cancelled'}


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
