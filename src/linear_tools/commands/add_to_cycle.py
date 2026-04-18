"""Add Linear issues to a team cycle."""
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from typing import Annotated

import typer

from linear_tools import utils as linear_utils


def parse_identifiers_from_text(text):
    """Extract Linear issue identifiers from arbitrary text."""
    return re.findall(r'\b([A-Z]+-\d+)\b', text)


def parse_identifiers_from_jira_json(text):
    """Extract Linear issue identifiers from jira_to_linear JSON output.

    Accepts the JSON array produced by jira_to_linear.py --json.
    Skips entries where linear_id is null (JIRA tickets with no Linear link).

    Args:
        text: JSON string — array of {jira_key, linear_id, linear_url} objects

    Returns:
        tuple: (identifiers list, skipped list)
               identifiers: Linear issue IDs that were successfully resolved
               skipped: jira_key values that had no linear_id
    """
    data = json.loads(text)
    identifiers = []
    skipped = []
    for entry in data:
        linear_id = entry.get('linear_id')
        if linear_id:
            identifiers.append(linear_id)
        else:
            skipped.append(entry.get('jira_key', '?'))
    return identifiers, skipped


def format_date(iso_string):
    """Format an ISO date string to a short human-readable date (e.g. 'Mar 17')."""
    if not iso_string:
        return '?'
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime('%b %-d')
    except (ValueError, AttributeError):
        return iso_string[:10]


def add_issues_to_cycle(identifiers, team_key, cycle_number=None):
    """Add a list of issues to a cycle for the given team.

    Args:
        identifiers: List of Linear issue identifiers, e.g. ["WEB-458", "WEB-461"]
        team_key: Team key string (e.g. "WEB")
        cycle_number: Optional cycle number to target. If None, uses the active cycle.

    Returns:
        tuple: (cycle_info dict, results list)
               cycle_info: {id, name, number, startsAt, endsAt} or None on failure
               results: list of {identifier, success, error (optional)}
    """
    if cycle_number is not None:
        cycle = linear_utils.get_cycle_by_number(team_key, cycle_number)
    else:
        cycle = linear_utils.get_active_cycle(team_key)
    if not cycle:
        return None, []

    id_map = linear_utils.resolve_issue_ids(identifiers)

    results = []
    for identifier in identifiers:
        uuid = id_map.get(identifier)
        if not uuid:
            results.append({
                'identifier': identifier,
                'success': False,
                'error': 'Issue not found',
            })
            continue

        try:
            response = linear_utils.update_issue(uuid, {'cycleId': cycle['id']})
            results.append({
                'identifier': identifier,
                'success': response.get('success', False),
            })
        except Exception as e:
            results.append({
                'identifier': identifier,
                'success': False,
                'error': str(e),
            })

    return cycle, results


def print_table(cycle, results, team_key):
    """Print results as a human-readable table."""
    if not cycle:
        print(f"Error: No cycle found for team {team_key}", file=sys.stderr)
        return

    start = format_date(cycle.get('startsAt'))
    end = format_date(cycle.get('endsAt'))
    cycle_label = cycle.get('name') or f"Cycle {cycle.get('number')}"
    print(f"\nTeam {team_key} — {cycle_label} ({start} – {end})\n")

    for r in results:
        if r['success']:
            print(f"  {r['identifier']}  ✓  Added to cycle")
        else:
            error = r.get('error', 'Unknown error')
            print(f"  {r['identifier']}  ✗  {error}")

    success_count = sum(1 for r in results if r['success'])
    print(f"\n{success_count}/{len(results)} issues added to cycle.")


def print_json(cycle, results):
    """Print results as a JSON object."""
    cycle_out = None
    if cycle:
        cycle_out = {
            'id': cycle.get('id'),
            'name': cycle.get('name'),
            'number': cycle.get('number'),
            'startsAt': cycle.get('startsAt'),
            'endsAt': cycle.get('endsAt'),
        }
    print(json.dumps({'cycle': cycle_out, 'results': results}, indent=2))


def add_to_cycle(
    identifiers: Annotated[Optional[List[str]], typer.Argument(help="Linear issue identifiers (e.g. WEB-458)")] = None,
    file: Annotated[Optional[str], typer.Option("-f", "--file", help="File containing issue identifiers")] = None,
    jira_json: Annotated[Optional[str], typer.Option("--jira-json", help="JSON from jira to-linear --json (use - for stdin)")] = None,
    team: Annotated[str, typer.Option("--team", help="Linear team key")] = "WEB",
    cycle: Annotated[Optional[int], typer.Option("--cycle", help="Target cycle number (default: active)")] = None,
    list_cycles: Annotated[bool, typer.Option("--list-cycles", help="List available cycles and exit")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output results as JSON")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    if verbose:
        linear_utils.VERBOSE = True

    if list_cycles:
        try:
            cycles = linear_utils.list_cycles(team)
        except Exception as e:
            typer.echo(f"Error: could not fetch cycles: {e}", err=True)
            raise typer.Exit(1)
        if not cycles:
            typer.echo(f"No cycles found for team {team}.", err=True)
            raise typer.Exit(1)
        if json_output:
            typer.echo(json.dumps(cycles, indent=2))
        else:
            typer.echo(f"\nCycles for team {team}:\n")
            for c in cycles:
                start = format_date(c.get('startsAt'))
                end = format_date(c.get('endsAt'))
                label = c.get('name') or f"Cycle {c.get('number')}"
                typer.echo(f"  #{c.get('number'):>3}  {label}  ({start} – {end})")
        raise typer.Exit(0)

    all_identifiers = list(identifiers or [])

    if file:
        try:
            text = Path(file).read_text()
        except FileNotFoundError:
            typer.echo(f"Error: file not found: {file}", err=True)
            raise typer.Exit(1)
        all_identifiers.extend(parse_identifiers_from_text(text))

    if jira_json:
        try:
            text = sys.stdin.read() if jira_json == "-" else Path(jira_json).read_text()
        except FileNotFoundError:
            typer.echo(f"Error: file not found: {jira_json}", err=True)
            raise typer.Exit(1)
        try:
            from_json, skipped = parse_identifiers_from_jira_json(text)
        except (json.JSONDecodeError, TypeError) as e:
            typer.echo(f"Error: could not parse JSON input: {e}", err=True)
            raise typer.Exit(1)
        if skipped:
            typer.echo(f"Skipping {len(skipped)} JIRA ticket(s) with no Linear link: {', '.join(skipped)}", err=True)
        all_identifiers.extend(from_json)

    seen = set()
    unique_identifiers = []
    for k in all_identifiers:
        if k not in seen:
            seen.add(k)
            unique_identifiers.append(k)

    if not unique_identifiers:
        typer.echo("Error: no issue identifiers provided. Use positional args, -f FILE, or --jira-json FILE.", err=True)
        raise typer.Exit(1)

    try:
        cycle_info, results = add_issues_to_cycle(unique_identifiers, team, cycle_number=cycle)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not cycle_info:
        if cycle:
            typer.echo(f"Error: Cycle #{cycle} not found for team {team}.", err=True)
        else:
            typer.echo(f"Error: No active cycle found for team {team}.", err=True)
        raise typer.Exit(1)

    if json_output:
        print_json(cycle_info, results)
    else:
        print_table(cycle_info, results, team)
