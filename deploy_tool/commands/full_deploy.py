# deploy_tool/commands/full_deploy.py

import typer
import os
import datetime
import json
import time
from pathlib import Path
from git import Repo
from urllib.parse import urlparse
import shutil

# Import all necessary core modules
from deploy_tool.core.init_logic import detect_framework
from deploy_tool.core.docker import build_docker_image
from deploy_tool.core.ecr import push_to_ecr
from deploy_tool.core.terraform import apply_terraform, get_terraform_output, write_tfvars, destroy_terraform, TF_BASE_DIR, TF_EC2_DIR
from deploy_tool.core.ec2_provision import provision_ec2
from deploy_tool.core import history_manager
# --- NEW IMPORT for the new rollback logic ---
from deploy_tool.core.rollback import rollback_ecs_service

# --- UPDATED: Help text now includes the new 'rollback' command ---
full_deploy_app = typer.Typer(help="Commands for the full deployment pipeline (run, destroy, rollback, history, cleanup-local).")


@full_deploy_app.command("run")
def run_command_logic(
    repo_url: str = typer.Argument(..., help="GitHub repo URL"),
    region: str = typer.Option("ap-south-1", "--region", help="AWS region"),
    port: int = typer.Option(3000, "--port", help="Port your container listens on"),
    aws_profile: str = typer.Option("mayur-sso", "--aws-profile", help="AWS CLI profile to use."),
    max_retries: int = typer.Option(3, "--retries", "-R", help="Maximum number of retries for failed steps."),
    retry_delay_seconds: int = typer.Option(10, "--retry-delay", "-D", help="Delay in seconds between retries.")
):
    """
    Clones repo, detects framework, builds image, pushes to ECR, deploys via Terraform, and sets up monitoring.
    """
    # This function's logic is based on your latest version
    typer.echo(f"Starting full deployment for {repo_url}...")

    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    workspace_path = Path("workspace")
    workspace_path.mkdir(exist_ok=True)
    clone_path = workspace_path / repo_name

    typer.echo("Step 1: Cloning repository...")
    if clone_path.exists():
        shutil.rmtree(clone_path, ignore_errors=True)
    Repo.clone_from(repo_url, clone_path)

    typer.echo("Step 2: Detecting framework...")
    framework, project_path_for_build, project_name = detect_framework(clone_path)
    typer.echo(f"Detected framework: {framework}")

    typer.echo("Step 3: Building Docker image...")
    safe_name = project_name.lower().replace("_", "-")
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    image_tag = f"{safe_name}:{timestamp}"
    build_docker_image(
        project_path=project_path_for_build,
        framework=framework,
        image_tag=image_tag
    )
    typer.echo(f"Docker image built: {image_tag}")

    typer.echo("Step 4: Pushing image to ECR...")
    image_uri = push_to_ecr(image_tag=image_tag, repo_name=safe_name, aws_profile=aws_profile)
    typer.echo(f"Image pushed to ECR: {image_uri}")

    typer.echo("Step 5: Writing terraform.tfvars.json for ECS deployment...")
    write_tfvars(
        project_name=safe_name, region=region, container_port=port,
        image_url=image_uri, aws_profile=aws_profile, env_vars={}
    )

    typer.echo("Step 6: Applying ECS Terraform infrastructure...")
    public_url = None
    try:
        apply_terraform(aws_profile=aws_profile, project_name=safe_name)
        public_url = get_terraform_output(aws_profile=aws_profile)
        typer.echo(f"App deployed at: {public_url}")
    except Exception as e:
        typer.echo(f"Terraform apply failed for ECS: {e}")
        raise typer.Exit(code=1)

    typer.echo("Step 7: Provisioning EC2 for monitoring...")
    ec2_ip = None
    try:
        parsed = urlparse(public_url)
        host = parsed.hostname
        ecs_metrics_url = f"{host}:{port}"
        
        typer.echo(f"Prometheus will scrape: {ecs_metrics_url} ...")
        ec2_ip = provision_ec2(
            project_name=safe_name, key_name="monitor-ec2-key", region=region,
            ecs_metrics_url=ecs_metrics_url, aws_profile=aws_profile
        )
        if ec2_ip:
            typer.echo(f"EC2 provisioned and monitoring setup initiated.")
        else:
            typer.echo("EC2 provisioning failed.")
    except Exception as e:
        typer.echo(f"EC2 provisioning error: {e}")

    if public_url:
        typer.echo(f"\nDeployment complete for {project_name}!")
        typer.echo(f"Your application is live at: {public_url}")
        if ec2_ip:
            typer.echo(f"Access Prometheus at: http://{ec2_ip}:9090")
            typer.echo(f"Access Grafana at: http://{ec2_ip}:3000 (User: admin, Pass: admin)")
        
        deployment_record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "action": "run",
            "status": "success",
            "project_name": safe_name,
            "image_uri": image_uri,
            "public_app_url": public_url,
            "monitor_ec2_ip": ec2_ip if ec2_ip else "N/A",
        }
        history_manager.add_deployment_record(safe_name, deployment_record)
    else:
        raise typer.Exit(code=1)


# --- NEW COMMAND: A true software version rollback ---
@full_deploy_app.command("rollback")
def rollback_command_logic(
    project_name: str = typer.Argument(..., help="Name of the project to roll back (e.g., 'mayur-recipe-finder-react')."),
    region: str = typer.Option("ap-south-1", "--region", help="AWS region where the service is running."),
    aws_profile: str = typer.Option("mayur-sso", "--aws-profile", help="AWS CLI profile to use.")
):
    """
    Rolls back the ECS service to the previous stable software version.
    """
    typer.echo(f"Initiating software rollback for project: '{project_name}'...")
    
    timestamp = datetime.datetime.now().isoformat()
    entry = {
        "timestamp": timestamp, "project_name": project_name, "action": "rollback",
        "status": "failure", "details": ""
    }

    try:
        # We need the cluster and service names from Terraform's state
        cluster_name = get_terraform_output(aws_profile=aws_profile, output_name="ecs_cluster_name")
        service_name = get_terraform_output(aws_profile=aws_profile, output_name="ecs_service_name")

        if not cluster_name or not service_name:
            raise ValueError("Could not find 'ecs_cluster_name' or 'ecs_service_name' in Terraform outputs. Has the project been deployed?")

        from_rev, to_rev = rollback_ecs_service(cluster_name, service_name, aws_profile, region)
        
        entry["status"] = "success"
        entry["details"] = f"Rolled back from {from_rev.split('/')[-1]} to {to_rev.split('/')[-1]}"
        typer.echo(f"✅ {entry['details']}")

    except Exception as e:
        error_message = f"Rollback failed: {e}"
        typer.echo(f"❌ {error_message}")
        entry["details"] = error_message
    finally:
        history_manager.add_deployment_record(project_name, entry)


# --- Your existing 'destroy' command for infrastructure ---
@full_deploy_app.command("destroy")
def destroy_command_logic(
    project_name: str = typer.Argument(..., help="Name of the project to destroy (e.g., 'mayur-recipe-finder-react')."),
    target: str = typer.Option("all", "--target", "-t", help="Specific part to destroy: 'ecs-app', 'ec2-monitor', or 'all'."),
    aws_profile: str = typer.Option("mayur-sso", "--aws-profile", help="AWS CLI profile to use.")
):
    """
    Destroys AWS infrastructure provisioned for a project.
    """
    typer.echo(f"Initiating infrastructure destroy for project: '{project_name}' (Target: {target})...")
    
    timestamp = datetime.datetime.now().isoformat()
    entry = {
        "timestamp": timestamp, "project_name": project_name, "action": "destroy",
        "target": target, "status": "failure", "details": ""
    }

    try:
        if target in ["all", "ec2-monitor"]:
            destroy_terraform(
                aws_profile=aws_profile, project_name=project_name,
                terraform_dir=TF_EC2_DIR, tfstate_key_suffix="ec2-monitor.tfstate"
            )
        if target in ["all", "ecs-app"]:
            destroy_terraform(
                aws_profile=aws_profile, project_name=project_name,
                terraform_dir=TF_BASE_DIR, tfstate_key_suffix="ecs-app.tfstate"
            )
        entry["status"] = "success"
        entry["details"] = "All targeted resources destroyed successfully."
        typer.echo(f"✅ {entry['details']}")
    except Exception as e:
        entry["details"] = f"Error destroying infrastructure: {e}"
        typer.echo(f"❌ {entry['details']}")
    finally:
        history_manager.add_deployment_record(project_name, entry)


# --- The 'history' and 'cleanup-local' commands remain the same ---
@full_deploy_app.command("history")
def history_command_logic(
    project_name: str = typer.Argument(..., help="Name of the project to view history for."),
):
    typer.echo(f"Retrieving deployment history for project: '{project_name}'...")
    history_records = history_manager.load_history(project_name)
    if not history_records:
        typer.echo(f"No deployment history found for project '{project_name}'.")
        return
    typer.echo(json.dumps(history_records, indent=2))

@full_deploy_app.command("cleanup-local")
def cleanup_local_command_logic():
    workspace_path = Path("workspace")
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
        typer.echo("✅ Local workspace/ directory cleaned up successfully.")
    else:
        typer.echo("Local workspace/ directory not found.")
