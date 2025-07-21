# deploy_tool/core/init_logic.py

import json
from pathlib import Path
from typing import Tuple

def detect_framework(project_path: Path) -> Tuple[str, Path, str]:
    """
    Recursively detect the framework by scanning for package.json inside the project.

    Returns:
        A tuple of (framework_name, path_to_detected_project, mayur_project_name)
    """
    for pkg_path in project_path.rglob("package.json"):
        try:
            with open(pkg_path, "r") as f:
                package = json.load(f)

            deps = package.get("dependencies", {})
            dev_deps = package.get("devDependencies", {})
            project_dir = pkg_path.parent
            project_name = "mayur-" + project_dir.name

            if "next" in deps:
                return "nextjs", project_dir, project_name
            elif "vite" in deps or "vite" in dev_deps:
                return "vite", project_dir, project_name
            elif "react-scripts" in deps:
                return "cra", project_dir, project_name
            elif "react" in deps:
                return "react", project_dir, project_name  # fallback if no CRA
        except Exception:
            continue

    return "unknown", project_path, "mayur-unknown"
