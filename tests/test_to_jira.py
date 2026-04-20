"""
Tests for to_jira.py

Tests the pure data transformation functions that require no API calls:
- find_jira_link: extract JIRA key and URL from Linear attachment objects
- read_export_ids: extract Linear IDs from a linear export-issues --json file

API-dependent functions (lookup_jira_ids) are tested with a mocked graphql_request.
"""
import json
import os
import pytest

os.environ.setdefault('LINEAR_API_KEY', 'test')

from linear_tools.commands.to_jira import find_jira_link, read_export_ids, read_csv_ids, lookup_jira_ids


# ---------------------------------------------------------------------------
# TestFindJiraLink
# ---------------------------------------------------------------------------

class TestFindJiraLink:
    """Tests for extracting JIRA key and URL from Linear attachment nodes."""

    def test_finds_jira_link(self):
        attachments = [{"url": "https://company.atlassian.net/browse/PROJ-123", "title": "PROJ-123"}]
        jira_key, url = find_jira_link(attachments)
        assert jira_key == "PROJ-123"
        assert url == "https://company.atlassian.net/browse/PROJ-123"

    def test_returns_none_for_empty_list(self):
        jira_key, url = find_jira_link([])
        assert jira_key is None
        assert url is None

    def test_ignores_non_jira_attachments(self):
        attachments = [{"url": "https://github.com/org/repo/pull/42", "title": "PR #42"}]
        jira_key, url = find_jira_link(attachments)
        assert jira_key is None
        assert url is None

    def test_finds_jira_link_among_multiple_attachments(self):
        attachments = [
            {"url": "https://github.com/org/repo/pull/1", "title": "PR #1"},
            {"url": "https://company.atlassian.net/browse/CE-999", "title": "CE-999"},
        ]
        jira_key, url = find_jira_link(attachments)
        assert jira_key == "CE-999"
        assert url == "https://company.atlassian.net/browse/CE-999"

    def test_extracts_key_with_extra_url_path_segments(self):
        attachments = [{"url": "https://company.atlassian.net/browse/WEB-42?something=1", "title": ""}]
        jira_key, url = find_jira_link(attachments)
        assert jira_key == "WEB-42"

    def test_returns_none_key_when_no_key_in_url(self):
        """URL has /browse/ but no JIRA key pattern — url returned, key is None."""
        attachments = [{"url": "https://company.atlassian.net/browse/", "title": ""}]
        jira_key, url = find_jira_link(attachments)
        assert jira_key is None
        assert url == "https://company.atlassian.net/browse/"


# ---------------------------------------------------------------------------
# TestReadExportIds
# ---------------------------------------------------------------------------

class TestReadExportIds:
    """Tests for reading Linear IDs from a linear export-issues --json output file."""

    def test_reads_identifier_field(self, tmp_path):
        data = [{"identifier": "WEB-1", "title": "Issue one"}, {"identifier": "WEB-2", "title": "Issue two"}]
        p = tmp_path / "export.json"
        p.write_text(json.dumps(data))
        assert read_export_ids(str(p)) == ["WEB-1", "WEB-2"]

    def test_preserves_order(self, tmp_path):
        data = [{"identifier": "ENG-3"}, {"identifier": "ENG-1"}, {"identifier": "ENG-2"}]
        p = tmp_path / "export.json"
        p.write_text(json.dumps(data))
        assert read_export_ids(str(p)) == ["ENG-3", "ENG-1", "ENG-2"]

    def test_skips_entries_missing_identifier_field(self, tmp_path):
        data = [{"identifier": "WEB-1"}, {"title": "No identifier here"}, {"identifier": "WEB-3"}]
        p = tmp_path / "export.json"
        p.write_text(json.dumps(data))
        assert read_export_ids(str(p)) == ["WEB-1", "WEB-3"]

    def test_returns_empty_list_for_empty_array(self, tmp_path):
        p = tmp_path / "export.json"
        p.write_text("[]")
        assert read_export_ids(str(p)) == []

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            read_export_ids(str(tmp_path / "missing.json"))

    def test_invalid_json_exits(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not valid json")
        with pytest.raises(SystemExit):
            read_export_ids(str(p))

    def test_non_array_json_exits(self, tmp_path):
        p = tmp_path / "obj.json"
        p.write_text('{"identifier": "WEB-1"}')
        with pytest.raises(SystemExit):
            read_export_ids(str(p))


# ---------------------------------------------------------------------------
# TestReadCsvIds
# ---------------------------------------------------------------------------

def write_csv(tmp_path, lines):
    p = tmp_path / "export.csv"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


class TestReadCsvIds:
    """Tests for reading Linear IDs from a Linear native CSV export (ID column)."""

    def test_reads_id_column(self, tmp_path):
        path = write_csv(tmp_path, ["ID,Title,State", "WEB-1,First,In Progress", "WEB-2,Second,Done"])
        assert read_csv_ids(path) == ["WEB-1", "WEB-2"]

    def test_preserves_order(self, tmp_path):
        path = write_csv(tmp_path, ["ID,Title", "ENG-3,Third", "ENG-1,First", "ENG-2,Second"])
        assert read_csv_ids(path) == ["ENG-3", "ENG-1", "ENG-2"]

    def test_skips_empty_rows(self, tmp_path):
        path = write_csv(tmp_path, ["ID,Title", "WEB-1,Has ID", ",No ID", "WEB-3,Also has ID"])
        assert read_csv_ids(path) == ["WEB-1", "WEB-3"]

    def test_handles_utf8_bom(self, tmp_path):
        p = tmp_path / "bom.csv"
        p.write_bytes(b'\xef\xbb\xbfID,Title\r\nWEB-1,BOM test\r\n')
        assert read_csv_ids(str(p)) == ["WEB-1"]

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            read_csv_ids(str(tmp_path / "missing.csv"))

    def test_no_id_column_exits(self, tmp_path):
        path = write_csv(tmp_path, ["Title,State", "Some issue,Done"])
        with pytest.raises(SystemExit):
            read_csv_ids(path)


# ---------------------------------------------------------------------------
# TestLookupJiraIds
# ---------------------------------------------------------------------------

class TestLookupJiraIds:
    """Tests for the GraphQL-backed lookup of JIRA keys from Linear IDs."""

    def _make_node(self, identifier, jira_url=None):
        attachments = []
        if jira_url:
            attachments.append({"url": jira_url, "title": ""})
        return {
            "identifier": identifier,
            "attachments": {"nodes": attachments},
        }

    def test_returns_jira_key_for_matched_issue(self, monkeypatch):
        import linear_tools.utils as utils
        monkeypatch.setattr(utils, "graphql_request", lambda q, variables=None: {
            "issues": {
                "nodes": [self._make_node("WEB-1", "https://co.atlassian.net/browse/CE-100")],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        })
        results = lookup_jira_ids(["WEB-1"])
        assert results == [{"linear_id": "WEB-1", "jira_key": "CE-100", "jira_url": "https://co.atlassian.net/browse/CE-100"}]

    def test_includes_null_row_for_id_not_in_api_response(self, monkeypatch):
        import linear_tools.utils as utils
        monkeypatch.setattr(utils, "graphql_request", lambda q, variables=None: {
            "issues": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        })
        results = lookup_jira_ids(["WEB-999"])
        assert results == [{"linear_id": "WEB-999", "jira_key": None, "jira_url": None}]

    def test_includes_null_row_for_issue_with_no_jira_attachment(self, monkeypatch):
        import linear_tools.utils as utils
        monkeypatch.setattr(utils, "graphql_request", lambda q, variables=None: {
            "issues": {
                "nodes": [self._make_node("WEB-2")],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        })
        results = lookup_jira_ids(["WEB-2"])
        assert results == [{"linear_id": "WEB-2", "jira_key": None, "jira_url": None}]

    def test_preserves_input_order(self, monkeypatch):
        import linear_tools.utils as utils
        monkeypatch.setattr(utils, "graphql_request", lambda q, variables=None: {
            "issues": {
                # API returns in different order than input
                "nodes": [
                    self._make_node("WEB-2", "https://co.atlassian.net/browse/CE-2"),
                    self._make_node("WEB-1", "https://co.atlassian.net/browse/CE-1"),
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        })
        results = lookup_jira_ids(["WEB-1", "WEB-2"])
        assert [r["linear_id"] for r in results] == ["WEB-1", "WEB-2"]

    def test_handles_pagination(self, monkeypatch):
        import linear_tools.utils as utils
        call_count = 0

        def mock_graphql(q, variables=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "issues": {
                        "nodes": [self._make_node("WEB-1", "https://co.atlassian.net/browse/CE-1")],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                    }
                }
            return {
                "issues": {
                    "nodes": [self._make_node("WEB-2", "https://co.atlassian.net/browse/CE-2")],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }

        monkeypatch.setattr(utils, "graphql_request", mock_graphql)
        # Both WEB- ids are in same team, so pagination happens within one team batch
        results = lookup_jira_ids(["WEB-1", "WEB-2"])
        assert call_count == 2
        assert {r["linear_id"] for r in results} == {"WEB-1", "WEB-2"}

    def test_groups_by_team_key(self, monkeypatch):
        import linear_tools.utils as utils
        seen_team_keys = []

        def mock_graphql(q, variables=None):
            seen_team_keys.append(variables["teamKey"])
            return {
                "issues": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }

        monkeypatch.setattr(utils, "graphql_request", mock_graphql)
        lookup_jira_ids(["WEB-1", "ENG-2"])
        assert set(seen_team_keys) == {"WEB", "ENG"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
