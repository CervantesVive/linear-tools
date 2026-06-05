"""Tests for query_parser.py — identifier and number fields, plus composability."""
import os
import pytest

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.query_parser import parse_query, build_condition, _parse_identifier


# ---------------------------------------------------------------------------
# _parse_identifier helper
# ---------------------------------------------------------------------------

def test_parse_identifier_valid():
    assert _parse_identifier('WEB-1086') == ('WEB', 1086)

def test_parse_identifier_lowercase():
    assert _parse_identifier('web-1086') == ('WEB', 1086)

def test_parse_identifier_invalid_no_number():
    with pytest.raises(ValueError, match="Invalid identifier format"):
        _parse_identifier('WEB')

def test_parse_identifier_invalid_format():
    with pytest.raises(ValueError, match="Invalid identifier format"):
        _parse_identifier('1086-WEB')

def test_parse_identifier_invalid_bare_word():
    with pytest.raises(ValueError, match="Invalid identifier format"):
        _parse_identifier('invalid')


# ---------------------------------------------------------------------------
# identifier field — single value
# ---------------------------------------------------------------------------

def test_identifier_eq():
    result = parse_query('identifier = WEB-1086')
    assert result == {'and': [
        {'team': {'key': {'eq': 'WEB'}}},
        {'number': {'eq': 1086}},
    ]}

def test_identifier_eq_lowercase():
    result = parse_query('identifier = web-1086')
    assert result == {'and': [
        {'team': {'key': {'eq': 'WEB'}}},
        {'number': {'eq': 1086}},
    ]}

def test_identifier_neq():
    result = parse_query('identifier != WEB-1086')
    assert result == {'or': [
        {'team': {'key': {'neq': 'WEB'}}},
        {'number': {'neq': 1086}},
    ]}

def test_identifier_alias_id():
    result = parse_query('id = WEB-1086')
    assert result == parse_query('identifier = WEB-1086')


# ---------------------------------------------------------------------------
# identifier field — in list (same team)
# ---------------------------------------------------------------------------

def test_identifier_in_same_team():
    result = parse_query('identifier in [WEB-1086, WEB-1087]')
    assert result == {'and': [
        {'team': {'key': {'eq': 'WEB'}}},
        {'number': {'in': [1086, 1087]}},
    ]}

def test_identifier_in_single_item_list():
    result = parse_query('identifier in [WEB-1086]')
    assert result == {'and': [
        {'team': {'key': {'eq': 'WEB'}}},
        {'number': {'in': [1086]}},
    ]}


# ---------------------------------------------------------------------------
# identifier field — in list (mixed teams)
# ---------------------------------------------------------------------------

def test_identifier_in_mixed_teams():
    result = parse_query('identifier in [WEB-1086, ENG-42]')
    assert result == {'or': [
        {'and': [{'team': {'key': {'eq': 'WEB'}}}, {'number': {'in': [1086]}}]},
        {'and': [{'team': {'key': {'eq': 'ENG'}}}, {'number': {'in': [42]}}]},
    ]}

def test_identifier_in_mixed_teams_grouping():
    # Two WEB issues and one ENG issue — WEB should be grouped together
    result = parse_query('identifier in [WEB-100, ENG-42, WEB-200]')
    assert result == {'or': [
        {'and': [{'team': {'key': {'eq': 'WEB'}}}, {'number': {'in': [100, 200]}}]},
        {'and': [{'team': {'key': {'eq': 'ENG'}}}, {'number': {'in': [42]}}]},
    ]}


# ---------------------------------------------------------------------------
# identifier field — error cases
# ---------------------------------------------------------------------------

def test_identifier_invalid_value_raises():
    # ValueError from inside Lark transformer gets wrapped as SyntaxError by parse_query
    with pytest.raises((ValueError, SyntaxError), match="(?i)invalid identifier|query parse error"):
        parse_query('identifier = invalid')

def test_identifier_unsupported_operator():
    with pytest.raises((ValueError, SyntaxError)):
        parse_query('identifier > WEB-1086')


# ---------------------------------------------------------------------------
# number field
# ---------------------------------------------------------------------------

def test_number_eq():
    result = parse_query('number = 1086')
    assert result == {'number': {'eq': 1086}}

def test_number_neq():
    result = parse_query('number != 1086')
    assert result == {'number': {'neq': 1086}}

def test_number_gte():
    result = parse_query('number >= 1000')
    assert result == {'number': {'gte': 1000}}

def test_number_lt():
    result = parse_query('number < 500')
    assert result == {'number': {'lt': 500}}

def test_number_in():
    result = parse_query('number in [100, 200, 300]')
    assert result == {'number': {'in': [100, 200, 300]}}

def test_number_non_integer_raises():
    # ValueError from inside Lark transformer gets wrapped as SyntaxError by parse_query
    with pytest.raises((ValueError, SyntaxError)):
        parse_query('number = abc')


# ---------------------------------------------------------------------------
# Composability
# ---------------------------------------------------------------------------

def test_identifier_and_state():
    result = parse_query('identifier = WEB-1086 AND state = Done')
    assert result == {'and': [
        {'and': [
            {'team': {'key': {'eq': 'WEB'}}},
            {'number': {'eq': 1086}},
        ]},
        {'state': {'name': {'eqIgnoreCase': 'Done'}}},
    ]}

def test_state_eq_is_case_insensitive():
    result = parse_query('state = done')
    assert result == {'state': {'name': {'eqIgnoreCase': 'done'}}}

def test_state_neq_is_case_insensitive():
    result = parse_query('state != done')
    assert result == {'state': {'name': {'neqIgnoreCase': 'done'}}}

def test_state_in_is_case_insensitive():
    result = parse_query('state in [todo, "In Progress", DONE]')
    assert result == {'or': [
        {'state': {'name': {'eqIgnoreCase': 'todo'}}},
        {'state': {'name': {'eqIgnoreCase': 'In Progress'}}},
        {'state': {'name': {'eqIgnoreCase': 'DONE'}}},
    ]}

def test_team_and_number():
    result = parse_query('team = WEB AND number = 1086')
    assert result == {'and': [
        {'team': {'key': {'eq': 'WEB'}}},
        {'number': {'eq': 1086}},
    ]}

def test_team_and_number_range():
    result = parse_query('team = WEB AND number >= 1000')
    assert result == {'and': [
        {'team': {'key': {'eq': 'WEB'}}},
        {'number': {'gte': 1000}},
    ]}
