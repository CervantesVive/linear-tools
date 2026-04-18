"""Linear CLI — entry point for the `linear` command."""
import typer

from linear_tools.commands.export_issues import app as export_app
from linear_tools.commands.add_to_cycle import app as add_to_cycle_app
from linear_tools.commands.sync_jira_metadata import app as sync_app

app = typer.Typer(
    name="linear",
    help="Linear API CLI tools.",
    no_args_is_help=True,
)
app.add_typer(export_app, name="export-issues")
app.add_typer(add_to_cycle_app, name="add-to-cycle")
app.add_typer(sync_app, name="sync-jira-metadata")

if __name__ == "__main__":
    app()
