"""Report GitHub PR merge status for Linear issues matched by a query."""
import json
import subprocess
import sys
from typing import Annotated

import typer

from linear_tools import utils as linear_utils
from linear_tools.query_parser import parse_query


_ISSUES_QUERY = """
query MergedIssues($filter: IssueFilter!, $first: Int!, $after: String) {
  issues(filter: $filter, first: $first, after: $after, orderBy: createdAt) {
    nodes {
      id
      identifier
      title
      attachments(filter: { sourceType: { eq: "github" } }) {
        nodes {
          url
          title
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


def _fetch_issues(graphql_filter: dict) -> list[dict]:
    """Fetch all issues matching the compiled Linear filter."""
    all_issues: list[dict] = []
    cursor = None
    page = 0

    while True:
        data = linear_utils.graphql_request(
            _ISSUES_QUERY,
            variables={"filter": graphql_filter, "first": 100, "after": cursor},
        )
        connection = data.get("issues", {})
        nodes = connection.get("nodes", [])
        all_issues.extend(nodes)
        page += 1

        if linear_utils.VERBOSE:
            print(
                f"Page {page}: {len(nodes)} issues fetched "
                f"(running total: {len(all_issues)})",
                file=sys.stderr,
            )

        page_info = connection.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info["endCursor"]

    return all_issues


def _pr_status(pr_data: dict) -> str:
    """Normalize GitHub PR JSON into the statuses shown by this command."""
    state = (pr_data.get("state") or "").upper()
    if state == "MERGED":
        return "Merged"
    if pr_data.get("isDraft") and state == "OPEN":
        return "Draft"
    if state == "CLOSED":
        return "Closed"
    if state == "OPEN":
        return "Open"
    return "Unknown"


def _fetch_pr(url: str) -> dict:
    """Fetch current PR details from GitHub CLI for a PR URL."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", url, "--json", "title,state,isDraft,url"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return {
            "url": url,
            "title": None,
            "status": "Unknown",
        }

    data["status"] = _pr_status(data)
    data["url"] = data.get("url") or url
    return data


def _issue_pr_urls(issue: dict) -> list[str]:
    nodes = (issue.get("attachments") or {}).get("nodes", [])
    urls = []
    seen = set()
    for node in nodes:
        url = node.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def build_rows(issues: list[dict], pr_fetcher=None) -> list[dict]:
    """Return flat rows: one row per associated GitHub PR."""
    if pr_fetcher is None:
        pr_fetcher = _fetch_pr

    rows = []
    for issue in issues:
        identifier = issue.get("identifier")
        issue_url = linear_utils.issue_url(identifier) if identifier else None
        urls = _issue_pr_urls(issue)
        if not urls:
            rows.append({
                "identifier": identifier,
                "issueTitle": issue.get("title"),
                "issueUrl": issue_url,
                "prTitle": None,
                "prUrl": None,
                "status": "No PR",
            })
            continue

        for url in urls:
            pr = pr_fetcher(url)
            rows.append({
                "identifier": identifier,
                "issueTitle": issue.get("title"),
                "issueUrl": issue_url,
                "prTitle": pr.get("title"),
                "prUrl": pr.get("url") or url,
                "status": pr.get("status") or "Unknown",
            })
    return rows


def _style_status(status: str, color: bool = True) -> str:
    if color and status == "Merged":
        return typer.style(status, fg=typer.colors.GREEN)
    return status


def output_table(rows: list[dict], color: bool = True) -> None:
    """Print rows as a compact terminal table."""
    if not rows:
        typer.echo("No issues found.")
        return

    id_width = max(len(row.get("identifier") or "") for row in rows)
    status_width = max(len(row.get("status") or "") for row in rows)

    typer.echo(f"{'ISSUE':<{id_width}}  {'STATUS':<{status_width}}  ISSUE URL  PR")
    typer.echo(f"{'-' * id_width}  {'-' * status_width}  {'-' * 9}  {'-' * 2}")
    for row in rows:
        status = row.get("status") or "Unknown"
        issue_url = row.get("issueUrl") or ""
        pr_label = row.get("prUrl") or "(no GitHub PR)"
        if row.get("prTitle"):
            pr_label = f"{row['prTitle']} - {pr_label}"

        typer.echo(
            f"{row.get('identifier') or '':<{id_width}}  "
            f"{_style_status(status, color):<{status_width}}  "
            f"{issue_url}  "
            f"{pr_label}"
        )


def merged_issues(
    query: Annotated[str, typer.Argument(help="JQL-like Linear issue query")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colored status output")] = False,
):
    """Show associated GitHub PRs and whether each is merged, draft, closed, or open."""
    linear_utils.VERBOSE = verbose

    if not query.strip():
        typer.echo("Error: query cannot be empty.", err=True)
        raise typer.Exit(1)

    try:
        graphql_filter = parse_query(query)
    except (SyntaxError, ValueError) as e:
        typer.echo(f"Query error: {e}", err=True)
        raise typer.Exit(1)

    if verbose:
        typer.echo(f"Compiled filter:\n{json.dumps(graphql_filter, indent=2)}", err=True)

    try:
        issues = _fetch_issues(graphql_filter)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    rows = build_rows(issues)
    output_table(rows, color=not no_color)
    typer.echo(f"Checked {len(issues)} issue(s), {len([r for r in rows if r['prUrl']])} PR(s).", err=True)
