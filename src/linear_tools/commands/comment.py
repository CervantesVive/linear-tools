"""Post markdown comments to Linear issues matched by a query."""
import json
from pathlib import Path
from typing import Optional
from typing import Annotated

import typer

from linear_tools import utils as linear_utils
from linear_tools.query_parser import parse_query

COMMENT_ISSUES_QUERY = """
query FetchIssuesForComment($filter: IssueFilter!, $first: Int!, $after: String) {
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


def fetch_issues_for_comment(graphql_filter):
    """Fetch issues matching filter, returning list of {id, identifier, title} dicts."""
    all_issues = []
    cursor = None
    while True:
        variables = {'filter': graphql_filter, 'first': 100, 'after': cursor}
        data = linear_utils.graphql_request(COMMENT_ISSUES_QUERY, variables=variables)
        connection = data.get('issues', {})
        nodes = connection.get('nodes', [])
        all_issues.extend(nodes)
        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']
    return all_issues


def post_comments(issues, body):
    """Post body as a comment to each issue. Returns list of result dicts."""
    results = []
    for issue in issues:
        identifier = issue['identifier']
        try:
            success, message = linear_utils.post_comment_to_linear_issue(identifier, body)
            result = {'identifier': identifier, 'title': issue.get('title', ''), 'success': success}
            if not success:
                result['error'] = message
        except Exception as e:
            result = {'identifier': identifier, 'title': issue.get('title', ''), 'success': False, 'error': str(e)}
        results.append(result)
    return results


def print_table(results):
    """Print per-ticket results as a human-readable table."""
    for r in results:
        mark = '✓' if r['success'] else '✗'
        detail = r.get('error', '') if not r['success'] else 'Comment posted'
        typer.echo(f"  {r['identifier']}  {mark}  {r['title']}  — {detail}")
    success_count = sum(1 for r in results if r['success'])
    typer.echo(f"\n{success_count}/{len(results)} comment(s) posted.")


def comment(
    query: Annotated[str, typer.Option("--query", "-q", help="JQL-like query string to match issues")],
    message: Annotated[Optional[str], typer.Option("-m", "--message", help="Inline comment body")] = None,
    file: Annotated[Optional[str], typer.Option("-f", "--file", help="Path to markdown file to post as comment")] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation when multiple tickets match")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output results as JSON")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable verbose output")] = False,
):
    if verbose:
        linear_utils.VERBOSE = True

    if message is None and file is None:
        typer.echo("Error: provide --message or --file.", err=True)
        raise typer.Exit(1)
    if message is not None and file is not None:
        typer.echo("Error: --message and --file are mutually exclusive.", err=True)
        raise typer.Exit(1)

    body = message
    if file is not None:
        try:
            body = Path(file).read_text()
        except FileNotFoundError:
            typer.echo(f"Error: file not found: {file}", err=True)
            raise typer.Exit(1)

    try:
        graphql_filter = parse_query(query)
    except (SyntaxError, ValueError) as e:
        typer.echo(f"Query error: {e}", err=True)
        raise typer.Exit(1)

    try:
        issues = fetch_issues_for_comment(graphql_filter)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    if not issues:
        typer.echo("No issues found for query.", err=True)
        raise typer.Exit(1)

    if len(issues) > 1 and not yes:
        typer.echo(f"\nMatched {len(issues)} tickets:")
        for issue in issues:
            typer.echo(f"  {issue['identifier']}  {issue['title']}")
        confirmed = typer.confirm(f"\nPost comment to {len(issues)} tickets?", default=False)
        if not confirmed:
            raise typer.Exit(0)

    results = post_comments(issues, body)

    if json_output:
        typer.echo(json.dumps(results, indent=2))
    else:
        print_table(results)

    if any(not r['success'] for r in results):
        raise typer.Exit(1)
