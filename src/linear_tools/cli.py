"""Linear CLI — entry point for the `linear` command."""
import typer

from linear_tools.commands.export_issues import export_issues
from linear_tools.commands.add_to_cycle import add_to_cycle
from linear_tools.commands.sync_jira_metadata import sync_jira_metadata
from linear_tools.commands.to_jira import to_jira
from linear_tools.commands.get_statistics import get_statistics
from linear_tools.commands.export_projects import export_projects

app = typer.Typer(
    name="linear",
    help="Linear API CLI tools.",
    no_args_is_help=True,
)

app.command(name="export-issues")(export_issues)
app.command(name="export-projects")(export_projects)
app.command(name="add-to-cycle")(add_to_cycle)
app.command(name="sync-jira-metadata")(sync_jira_metadata)
app.command(name="to-jira")(to_jira)
app.command(name="get-statistics")(get_statistics)

if __name__ == "__main__":
    app()
