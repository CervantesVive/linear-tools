"""JQL-like query parser for Linear ProjectFilter dicts.

Reuses the shared grammar and priority helpers from query_parser.py.
Supports: team, label, state, lead, priority, name, createdAt, updatedAt,
          startDate, targetDate (and aliases: created, updated).
"""
from lark import Transformer, exceptions
from lark.exceptions import VisitError

from linear_tools.query_parser import (
    SCALAR_OPERATOR_MAP,
    _get_parser,
    _resolve_priority,
    _priority_range_filter,
)

SUPPORTED_PROJECT_FIELDS = frozenset({
    'team', 'label', 'state', 'lead', 'priority',
    'name', 'createdAt', 'updatedAt', 'startDate', 'targetDate',
})

PROJECT_FIELD_ALIASES = {
    'created': 'createdAt',
    'updated': 'updatedAt',
}


def build_project_condition(field, op, value):
    """Compile a single comparison into a Linear ProjectFilter dict fragment.

    Args:
        field: Field name string (e.g. 'team', 'label', 'state')
        op:    Operator string (e.g. '=', 'in', '>=')
        value: String (scalar) or list of strings (for 'in' operator)

    Returns:
        dict: A fragment of a Linear ProjectFilter.

    Raises:
        ValueError: On unsupported field/operator or invalid values.
    """
    field = PROJECT_FIELD_ALIASES.get(field, field)

    if field not in SUPPORTED_PROJECT_FIELDS:
        all_fields = sorted(SUPPORTED_PROJECT_FIELDS | set(PROJECT_FIELD_ALIASES))
        raise ValueError(
            f"Unknown filter field '{field}'. "
            f"Supported: {', '.join(all_fields)}."
        )

    is_list = isinstance(value, list)

    # --- team ---
    if field == 'team':
        if op != '=':
            raise ValueError("'team' only supports the '=' operator.")
        return {'accessibleTeams': {'some': {'key': {'eq': value}}}}

    # --- label ---
    elif field == 'label':
        if op == 'in' or is_list:
            names = value if is_list else [value]
            return {'labels': {'some': {'name': {'in': names}}}}
        elif op == '=':
            return {'labels': {'some': {'name': {'eq': value}}}}
        elif op == '!=':
            return {'labels': {'every': {'name': {'neq': value}}}}
        raise ValueError("'label' supports =, !=, and in operators.")

    # --- state ---
    elif field == 'state':
        if op == 'in' or is_list:
            names = value if is_list else [value]
            return {'status': {'name': {'in': names}}}
        gql_op = SCALAR_OPERATOR_MAP.get(op)
        if gql_op not in ('eq', 'neq'):
            raise ValueError("'state' supports =, !=, and in operators.")
        return {'status': {'name': {gql_op: value}}}

    # --- lead ---
    elif field == 'lead':
        if op in ('=', 'contains'):
            return {'lead': {'displayName': {'containsIgnoreCase': value}}}
        raise ValueError("'lead' supports = and contains operators.")

    # --- priority ---
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

    # --- name ---
    elif field == 'name':
        if op in ('=', 'contains'):
            return {'name': {'containsIgnoreCase': value}}
        raise ValueError("'name' supports = and contains operators.")

    # --- date fields ---
    elif field in ('createdAt', 'updatedAt', 'startDate', 'targetDate'):
        gql_op = SCALAR_OPERATOR_MAP.get(op)
        if gql_op is None:
            raise ValueError(f"Date field '{field}' does not support '{op}' operator.")
        return {field: {gql_op: value}}

    raise ValueError(f"Field '{field}' has no handler (this is a bug).")


class ProjectFilterCompiler(Transformer):
    """Walks the Lark parse tree and compiles it into a Linear ProjectFilter dict."""

    def QUOTED_STRING(self, token):  # noqa: N802
        return str(token)[1:-1]

    def BARE_WORD(self, token):  # noqa: N802
        return str(token)

    def FIELD(self, token):  # noqa: N802
        return str(token).lower()

    def OPERATOR(self, token):  # noqa: N802
        return str(token).lower()

    def value(self, items):
        return items[0]

    def valuelist(self, items):
        return list(items)

    def comparison(self, items):
        field, op, value = items
        return build_project_condition(field, op, value)

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


def parse_project_query(query_string):
    """Parse a JQL-like query string into a Linear ProjectFilter dict.

    Args:
        query_string: Filter expression, e.g.:
            'team = WEB AND label = "Q2\\'26"'
            'state in ["Blocked", "In Progress"]'
            'priority >= High AND targetDate < 2026-06-30'

    Returns:
        dict: Linear ProjectFilter ready to pass as a GraphQL $filter variable.

    Raises:
        ValueError: On invalid field/operator/value combinations.
        SyntaxError: On malformed query syntax.
    """
    try:
        tree = _get_parser().parse(query_string.strip())
        return ProjectFilterCompiler().transform(tree)
    except VisitError as e:
        # Lark wraps transformer exceptions in VisitError; unwrap ValueError
        if isinstance(e.__context__, ValueError):
            raise e.__context__
        raise SyntaxError(f"Query parse error: {e}") from e
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
