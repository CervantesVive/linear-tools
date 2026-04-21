"""
linear_utils.py

Purpose
- Provide shared helpers for interacting with the Linear GraphQL API.
- Centralize authentication, retry logic, and common query/mutation wrappers.

Provided functions
- graphql_request(query, variables=None): Execute a GraphQL operation against the Linear API.
- get_active_cycle(team_key): Fetch the current active cycle for a team.
- get_cycle_by_number(team_key, cycle_number): Fetch a specific cycle by its number.
- list_cycles(team_key, first=20): List recent cycles for a team.
- resolve_issue_ids(identifiers): Batch-resolve human-readable identifiers to internal UUIDs.
- update_issue(issue_uuid, input_dict): Update an issue via the issueUpdate mutation.

Environment and Dependencies
- Environment variables required (loaded via .env):
  - LINEAR_API_KEY: Personal API key from Linear Settings > Security & Access > API
  - LINEAR_ORG_SLUG: Org slug for constructing issue URLs (default: bitgo)
- External deps: requests, python-dotenv

Usage notes
- Set linear_utils.VERBOSE = True in calling scripts to enable debug output to stderr.
- The API key is masked in verbose output to prevent credential exposure.
- Environment variables are validated at module load time — missing LINEAR_API_KEY raises EnvironmentError.
"""
import sys
import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

LINEAR_API_KEY = os.getenv('LINEAR_API_KEY')
LINEAR_ORG_SLUG = os.getenv('LINEAR_ORG_SLUG', 'bitgo')
LINEAR_GRAPHQL_URL = 'https://api.linear.app/graphql'

# Retry configuration (mirrors jira_utils constants)
MAX_RETRY_ATTEMPTS = 5
RATE_LIMIT_BASE_BACKOFF = 1  # seconds; exponential: 1s, 2s, 4s, 8s, 16s

# Module-level verbose flag — set linear_utils.VERBOSE = True in calling scripts
VERBOSE = False


def _require_env():
    """Validate required environment variables are set.

    Called lazily at the start of any function that makes API calls,
    so that --help and other non-API operations work without credentials.

    Raises:
        EnvironmentError: If LINEAR_API_KEY is missing.
    """
    if not LINEAR_API_KEY:
        raise EnvironmentError(
            "Missing required environment variable: LINEAR_API_KEY\n\n"
            "Please ensure the following is set:\n"
            "  - LINEAR_API_KEY: Your Linear personal API key\n\n"
            "To create one: Linear Settings > Security & Access > Personal API Keys\n"
            "You can set this in a .env file in the project root or export it as an environment variable."
        )


def graphql_request(query, variables=None):
    """Execute a GraphQL operation against the Linear API.

    Implements retry with exponential backoff for rate limiting (429).

    Args:
        query: GraphQL query or mutation string
        variables: Optional dict of GraphQL variables

    Returns:
        dict: The 'data' field from the GraphQL response

    Raises:
        Exception: On HTTP errors, rate limit exhaustion, or GraphQL-level errors
    """
    _require_env()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': LINEAR_API_KEY,
    }
    payload = {'query': query}
    if variables:
        payload['variables'] = variables

    if VERBOSE:
        print(f"GraphQL request to {LINEAR_GRAPHQL_URL}", file=sys.stderr)
        masked_key = LINEAR_API_KEY[:10] + '*' * 8 if LINEAR_API_KEY else '(none)'
        print(f"  Authorization: {masked_key}", file=sys.stderr)
        if variables:
            print(f"  Variables: {variables}", file=sys.stderr)

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            response = requests.post(LINEAR_GRAPHQL_URL, headers=headers, json=payload)
        except requests.RequestException as e:
            raise Exception(f"Request failed: {e}")

        if response.status_code == 429:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                wait_time = RATE_LIMIT_BASE_BACKOFF * (2 ** attempt)
                if VERBOSE:
                    print(f"Rate limited (429), retrying in {wait_time}s (attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS})...", file=sys.stderr)
                time.sleep(wait_time)
                continue
            raise Exception(f"Rate limit exceeded after {MAX_RETRY_ATTEMPTS} retry attempts")

        if response.status_code != 200:
            raise Exception(f"HTTP Error {response.status_code}: {response.text}")

        body = response.json()

        # GraphQL errors are returned with status 200 but in the 'errors' field
        if 'errors' in body:
            messages = '; '.join(e.get('message', str(e)) for e in body['errors'])
            raise Exception(f"GraphQL error: {messages}")

        return body.get('data', {})

    raise Exception(f"Failed after {MAX_RETRY_ATTEMPTS} attempts")


def get_active_cycle(team_key):
    """Fetch the current active cycle for a team.

    Args:
        team_key: Team key string (e.g. "WEB")

    Returns:
        dict with keys: id, name, number, startsAt, endsAt
        None if the team has no active cycle or the team key is not found.
    """
    query = """
    query GetActiveCycle($teamKey: String!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          id
          name
          key
          activeCycle {
            id
            name
            number
            startsAt
            endsAt
          }
        }
      }
    }
    """
    data = graphql_request(query, variables={'teamKey': team_key})
    nodes = data.get('teams', {}).get('nodes', [])

    if not nodes:
        if VERBOSE:
            print(f"Team '{team_key}' not found", file=sys.stderr)
        return None

    team = nodes[0]
    cycle = team.get('activeCycle')

    if VERBOSE:
        if cycle:
            print(f"Active cycle for {team_key}: {cycle.get('name')} (#{cycle.get('number')})", file=sys.stderr)
        else:
            print(f"No active cycle for team {team_key}", file=sys.stderr)

    return cycle


def get_cycle_by_number(team_key, cycle_number):
    """Fetch a specific cycle by its number for a team.

    Args:
        team_key: Team key string (e.g. "WEB")
        cycle_number: Cycle number (e.g. 47)

    Returns:
        dict with keys: id, name, number, startsAt, endsAt
        None if the cycle or team is not found.
    """
    query = """
    query GetCycleByNumber($teamKey: String!, $cycleNumber: Float!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          cycles(filter: { number: { eq: $cycleNumber } }) {
            nodes {
              id
              name
              number
              startsAt
              endsAt
            }
          }
        }
      }
    }
    """
    data = graphql_request(query, variables={'teamKey': team_key, 'cycleNumber': cycle_number})
    nodes = data.get('teams', {}).get('nodes', [])

    if not nodes:
        if VERBOSE:
            print(f"Team '{team_key}' not found", file=sys.stderr)
        return None

    cycle_nodes = nodes[0].get('cycles', {}).get('nodes', [])
    cycle = cycle_nodes[0] if cycle_nodes else None

    if VERBOSE:
        if cycle:
            print(f"Found cycle #{cycle_number} for {team_key}: {cycle.get('name')}", file=sys.stderr)
        else:
            print(f"Cycle #{cycle_number} not found for team {team_key}", file=sys.stderr)

    return cycle


def list_cycles(team_key, first=20):
    """List recent cycles for a team.

    Args:
        team_key: Team key string (e.g. "WEB")
        first: Maximum number of cycles to return (default: 20)

    Returns:
        List of cycle dicts with keys: id, name, number, startsAt, endsAt
        Empty list if the team is not found or has no cycles.
    """
    query = """
    query ListCycles($teamKey: String!, $first: Int!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          cycles(first: $first, orderBy: createdAt) {
            nodes {
              id
              name
              number
              startsAt
              endsAt
            }
          }
        }
      }
    }
    """
    data = graphql_request(query, variables={'teamKey': team_key, 'first': first})
    nodes = data.get('teams', {}).get('nodes', [])

    if not nodes:
        if VERBOSE:
            print(f"Team '{team_key}' not found", file=sys.stderr)
        return []

    cycles = nodes[0].get('cycles', {}).get('nodes', [])

    if VERBOSE:
        print(f"Found {len(cycles)} cycle(s) for team {team_key}", file=sys.stderr)

    return cycles


def resolve_issue_ids(identifiers):
    """Batch-resolve Linear issue identifiers to internal UUIDs.

    Groups identifiers by team key and queries each team's issues in one
    request to minimize API calls.

    Args:
        identifiers: List of issue identifiers, e.g. ["WEB-458", "WEB-461"]

    Returns:
        dict: Mapping of identifier -> UUID, e.g. {"WEB-458": "abc-123-..."}
              Identifiers that could not be resolved are omitted.
    """
    # Group by team key (the prefix before the dash)
    by_team = {}
    for identifier in identifiers:
        match = re.match(r'^([A-Z]+)-(\d+)$', identifier)
        if not match:
            if VERBOSE:
                print(f"Skipping invalid identifier: {identifier}", file=sys.stderr)
            continue
        team_key = match.group(1)
        number = int(match.group(2))
        by_team.setdefault(team_key, []).append((identifier, number))

    if VERBOSE:
        for team_key, items in by_team.items():
            print(f"Resolving {len(items)} issue(s) for team {team_key}", file=sys.stderr)

    result = {}
    for team_key, items in by_team.items():
        numbers = [n for _, n in items]
        query = """
        query ResolveIssues($teamKey: String!, $numbers: [Float!]!) {
          issues(filter: {
            team: { key: { eq: $teamKey } },
            number: { in: $numbers }
          }, first: 250) {
            nodes {
              id
              identifier
              number
            }
          }
        }
        """
        data = graphql_request(query, variables={'teamKey': team_key, 'numbers': numbers})
        nodes = data.get('issues', {}).get('nodes', [])
        for node in nodes:
            result[node['identifier']] = node['id']

    if VERBOSE:
        print(f"Resolved {len(result)}/{len(identifiers)} identifiers", file=sys.stderr)

    return result


def update_issue(issue_uuid, input_dict):
    """Update a Linear issue via the issueUpdate mutation.

    Args:
        issue_uuid: Internal UUID of the issue
        input_dict: Dict of fields to update (e.g. {"cycleId": "cycle-uuid"})

    Returns:
        dict: {"success": bool, "issue": {...}} from the mutation response
    """
    mutation = """
    mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue {
          id
          identifier
          title
          cycle {
            id
            name
            number
          }
        }
      }
    }
    """
    data = graphql_request(mutation, variables={'id': issue_uuid, 'input': input_dict})
    return data.get('issueUpdate', {})


def get_workflow_states(team_key):
    """Fetch all workflow states for a team, returning a name-to-UUID mapping.

    Args:
        team_key: Team key string (e.g. "WEB")

    Returns:
        dict: Mapping of state name (str) -> state UUID (str),
              e.g. {"Backlog": "uuid-1", "In Progress": "uuid-2", ...}
              Returns empty dict if the team is not found.
    """
    query = """
    query GetWorkflowStates($teamKey: String!) {
      teams(filter: { key: { eq: $teamKey } }) {
        nodes {
          states {
            nodes {
              id
              name
              type
            }
          }
        }
      }
    }
    """
    data = graphql_request(query, variables={'teamKey': team_key})
    nodes = data.get('teams', {}).get('nodes', [])

    if not nodes:
        if VERBOSE:
            print(f"Team '{team_key}' not found", file=sys.stderr)
        return {}

    states = nodes[0].get('states', {}).get('nodes', [])
    result = {s['name']: s['id'] for s in states}

    if VERBOSE:
        print(f"Found {len(result)} workflow state(s) for team {team_key}: {list(result.keys())}", file=sys.stderr)

    return result


def get_org_users():
    """Fetch all organization users, returning a display-name-to-UUID mapping.

    Paginates through all users using cursor-based pagination.

    Returns:
        dict: {display_name_lower: user_uuid} for active users only,
              e.g. {"dean hamilton": "uuid-..."}
              Falls back to 'name' if 'displayName' is empty.
    """
    query = """
    query GetOrgUsers($after: String) {
      users(first: 100, after: $after) {
        nodes {
          id
          name
          displayName
          active
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
    """
    result = {}
    cursor = None
    while True:
        data = graphql_request(query, variables={'after': cursor})
        connection = data.get('users', {})
        for u in connection.get('nodes', []):
            if not u.get('active', True):
                continue
            display = u.get('displayName') or u.get('name', '')
            if display:
                result[display.lower()] = u['id']
        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']

    if VERBOSE:
        print(f"Found {len(result)} active user(s) in organization", file=sys.stderr)

    return result


def get_workspace_labels():
    """Fetch all workspace issue labels, returning a name-to-UUID mapping.

    Paginates through all labels using cursor-based pagination.

    Returns:
        dict: {label_name_lower: label_uuid},
              e.g. {"bug": "uuid-..."}
    """
    query = """
    query GetWorkspaceLabels($after: String) {
      issueLabels(first: 100, after: $after) {
        nodes {
          id
          name
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
    """
    result = {}
    cursor = None
    while True:
        data = graphql_request(query, variables={'after': cursor})
        connection = data.get('issueLabels', {})
        for label in connection.get('nodes', []):
            name = label.get('name', '')
            if name:
                result[name.lower()] = label['id']
        page_info = connection.get('pageInfo', {})
        if not page_info.get('hasNextPage'):
            break
        cursor = page_info['endCursor']

    if VERBOSE:
        print(f"Found {len(result)} label(s) in workspace", file=sys.stderr)

    return result


def issue_url(identifier):
    """Construct a human-readable Linear issue URL."""
    return f"https://linear.app/{LINEAR_ORG_SLUG}/issue/{identifier}"


_COMMENT_CREATE_MUTATION = """
mutation CreateComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
  }
}
"""


def post_comment_to_linear_issue(identifier, body):
    """Post a markdown comment to a Linear issue by its identifier.

    Resolves the identifier (e.g. "CSI-1907") to an internal UUID, then
    posts the comment via the commentCreate GraphQL mutation.

    Args:
        identifier: Human-readable Linear issue identifier (e.g. "CSI-1907").
        body: Markdown-formatted comment body.

    Returns:
        tuple: (success: bool, message: str)
    """
    resolved = resolve_issue_ids([identifier])
    issue_uuid = resolved.get(identifier)
    if not issue_uuid:
        return (False, f"Issue {identifier} not found")

    result = graphql_request(
        _COMMENT_CREATE_MUTATION,
        variables={'issueId': issue_uuid, 'body': body},
    )
    success = result.get('commentCreate', {}).get('success', False)
    if success:
        return (True, "Comment posted")
    return (False, f"Mutation failed for {identifier}")
