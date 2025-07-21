import boto3
import subprocess
import base64
import typer
from botocore.config import Config

def get_region(aws_profile: str = "mayur-sso"):
    session = boto3.Session(profile_name=aws_profile)
    return session.region_name

def push_to_ecr(image_tag: str, repo_name: str, aws_profile: str = "mayur-sso") -> str:
    """
    Pushes a Docker image to AWS ECR and returns the full image URI.
    Ensures ECR repository is created if it doesn't exist.
    """
    region = get_region(aws_profile)

    try:
        session = boto3.Session(profile_name=aws_profile, region_name=region)
        client = session.client('ecr', config=Config(retries={'max_attempts': 10}))
    except Exception as e:
        typer.echo(f"Failed to create Boto3 ECR client with profile '{aws_profile}': {e}")
        raise typer.Exit(code=1)

    typer.echo(f"Authenticating Docker to ECR in region {region} with profile {aws_profile}...")
    try:
        # CRITICAL FIX: Changed back to get_authorization_token()
        auth_data = client.get_authorization_token()["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":")
        proxy_endpoint = auth_data["proxyEndpoint"]

        # Use subprocess to run the docker login command
        login_command = [
            "docker", "login",
            "--username", username,
            "--password", password,
            proxy_endpoint # Use the proxy_endpoint from auth_data
        ]
        subprocess.run(login_command, check=True, capture_output=True, text=True)
        typer.echo("Docker authentication to ECR successful.")
    except IndexError:
        typer.echo("No existing ECR repositories found. This might be fine if we are creating one.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Docker login failed: {e.stderr}")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Failed to get ECR authorization token or perform Docker login: {e}")
        raise

    typer.echo(f"Ensuring ECR repository '{repo_name}' exists...")
    try:
        client.describe_repositories(repositoryNames=[repo_name])
        typer.echo(f"ECR repository '{repo_name}' already exists.")
    except client.exceptions.RepositoryNotFoundException:
        try:
            client.create_repository(repositoryName=repo_name)
            typer.echo(f"ECR repository '{repo_name}' created.")
        except Exception as e:
            typer.echo(f"Failed to create ECR repository '{repo_name}': {e}")
            raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Failed to describe ECR repositories: {e}")
        raise typer.Exit(code=1)

    ecr_repository_uri = f"{client.describe_repositories(repositoryNames=[repo_name])['repositories'][0]['repositoryUri']}"
    full_image_uri = f"{ecr_repository_uri}:{image_tag.split(':')[-1]}"
    
    typer.echo(f"Tagging image {image_tag} to {full_image_uri}...")
    try:
        subprocess.run(["docker", "tag", image_tag, full_image_uri], check=True, capture_output=True, text=True)
        typer.echo("Docker image tagged.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Docker tag failed: {e.stderr}")
        raise typer.Exit(code=1)

    typer.echo(f"Pushing image to ECR: {full_image_uri}...")
    try:
        subprocess.run(["docker", "push", full_image_uri], check=True, capture_output=True, text=True)
        typer.echo(f"Docker image pushed to ECR successfully.")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Docker push failed: {e.stderr}")
        typer.echo(f"stdout: {e.stdout}")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"An unexpected error occurred during Docker push: {e}")
        raise

    return full_image_uri