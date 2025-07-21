import typer
import os
import json
from pathlib import Path
import subprocess
from git import Repo

app = typer.Typer(help="Initialize a new project for deployment.")

CONFIG_FILE_NAME = ".deploytool.json"

@app.callback(invoke_without_command=True)
def initialize_project(
    ctx: typer.Context,
    project_path: Path = typer.Argument(
        Path("."),
        help="Path to the project directory. Defaults to current directory."
    ),
    region: str = typer.Option(
        "ap-south-1",
        "--region", "-r",
        help="Default AWS region for deployments."
    ),
    container_port: int = typer.Option(
        3000,
        "--port", "-p",
        help="Default container port your application listens on."
    ),
    aws_profile: str = typer.Option(
        "mayur-sso", # CRITICAL CHANGE: Default profile name updated
        "--aws-profile",
        help="AWS CLI profile to use for authentication."
    ),
):
    if ctx.invoked_subcommand is not None:
        return

    typer.echo(f"Initializing project in: {project_path.resolve()}")

    if not project_path.is_dir():
        typer.echo(f"Error: Project path '{project_path}' is not a directory.")
        raise typer.Exit(code=1)

    config_file_path = project_path / CONFIG_FILE_NAME

    if config_file_path.exists():
        overwrite = typer.confirm(
            f"A {CONFIG_FILE_NAME} file already exists at {config_file_path}. Overwrite it?"
        )
        if not overwrite:
            typer.echo("Initialization cancelled.")
            raise typer.Exit()

    project_name = None
    package_json_path = project_path / "package.json"
    if package_json_path.exists():
        try:
            with open(package_json_path, 'r', encoding='utf-8') as f:
                package_json = json.load(f)
                project_name = package_json.get("name")
        except json.JSONDecodeError:
            typer.echo(f"Could not parse package.json at {package_json_path}. Using directory name as project name.")

    if not project_name:
        project_name = project_path.name.lower().replace(" ", "-").replace("_", "-")
        typer.echo(f"Using directory name '{project_name}' as project name.")
    else:
        typer.echo(f"Detected project name: {project_name} from package.json.")

    typer.echo("\n--- AWS Configuration Check ---")
    try:
        command = ["aws", "--profile", aws_profile, "s3", "ls"]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy()
        )
        typer.echo(f"AWS profile '{aws_profile}' appears to be configured and accessible.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"AWS profile '{aws_profile}' might not be configured correctly or lacks permissions.")
        typer.echo("   Please ensure you have run 'aws configure --profile " + aws_profile + "'")
        typer.echo(f"   Error details: {e.stderr.strip()}")
    except FileNotFoundError:
        typer.echo("AWS CLI is not installed or not found in your PATH.")
        typer.echo("   Please install AWS CLI to manage credentials: https://docs.aws.com/cli/latest/userguide/getting-started-install.html")


    initial_config = {
        "project_name": project_name,
        "repo_url": "NOT_SET",
        "region": region,
        "container_port": container_port,
        "aws_profile": aws_profile,
        "environments": {
            "development": {
                "build_command": "npm run build",
                "env_vars": {}
            },
            "production": {
                "build_command": "npm run build",
                "env_vars": {}
            }
        },
        "deployments": []
    }

    try:
        from git import Repo
        repo = Repo(project_path)
        if not repo.bare:
            if 'origin' in repo.remotes:
                initial_config["repo_url"] = next(repo.remotes.origin.urls)
                typer.echo(f"Detected GitHub repo URL: {initial_config['repo_url']}")
    except Exception as e:
        typer.echo(f"Could not detect Git repository URL: {e}. Please set 'repo_url' manually later.")

    try:
        with open(config_file_path, "w", encoding='utf-8') as f:
            json.dump(initial_config, f, indent=2)
        typer.echo(f"Created {CONFIG_FILE_NAME} at {config_file_path}")
    except Exception as e:
        typer.echo(f"Error writing {CONFIG_FILE_NAME}: {e}")
        raise typer.Exit(code=1)

    typer.echo("\nProject initialized successfully!")
    typer.echo(f"Next steps:")
    typer.echo(f"- Review and customize {CONFIG_FILE_NAME}.")
    typer.echo(f"- Run 'deploy-tool deploy' from this directory to deploy your application.")