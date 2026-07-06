#!/usr/bin/env python3
"""
Tests for sync_jira_metadata.py

Tests the pure data transformation functions that require no API calls:
- load_csv: CSV parsing with duplicate column deduplication
- detect_columns: auto-detection of relevant columns from headers
- extract_story_points: first-non-empty value from candidate columns
- build_update_input: full JIRA-to-Linear field mapping per row
- load_jira_mapping: JSON mapping file parsing

API-dependent functions (sync_metadata, get_state_id) are not tested here
as they require a live Linear environment.
"""
import os
import json
import pytest
import sys

# Set fake credentials before importing the module, since linear_utils
# validates LINEAR_API_KEY at import time.
os.environ.setdefault('LINEAR_API_KEY', 'lin_api_test_key')

from linear_tools.commands import sync_jira_metadata
from linear_tools.commands.sync_jira_metadata import (
    load_csv,
    detect_columns,
    extract_story_points,
    build_update_input,
    load_jira_mapping,
    JIRA_PRIORITY_TO_LINEAR,
    JIRA_STATUS_TO_LINEAR,
    get_user_id,
    get_label_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_csv(tmp_path, lines):
    """Write lines to a temp CSV file and return its path."""
    p = tmp_path / "test.csv"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def write_mapping(tmp_path, entries):
    """Write a jira_to_linear JSON mapping to a temp file and return its path."""
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# TestLoadCsv
# ---------------------------------------------------------------------------

class TestLoadCsv:
    """Tests for CSV loading and duplicate header handling."""

    def test_basic_load(self, tmp_path):
        path = write_csv(tmp_path, [
            "Issue key,Status,Priority",
            "CE-100,In Progress,High",
            "CE-101,Done,Low",
        ])
        headers, rows = load_csv(path)
        assert headers == ["Issue key", "Status", "Priority"]
        assert len(rows) == 2
        assert rows[0]["Issue key"] == "CE-100"
        assert rows[1]["Status"] == "Done"

    def test_deduplicates_duplicate_headers(self, tmp_path):
        """Duplicate column names get a .1, .2 suffix — matching csv.DictReader behaviour."""
        path = write_csv(tmp_path, [
            "Issue key,Custom field (Story Points),Custom field (Story Points),Priority",
            "CE-100,3.0,,High",
        ])
        headers, rows = load_csv(path)
        assert "Custom field (Story Points)" in headers
        assert "Custom field (Story Points).1" in headers
        assert rows[0]["Custom field (Story Points)"] == "3.0"
        assert rows[0]["Custom field (Story Points).1"] == ""

    def test_pads_short_rows(self, tmp_path):
        """Rows shorter than the header are padded with empty strings."""
        path = write_csv(tmp_path, [
            "Issue key,Status,Priority",
            "CE-100",          # only 1 of 3 columns present
        ])
        _, rows = load_csv(path)
        assert rows[0]["Issue key"] == "CE-100"
        assert rows[0]["Status"] == ""
        assert rows[0]["Priority"] == ""

    def test_handles_utf8_bom(self, tmp_path):
        """Files with a UTF-8 BOM (common in Excel exports) are parsed correctly."""
        p = tmp_path / "bom.csv"
        p.write_bytes(b"\xef\xbb\xbfIssue key,Status\nCE-100,Done\n")
        headers, rows = load_csv(str(p))
        assert headers[0] == "Issue key"   # BOM stripped
        assert rows[0]["Issue key"] == "CE-100"

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            load_csv(str(tmp_path / "nonexistent.csv"))


# ---------------------------------------------------------------------------
# TestDetectColumns
# ---------------------------------------------------------------------------

class TestDetectColumns:
    """Tests for auto-detection of CSV column names."""

    def test_detects_issue_key_variants(self):
        for col in ["Issue Key", "Issue key", "Key"]:
            result = detect_columns([col, "Status", "Priority"])
            assert result["issue_key"] == col

    def test_detects_status_and_priority(self):
        headers = ["Issue key", "Status", "Priority"]
        result = detect_columns(headers)
        assert result["status"] == "Status"
        assert result["priority"] == "Priority"

    def test_detects_single_story_points_column(self):
        headers = ["Issue key", "Custom field (Story Points)", "Priority"]
        result = detect_columns(headers)
        assert result["story_points"] == ["Custom field (Story Points)"]

    def test_detects_duplicate_story_points_columns(self):
        """Both deduplicated story-points headers are returned as a list."""
        headers = [
            "Issue key",
            "Custom field (Story Points)",
            "Custom field (Story Points).1",
            "Priority",
        ]
        result = detect_columns(headers)
        assert "Custom field (Story Points)" in result["story_points"]
        assert "Custom field (Story Points).1" in result["story_points"]
        assert len(result["story_points"]) == 2

    def test_returns_none_for_missing_columns(self):
        headers = ["Summary"]
        result = detect_columns(headers)
        assert result["issue_key"] is None
        assert result["status"] is None
        assert result["priority"] is None
        assert result["story_points"] == []
        assert result["assignee"] is None
        assert result["labels"] is None

    def test_overrides_respected(self):
        headers = ["Issue key", "My Status", "My Priority", "My Points"]
        overrides = {
            "status": "My Status",
            "priority": "My Priority",
            "story_points": "My Points",
        }
        result = detect_columns(headers, overrides)
        assert result["status"] == "My Status"
        assert result["priority"] == "My Priority"
        assert result["story_points"] == ["My Points"]

    def test_issue_key_override(self):
        headers = ["Ticket", "Status"]
        result = detect_columns(headers, {"issue_key": "Ticket"})
        assert result["issue_key"] == "Ticket"


# ---------------------------------------------------------------------------
# TestExtractStoryPoints
# ---------------------------------------------------------------------------

class TestExtractStoryPoints:
    """Tests for story points extraction with fallback column support."""

    def test_extracts_from_primary_column(self):
        row = {"Custom field (Story Points)": "5.0", "Custom field (Story Points).1": ""}
        assert extract_story_points(row, ["Custom field (Story Points)", "Custom field (Story Points).1"]) == 5.0

    def test_falls_back_to_second_column(self):
        """When the primary column is empty, the secondary column is used."""
        row = {"Custom field (Story Points)": "", "Custom field (Story Points).1": "3.0"}
        assert extract_story_points(row, ["Custom field (Story Points)", "Custom field (Story Points).1"]) == 3.0

    def test_returns_none_when_all_empty(self):
        row = {"Custom field (Story Points)": "", "Custom field (Story Points).1": ""}
        assert extract_story_points(row, ["Custom field (Story Points)", "Custom field (Story Points).1"]) is None

    def test_returns_none_for_non_numeric(self):
        row = {"Custom field (Story Points)": "N/A"}
        assert extract_story_points(row, ["Custom field (Story Points)"]) is None

    def test_converts_integer_string_to_float(self):
        row = {"Custom field (Story Points)": "8"}
        assert extract_story_points(row, ["Custom field (Story Points)"]) == 8.0

    def test_returns_none_for_empty_candidates_list(self):
        row = {"Custom field (Story Points)": "5.0"}
        assert extract_story_points(row, []) is None

    def test_skips_non_numeric_and_continues(self):
        """A non-numeric primary column falls through to a valid secondary column."""
        row = {"col_a": "bad", "col_b": "2.0"}
        assert extract_story_points(row, ["col_a", "col_b"]) == 2.0


# ---------------------------------------------------------------------------
# TestBuildUpdateInput — priority mapping
# ---------------------------------------------------------------------------

class TestBuildUpdateInputPriority:
    """Tests for JIRA priority → Linear priority integer mapping."""

    BASE_COLUMNS = {
        "issue_key": "Issue key",
        "priority": "Priority",
        "status": None,
        "story_points": [],
        "assignee": None,
        "labels": None,
    }
    ENABLED = {"priority"}

    @pytest.mark.parametrize("jira_priority,expected_int", [
        ("Highest", 1),
        ("High", 2),
        ("Medium", 3),
        ("Low", 4),
        ("Lowest", 4),
        ("Needs Triage", 0),
    ])
    def test_priority_mapping(self, jira_priority, expected_int):
        row = {"Issue key": "CE-100", "Priority": jira_priority}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert input_dict.get("priority") == expected_int
        assert not any(f == "priority" for f, _ in skipped)

    def test_unknown_priority_is_skipped(self):
        row = {"Issue key": "CE-100", "Priority": "Critical"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "priority" not in input_dict
        assert any(f == "priority" for f, _ in skipped)

    def test_empty_priority_is_skipped(self):
        row = {"Issue key": "CE-100", "Priority": ""}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "priority" not in input_dict
        assert any(f == "priority" for f, _ in skipped)

    def test_priority_excluded_when_not_in_enabled_fields(self):
        row = {"Issue key": "CE-100", "Priority": "High"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", set())
        assert "priority" not in input_dict


# ---------------------------------------------------------------------------
# TestBuildUpdateInput — estimate (story points)
# ---------------------------------------------------------------------------

class TestBuildUpdateInputEstimate:
    """Tests for story points → Linear estimate mapping."""

    BASE_COLUMNS = {
        "issue_key": "Issue key",
        "priority": None,
        "status": None,
        "story_points": ["Custom field (Story Points)"],
        "assignee": None,
        "labels": None,
    }
    ENABLED = {"estimate"}

    def test_maps_story_points_to_estimate(self):
        row = {"Issue key": "CE-100", "Custom field (Story Points)": "5.0"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert input_dict.get("estimate") == 5.0
        assert not any(f == "estimate" for f, _ in skipped)

    def test_empty_story_points_is_skipped(self):
        row = {"Issue key": "CE-100", "Custom field (Story Points)": ""}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "estimate" not in input_dict
        assert any(f == "estimate" for f, _ in skipped)

    def test_non_numeric_story_points_is_skipped(self):
        row = {"Issue key": "CE-100", "Custom field (Story Points)": "TBD"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "estimate" not in input_dict
        assert any(f == "estimate" for f, _ in skipped)

    def test_estimate_excluded_when_not_in_enabled_fields(self):
        row = {"Issue key": "CE-100", "Custom field (Story Points)": "3.0"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", set())
        assert "estimate" not in input_dict


# ---------------------------------------------------------------------------
# TestBuildUpdateInput — status mapping
# ---------------------------------------------------------------------------

class TestBuildUpdateInputStatus:
    """Tests for JIRA status → Linear workflow state mapping."""

    BASE_COLUMNS = {
        "issue_key": "Issue key",
        "priority": None,
        "status": "Status",
        "story_points": [],
        "assignee": None,
        "labels": None,
    }
    ENABLED = {"status"}

    @pytest.mark.parametrize("jira_status,expected_linear_name", [
        ("Backlog", "Backlog"),
        ("BACKLOG", "Backlog"),            # case-insensitive
        ("To Do", "Todo"),
        ("Triaged", "Todo"),
        ("In Progress", "In Progress"),
        ("Blocked", "Blocked"),
        ("Waiting For Support", "Blocked"),
        ("In Review", "In Review"),
        ("Review", "In Review"),
        ("In Test Review", "In Review"),
        ("In Test", "Merged"),
        ("Ready For Test", "Merged"),
        ("Ready For Prod", "Merged"),
        ("Done", "Done"),
        ("Tested", "Done"),
        ("Won't Fix", "Canceled"),
        ("Duplicate", "Duplicate"),
    ])
    def test_status_resolves_to_state_id(self, jira_status, expected_linear_name, monkeypatch):
        """Each mapped JIRA status resolves to the correct Linear state name and calls get_state_id."""
        resolved_ids = {}

        def fake_get_state_id(team_key, linear_state_name):
            resolved_ids["name"] = linear_state_name
            return f"uuid-{linear_state_name.lower().replace(' ', '-')}"

        monkeypatch.setattr(sync_jira_metadata, "get_state_id", fake_get_state_id)

        row = {"Issue key": "CE-100", "Status": jira_status}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)

        assert "stateId" in input_dict
        assert resolved_ids["name"] == expected_linear_name
        assert not any(f == "status" for f, _ in skipped)

    def test_unknown_status_is_skipped(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: "some-uuid")
        row = {"Issue key": "CE-100", "Status": "FUNKY STATUS"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "stateId" not in input_dict
        assert any(f == "status" for f, _ in skipped)

    def test_empty_status_is_skipped(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: "some-uuid")
        row = {"Issue key": "CE-100", "Status": ""}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "stateId" not in input_dict
        assert any(f == "status" for f, _ in skipped)

    def test_unresolvable_state_uuid_is_skipped(self, monkeypatch):
        """If the team doesn't have the mapped state, the field is skipped."""
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: None)
        row = {"Issue key": "CE-100", "Status": "In Progress"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "stateId" not in input_dict
        assert any(f == "status" for f, _ in skipped)

    def test_status_excluded_when_not_in_enabled_fields(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: "some-uuid")
        row = {"Issue key": "CE-100", "Status": "Done"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", set())
        assert "stateId" not in input_dict


# ---------------------------------------------------------------------------
# TestBuildUpdateInput — combined fields
# ---------------------------------------------------------------------------

class TestBuildUpdateInputCombined:
    """Tests for rows with multiple fields, partial data, and field filtering."""

    FULL_COLUMNS = {
        "issue_key": "Issue key",
        "priority": "Priority",
        "status": "Status",
        "story_points": ["Custom field (Story Points)", "Custom field (Story Points).1"],
        "assignee": None,
        "labels": None,
    }
    ALL_FIELDS = {"priority", "estimate", "status"}

    def test_all_fields_populated(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: "uuid-in-progress")
        row = {
            "Issue key": "CE-100",
            "Priority": "High",
            "Status": "In Progress",
            "Custom field (Story Points)": "5.0",
            "Custom field (Story Points).1": "",
        }
        input_dict, skipped = build_update_input(row, self.FULL_COLUMNS, "WEB", self.ALL_FIELDS)
        assert input_dict == {"priority": 2, "estimate": 5.0, "stateId": "uuid-in-progress"}
        assert skipped == []

    def test_partial_fields_others_skipped(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: None)
        row = {
            "Issue key": "CE-100",
            "Priority": "Medium",
            "Status": "",                              # empty — skipped
            "Custom field (Story Points)": "",         # empty — skipped
            "Custom field (Story Points).1": "",
        }
        input_dict, skipped = build_update_input(row, self.FULL_COLUMNS, "WEB", self.ALL_FIELDS)
        assert input_dict == {"priority": 3}
        skipped_fields = [f for f, _ in skipped]
        assert "estimate" in skipped_fields
        assert "status" in skipped_fields

    def test_fields_filter_limits_output(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: "some-uuid")
        row = {
            "Issue key": "CE-100",
            "Priority": "High",
            "Status": "Done",
            "Custom field (Story Points)": "8.0",
            "Custom field (Story Points).1": "",
        }
        input_dict, _ = build_update_input(row, self.FULL_COLUMNS, "WEB", {"priority"})
        assert "priority" in input_dict
        assert "estimate" not in input_dict
        assert "stateId" not in input_dict

    def test_team_key_derived_from_linear_id(self, monkeypatch):
        """The team key passed to get_state_id comes from the caller, not the row."""
        captured = {}
        def fake_get_state_id(team_key, linear_state_name):
            captured["team_key"] = team_key
            return "some-uuid"
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", fake_get_state_id)

        row = {"Issue key": "CE-100", "Status": "Done"}
        columns = {"issue_key": "Issue key", "priority": None, "status": "Status", "story_points": [], "assignee": None, "labels": None}
        build_update_input(row, columns, "MOBILE", {"status"})
        assert captured["team_key"] == "MOBILE"


# ---------------------------------------------------------------------------
# TestLoadJiraMapping
# ---------------------------------------------------------------------------

class TestLoadJiraMapping:
    """Tests for JIRA → Linear mapping JSON loading."""

    def test_loads_valid_mapping(self, tmp_path):
        entries = [
            {"jira_key": "CE-100", "linear_id": "WEB-458", "linear_url": "https://linear.app/..."},
            {"jira_key": "CE-101", "linear_id": "WEB-461", "linear_url": "https://linear.app/..."},
        ]
        path = write_mapping(tmp_path, entries)
        mapping = load_jira_mapping(path)
        assert mapping == {"CE-100": "WEB-458", "CE-101": "WEB-461"}

    def test_handles_null_linear_id(self, tmp_path):
        entries = [{"jira_key": "CE-100", "linear_id": None, "linear_url": None}]
        path = write_mapping(tmp_path, entries)
        mapping = load_jira_mapping(path)
        assert mapping["CE-100"] is None

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            load_jira_mapping(str(tmp_path / "missing.json"))

    def test_invalid_json_exits(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        with pytest.raises(SystemExit):
            load_jira_mapping(str(p))

    def test_loads_from_stdin(self, monkeypatch):
        import io
        entries = [{"jira_key": "CE-200", "linear_id": "WEB-500"}]
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(entries)))
        mapping = load_jira_mapping("-")
        assert mapping == {"CE-200": "WEB-500"}


# ---------------------------------------------------------------------------
# TestJiraStatusMapping — completeness check
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TestDetectColumnsAssigneeLabels
# ---------------------------------------------------------------------------

class TestDetectColumnsAssigneeLabels:
    """Tests for assignee and labels column detection."""

    def test_detects_assignee_column(self):
        headers = ["Issue key", "Assignee", "Status"]
        result = detect_columns(headers)
        assert result["assignee"] == "Assignee"

    def test_returns_none_when_assignee_missing(self):
        headers = ["Issue key", "Status"]
        result = detect_columns(headers)
        assert result["assignee"] is None

    def test_detects_labels_column(self):
        headers = ["Issue key", "Labels", "Status"]
        result = detect_columns(headers)
        assert result["labels"] == "Labels"

    def test_returns_none_when_labels_missing(self):
        headers = ["Issue key", "Status"]
        result = detect_columns(headers)
        assert result["labels"] is None

    def test_overrides_assignee_column(self):
        headers = ["Issue key", "Owner"]
        result = detect_columns(headers, {"assignee": "Owner"})
        assert result["assignee"] == "Owner"

    def test_overrides_labels_column(self):
        headers = ["Issue key", "Tags"]
        result = detect_columns(headers, {"labels": "Tags"})
        assert result["labels"] == "Tags"


# ---------------------------------------------------------------------------
# TestBuildUpdateInputAssignee
# ---------------------------------------------------------------------------

class TestBuildUpdateInputAssignee:
    """Tests for JIRA assignee → Linear assigneeId mapping."""

    BASE_COLUMNS = {
        "issue_key": "Issue key",
        "priority": None,
        "status": None,
        "story_points": [],
        "assignee": "Assignee",
        "labels": None,
    }
    ENABLED = {"assignee"}

    def test_assignee_resolved_to_user_id(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_user_id", lambda name: "uuid-jamie")
        row = {"Issue key": "CE-100", "Assignee": "Jamie Rivera"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert input_dict["assigneeId"] == "uuid-jamie"
        assert not any(f == "assignee" for f, _ in skipped)

    def test_unknown_assignee_skipped(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_user_id", lambda name: None)
        row = {"Issue key": "CE-100", "Assignee": "Unknown Person"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "assigneeId" not in input_dict
        assert any(f == "assignee" for f, _ in skipped)

    def test_empty_assignee_skipped(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_user_id", lambda name: "uuid-jamie")
        row = {"Issue key": "CE-100", "Assignee": ""}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "assigneeId" not in input_dict
        assert any(f == "assignee" for f, _ in skipped)

    def test_assignee_excluded_when_not_enabled(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_user_id", lambda name: "uuid-jamie")
        row = {"Issue key": "CE-100", "Assignee": "Jamie Rivera"}
        input_dict, _ = build_update_input(row, self.BASE_COLUMNS, "WEB", set())
        assert "assigneeId" not in input_dict

    def test_assignee_skipped_when_column_missing(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_user_id", lambda name: "uuid-jamie")
        columns = dict(self.BASE_COLUMNS, assignee=None)
        row = {"Issue key": "CE-100"}
        input_dict, skipped = build_update_input(row, columns, "WEB", self.ENABLED)
        assert "assigneeId" not in input_dict
        assert not any(f == "assignee" for f, _ in skipped)


# ---------------------------------------------------------------------------
# TestBuildUpdateInputLabels
# ---------------------------------------------------------------------------

class TestBuildUpdateInputLabels:
    """Tests for JIRA labels → Linear labelIds mapping."""

    BASE_COLUMNS = {
        "issue_key": "Issue key",
        "priority": None,
        "status": None,
        "story_points": [],
        "assignee": None,
        "labels": "Labels",
    }
    ENABLED = {"labels"}

    def test_single_label_resolved(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: f"uuid-{name.lower()}")
        row = {"Issue key": "CE-100", "Labels": "Bug"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert input_dict["labelIds"] == ["uuid-bug"]
        assert not any(f == "labels" for f, _ in skipped)

    def test_multiple_comma_separated_labels(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: f"uuid-{name.lower()}")
        row = {"Issue key": "CE-100", "Labels": "Bug, Feature, UX"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert input_dict["labelIds"] == ["uuid-bug", "uuid-feature", "uuid-ux"]

    def test_unknown_labels_skipped_with_reason(self, monkeypatch):
        def fake_get_label_id(name):
            return "uuid-bug" if name.lower() == "bug" else None
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", fake_get_label_id)
        row = {"Issue key": "CE-100", "Labels": "Bug, NonExistent"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert input_dict["labelIds"] == ["uuid-bug"]
        assert any("NonExistent" in reason for f, reason in skipped if f == "labels")

    def test_all_unknown_labels_no_label_ids(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: None)
        row = {"Issue key": "CE-100", "Labels": "Foo, Bar"}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "labelIds" not in input_dict
        assert any(f == "labels" for f, _ in skipped)

    def test_empty_labels_skipped(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: "uuid-x")
        row = {"Issue key": "CE-100", "Labels": ""}
        input_dict, skipped = build_update_input(row, self.BASE_COLUMNS, "WEB", self.ENABLED)
        assert "labelIds" not in input_dict
        assert any(f == "labels" for f, _ in skipped)

    def test_labels_excluded_when_not_enabled(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: "uuid-x")
        row = {"Issue key": "CE-100", "Labels": "Bug"}
        input_dict, _ = build_update_input(row, self.BASE_COLUMNS, "WEB", set())
        assert "labelIds" not in input_dict

    def test_labels_skipped_when_column_missing(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: "uuid-x")
        columns = dict(self.BASE_COLUMNS, labels=None)
        row = {"Issue key": "CE-100"}
        input_dict, skipped = build_update_input(row, columns, "WEB", self.ENABLED)
        assert "labelIds" not in input_dict
        assert not any(f == "labels" for f, _ in skipped)


# ---------------------------------------------------------------------------
# TestBuildUpdateInputCombinedAllFields
# ---------------------------------------------------------------------------

class TestBuildUpdateInputCombinedAllFields:
    """Integration test with all five fields together."""

    def test_all_five_fields_populated(self, monkeypatch):
        monkeypatch.setattr(sync_jira_metadata, "get_state_id", lambda t, n: "uuid-in-progress")
        monkeypatch.setattr(sync_jira_metadata, "get_user_id", lambda name: "uuid-jamie")
        monkeypatch.setattr(sync_jira_metadata, "get_label_id", lambda name: f"uuid-{name.lower()}")

        columns = {
            "issue_key": "Issue key",
            "priority": "Priority",
            "status": "Status",
            "story_points": ["Custom field (Story Points)"],
            "assignee": "Assignee",
            "labels": "Labels",
        }
        row = {
            "Issue key": "CE-100",
            "Priority": "High",
            "Status": "In Progress",
            "Custom field (Story Points)": "5.0",
            "Assignee": "Jamie Rivera",
            "Labels": "Bug, Feature",
        }
        all_fields = {"priority", "estimate", "status", "assignee", "labels"}
        input_dict, skipped = build_update_input(row, columns, "WEB", all_fields)
        assert input_dict == {
            "priority": 2,
            "estimate": 5.0,
            "stateId": "uuid-in-progress",
            "assigneeId": "uuid-jamie",
            "labelIds": ["uuid-bug", "uuid-feature"],
        }
        assert skipped == []


# ---------------------------------------------------------------------------
# TestJiraStatusMapping — completeness check
# ---------------------------------------------------------------------------

class TestJiraStatusMapping:
    """Verify the status mapping dict covers all statuses found in real JIRA exports."""

    KNOWN_STATUSES_FROM_CSV = [
        "Backlog", "Blocked", "Done", "In Progress", "In Review",
        "In Test", "Ready For Prod", "To Do", "Won't Fix",
    ]

    def test_all_csv_statuses_are_mapped(self):
        """Every status from a real Cycle1 JIRA export maps to a Linear state."""
        for status in self.KNOWN_STATUSES_FROM_CSV:
            normalized = status.upper()
            assert normalized in JIRA_STATUS_TO_LINEAR, (
                f"JIRA status '{status}' (normalized: '{normalized}') has no mapping"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
