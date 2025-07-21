# deploy_tool/terraform_ec2/variables.tf

variable "region" {
  description = "AWS region for deployment"
  type        = string
  default     = "ap-south-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication"
  type        = string
  default     = "mayur-sso"
}

variable "project_name" {
  description = "Project name"
  type        = string
}

variable "instance_type" {
  description = "The EC2 instance type"
  type        = string
  default     = "t2.micro"
}

variable "ami_id" {
  description = "AMI ID for the EC2 instance"
  type        = string
  default     = "ami-0a1235697f4afa8a4" # Ensure this is the correct AMI for your region
}

variable "ecs_metrics_target" {
  description = "Host:port of the ECS app Prometheus should scrape"
  type        = string
}