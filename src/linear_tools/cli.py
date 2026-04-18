"""Linear CLI — entry point for the `linear` command."""
import typer

from linear_tools.commands.export_issues import export_issues
from linear_tools.commands.add_to_cycle import add_to_cycle
from linear_tools.commands.sync_jira_metadata import sync_jira_metadata

app = typer.Typer(
    name="linear",
    help="Linear API CLI tools.",
    no_args_is_help=True,
)

app.command(name="export-issues")(export_issues)
app.command(name="add-to-cycle")(add_to_cycle)
app.command(name="sync-jira-metadata")(sync_jira_metadata)

if __name__ == "__main__":
    app()
