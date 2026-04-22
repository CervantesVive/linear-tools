"""Tests for linear_tools.utils.post_comment_to_linear_issue."""
import os
from unittest.mock import patch

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.utils import post_comment_to_linear_issue


class TestPostCommentToLinearIssue:
    def test_resolves_key_then_posts_comment(self):
        with patch('linear_tools.utils.resolve_issue_ids', return_value={'CSI-1907': 'uuid-abc'}) as resolver, \
             patch('linear_tools.utils.graphql_request', return_value={'commentCreate': {'success': True}}) as gql:
            success, message = post_comment_to_linear_issue('CSI-1907', 'hello body')
        assert success is True
        assert 'posted' in message.lower()
        resolver.assert_called_once_with(['CSI-1907'])
        # graphql_request is called once for the mutation (resolver is patched separately)
        assert gql.call_count == 1
        call_kwargs = gql.call_args.kwargs or {}
        variables = call_kwargs.get('variables') or gql.call_args.args[1]
        assert variables['issueId'] == 'uuid-abc'
        assert variables['body'] == 'hello body'

    def test_returns_not_found_when_resolver_empty(self):
        with patch('linear_tools.utils.resolve_issue_ids', return_value={}), \
             patch('linear_tools.utils.graphql_request') as gql:
            success, message = post_comment_to_linear_issue('CSI-9999', 'body')
        assert success is False
        assert 'not found' in message.lower()
        gql.assert_not_called()

    def test_returns_failure_when_mutation_success_false(self):
        with patch('linear_tools.utils.resolve_issue_ids', return_value={'CSI-1907': 'uuid-abc'}), \
             patch('linear_tools.utils.graphql_request', return_value={'commentCreate': {'success': False}}):
            success, message = post_comment_to_linear_issue('CSI-1907', 'body')
        assert success is False
        assert 'failed' in message.lower() or 'unsuccessful' in message.lower()

    def test_sends_comment_create_mutation(self):
        with patch('linear_tools.utils.resolve_issue_ids', return_value={'CSI-1907': 'uuid-abc'}), \
             patch('linear_tools.utils.graphql_request', return_value={'commentCreate': {'success': True}}) as gql:
            post_comment_to_linear_issue('CSI-1907', 'body')
        query_arg = gql.call_args.args[0]
        assert 'commentCreate' in query_arg
        assert 'issueId' in query_arg
