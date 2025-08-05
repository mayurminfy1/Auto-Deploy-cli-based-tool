# deploy_tool/core/terraform.py
# Manages Terraform operations for both ECS application and EC2 monitoring infrastructure.

import subprocess
import json
import os
from pathlib import Path
import typer

# Define base directories for Terraform configurations
TF_BASE_DIR = Path(__file__).parent.parent / "terraform" # For ECS app Terraform
TF_EC2_DIR = Path(__file__).parent.parent / "terraform_ec2" # For EC2 monitor Terraform

# Function to write Terraform variables to tfvars.json
def write_tfvars(
    project_name: str,
    region: str,
    container_port: int,
    image_url: str,
    aws_profile: str = "mayur-sso",
    env_vars: dict = None
):
    if env_vars is None:
        env_vars = {}

    # Prepare variables for Terraform
    tfvars = {
        "project_name": project_name,
        "region": region,
        "container_port": container_port,
        "image_url": image_url,
        "aws_profile": aws_profile,
        "app_env_vars": json.dumps(env_vars)
    }

    tfvars_path = TF_BASE_DIR / "terraform.tfvars.json"

    typer.echo(f"Writing terraform.tfvars.json to: {tfvars_path}")
    try:
        tfvars_path.write_text(json.dumps(tfvars, indent=4))
        typer.echo("terraform.tfvars.json updated.")
    except Exception as e:
        typer.echo(f"Error writing terraform.tfvars.json: {e}")
        raise typer.Exit(code=1)

# Function to apply Terraform configuration
def apply_terraform(aws_profile: str = "mayur-sso", project_name: str = "default-project"):
    env = os.environ.copy()
    env["AWS_PROFILE"] = aws_profile

    # Initialize Terraform for the ECS app
    typer.echo(f"Initializing Terraform in {TF_BASE_DIR}...")
    try:
        subprocess.run([
            "terraform", "init",
            "-reconfigure", # Reconfigure backend settings
            f"-backend-config=bucket=mayur-devops-cli-terraform-states-ap-south-1",
            f"-backend-config=key={project_name}/ecs-app.tfstate", # Dynamic state key for ECS app
            f"-backend-config=region=ap-south-1",
            f"-backend-config=profile={aws_profile}",
            f"-backend-config=use_lockfile=true",
            f"-backend-config=encrypt=true"
        ], cwd=TF_BASE_DIR, check=True, env=env, capture_output=True, text=True)
        typer.echo("Terraform init complete.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Terraform init failed: {e.stderr}")
        raise typer.Exit(code=1)

    # Run Terraform plan
    typer.echo("Running Terraform plan...")
    try:
        subprocess.run(["terraform", "plan", "-var-file=terraform.tfvars.json"], cwd=TF_BASE_DIR, check=True, env=env, capture_output=True, text=True)
        typer.echo("Terraform plan complete.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Terraform plan failed: {e.stderr}")
        raise typer.Exit(code=1)

    # Apply Terraform changes
    typer.echo("Applying Terraform changes...")
    try:
        subprocess.run(["terraform", "apply", "-auto-approve", "-var-file=terraform.tfvars.json"], cwd=TF_BASE_DIR, check=True, env=env, capture_output=True, text=True)
        typer.echo("Terraform apply complete.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Terraform apply failed: {e.stderr}")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"An unexpected error occurred during Terraform apply: {e}")
        raise

# --- THIS FUNCTION HAS BEEN CORRECTED ---
# It now accepts an 'output_name' argument to be more flexible.
def get_terraform_output(aws_profile: str = "mayur-sso", output_name: str = "app_url") -> str:
    env = os.environ.copy()
    env["AWS_PROFILE"] = aws_profile

    typer.echo(f"Retrieving Terraform output '{output_name}' from {TF_BASE_DIR}...")
    try:
        # The output name is now a dynamic parameter
        output_value = subprocess.check_output(
            ["terraform", "output", "-raw", output_name],
            cwd=TF_BASE_DIR,
            env=env,
            text=True
        ).strip()

        if not output_value:
            typer.echo(f"Warning: '{output_name}' output not found or is empty in Terraform state.")
            # Return an empty string instead of exiting, so the caller can handle it
            return ""
        
        typer.echo(f"Retrieved {output_name}: {output_value}")
        return output_value
    except subprocess.CalledProcessError as e:
        typer.echo(f"Failed to get Terraform output '{output_name}': {e.stderr}")
        # Return an empty string on failure
        return ""
    except Exception as e:
        typer.echo(f"An unexpected error occurred while getting Terraform output '{output_name}': {e}")
        raise

# Function to destroy Terraform-managed infrastructure
def destroy_terraform(aws_profile: str, project_name: str, terraform_dir: Path, tfstate_key_suffix: str):
    env = os.environ.copy()
    env["AWS_PROFILE"] = aws_profile

    # Initialize Terraform for destroy operation
    typer.echo(f"Initializing Terraform for destroy in {terraform_dir}...")
    try:
        subprocess.run([
            "terraform", "init",
            "-reconfigure", # Reconfigure backend settings for destroy
            f"-backend-config=bucket=mayur-devops-cli-terraform-states-ap-south-1",
            f"-backend-config=key={project_name}/{tfstate_key_suffix}", # Use specific state key
            f"-backend-config=region=ap-south-1",
            f"-backend-config=profile={aws_profile}",
            f"-backend-config=use_lockfile=true",
            f"-backend-config=encrypt=true"
        ], cwd=terraform_dir, check=True, env=env, capture_output=True, text=True)
        typer.echo("Terraform init for destroy complete.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Terraform init for destroy failed: {e.stderr}")
        raise typer.Exit(code=1)

    # Run Terraform destroy
    typer.echo(f"Running Terraform destroy in {terraform_dir}...")
    try:
        subprocess.run(["terraform", "destroy", "-auto-approve"], cwd=terraform_dir, check=True, env=env, capture_output=True, text=True)
        typer.echo(f"Terraform destroy for {project_name}/{tfstate_key_suffix} complete.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Terraform destroy failed: {e.stderr}")
        typer.echo(f"STDOUT: {e.stdout}") # Print stdout for more context on destroy failure
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"An unexpected error occurred during Terraform destroy: {e}")
        raise
