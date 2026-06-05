# linear-tools

CLI for working with Linear issues and projects from the terminal. Covers exports, cycle management, bulk commenting, cross-system linking, and statistics.

## Install

```bash
uv tool install ./packages/linear
```

## Prerequisites

Create a `.env` file (or export environment variables) with:

```
LINEAR_API_KEY=<your-api-key>
LINEAR_ORG_SLUG=your-org   # defaults to "bitgo" if not set
```

Get an API key at: **Linear → Settings → Security & Access → Personal API keys**.

## Commands

| Command | What it does |
|---------|-------------|
| `linear export-issues` | Export issues to JSON or CSV with flexible query filtering |
| `linear export-projects` | Export projects to JSON or CSV |
| `linear add-to-cycle` | Add issues to the active (or a specific) sprint cycle |
| `linear get-statistics` | Estimate point stats by query or project URL |
| `linear comment` | Post a Markdown comment to query-matched issues |
| `linear add-links` | Attach a URL to query-matched issues |
| `linear to-jira` | Resolve Linear issue IDs to their linked JIRA keys |
| `linear merged-issues` | Show GitHub PR status for query-matched issues |
| `linear sync-jira-metadata` | Copy JIRA priority/status/story points to Linear issues |

---

## Query language

Most commands accept `--query` / `-q` with a JQL-like syntax:

```
team = WEB AND state = "In Progress"
priority >= High AND label in [Bug, P0]
team = WEB AND created > 2025-01-01
title contains "auth" AND assignee = "Alice"
identifier in [WEB-1086, WEB-1087]
```

**Supported fields:** `team`, `state`, `assignee`, `label`, `priority`, `estimate`, `created`, `updated`, `project`, `cycle`, `title`, `identifier`, `number`

**Supported operators:** `=`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `contains`

**State note:** `state` values are matched case-insensitively, so `state = done` matches `Done`.

**Priority note:** Linear's scale is inverted numerically (1 = Urgent, 4 = Low). The query language maps human intent correctly — `priority >= High` returns Urgent and High issues.

---

## FAQ

### How do I export issues to a spreadsheet?

```bash
# All In Progress issues for the WEB team → CSV
linear export-issues --query 'team = WEB AND state = "In Progress"' --csv

# Pick specific columns
linear export-issues --query 'team = WEB' --csv \
  --fields identifier,title,state,assignee,estimate

# Export a specific issue by ID
linear export-issues --id WEB-1086
```

Pipe `--csv` output directly to a file: `linear export-issues -q '...' --csv > issues.csv`

### How do I export projects?

```bash
linear export-projects --query 'state = "In Progress"'
linear export-projects --query 'team = WEB' --csv --fields name,url,state,lead
```

### How do I add a set of issues to the current sprint?

```bash
# By identifier (active cycle is auto-detected)
linear add-to-cycle WEB-458 WEB-461 WEB-470 --team WEB

# From a file containing identifiers
linear add-to-cycle -f issues.txt --team WEB

# From JSON produced by `jira to-linear --json` (pipes cleanly)
jira to-linear --json CE-1234 CE-1235 | linear add-to-cycle --jira-json - --team WEB

# Target a specific cycle number instead of the active one
linear add-to-cycle WEB-458 --team WEB --cycle 42

# List available cycles
linear add-to-cycle --list-cycles --team WEB
```

### How do I get story point statistics for a project or query?

```bash
# By project URL (paste from the browser)
linear get-statistics --project "https://linear.app/my-org/project/my-project-abc123"

# By query
linear get-statistics 'team = WEB AND state != Done'

# Combine project + extra filter
linear get-statistics --project "https://..." 'priority >= High'
```

Output is JSON with total/resolved/unresolved issue counts and estimate totals.

### How do I post a comment to multiple issues at once?

```bash
# Inline message
linear comment --query 'label = "Release v2.1"' -m "This ships in the v2.1 release."

# From a Markdown file
linear comment --query 'project = "Q3 Roadmap"' -f announcement.md

# Skip the confirmation prompt when more than one ticket matches
linear comment --query 'label = "Release v2.1"' -m "Shipped." --yes
```

### How do I attach a link to multiple issues at once?

```bash
linear add-links \
  --query 'label = "Release v2.1"' \
  --url "https://github.com/my-org/my-repo/releases/tag/v2.1" \
  --title "v2.1 Release"
```

### How do I check whether PRs linked to issues are merged?

```bash
linear merged-issues 'team = WEB AND state != Done'
```

The command lists each query-matched issue's GitHub PR attachments and shows each PR as `Merged`, `Draft`, `Closed`, or `Open`. `Merged` is highlighted in green.

### How do I find the JIRA key for a Linear issue?

```bash
linear to-jira WEB-458 WEB-461

# From a file
linear to-jira -f identifiers.txt

# JSON output
linear to-jira --json WEB-458
```

The command looks for a JIRA attachment on each Linear issue.

### How do I sync JIRA metadata (priority, status, points) to Linear?

```bash
linear sync-jira-metadata --csv export.csv
```

The CSV should be a JIRA export containing the relevant columns. The command maps JIRA priority and status names to their Linear equivalents and updates each linked issue.

### How do I see all available fields for export?

Run any export command with `--help`:

```bash
linear export-issues --help
linear export-projects --help
```

The `--fields` help text lists every available column name.
