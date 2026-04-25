"""Attach URL links to Linear issues matched by a query."""
import json
from typing import Optional
from typing import Annotated

import typer

from linear_tools import utils as linear_utils
from linear_tools.query_parser import parse_query

ADD_LINKS_ISSUES_QUERY = """
query FetchIssuesForAddLinks($filter: IssueFilter!, $first: Int!, $after: String) {
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


def fetch_issues_for_add_links(graphql_filter):
    """Fetch issues matching filter, returning list of {id, identifier, title} dicts."""
    all_issues = []
    cursor = None
    while True:
        variables = {'filter': graphql_filter, 'first': 100, 'after': cursor}
        data = linear_utils.graphql_request(ADD_LINKS_ISSUES_QUERY, variables=variables)
        connection = data.get('issues', {})
        nodes = connection.get('nodes', [])
        all_issues.extend(nodes)
        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']
    return all_issues


def attach_links(issues, url, title=None):
    """Attach url to each issue. Returns list of result dicts."""
    results = []
    for issue in issues:
        identifier = issue['identifier']
        result = {'identifier': identifier, 'title': issue.get('title', '')}
        try:
            response = linear_utils.attach_url_to_issue(issue['id'], url, title=title)
            success = response.get('success', False)
            result['success'] = success
            if not success:
                result['error'] = f"Mutation failed for {identifier}"
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)
        results.append(result)
    return results


def print_table(results, url):
    """Print per-ticket results as a human-readable table."""
    for r in results:
        mark = '✓' if r['success'] else '✗'
        detail = r.get('error', '') if not r['success'] else f"Linked {url}"
        typer.echo(f"  {r['identifier']}  {mark}  {r['title']}  — {detail}")
    success_count = sum(1 for r in results if r['success'])
    typer.echo(f"\n{success_count}/{len(results)} link(s) attached.")


def add_links(
    query: Annotated[str, typer.Option("--query", "-q", help="JQL-like query string to match issues")],
    url: Annotated[str, typer.Option("--url", "-u", help="URL to attach to each matched issue")],
    title: Annotated[Optional[str], typer.Option("--title", "-t", help="Optional display title for the link")] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation when multiple tickets match")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output results as JSON")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable verbose output")] = False,
):
    if verbose:
        linear_utils.VERBOSE = True

    try:
        graphql_filter = parse_query(query)
    except (SyntaxError, ValueError) as e:
        typer.echo(f"Query error: {e}", err=True)
        raise typer.Exit(1)

    try:
        issues = fetch_issues_for_add_links(graphql_filter)
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
        confirmed = typer.confirm(f"\nAttach link to {len(issues)} tickets?", default=False)
        if not confirmed:
            raise typer.Exit(0)

    results = attach_links(issues, url, title=title)

    if json_output:
        typer.echo(json.dumps(results, indent=2))
    else:
        print_table(results, url)

    if any(not r['success'] for r in results):
        raise typer.Exit(1)
