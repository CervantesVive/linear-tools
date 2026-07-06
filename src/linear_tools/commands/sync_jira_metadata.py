"""Sync JIRA metadata (priority, status, story points) to Linear issues."""
import sys
import csv
import json
from pathlib import Path
from typing import Optional
from typing import Annotated

import typer

from linear_tools import utils as linear_utils

# ---------------------------------------------------------------------------
# Field mapping constants
# ---------------------------------------------------------------------------

JIRA_PRIORITY_TO_LINEAR = {
    "Highest": 1,       # Urgent
    "High": 2,
    "Medium": 3,
    "Low": 4,
    "Lowest": 4,
    "Needs Triage": 0,  # No Priority
}

JIRA_STATUS_TO_LINEAR = {
    "BACKLOG": "Backlog",
    "TO DO": "Todo",
    "TRIAGED": "Todo",
    "IN PROGRESS": "In Progress",
    "BLOCKED": "Blocked",
    "WAITING FOR SUPPORT": "Blocked",
    "IN REVIEW": "In Review",
    "REVIEW": "In Review",
    "IN TEST REVIEW": "In Review",
    "IN TEST": "Merged",
    "READY FOR TEST": "Merged",
    "READY FOR PROD": "Merged",
    "DONE": "Done",
    "TESTED": "Done",
    "WON'T FIX": "Canceled",
    "DUPLICATE": "Duplicate",
}

# Known candidate names for the issue key column
ISSUE_KEY_CANDIDATES = ["Issue Key", "Issue key", "Key"]

# ---------------------------------------------------------------------------
# Workflow state cache (team_key -> {state_name: state_uuid})
# ---------------------------------------------------------------------------

_workflow_state_cache = {}
_user_cache = None   # {display_name_lower: user_uuid}
_label_cache = None  # {label_name_lower: label_uuid}


def get_state_id(team_key, linear_state_name):
    """Resolve a Linear state name to its UUID for the given team.

    Caches the team's workflow states on first call to avoid repeated API queries.

    Args:
        team_key: e.g. "WEB"
        linear_state_name: e.g. "In Progress"

    Returns:
        str: state UUID, or None if no matching state found
    """
    if team_key not in _workflow_state_cache:
        _workflow_state_cache[team_key] = linear_utils.get_workflow_states(team_key)

    states = _workflow_state_cache[team_key]
    # Case-insensitive match
    for name, uuid in states.items():
        if name.lower() == linear_state_name.lower():
            return uuid
    return None


def get_user_id(display_name):
    """Resolve a display name to a Linear user UUID.

    Caches all org users on first call to avoid repeated API queries.

    Args:
        display_name: e.g. "Jamie Rivera"

    Returns:
        str: user UUID, or None if no matching user found
    """
    global _user_cache
    if _user_cache is None:
        _user_cache = linear_utils.get_org_users()
    return _user_cache.get(display_name.lower())


def get_label_id(label_name):
    """Resolve a label name to a Linear label UUID.

    Caches all workspace labels on first call to avoid repeated API queries.

    Args:
        label_name: e.g. "Bug"

    Returns:
        str: label UUID, or None if no matching label found
    """
    global _label_cache
    if _label_cache is None:
        _label_cache = linear_utils.get_workspace_labels()
    return _label_cache.get(label_name.strip().lower())


# ---------------------------------------------------------------------------
# CSV parsing with duplicate column handling
# ---------------------------------------------------------------------------

def load_csv(csv_path):
    """Load a JIRA CSV export, returning (headers, rows) with duplicate column handling.

    JIRA exports can produce duplicate column names (e.g. two "Custom field (Story Points)"
    columns where only one contains data). This function uses csv.reader to preserve all
    column indices, then renames duplicates as "col", "col.1", "col.2", etc. — matching
    what csv.DictReader would produce — so callers can work with named fields.

    Returns:
        tuple: (headers: list[str], rows: list[dict])
               headers: deduplicated header names
               rows: list of dicts keyed by deduplicated header names
    """
    path = Path(csv_path)
    if not path.exists():
        print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        raw_headers = next(reader)

        # Deduplicate headers the same way csv.DictReader does
        seen = {}
        headers = []
        for h in raw_headers:
            if h in seen:
                seen[h] += 1
                headers.append(f"{h}.{seen[h]}")
            else:
                seen[h] = 0
                headers.append(h)

        rows = []
        for raw_row in reader:
            # Pad short rows to header length
            padded = raw_row + [''] * (len(headers) - len(raw_row))
            rows.append(dict(zip(headers, padded)))

    return headers, rows


def detect_columns(headers, overrides=None):
    """Auto-detect CSV column names for the fields we need.

    For the story points field, JIRA exports two "Custom field (Story Points)" columns.
    After deduplication by load_csv, they become "Custom field (Story Points)" and
    "Custom field (Story Points).1". We return both candidates so the caller can
    pick whichever has data for each row.

    Args:
        headers: List of (possibly deduplicated) column header strings
        overrides: dict of field -> column name, from CLI flags

    Returns:
        dict with keys: 'issue_key', 'story_points', 'priority', 'status', 'assignee', 'labels'
             Values are the header string(s) to use:
             - 'story_points' may be a list [primary, fallback] for duplicate handling
             - others are a single string or None if not detected
    """
    overrides = overrides or {}
    result = {}

    def find(candidates):
        for c in candidates:
            if c in headers:
                return c
        return None

    result['issue_key'] = overrides.get('issue_key') or find(ISSUE_KEY_CANDIDATES)
    result['priority'] = overrides.get('priority') or find(["Priority"])
    result['status'] = overrides.get('status') or find(["Status"])

    # Story points: handle the duplicate column case
    if overrides.get('story_points'):
        result['story_points'] = [overrides['story_points']]
    else:
        sp_candidates = [
            h for h in headers
            if h == "Custom field (Story Points)" or h.startswith("Custom field (Story Points).")
        ]
        result['story_points'] = sp_candidates if sp_candidates else []

    result['assignee'] = overrides.get('assignee') or find(["Assignee"])
    result['labels'] = overrides.get('labels') or find(["Labels"])

    return result


def extract_story_points(row, sp_columns):
    """Extract story points value from a row, trying each candidate column.

    Returns the first non-empty numeric value found, or None.
    """
    for col in sp_columns:
        val = row.get(col, '').strip()
        if val:
            try:
                return float(val)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# JIRA mapping loading
# ---------------------------------------------------------------------------

def load_jira_mapping(path):
    """Load a JIRA-to-Linear ID mapping from jira_to_linear.py --json output.

    Args:
        path: File path string, or '-' to read from stdin

    Returns:
        dict: {jira_key: linear_id}, e.g. {"CE-10239": "WEB-458"}
              linear_id may be None for unmapped entries.
    """
    if path == '-':
        text = sys.stdin.read()
    else:
        p = Path(path)
        if not p.exists():
            print(f"Error: mapping file not found: {path}", file=sys.stderr)
            sys.exit(1)
        text = p.read_text()

    try:
        entries = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in mapping file: {e}", file=sys.stderr)
        sys.exit(1)

    return {entry['jira_key']: entry.get('linear_id') for entry in entries}


# ---------------------------------------------------------------------------
# Per-row update building
# ---------------------------------------------------------------------------

def build_update_input(row, columns, team_key, enabled_fields):
    """Build a Linear IssueUpdateInput dict from one CSV row.

    Args:
        row: dict from load_csv rows
        columns: detected column mapping from detect_columns()
        team_key: Linear team key for workflow state resolution (e.g. "WEB")
        enabled_fields: set of field names to include: {'priority', 'estimate', 'status', 'assignee', 'labels'}

    Returns:
        tuple: (input_dict, skipped_fields)
               input_dict: dict of Linear fields to update
               skipped_fields: list of (field, reason) tuples for fields that were skipped
    """
    input_dict = {}
    skipped = []

    # Priority
    if 'priority' in enabled_fields and columns.get('priority'):
        raw = row.get(columns['priority'], '').strip()
        if raw:
            mapped = JIRA_PRIORITY_TO_LINEAR.get(raw)
            if mapped is not None:
                input_dict['priority'] = mapped
            else:
                skipped.append(('priority', f"unmapped value '{raw}'"))
        else:
            skipped.append(('priority', 'empty'))

    # Estimate (story points)
    if 'estimate' in enabled_fields and columns.get('story_points'):
        val = extract_story_points(row, columns['story_points'])
        if val is not None:
            input_dict['estimate'] = val
        else:
            skipped.append(('estimate', 'empty or non-numeric'))

    # Status -> stateId
    if 'status' in enabled_fields and columns.get('status'):
        raw = row.get(columns['status'], '').strip()
        if raw:
            linear_state_name = JIRA_STATUS_TO_LINEAR.get(raw.upper())
            if linear_state_name:
                state_id = get_state_id(team_key, linear_state_name)
                if state_id:
                    input_dict['stateId'] = state_id
                else:
                    skipped.append(('status', f"Linear state '{linear_state_name}' not found in team {team_key}"))
            else:
                skipped.append(('status', f"unmapped JIRA status '{raw}'"))
        else:
            skipped.append(('status', 'empty'))

    # Assignee -> assigneeId
    if 'assignee' in enabled_fields and columns.get('assignee'):
        raw = row.get(columns['assignee'], '').strip()
        if raw:
            user_id = get_user_id(raw)
            if user_id:
                input_dict['assigneeId'] = user_id
            else:
                skipped.append(('assignee', f"user '{raw}' not found in Linear"))
        else:
            skipped.append(('assignee', 'empty'))

    # Labels -> labelIds
    if 'labels' in enabled_fields and columns.get('labels'):
        raw = row.get(columns['labels'], '').strip()
        if raw:
            raw_labels = [l.strip() for l in raw.split(',') if l.strip()]
            label_ids = []
            unknown_labels = []
            for label_name in raw_labels:
                lid = get_label_id(label_name)
                if lid:
                    label_ids.append(lid)
                else:
                    unknown_labels.append(label_name)
            if label_ids:
                input_dict['labelIds'] = label_ids
            if unknown_labels:
                skipped.append(('labels', f"unknown label(s): {', '.join(unknown_labels)}"))
            if not label_ids and not unknown_labels:
                skipped.append(('labels', 'empty after parsing'))
        else:
            skipped.append(('labels', 'empty'))

    return input_dict, skipped


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync_metadata(rows, columns, jira_mapping, team_key, enabled_fields, dry_run=False):
    """Sync JIRA metadata to Linear for all CSV rows.

    Args:
        rows: list of CSV row dicts from load_csv
        columns: detected column mapping from detect_columns()
        jira_mapping: {jira_key: linear_id} from load_jira_mapping()
        team_key: Linear team key for workflow state resolution
        enabled_fields: set of fields to sync: {'priority', 'estimate', 'status'}
        dry_run: if True, compute changes but do not call the API

    Returns:
        list of result dicts with keys:
            jira_key, linear_id, updates, skipped, success, error (optional)
    """
    issue_key_col = columns.get('issue_key')
    if not issue_key_col:
        print("Error: could not detect JIRA issue key column. Use --issue-key-column.", file=sys.stderr)
        sys.exit(1)

    # Collect all Linear identifiers from rows that have a mapping
    linear_ids = []
    row_mappings = []
    for row in rows:
        jira_key = row.get(issue_key_col, '').strip()
        if not jira_key:
            continue
        linear_id = jira_mapping.get(jira_key)
        row_mappings.append((row, jira_key, linear_id))
        if linear_id:
            linear_ids.append(linear_id)

    # Batch-resolve Linear identifiers to internal UUIDs
    # (done even in dry_run, to surface any resolution failures in the preview)
    uuid_map = {}
    if linear_ids:
        uuid_map = linear_utils.resolve_issue_ids(linear_ids)

    results = []
    for row, jira_key, linear_id in row_mappings:
        result = {'jira_key': jira_key, 'linear_id': linear_id}

        if not linear_id:
            result['updates'] = {}
            result['skipped'] = [('all', 'no Linear ID in mapping')]
            result['success'] = False
            results.append(result)
            continue

        # Determine team from linear_id (e.g. "WEB-458" -> "WEB")
        parts = linear_id.split('-')
        row_team_key = parts[0] if len(parts) == 2 else team_key

        input_dict, skipped = build_update_input(row, columns, row_team_key, enabled_fields)
        result['updates'] = input_dict
        result['skipped'] = skipped

        if not input_dict:
            result['success'] = True
            results.append(result)
            if linear_utils.VERBOSE:
                print(f"{linear_id}: no fields to update", file=sys.stderr)
            continue

        if dry_run:
            result['success'] = True
            results.append(result)
            continue

        issue_uuid = uuid_map.get(linear_id)
        if not issue_uuid:
            result['success'] = False
            result['error'] = f"Could not resolve {linear_id} to UUID"
            results.append(result)
            continue

        try:
            response = linear_utils.update_issue(issue_uuid, input_dict)
            result['success'] = response.get('success', False)
            if not result['success']:
                result['error'] = 'API returned success=false'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def print_table(results, dry_run=False):
    """Print results as a human-readable table."""
    label = " (dry run)" if dry_run else ""
    updated = sum(1 for r in results if r['success'] and r['updates'])
    skipped = sum(1 for r in results if not r['updates'])
    failed = sum(1 for r in results if not r['success'] and r['updates'])

    for r in results:
        linear_id = r['linear_id'] or '(no mapping)'
        jira_key = r['jira_key']
        updates = r['updates']
        skipped_fields = r['skipped']
        error = r.get('error')

        if not updates and not error:
            print(f"{jira_key}  {linear_id}  (no changes)")
            continue

        if error and not updates:
            print(f"{jira_key}  {linear_id}  ERROR: {error}")
            continue

        parts = []
        if 'priority' in updates:
            parts.append(f"priority={updates['priority']}")
        if 'estimate' in updates:
            parts.append(f"estimate={updates['estimate']}")
        if 'stateId' in updates:
            parts.append("status=updated")
        if 'assigneeId' in updates:
            parts.append("assignee=updated")
        if 'labelIds' in updates:
            parts.append(f"labels={len(updates['labelIds'])}")

        status_mark = "✓" if r['success'] else "✗"
        line = f"{jira_key}  {linear_id}  {', '.join(parts)}  {status_mark}"

        if skipped_fields:
            skipped_names = [f for f, _ in skipped_fields]
            line += f"  (skipped: {', '.join(skipped_names)})"
        if error:
            line += f"  ERROR: {error}"

        print(line)

    print(f"\nSummary{label}: {updated} updated, {skipped} no-op, {failed} failed")


def sync_jira_metadata(
    csv_file: Annotated[str, typer.Argument(help="Path to the JIRA CSV export")],
    jira_mapping: Annotated[str, typer.Option("--jira-mapping", help="JSON mapping file from jira to-linear --json (use - for stdin)", show_default=False)],
    team: Annotated[str, typer.Option("--team", help="Linear team key for workflow state resolution")] = "WEB",
    fields: Annotated[str, typer.Option("--fields", help="Comma-separated fields to sync: priority,estimate,status,assignee,labels")] = "priority,estimate,status",
    issue_key_column: Annotated[Optional[str], typer.Option("--issue-key-column", help="CSV column for JIRA issue key (auto-detected)")] = None,
    priority_column: Annotated[Optional[str], typer.Option("--priority-column", help="CSV column for priority (auto-detected)")] = None,
    status_column: Annotated[Optional[str], typer.Option("--status-column", help="CSV column for status (auto-detected)")] = None,
    story_points_column: Annotated[Optional[str], typer.Option("--story-points-column", help="CSV column for story points (auto-detected)")] = None,
    assignee_column: Annotated[Optional[str], typer.Option("--assignee-column", help="CSV column for assignee (auto-detected)")] = None,
    labels_column: Annotated[Optional[str], typer.Option("--labels-column", help="CSV column for labels (auto-detected)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview changes without applying them")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output results as JSON array")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output")] = False,
):
    if verbose:
        linear_utils.VERBOSE = True

    enabled_fields = {f.strip() for f in fields.split(",") if f.strip()}
    valid_fields = {"priority", "estimate", "status", "assignee", "labels"}
    unknown = enabled_fields - valid_fields
    if unknown:
        typer.echo(f"Error: unknown field(s): {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(valid_fields))}", err=True)
        raise typer.Exit(1)

    headers, rows = load_csv(csv_file)
    jira_map = load_jira_mapping(jira_mapping)

    if linear_utils.VERBOSE:
        typer.echo(f"Loaded {len(rows)} CSV rows, {len(jira_map)} mapping entries", err=True)
        typer.echo(f"CSV headers: {headers}", err=True)

    overrides = {}
    if issue_key_column:
        overrides["issue_key"] = issue_key_column
    if priority_column:
        overrides["priority"] = priority_column
    if status_column:
        overrides["status"] = status_column
    if story_points_column:
        overrides["story_points"] = story_points_column
    if assignee_column:
        overrides["assignee"] = assignee_column
    if labels_column:
        overrides["labels"] = labels_column

    columns = detect_columns(headers, overrides)

    if linear_utils.VERBOSE:
        typer.echo(f"Detected columns: {columns}", err=True)

    if not columns.get("issue_key"):
        typer.echo(
            f"Error: could not detect JIRA issue key column in: {headers}\n"
            "Use --issue-key-column to specify it.",
            err=True,
        )
        raise typer.Exit(1)

    results = sync_metadata(rows, columns, jira_map, team, enabled_fields, dry_run=dry_run)

    if json_output:
        output = []
        for r in results:
            entry = dict(r)
            entry["skipped"] = [{"field": f, "reason": reason} for f, reason in r["skipped"]]
            output.append(entry)
        typer.echo(json.dumps(output, indent=2))
    else:
        print_table(results, dry_run=dry_run)
