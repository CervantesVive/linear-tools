"""Tests for linear comment command."""
import json
import os
from unittest.mock import patch

os.environ.setdefault('LINEAR_API_KEY', 'test')

import pytest
from typer.testing import CliRunner
from typer import Typer

from linear_tools.commands.comment import fetch_issues_for_comment, post_comments, comment

runner = CliRunner()
app = Typer()
app.command()(comment)


def _page(nodes, has_next=False, cursor=None):
    return {
        'issues': {
            'nodes': nodes,
            'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        }
    }


def _issue(identifier, title='Test', uid=None):
    return {'id': uid or f'u-{identifier}', 'identifier': identifier, 'title': title}


class TestFetchIssuesForComment:
    def test_returns_issues_from_single_page(self):
        with patch('linear_tools.utils.graphql_request', return_value=_page([_issue('WEB-1')])) as gql:
            result = fetch_issues_for_comment({'team': {'key': {'eq': 'WEB'}}})
        assert len(result) == 1
        assert result[0]['identifier'] == 'WEB-1'
        gql.assert_called_once()

    def test_paginates_through_all_pages(self):
        page1 = _page([_issue('WEB-1')], has_next=True, cursor='c1')
        page2 = _page([_issue('WEB-2')])
        with patch('linear_tools.utils.graphql_request', side_effect=[page1, page2]) as gql:
            result = fetch_issues_for_comment({})
        assert len(result) == 2
        assert gql.call_count == 2
        # second call should pass cursor
        second_vars = gql.call_args_list[1][1]['variables']
        assert second_vars['after'] == 'c1'

    def test_returns_empty_list_when_no_issues(self):
        with patch('linear_tools.utils.graphql_request', return_value=_page([])):
            result = fetch_issues_for_comment({})
        assert result == []
