# deploy_tool/core/rollback.py

import boto3
import typer
import time

def rollback_ecs_service(cluster_name: str, service_name: str, aws_profile: str, region: str) -> tuple[str, str]:
    """
    Rolls back an ECS service to its previous task definition revision.

    Returns:
        A tuple containing (current_task_definition_arn, new_task_definition_arn)
    """
    typer.echo(f"Connecting to AWS in region {region} with profile {aws_profile}...")
    try:
        session = boto3.Session(profile_name=aws_profile, region_name=region)
        ecs_client = session.client("ecs")
    except Exception as e:
        typer.echo(f"❌ Failed to create AWS session: {e}")
        raise

    # Step 1: Get the current task definition for the service
    typer.echo(f"Fetching current details for service '{service_name}' in cluster '{cluster_name}'...")
    try:
        service_desc = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
        if not service_desc.get("services"):
            raise ValueError(f"Service '{service_name}' not found.")
        
        current_task_arn = service_desc["services"][0]["taskDefinition"]
        task_family = current_task_arn.split('/')[-1].split(':')[0]
        typer.echo(f"Current active task definition: {current_task_arn.split('/')[-1]}")
    except Exception as e:
        typer.echo(f"❌ Failed to get current service details: {e}")
        raise

    # Step 2: Find the previous task definition revision
    typer.echo(f"Searching for previous task definition in family '{task_family}'...")
    try:
        task_definitions = ecs_client.list_task_definitions(
            familyPrefix=task_family,
            sort="DESC"
        )
        
        arns = task_definitions.get("taskDefinitionArns", [])
        if len(arns) < 2:
            typer.echo("⚠️ No previous task definition found to roll back to. This might be the first deployment.")
            raise ValueError("No previous revision available for rollback.")
        
        # The first ARN (index 0) is the current one, the second (index 1) is the previous one.
        previous_task_arn = arns[1]
        typer.echo(f"Found previous stable task definition: {previous_task_arn.split('/')[-1]}")
    except Exception as e:
        typer.echo(f"❌ Failed to find previous task definition: {e}")
        raise

    # Step 3: Update the service to use the previous task definition
    typer.echo("Initiating rollback by updating the ECS service...")
    try:
        ecs_client.update_service(
            cluster=cluster_name,
            service=service_name,
            taskDefinition=previous_task_arn,
            forceNewDeployment=True
        )
        typer.echo("✅ Service update command sent successfully.")
    except Exception as e:
        typer.echo(f"❌ Failed to update ECS service: {e}")
        raise

    # Step 4: Wait for the rollback deployment to become stable
    typer.echo("Waiting for the rollback deployment to stabilize...")
    waiter = ecs_client.get_waiter('services_stable')
    try:
        waiter.wait(
            cluster=cluster_name,
            services=[service_name],
            WaiterConfig={'Delay': 15, 'MaxAttempts': 40} # Wait up to 10 minutes
        )
        typer.echo("✅ Rollback complete and service is now stable.")
    except Exception as e:
        typer.echo(f"⚠️ Waiter failed, but rollback may still be in progress. Please check the AWS console. Error: {e}")
        # Don't re-raise, as the update command was sent.

    return (current_task_arn, previous_task_arn)
