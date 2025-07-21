# deploy_tool/core/history_manager.py

import json
from pathlib import Path
import datetime
import typer

HISTORY_FILE_NAME = "deployments_history.json"
# The history will be stored in workspace/<project_name>/deployments_history.json
HISTORY_BASE_DIR = Path("workspace")

def _get_history_file_path(project_name: str) -> Path:
    """Returns the full path to the history file for a given project."""
    project_history_dir = HISTORY_BASE_DIR / project_name
    project_history_dir.mkdir(parents=True, exist_ok=True) # Ensure directory exists
    return project_history_dir / HISTORY_FILE_NAME

def load_history(project_name: str) -> list:
    """Loads deployment history for a project from its JSON file."""
    history_file = _get_history_file_path(project_name)
    if not history_file.exists():
        return []
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        typer.echo(f"Warning: History file for '{project_name}' is corrupted ({e}). Starting with empty history.")
        return []
    except Exception as e:
        typer.echo(f"Warning: Could not load history for '{project_name}': {e}. Starting with empty history.")
        return []

def save_history(project_name: str, history_data: list):
    """Saves deployment history for a project to its JSON file."""
    history_file = _get_history_file_path(project_name)
    try:
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, indent=2)
    except Exception as e:
        typer.echo(f"Error: Failed to save history for '{project_name}': {e}")
        # Optionally, raise an exception or handle more robustly

def add_deployment_record(project_name: str, record: dict):
    """Adds a new deployment record to the history for a project."""
    history = load_history(project_name)
    history.append(record)
    save_history(project_name, history)
    typer.echo(f"Deployment record saved for '{project_name}'.")