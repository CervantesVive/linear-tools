"""Tests for linear add-links command."""
import json
import os
from unittest.mock import patch

os.environ.setdefault('LINEAR_API_KEY', 'test')

from typer.testing import CliRunner
from typer import Typer

from linear_tools.commands.add_links import (
    fetch_issues_for_add_links,
    attach_links,
    add_links,
)

runner = CliRunner()
app = Typer()
app.command()(add_links)


def _page(nodes, has_next=False, cursor=None):
    return {
        'issues': {
            'nodes': nodes,
            'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        }
    }


def _issue(identifier, title='Test', uid=None):
    return {'id': uid or f'u-{identifier}', 'identifier': identifier, 'title': title}


class TestFetchIssuesForAddLinks:
    def test_returns_issues_from_single_page(self):
        with patch('linear_tools.utils.graphql_request', return_value=_page([_issue('WEB-1')])) as gql:
            result = fetch_issues_for_add_links({'team': {'key': {'eq': 'WEB'}}})
        assert len(result) == 1
        assert result[0]['identifier'] == 'WEB-1'
        gql.assert_called_once()

    def test_paginates_through_all_pages(self):
        page1 = _page([_issue('WEB-1')], has_next=True, cursor='c1')
        page2 = _page([_issue('WEB-2')])
        with patch('linear_tools.utils.graphql_request', side_effect=[page1, page2]) as gql:
            result = fetch_issues_for_add_links({})
        assert len(result) == 2
        assert gql.call_count == 2
        second_vars = gql.call_args_list[1][1]['variables']
        assert second_vars['after'] == 'c1'

    def test_returns_empty_list_when_no_issues(self):
        with patch('linear_tools.utils.graphql_request', return_value=_page([])):
            result = fetch_issues_for_add_links({})
        assert result == []


class TestAttachLinks:
    def test_attaches_to_all_issues(self):
        issues = [_issue('WEB-1'), _issue('WEB-2')]
        with patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}) as mock:
            results = attach_links(issues, 'https://example.com')
        assert len(results) == 2
        assert mock.call_count == 2

    def test_passes_url_and_title(self):
        issues = [_issue('WEB-1', uid='uuid-1')]
        with patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}) as mock:
            attach_links(issues, 'https://example.com', title='Docs')
        mock.assert_called_once_with('uuid-1', 'https://example.com', title='Docs')

    def test_collects_success_result(self):
        issues = [_issue('WEB-1', 'My ticket')]
        with patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}):
            results = attach_links(issues, 'https://example.com')
        assert results[0] == {'identifier': 'WEB-1', 'title': 'My ticket', 'success': True}

    def test_collects_failure_result(self):
        issues = [_issue('WEB-1', 'My ticket')]
        with patch('linear_tools.utils.attach_url_to_issue', return_value={'success': False}):
            results = attach_links(issues, 'https://example.com')
        assert results[0]['success'] is False
        assert 'WEB-1' in results[0]['error']

    def test_captures_exception_as_failure(self):
        issues = [_issue('WEB-1', 'My ticket')]
        with patch('linear_tools.utils.attach_url_to_issue', side_effect=Exception('Network error')):
            results = attach_links(issues, 'https://example.com')
        assert results[0]['success'] is False
        assert 'Network error' in results[0]['error']


class TestAddLinksCommandExecution:
    def _single(self):
        return _page([_issue('WEB-1', 'My ticket')])

    def _multi(self):
        return _page([_issue('WEB-1', 'First'), _issue('WEB-2', 'Second')])

    def test_single_ticket_attaches_without_confirmation(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._single()), \
             patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}):
            result = runner.invoke(app, [
                '--query', 'identifier = WEB-1',
                '--url', 'https://example.com',
            ])
        assert result.exit_code == 0
        assert 'WEB-1' in result.output

    def test_multiple_tickets_prompts_shows_list(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._multi()), \
             patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}):
            result = runner.invoke(
                app,
                ['--query', 'team = WEB', '--url', 'https://example.com'],
                input='n\n',
            )
        assert result.exit_code == 0
        assert 'WEB-1' in result.output
        assert 'WEB-2' in result.output

    def test_confirmation_declined_skips_attach(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._multi()), \
             patch('linear_tools.utils.attach_url_to_issue') as mock_attach:
            result = runner.invoke(
                app,
                ['--query', 'team = WEB', '--url', 'https://example.com'],
                input='n\n',
            )
        assert result.exit_code == 0
        mock_attach.assert_not_called()

    def test_yes_flag_skips_confirmation(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._multi()), \
             patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}):
            result = runner.invoke(app, [
                '--query', 'team = WEB',
                '--url', 'https://example.com',
                '--yes',
            ])
        assert result.exit_code == 0
        assert 'WEB-1' in result.output
        assert 'WEB-2' in result.output

    def test_exits_1_when_no_issues_found(self):
        with patch('linear_tools.utils.graphql_request', return_value=_page([])):
            result = runner.invoke(app, [
                '--query', 'identifier = WEB-9999',
                '--url', 'https://example.com',
            ])
        assert result.exit_code == 1
        assert 'no issues' in result.output.lower()

    def test_json_output_format(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._single()), \
             patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}):
            result = runner.invoke(app, [
                '--query', 'identifier = WEB-1',
                '--url', 'https://example.com',
                '--json',
            ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]['identifier'] == 'WEB-1'
        assert data[0]['success'] is True

    def test_exit_code_1_when_any_attach_fails(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._single()), \
             patch('linear_tools.utils.attach_url_to_issue', return_value={'success': False}):
            result = runner.invoke(app, [
                '--query', 'identifier = WEB-1',
                '--url', 'https://example.com',
            ])
        assert result.exit_code == 1

    def test_title_passed_through_to_helper(self):
        with patch('linear_tools.utils.graphql_request', return_value=self._single()), \
             patch('linear_tools.utils.attach_url_to_issue', return_value={'success': True}) as mock_attach:
            result = runner.invoke(app, [
                '--query', 'identifier = WEB-1',
                '--url', 'https://example.com',
                '--title', 'Design doc',
            ])
        assert result.exit_code == 0
        mock_attach.assert_called_once_with('u-WEB-1', 'https://example.com', title='Design doc')
