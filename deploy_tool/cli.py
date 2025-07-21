# cli.py

import typer
# Import the Typer app from your commands modules
from deploy_tool.commands import init
from deploy_tool.commands.full_deploy import full_deploy_app # Correctly import full_deploy_app

# Define the main Typer application
app = typer.Typer(help="Your Vercel-like CLI tool for frontend deployment.")

# Add the 'init' command group
app.add_typer(init.app, name="init", help="Initialize a new project configuration.")

# Add the 'full-deploy' command group, which contains 'run' and 'rollback'
app.add_typer(full_deploy_app, name="full-deploy", help="Commands for the full deployment pipeline (run, rollback).")

if __name__ == "__main__":
    app()