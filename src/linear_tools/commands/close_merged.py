"""Promote Linear issues to 'Done' once their GitHub PRs have been merged long enough."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated

import typer

from linear_tools import utils as lu
from linear_tools.query_parser import parse_query
from tools_shared.logging import setup_logging, log_info, log_warning, log_error, log_success


_ISSUES_QUERY = """
query CloseMerged($filter: IssueFilter!, $first: Int!, $after: String) {
  issues(filter: $filter, first: $first, after: $after, orderBy: createdAt) {
    nodes {
      id
      identifier
      title
      state { name }
      attachments(filter: { sourceType: { eq: "github" } }) {
        nodes { url }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _fetch_issues(graphql_filter: dict) -> list[dict]:
    all_issues: list[dict] = []
    cursor = None
    while True:
        data = lu.graphql_request(
            _ISSUES_QUERY,
            variables={"filter": graphql_filter, "first": 100, "after": cursor},
        )
        connection = data.get("issues", {})
        nodes = connection.get("nodes", [])
        all_issues.extend(nodes)
        page_info = connection.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info["endCursor"]
    return all_issues


def _pr_merged_at(url: str) -> datetime | None:
    """Return the merge timestamp of a GitHub PR URL, or None if not merged / not found."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", url, "--json", "mergedAt,state"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return None
    if data.get("state") != "MERGED" or not data.get("mergedAt"):
        return None
    return datetime.fromisoformat(data["mergedAt"].replace("Z", "+00:00"))


def close_merged(
    query: Annotated[str, typer.Option("--query", "-q", help="JQL-like filter (same syntax as export-issues)")],
    merged_since: Annotated[
        Optional[str],
        typer.Option("--merged-since", "-s", help="YYYY-MM-DD cutoff date; default is 3 days ago"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview changes without mutating")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    """Move issues to Done when their GitHub PRs merged on or before the cutoff date."""
    setup_logging(verbose)
    lu.VERBOSE = verbose

    if merged_since:
        try:
            cutoff = datetime.fromisoformat(merged_since).replace(tzinfo=timezone.utc)
        except ValueError:
            log_error(f"Invalid date '{merged_since}' — expected YYYY-MM-DD.")
            raise typer.Exit(1)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        log_info(f"No --merged-since given; using {cutoff.date()} (3 days ago).")

    try:
        graphql_filter = parse_query(query)
    except (SyntaxError, ValueError) as e:
        log_error(f"Query error: {e}")
        raise typer.Exit(1)

    if verbose:
        log_info(f"Compiled filter:\n{json.dumps(graphql_filter, indent=2)}")

    log_info("Fetching issues from Linear…")
    try:
        issues = _fetch_issues(graphql_filter)
    except Exception as e:
        log_error(f"Linear API error: {e}")
        raise typer.Exit(1)

    log_info(f"{len(issues)} issue(s) matched query.")

    qualifying: list[dict] = []
    skipped_no_pr = 0
    skipped_too_recent = 0

    for issue in issues:
        pr_nodes = (issue.get("attachments") or {}).get("nodes", [])
        if not pr_nodes:
            skipped_no_pr += 1
            if verbose:
                log_info(f"  {issue['identifier']}: no GitHub PR — skip")
            continue

        best_url: str | None = None
        best_ts: datetime | None = None
        for node in pr_nodes:
            url = node.get("url", "")
            merged_at = _pr_merged_at(url)
            if verbose:
                ts = merged_at.isoformat() if merged_at else "not merged"
                log_info(f"  {issue['identifier']} → {url}: {ts}")
            if merged_at and merged_at <= cutoff:
                if best_ts is None or merged_at < best_ts:
                    best_ts = merged_at
                    best_url = url

        if best_ts is not None:
            qualifying.append({"issue": issue, "pr_url": best_url, "merged_at": best_ts})
        else:
            skipped_too_recent += 1

    log_info(
        f"Will update: {len(qualifying)} | no PR: {skipped_no_pr} | PR too recent: {skipped_too_recent}"
    )

    if not qualifying:
        log_info("Nothing to update.")
        return

    # Print preview table
    id_width = max(len(q["issue"]["identifier"]) for q in qualifying)
    header = f"  {'IDENTIFIER':<{id_width}}  {'MERGED AT':<10}  PR URL"
    typer.echo(header, err=True)
    typer.echo("  " + "-" * (len(header) - 2), err=True)
    for q in qualifying:
        typer.echo(
            f"  {q['issue']['identifier']:<{id_width}}  "
            f"{str(q['merged_at'].date()):<10}  "
            f"{q['pr_url']}",
            err=True,
        )

    if dry_run:
        log_info("Dry run — no changes made.")
        return

    # Resolve 'Done' state UUID per team (one API call per team)
    team_states: dict[str, dict] = {}
    updated = 0
    failed = 0

    for q in qualifying:
        issue = q["issue"]
        team_key = issue["identifier"].split("-")[0]

        if team_key not in team_states:
            team_states[team_key] = lu.get_workflow_states(team_key)

        done_id = team_states[team_key].get("Done")
        if not done_id:
            log_warning(f"{issue['identifier']}: no 'Done' state for team {team_key} — skip")
            failed += 1
            continue

        result = lu.update_issue(issue["id"], {"stateId": done_id})
        if result.get("success"):
            log_success(f"{issue['identifier']}: → Done")
            updated += 1
        else:
            log_error(f"{issue['identifier']}: update failed")
            failed += 1

    log_info(f"Done. Updated: {updated} | failed: {failed}")
