"""Map Linear issue IDs to their corresponding JIRA issue keys."""
import csv
import json
import re
import sys
from pathlib import Path
from typing import Annotated, List, Optional

import typer

from linear_tools import utils as linear_utils

JIRA_QUERY = """
query ToJira($teamKey: String!, $numbers: [Float!]!, $first: Int!, $after: String) {
  issues(filter: {
    team: { key: { eq: $teamKey } },
    number: { in: $numbers }
  }, first: $first, after: $after) {
    nodes {
      identifier
      attachments {
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


def find_jira_link(attachments):
    """Extract JIRA key and URL from a list of Linear attachment nodes.

    Args:
        attachments: List of attachment dicts with 'url' and 'title' keys

    Returns:
        tuple: (jira_key, jira_url) or (None, None) if no JIRA link found
    """
    for attachment in attachments:
        url = attachment.get("url", "")
        if "/browse/" not in url:
            continue
        match = re.search(r"([A-Z]+-\d+)", url)
        jira_key = match.group(1) if match else None
        return jira_key, url
    return None, None


def read_export_ids(path):
    """Extract Linear IDs from a linear export-issues --json output file.

    Args:
        path: Path to the JSON file (array of objects with an 'identifier' field)

    Returns:
        list: Linear IDs in the order they appear

    Raises:
        SystemExit: If the file is not found, invalid JSON, or not a JSON array
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Error: JSON file not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in file: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print("Error: JSON file must contain an array", file=sys.stderr)
        sys.exit(1)

    return [item["identifier"] for item in data if "identifier" in item]


def read_csv_ids(path):
    """Extract Linear IDs from a Linear CSV export file (ID column).

    Args:
        path: Path to the CSV file with an 'ID' column

    Returns:
        list: Linear IDs in the order they appear

    Raises:
        SystemExit: If the file is not found or has no 'ID' column
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"Error: CSV file not found: {path}", file=sys.stderr)
        sys.exit(1)

    reader = csv.reader(text.splitlines())
    try:
        headers = next(reader)
    except StopIteration:
        print(f"Error: CSV file is empty: {path}", file=sys.stderr)
        sys.exit(1)

    if "ID" not in headers:
        print(
            f"Error: could not find 'ID' column in CSV headers: {headers}",
            file=sys.stderr,
        )
        sys.exit(1)

    col_index = headers.index("ID")
    return [
        row[col_index].strip()
        for row in reader
        if col_index < len(row) and row[col_index].strip()
    ]


def lookup_jira_ids(linear_ids):
    """Fetch JIRA keys for a list of Linear issue identifiers via the GraphQL API.

    Groups identifiers by team key and queries each team separately, matching
    the approach used by resolve_issue_ids() in utils.py — IssueFilter does not
    support filtering by identifier directly, only by team+number.

    Args:
        linear_ids: List of Linear issue identifiers (e.g. ["WEB-123", "ENG-456"])

    Returns:
        list of dicts: [{linear_id, jira_key, jira_url}] in input order;
                       jira_key and jira_url are None when no JIRA link is found
    """
    # Group by team key (prefix before the dash)
    by_team = {}
    for identifier in linear_ids:
        match = re.match(r'^([A-Z]+)-(\d+)$', identifier)
        if not match:
            if linear_utils.VERBOSE:
                print(f"Skipping invalid identifier: {identifier}", file=sys.stderr)
            continue
        team_key = match.group(1)
        number = int(match.group(2))
        by_team.setdefault(team_key, []).append((identifier, number))

    fetched = {}
    for team_key, items in by_team.items():
        numbers = [n for _, n in items]
        cursor = None

        while True:
            variables = {"teamKey": team_key, "numbers": numbers, "first": 250, "after": cursor}
            data = linear_utils.graphql_request(JIRA_QUERY, variables=variables)
            connection = data.get("issues", {})

            for node in connection.get("nodes", []):
                identifier = node["identifier"]
                attachment_nodes = (node.get("attachments") or {}).get("nodes", [])
                jira_key, jira_url = find_jira_link(attachment_nodes)
                fetched[identifier] = {"jira_key": jira_key, "jira_url": jira_url}

                if linear_utils.VERBOSE:
                    print(f"{identifier}  →  {jira_key or '(no JIRA link)'}", file=sys.stderr)

            page_info = connection.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info["endCursor"]

    # Merge back in input order, emitting null rows for IDs absent from API
    return [
        {
            "linear_id": lid,
            "jira_key": fetched.get(lid, {}).get("jira_key"),
            "jira_url": fetched.get(lid, {}).get("jira_url"),
        }
        for lid in linear_ids
    ]


def _print_table(results):
    for r in results:
        linear_id = r["linear_id"]
        if r["jira_key"]:
            print(f"{linear_id}  →  {r['jira_key']}  {r['jira_url']}")
        else:
            print(f"{linear_id}  →  (no JIRA link found)")


def to_jira(
    ctx: typer.Context,
    linear_ids: Annotated[Optional[List[str]], typer.Argument(help="Linear issue IDs (e.g. WEB-123)")] = None,
    file: Annotated[Optional[str], typer.Option("-f", "--file", help="File containing Linear IDs, one per line")] = None,
    from_json: Annotated[Optional[str], typer.Option("--from-json", help="linear export-issues --json output file")] = None,
    from_csv: Annotated[Optional[str], typer.Option("--from-csv", help="Linear CSV export file (ID column)")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON array")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    """Map Linear issue IDs to their corresponding JIRA issue keys."""
    if verbose:
        linear_utils.VERBOSE = True

    all_ids = list(linear_ids or [])

    if file:
        try:
            text = Path(file).read_text()
        except FileNotFoundError:
            typer.echo(f"Error: file not found: {file}", err=True)
            raise typer.Exit(1)
        all_ids.extend(line.strip() for line in text.splitlines() if line.strip())

    if from_json:
        all_ids.extend(read_export_ids(from_json))

    if from_csv:
        all_ids.extend(read_csv_ids(from_csv))

    if not all_ids:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)

    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for lid in all_ids:
        if lid not in seen:
            seen.add(lid)
            unique_ids.append(lid)

    try:
        results = lookup_jira_ids(unique_ids)
    except Exception as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(json.dumps(results, indent=2))
    else:
        _print_table(results)
