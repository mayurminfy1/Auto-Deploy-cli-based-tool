# deploy_tool/terraform/variables.tf

variable "region" {
  description = "AWS region for deployment (for ECS)"
  type        = string
  default     = "ap-south-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication (for ECS)"
  type        = string
  default     = "mayur-sso"
}

variable "project_name" {
  description = "Project name (for ECS resources)"
  type        = string
}

variable "image_url" {
  description = "Docker image URL for the ECS task"
  type        = string
}

variable "container_port" {
  description = "Port the application container listens on"
  type        = number
}

variable "app_env_vars" {
  description = "JSON string of environment variables for the application container"
  type        = string
  default     = "{}" # Default to empty JSON object
}