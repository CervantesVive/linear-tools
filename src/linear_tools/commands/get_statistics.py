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
