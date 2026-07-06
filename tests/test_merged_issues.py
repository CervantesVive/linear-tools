"""Tests for linear merged-issues command."""
import os

os.environ.setdefault("LINEAR_API_KEY", "test")

from unittest.mock import patch

from typer import Typer
from typer.testing import CliRunner

from linear_tools.commands.merged_issues import (
    _issue_pr_urls,
    _pr_status,
    _style_status,
    build_rows,
    merged_issues,
)


def _issue(identifier="WEB-1", title="Issue title", urls=None):
    urls = urls or []
    return {
        "id": "uuid-1",
        "identifier": identifier,
        "title": title,
        "attachments": {
            "nodes": [{"url": url, "title": "PR"} for url in urls],
        },
    }


class TestPrStatus:
    def test_merged(self):
        assert _pr_status({"state": "MERGED", "isDraft": False}) == "Merged"

    def test_draft(self):
        assert _pr_status({"state": "OPEN", "isDraft": True}) == "Draft"

    def test_open(self):
        assert _pr_status({"state": "OPEN", "isDraft": False}) == "Open"

    def test_closed(self):
        assert _pr_status({"state": "CLOSED", "isDraft": False}) == "Closed"

    def test_unknown(self):
        assert _pr_status({"state": "SOMETHING_ELSE"}) == "Unknown"


class TestIssuePrUrls:
    def test_deduplicates_urls(self):
        urls = _issue_pr_urls(_issue(urls=["https://github.com/o/r/pull/1", "https://github.com/o/r/pull/1"]))
        assert urls == ["https://github.com/o/r/pull/1"]

    def test_ignores_missing_urls(self):
        issue = _issue()
        issue["attachments"]["nodes"] = [{"title": "missing"}]
        assert _issue_pr_urls(issue) == []


class TestBuildRows:
    def test_one_row_per_pr(self):
        issue = _issue(urls=["https://github.com/o/r/pull/1", "https://github.com/o/r/pull/2"])

        def fetch(url):
            return {
                "url": url,
                "title": f"Title for {url[-1]}",
                "status": "Merged" if url.endswith("/1") else "Open",
            }

        rows = build_rows([issue], pr_fetcher=fetch)

        assert len(rows) == 2
        assert rows[0]["identifier"] == "WEB-1"
        assert rows[0]["issueUrl"] == "https://linear.app/sinchi/issue/WEB-1"
        assert rows[0]["status"] == "Merged"
        assert rows[1]["status"] == "Open"

    def test_no_pr_row(self):
        rows = build_rows([_issue()])
        assert rows == [{
            "identifier": "WEB-1",
            "issueTitle": "Issue title",
            "issueUrl": "https://linear.app/sinchi/issue/WEB-1",
            "prTitle": None,
            "prUrl": None,
            "status": "No PR",
        }]


class TestStyleStatus:
    def test_merged_is_green_when_color_enabled(self):
        styled = _style_status("Merged", color=True)
        assert "Merged" in styled
        assert "\x1b[" in styled

    def test_other_statuses_are_plain(self):
        assert _style_status("Open", color=True) == "Open"

    def test_color_can_be_disabled(self):
        assert _style_status("Merged", color=False) == "Merged"


class TestMergedIssuesCommand:
    def test_outputs_rows_with_mocked_linear_and_github(self):
        runner = CliRunner()
        app = Typer()
        app.command()(merged_issues)

        with patch("linear_tools.commands.merged_issues._fetch_issues", return_value=[
            _issue(urls=["https://github.com/o/r/pull/1"])
        ]), patch("linear_tools.commands.merged_issues._fetch_pr", return_value={
            "url": "https://github.com/o/r/pull/1",
            "title": "Fix bug",
            "status": "Merged",
        }):
            result = runner.invoke(app, ["team = WEB", "--no-color"])

        assert result.exit_code == 0
        assert "WEB-1" in result.stdout
        assert "https://linear.app/sinchi/issue/WEB-1" in result.stdout
        assert "Merged" in result.stdout
        assert "Fix bug" in result.stdout
        assert "Checked 1 issue(s), 1 PR(s)." in result.stderr

    def test_empty_query_exits_1(self):
        runner = CliRunner()
        app = Typer()
        app.command()(merged_issues)

        result = runner.invoke(app, [""])

        assert result.exit_code == 1
        assert "query cannot be empty" in result.stderr
