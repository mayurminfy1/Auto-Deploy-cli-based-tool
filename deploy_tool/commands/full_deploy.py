# deploy_tool/commands/full_deploy.py

import typer
import os
import datetime
import json
import time
from pathlib import Path
from git import Repo
from urllib.parse import urlparse
import shutil # --- NEW IMPORT: for shutil.rmtree ---

from deploy_tool.core.init_logic import detect_framework
from deploy_tool.core.docker import build_docker_image
from deploy_tool.core.ecr import push_to_ecr
from deploy_tool.core.terraform import apply_terraform, get_terraform_output, write_tfvars, destroy_terraform, TF_BASE_DIR, TF_EC2_DIR
from deploy_tool.core.ec2_provision import provision_ec2
from deploy_tool.core import history_manager

# Define a Typer app specifically for this command group
full_deploy_app = typer.Typer(help="Commands for the full deployment pipeline (run, rollback, history, cleanup-local).") # Updated help text


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
    Includes automated retries and rollback on Terraform failures.
    """
    typer.echo(f"Starting full deployment for {repo_url}...")

    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    workspace_path = Path("workspace")
    workspace_path.mkdir(exist_ok=True) # Ensure workspace directory exists at start of run
    clone_path = workspace_path / repo_name

    # Step 1: Cloning repository
    typer.echo("Step 1: Cloning repository...")
    if clone_path.exists():
        typer.echo(f"Repo already exists at {clone_path}. Pulling latest changes...")
        try:
            repo = Repo(clone_path)
            repo.remotes.origin.pull()
        except Exception as e:
            typer.echo(f"Failed to pull latest changes: {e}. Attempting full clone instead.")
            try:
                shutil.rmtree(clone_path, ignore_errors=True)
                Repo.clone_from(repo_url, clone_path)
                typer.echo(f"Cloned into {clone_path}.")
            except Exception as clone_e:
                typer.echo(f"Failed to clone repository {repo_url}: {clone_e}")
                raise typer.Exit(code=1)
    else:
        typer.echo(f"Cloning into {clone_path}...")
        try:
            Repo.clone_from(repo_url, clone_path)
        except Exception as e:
            typer.echo(f"Failed to clone repository {repo_url}: {e}")
            raise typer.Exit(code=1)

    # Step 2: Detect framework
    typer.echo("Step 2: Detecting framework...")
    try:
        framework, project_path_for_build, project_name = detect_framework(clone_path)
        typer.echo(f"Detected framework: {framework}")
    except Exception as e:
        typer.echo(f"Framework detection failed: {e}")
        raise typer.Exit(code=1)

    # Step 3: Build Docker image (with retries)
    typer.echo("Step 3: Building Docker image...")
    safe_name = project_name.lower().replace("_", "-")
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    image_tag = f"{safe_name}:{timestamp}"

    for attempt in range(max_retries):
        try:
            build_docker_image(
                project_path=project_path_for_build,
                framework=framework,
                image_tag=image_tag
            )
            typer.echo(f"Docker image built: {image_tag}")
            break
        except Exception as e:
            typer.echo(f"Docker build failed on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                typer.echo(f"Retrying in {retry_delay_seconds} seconds...")
                time.sleep(retry_delay_seconds)
            else:
                typer.echo("Maximum Docker build retries reached. Aborting deployment.")
                raise typer.Exit(code=1)

    # Step 4: Push to ECR
    typer.echo("Step 4: Pushing image to ECR...")
    try:
        image_uri = push_to_ecr(image_tag=image_tag, repo_name=safe_name, aws_profile=aws_profile)
        typer.echo(f"Image pushed to ECR: {image_uri}")
    except Exception as e:
        typer.echo(f"Failed to push to ECR: {e}")
        raise typer.Exit(code=1)

    # Step 5: Write terraform.tfvars.json (for ECS deployment)
    typer.echo("Step 5: Writing terraform.tfvars.json for ECS deployment...")
    try:
        write_tfvars(
            project_name=safe_name,
            region=region,
            container_port=port,
            image_url=image_uri,
            aws_profile=aws_profile,
            env_vars={}
        )
        typer.echo("terraform.tfvars.json written for ECS.")
    except Exception as e:
        typer.echo(f"Failed to write tfvars for ECS: {e}")
        raise typer.Exit(code=1)

    # Step 6: Apply ECS Terraform (with rollback on failure)
    typer.echo("Step 6: Applying ECS Terraform infrastructure...")
    public_url = None
    for attempt in range(max_retries):
        try:
            apply_terraform(aws_profile=aws_profile, project_name=safe_name)
            public_url = get_terraform_output(aws_profile=aws_profile)
            typer.echo(f"App deployed at: {public_url}")
            break
        except Exception as e:
            typer.echo(f"Terraform apply failed for ECS on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                typer.echo(f"Attempting rollback (destroy) of ECS app infrastructure before retrying...")
                try:
                    destroy_terraform(
                        aws_profile=aws_profile,
                        project_name=safe_name,
                        terraform_dir=TF_BASE_DIR,
                        tfstate_key_suffix="ecs-app.tfstate"
                    )
                    typer.echo("ECS app infrastructure destroyed for retry.")
                except Exception as destroy_e:
                    typer.echo(f"Warning: Failed to destroy ECS app infrastructure during rollback: {destroy_e}. Manual cleanup might be needed.")
                typer.echo(f"Retrying ECS Terraform apply in {retry_delay_seconds} seconds...")
                time.sleep(retry_delay_seconds)
            else:
                typer.echo("Maximum ECS Terraform apply retries reached. Aborting deployment.")
                raise typer.Exit(code=1)


    # Step 7: Provision EC2 for Monitoring (with rollback on failure)
    typer.echo("Step 7: Provisioning EC2 for monitoring...")
    ec2_ip = None
    for attempt in range(max_retries):
        try:
            parsed = urlparse(public_url)
            host = parsed.hostname
            metrics_port = port
            ecs_metrics_url = f"{host}:{metrics_port}"
            
            typer.echo(f"Prometheus will scrape: {ecs_metrics_url} ...")
            ec2_ip = provision_ec2(
                project_name=safe_name,
                key_name="monitor-ec2-key",
                region=region,
                ecs_metrics_url=ecs_metrics_url,
                aws_profile=aws_profile
            )
            if ec2_ip:
                typer.echo(f"EC2 provisioned and monitoring setup initiated.")
            else:
                typer.echo("EC2 provisioning failed.")
            break
        except Exception as e:
            typer.echo(f"EC2 provisioning failed on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                typer.echo(f"Attempting rollback (destroy) of EC2 monitoring infrastructure before retrying...")
                try:
                    destroy_terraform(
                        aws_profile=aws_profile,
                        project_name=safe_name,
                        terraform_dir=TF_EC2_DIR,
                        tfstate_key_suffix="ec2-monitor.tfstate"
                    )
                    typer.echo("EC2 monitoring infrastructure destroyed for retry.")
                except Exception as destroy_e:
                    typer.echo(f"Warning: Failed to destroy EC2 monitoring infrastructure during rollback: {destroy_e}. Manual cleanup might be needed.")
                typer.echo(f"Retrying EC2 provisioning in {retry_delay_seconds} seconds...")
                time.sleep(retry_delay_seconds)
            else:
                typer.echo("Maximum EC2 provisioning retries reached. Aborting deployment.")
                raise typer.Exit(code=1)

    # --- Final Output ---
    if public_url:
        typer.echo(f"\nDeployment complete for {project_name}!")
        typer.echo(f"Your application is live at: {public_url}")
        if ec2_ip:
            typer.echo(f"Access Prometheus at: http://{ec2_ip}:9090")
            typer.echo(f"Access Node Exporter at: http://{ec2_ip}:9100")
            typer.echo(f"Access Grafana at: http://{ec2_ip}:3000 (User: admin, Pass: admin - change on first login)")
            typer.echo("Note: It may take a minute or two for services to start after provisioning.")
        else:
            typer.echo("Monitoring setup might have failed or is not accessible.")
        
        # Save deployment record to history
        deployment_record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "project_name": safe_name,
            "repo_url": repo_url,
            "image_tag": image_tag,
            "image_uri": image_uri,
            "public_app_url": public_url,
            "monitor_ec2_ip": ec2_ip if ec2_ip else "N/A",
            "region": region,
            "aws_profile": aws_profile
        }
        history_manager.add_deployment_record(safe_name, deployment_record)


    else:
        typer.echo("Deployment failed. No public URL available.")
        raise typer.Exit(code=1)

# --- Define the 'rollback' command for 'full_deploy_app' (manual cleanup) ---
@full_deploy_app.command("rollback")
def rollback_command_logic(
    project_name: str = typer.Argument(..., help="Name of the project to roll back/destroy (e.g., 'mayur-recipe-finder-react')."),
    rollback_target: str = typer.Option("all", "--target", "-t",
        help="Specific part to roll back/destroy: 'ecs-app', 'ec2-monitor', or 'all'. "
             "Use 'all' to destroy everything related to the project."
    ),
    aws_profile: str = typer.Option("mayur-sso", "--aws-profile", help="AWS CLI profile to use.")
):
    """
    Rolls back/destroys AWS infrastructure provisioned for a project.
    """
    typer.echo(f"Initiating rollback for project: '{project_name}' (Target: {rollback_target})...")

    # Destroy EC2 Monitoring infrastructure
    if rollback_target in ["all", "ec2-monitor"]:
        typer.echo("Destroying EC2 Monitoring infrastructure...")
        try:
            destroy_terraform(
                aws_profile=aws_profile,
                project_name=project_name,
                terraform_dir=TF_EC2_DIR,
                tfstate_key_suffix="ec2-monitor.tfstate"
            )
            typer.echo("EC2 Monitoring infrastructure destroyed.")
        except Exception as e:
            typer.echo(f"Error destroying EC2 Monitoring infrastructure: {e}")
            typer.echo("Please check the AWS console for remaining resources and delete manually if necessary.")
            raise typer.Exit(code=1)
    
    # Destroy ECS Application infrastructure
    if rollback_target in ["all", "ecs-app"]:
        typer.echo("Destroying ECS Application infrastructure...")
        try:
            destroy_terraform(
                aws_profile=aws_profile,
                project_name=project_name,
                terraform_dir=TF_BASE_DIR,
                tfstate_key_suffix="ecs-app.tfstate"
            )
            typer.echo("ECS Application infrastructure destroyed.")
        except Exception as e:
            typer.echo(f"Error destroying ECS Application infrastructure: {e}")
            typer.echo("Please check the AWS console for remaining resources and delete manually if necessary.")
            raise typer.Exit(code=1)

    typer.echo(f"Rollback process complete for '{project_name}'.")

# --- Define the 'history' command ---
@full_deploy_app.command("history")
def history_command_logic(
    project_name: str = typer.Argument(..., help="Name of the project to view history for."),
    aws_profile: str = typer.Option("mayur-sso", "--aws-profile", help="AWS CLI profile used for deployment history.")
):
    """
    Displays the deployment history for a specific project.
    """
    typer.echo(f"Retrieving deployment history for project: '{project_name}'...")
    
    history_records = history_manager.load_history(project_name)

    if not history_records:
        typer.echo(f"No deployment history found for project '{project_name}'.")
        return

    typer.echo(f"\n--- Deployment History for '{project_name}' ({len(history_records)} records) ---")
    for i, record in enumerate(history_records):
        typer.echo(f"\nRecord {i+1}:")
        typer.echo(f"  Timestamp      : {record.get('timestamp', 'N/A')}")
        typer.echo(f"  Image Tag      : {record.get('image_tag', 'N/A')}")
        typer.echo(f"  App URL        : {record.get('public_app_url', 'N/A')}")
        typer.echo(f"  Monitor EC2 IP : {record.get('monitor_ec2_ip', 'N/A')}")
        typer.echo(f"  Region         : {record.get('region', 'N/A')}")
        typer.echo(f"  AWS Profile    : {record.get('aws_profile', 'N/A')}")
        typer.echo(f"  Image URI      : {record.get('image_uri', 'N/A')}")
    typer.echo("\n--- End of History ---")

# --- NEW COMMAND: cleanup-local ---
@full_deploy_app.command("cleanup-local")
def cleanup_local_command_logic():
    """
    Removes the local 'workspace/' directory to clean up cloned repos and temp files.
    """
    workspace_path = Path("workspace")
    if workspace_path.exists() and workspace_path.is_dir():
        try:
            shutil.rmtree(workspace_path)
            typer.echo("Local workspace/ directory cleaned up successfully.")
        except Exception as e:
            typer.echo(f"Error cleaning up local workspace/: {e}")
            raise typer.Exit(code=1)
    else:
        typer.echo("Local workspace/ directory not found or already cleaned.")