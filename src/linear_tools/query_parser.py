"""
query_parser.py

JQL-like query parser for Linear issue filters.

Parses expressions like:
  team = WEB AND state = "In Progress"
  priority >= High AND (label in [Bug, P0] OR state = Done)
  team = WEB AND created > 2025-01-01 AND updated < 2025-04-01
  title contains "auth" AND assignee = "Dean Hamilton"
  identifier = WEB-1086
  identifier in [WEB-1086, WEB-1087]

Returns a Linear IssueFilter dict suitable for the GraphQL $filter variable.

Supported fields:
  team        - Team key (e.g. WEB)
  state       - Workflow state name (e.g. "In Progress", Done)
  assignee    - Assignee display name (substring match)
  label       - Label name; use 'in [Bug, P0]' for multiple
  priority    - Urgent/High/Medium/Low/None or 0-4 (inverted scale: Urgent=1, Low=4)
  estimate    - Numeric estimate (story points)
  created     - ISO date (e.g. 2025-01-01); aliases createdAt
  updated     - ISO date; aliases updatedAt
  project     - Project name
  cycle       - Cycle name
  title       - Issue title (substring match with = or contains)
  identifier  - Issue identifier (e.g. WEB-1086); decomposes to team+number filter
  number      - Issue number within its team (numeric)

Supported operators:
  =   exact match (or substring for assignee/title/project/cycle)
  !=  not equal
  >   greater than (for dates, estimate; inverted for priority)
  >=  greater than or equal
  <   less than
  <=  less than or equal
  in  list membership: field in [val1, val2, ...]
  contains  substring match (for title, assignee, project, cycle)

Priority comparison note:
  Linear's priority scale is inverted (1=Urgent highest, 4=Low lowest).
  'priority >= High' means "High or more urgent" = Urgent + High = {in: [1, 2]}.
"""

import re

from lark import Lark, Transformer, exceptions

# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

QUERY_GRAMMAR = r"""
    start: or_expr

    or_expr: and_expr (_OR and_expr)*
    and_expr: atom (_AND atom)*
    atom: "(" or_expr ")" | comparison
    comparison: FIELD OPERATOR value
    value: QUOTED_STRING | valuelist | BARE_WORD

    valuelist: "[" value ("," value)* "]"

    _AND.2: /(?i:AND)/
    _OR.2:  /(?i:OR)/
    OPERATOR.1: /!=|>=|<=|(?i:contains)|(?i:in)|[=><]/
    FIELD: /[a-zA-Z_]\w*/
    QUOTED_STRING: /\"[^\"]*\"/ | /'[^']*'/
    BARE_WORD: /[^\s,\[\]()"'=!><]+/

    %ignore /\s+/
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Linear priority: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low
PRIORITY_MAP = {
    'none': 0, 'urgent': 1, 'high': 2, 'medium': 3, 'low': 4,
}

# Canonical field names (after alias resolution)
SUPPORTED_FIELDS = frozenset({
    'team', 'state', 'assignee', 'label', 'priority',
    'estimate', 'createdAt', 'updatedAt', 'project', 'cycle', 'title',
    'identifier', 'number',
})

FIELD_ALIASES = {
    'created': 'createdAt',
    'updated': 'updatedAt',
    'id': 'identifier',
}

SCALAR_OPERATOR_MAP = {
    '=': 'eq', '!=': 'neq',
    '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte',
}

_IDENTIFIER_RE = re.compile(r'^([A-Z]+)-(\d+)$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Priority helpers
# ---------------------------------------------------------------------------

def _resolve_priority(value_str):
    """Map a priority name or number string to Linear's numeric value (0-4)."""
    v = value_str.strip().lower()
    if v in PRIORITY_MAP:
        return PRIORITY_MAP[v]
    try:
        n = int(v)
        if 0 <= n <= 4:
            return n
    except ValueError:
        pass
    names = ', '.join(k.capitalize() for k in PRIORITY_MAP)
    raise ValueError(
        f"Unknown priority value '{value_str}'. Valid: {names} or 0-4."
    )


def _priority_range_filter(op, num):
    """Build a priority filter for >, >=, <, <= operators.

    Linear's scale is inverted: 1=Urgent (most urgent), 4=Low (least urgent).
    We translate user intent ('>= High' = 'High or more urgent') correctly
    by enumerating matching numeric values from [1, 2, 3, 4] (excluding 0=None).
    """
    all_vals = [1, 2, 3, 4]  # Exclude None (0) from range comparisons
    if op == '>=':
        matching = [p for p in all_vals if p <= num]  # <= in numeric = >= in urgency
    elif op == '>':
        matching = [p for p in all_vals if p < num]
    elif op == '<=':
        matching = [p for p in all_vals if p >= num]
    elif op == '<':
        matching = [p for p in all_vals if p > num]
    else:
        raise ValueError(f"Unexpected operator '{op}' for priority range.")

    if not matching:
        urgency = next((k for k, v in PRIORITY_MAP.items() if v == num), str(num))
        raise ValueError(
            f"No priority values match '{op} {urgency.capitalize()}'. "
            f"Note: priority scale is Urgent(1) > High(2) > Medium(3) > Low(4)."
        )
    if len(matching) == 1:
        return {'priority': {'eq': matching[0]}}
    return {'priority': {'in': matching}}


# ---------------------------------------------------------------------------
# Identifier helpers
# ---------------------------------------------------------------------------

def _parse_identifier(value):
    """Parse an issue identifier like 'WEB-1086' into (team_key, number).

    Args:
        value: Identifier string, e.g. 'WEB-1086' or 'web-1086'

    Returns:
        tuple: (team_key_upper: str, number: int)

    Raises:
        ValueError: If the format is not TEAM-NUMBER.
    """
    m = _IDENTIFIER_RE.match(value.strip())
    if not m:
        raise ValueError(
            f"Invalid identifier format '{value}'. Expected TEAM-NUMBER, e.g. 'WEB-1086'."
        )
    return m.group(1).upper(), int(m.group(2))


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def build_condition(field, op, value):
    """Compile a single comparison (field, operator, value) into a Linear IssueFilter dict.

    Args:
        field: Normalised field name string (e.g. 'state', 'priority')
        op:    Operator string (e.g. '=', '>=', 'in', 'contains')
        value: String (scalar) or list of strings (for 'in' operator)

    Returns:
        dict: A fragment of a Linear IssueFilter.

    Raises:
        ValueError: On unsupported field/operator combinations or invalid values.
    """
    field = FIELD_ALIASES.get(field, field)

    if field not in SUPPORTED_FIELDS:
        all_fields = sorted(SUPPORTED_FIELDS | set(FIELD_ALIASES))
        raise ValueError(
            f"Unknown filter field '{field}'. "
            f"Supported: {', '.join(all_fields)}."
        )

    is_list = isinstance(value, list)

    # --- team ----------------------------------------------------------
    if field == 'team':
        if op != '=':
            raise ValueError("'team' only supports the '=' operator.")
        return {'team': {'key': {'eq': value}}}

    # --- state ---------------------------------------------------------
    elif field == 'state':
        if op == 'in' or is_list:
            names = value if is_list else [value]
            return {'state': {'name': {'in': names}}}
        gql_op = SCALAR_OPERATOR_MAP.get(op)
        if gql_op not in ('eq', 'neq'):
            raise ValueError("'state' supports =, !=, and in operators.")
        return {'state': {'name': {gql_op: value}}}

    # --- assignee ------------------------------------------------------
    elif field == 'assignee':
        if op in ('=', 'contains'):
            return {'assignee': {'displayName': {'containsIgnoreCase': value}}}
        elif op == '!=':
            return {'assignee': {'displayName': {'neq': value}}}
        raise ValueError("'assignee' supports =, !=, and contains operators.")

    # --- label ---------------------------------------------------------
    elif field == 'label':
        if op == 'in' or is_list:
            names = value if is_list else [value]
            return {'labels': {'some': {'name': {'in': names}}}}
        elif op == '=':
            return {'labels': {'some': {'name': {'eq': value}}}}
        elif op == '!=':
            return {'labels': {'every': {'name': {'neq': value}}}}
        raise ValueError("'label' supports =, !=, and in operators.")

    # --- priority ------------------------------------------------------
    elif field == 'priority':
        if op == 'in' or is_list:
            vals = value if is_list else [value]
            nums = [_resolve_priority(v) for v in vals]
            return {'priority': {'in': nums}}
        num = _resolve_priority(value if not is_list else value[0])
        if op in ('=', '!='):
            return {'priority': {SCALAR_OPERATOR_MAP[op]: num}}
        if op in ('>', '>=', '<', '<='):
            return _priority_range_filter(op, num)
        raise ValueError(f"'priority' does not support '{op}' operator.")

    # --- estimate ------------------------------------------------------
    elif field == 'estimate':
        gql_op = SCALAR_OPERATOR_MAP.get(op)
        if gql_op is None:
            raise ValueError(f"'estimate' does not support '{op}' operator.")
        try:
            num = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"'estimate' value must be numeric, got '{value}'.")
        return {'estimate': {gql_op: num}}

    # --- createdAt / updatedAt ----------------------------------------
    elif field in ('createdAt', 'updatedAt'):
        gql_op = SCALAR_OPERATOR_MAP.get(op)
        if gql_op is None:
            raise ValueError(f"Date field '{field}' does not support '{op}' operator.")
        return {field: {gql_op: value}}

    # --- project -------------------------------------------------------
    elif field == 'project':
        if op in ('=', 'contains'):
            return {'project': {'name': {'containsIgnoreCase': value}}}
        raise ValueError("'project' supports = and contains operators.")

    # --- cycle ---------------------------------------------------------
    elif field == 'cycle':
        if op in ('=', 'contains'):
            return {'cycle': {'name': {'containsIgnoreCase': value}}}
        raise ValueError("'cycle' supports = and contains operators.")

    # --- title ---------------------------------------------------------
    elif field == 'title':
        if op in ('=', 'contains'):
            return {'title': {'containsIgnoreCase': value}}
        raise ValueError("'title' supports = and contains operators.")

    # --- identifier ----------------------------------------------------
    elif field == 'identifier':
        if op == '=':
            team_key, num = _parse_identifier(value)
            return {'and': [
                {'team': {'key': {'eq': team_key}}},
                {'number': {'eq': num}},
            ]}
        elif op == '!=':
            team_key, num = _parse_identifier(value)
            return {'or': [
                {'team': {'key': {'neq': team_key}}},
                {'number': {'neq': num}},
            ]}
        elif op == 'in' or is_list:
            vals = value if is_list else [value]
            parsed = [_parse_identifier(v) for v in vals]
            by_team = {}
            for team_key, num in parsed:
                by_team.setdefault(team_key, []).append(num)
            if len(by_team) == 1:
                team_key, numbers = next(iter(by_team.items()))
                return {'and': [
                    {'team': {'key': {'eq': team_key}}},
                    {'number': {'in': numbers}},
                ]}
            clauses = [
                {'and': [{'team': {'key': {'eq': t}}}, {'number': {'in': nums}}]}
                for t, nums in by_team.items()
            ]
            return {'or': clauses}
        raise ValueError(
            "'identifier' supports =, !=, and in operators. "
            "Value must be TEAM-NUMBER format, e.g. 'WEB-1086'."
        )

    # --- number --------------------------------------------------------
    elif field == 'number':
        if op == 'in' or is_list:
            vals = value if is_list else [value]
            try:
                nums = [int(v) for v in vals]
            except (ValueError, TypeError):
                raise ValueError(f"'number' values must be integers, got {vals}.")
            return {'number': {'in': nums}}
        gql_op = SCALAR_OPERATOR_MAP.get(op)
        if gql_op is None:
            raise ValueError(f"'number' does not support '{op}' operator.")
        try:
            num = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"'number' value must be an integer, got '{value}'.")
        return {'number': {gql_op: num}}

    raise ValueError(f"Field '{field}' has no handler (this is a bug).")


# ---------------------------------------------------------------------------
# Lark Transformer
# ---------------------------------------------------------------------------

class FilterCompiler(Transformer):
    """Walks the Lark parse tree and compiles it into a Linear IssueFilter dict."""

    def QUOTED_STRING(self, token):  # noqa: N802
        return str(token)[1:-1]  # strip surrounding quotes

    def BARE_WORD(self, token):  # noqa: N802
        return str(token)

    def FIELD(self, token):  # noqa: N802
        return str(token).lower()  # normalise field names to lowercase

    def OPERATOR(self, token):  # noqa: N802
        return str(token).lower()  # normalise operators to lowercase

    def value(self, items):
        return items[0]

    def valuelist(self, items):
        return list(items)  # list of strings

    def comparison(self, items):
        field, op, value = items
        return build_condition(field, op, value)

    def atom(self, items):
        return items[0]

    def and_expr(self, items):
        if len(items) == 1:
            return items[0]
        return {'and': list(items)}

    def or_expr(self, items):
        if len(items) == 1:
            return items[0]
        return {'or': list(items)}

    def start(self, items):
        return items[0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        _parser = Lark(QUERY_GRAMMAR, parser='lalr', lexer='contextual')
    return _parser


def parse_query(query_string):
    """Parse a JQL-like query string into a Linear IssueFilter dict.

    Args:
        query_string: Filter expression, e.g.:
            'team = WEB AND state = "In Progress"'
            'priority >= High AND (label in [Bug, P0] OR state = Done)'
            'team = WEB AND created > 2025-01-01'

    Returns:
        dict: Linear IssueFilter ready to pass as a GraphQL $filter variable.

    Raises:
        ValueError: On invalid field/operator/value combinations.
        SyntaxError: On malformed query syntax.
    """
    try:
        tree = _get_parser().parse(query_string.strip())
        return FilterCompiler().transform(tree)
    except exceptions.UnexpectedCharacters as e:
        col = getattr(e, 'column', '?')
        pointer = f"{'':>{col - 1}}^" if isinstance(col, int) else ''
        allowed = ', '.join(sorted(getattr(e, 'allowed', None) or []))
        raise SyntaxError(
            f"Unexpected character at position {col} in query:\n"
            f"  {query_string}\n"
            f"  {pointer}\n"
            + (f"Expected one of: {allowed}" if allowed else "")
        ) from e
    except exceptions.UnexpectedEOF as e:
        expected = ', '.join(sorted(getattr(e, 'expected', None) or []))
        raise SyntaxError(
            f"Unexpected end of query: {query_string!r}\n"
            + (f"Expected one of: {expected}" if expected else "")
        ) from e
    except exceptions.LarkError as e:
        raise SyntaxError(f"Query parse error: {e}") from e
