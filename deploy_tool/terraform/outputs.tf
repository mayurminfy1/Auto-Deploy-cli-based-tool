# deploy_tool/terraform/outputs.tf

output "app_url" {
  description = "The DNS name of the Application Load Balancer (ALB) for the deployed ECS application."
  value       = "http://${aws_lb.mayur_lb.dns_name}" # Prefixed with http:// for convenience
}

output "ecs_cluster_name" {
  description = "The name of the ECS cluster created for the application."
  value       = aws_ecs_cluster.mayur_cluster.name
}

output "ecs_service_name" {
  description = "The name of the ECS service created for the application."
  value       = aws_ecs_service.mayur_service.name
}

output "vpc_id" {
  description = "The ID of the VPC created for the application."
  value       = module.mayur_vpc.vpc_id
}

output "public_subnet_ids" {
  description = "A list of public subnet IDs created for the application."
  value       = module.mayur_vpc.public_subnets
}

output "security_group_id" {
  description = "The ID of the security group for the application (allowing HTTP/3000)."
  value       = aws_security_group.mayur_sg.id
}