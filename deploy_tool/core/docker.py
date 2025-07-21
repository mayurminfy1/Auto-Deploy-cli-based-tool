import shutil
import subprocess
import os
from pathlib import Path
import typer # Assuming typer is imported for echo messages

def build_docker_image(project_path: Path, framework: str, image_tag: str):
    """
    Builds a Docker image for the given project.
    """
    typer.echo(f"Starting Docker image build for '{framework}' project at {project_path}...")

    # This version directly constructs the path to the framework-specific Dockerfile template
    # and expects it to be at deploy_tool/dockerfiles/{framework}.Dockerfile
    dockerfile_source = Path(f"deploy_tool/dockerfiles/{framework}.Dockerfile")

    dockerfile_dest = project_path / "Dockerfile" # This is where the template will be copied to

    if not dockerfile_source.exists():
        typer.echo(f"Dockerfile for '{framework}' not found at {dockerfile_source}")
        raise FileNotFoundError(f"Dockerfile for {framework} not found at {dockerfile_source}")

    try:
        shutil.copyfile(dockerfile_source, dockerfile_dest)
        typer.echo(f"Copied Dockerfile template from {dockerfile_source.name} to {dockerfile_dest}")
    except Exception as e:
        typer.echo(f"Failed to copy Dockerfile template: {e}")
        raise typer.Exit(code=1)

    try:
        typer.echo(f"Running Docker build for image: {image_tag} from context: {project_path}...")
        # This command runs 'docker build' from the project_path, expecting Dockerfile to be there
        process = subprocess.run( # Changed to 'process =' to capture output
            ["docker", "build", "-t", image_tag, "."],
            cwd=project_path, # CRITICAL: Run docker build from the cloned project's path
            check=True,
            capture_output=True, # Capture output for better error messages
            text=True # Decode output as text
        )
        typer.echo("STDOUT:\n" + process.stdout) # Print captured stdout
        if process.stderr:
            typer.echo("STDERR:\n" + process.stderr) # Print captured stderr
        typer.echo(f"Docker image built: {image_tag}")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Docker build failed (exit code {e.returncode}): {e.stderr}")
        typer.echo(f"STDOUT from failed build:\n{e.stdout}")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"An unexpected error occurred during Docker build: {e}")
        # Only try to print stdout/stderr if 'process' was successfully assigned
        if process is not None and hasattr(process, 'stdout') and process.stdout:
            typer.echo("Partial STDOUT (if available):\n" + process.stdout)
        if process is not None and hasattr(process, 'stderr') and process.stderr:
            typer.echo("Partial STDERR (if available):\n" + process.stderr)
        raise typer.Exit(code=1)
    finally:
        # Clean up the copied Dockerfile from the project path after build
        if dockerfile_dest.exists():
            typer.echo(f"Cleaning up copied Dockerfile: {dockerfile_dest}")
            os.remove(dockerfile_dest)