# deploy_tool/terraform/main.tf
# This file is your blueprint for deploying a containerized application (like your React app)
# onto AWS using Elastic Container Service (ECS) Fargate, complete with a load balancer and networking.

# === Terraform Configuration ===
# This block sets up the basic behavior for Terraform itself, like which AWS provider to use
# and where to store its 'state' file.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0" # We're specifying a compatible version of the AWS provider plugin
    }
  }

  # This is how Terraform remembers what it built. It stores a 'state' file in S3.
  # This makes it robust and shareable across teams or automation runs.
  backend "s3" {
    bucket       = "mayur-devops-cli-terraform-states-ap-south-1" # Your dedicated S3 bucket for Terraform state
    region       = "ap-south-1" # The region where your S3 state bucket lives
    profile      = "mayur-sso" # The AWS CLI profile to use for accessing this S3 state
    key          = "ecs-app.tfstate" # This is a dynamic name for *this specific app's* state file in S3
    use_lockfile = true # Prevents multiple people/processes from changing infrastructure at once
    encrypt      = true # Ensures your state file is encrypted in S3 (good for security)
  }
}

# === AWS Provider Configuration ===
# This block tells Terraform which AWS account and region to deploy your resources into.
provider "aws" {
  region  = var.region # The AWS region for our deployment (e.g., 'ap-south-1' from your inputs)
  profile = var.aws_profile # Your configured AWS CLI profile to authenticate with AWS
}

# === VPC (Virtual Private Cloud) Setup ===
# We're using a well-known Terraform module to easily create a secure and robust network environment.
# This saves us from defining every single network component manually.
module "mayur_vpc" {
  source  = "terraform-aws-modules/vpc/aws" # Using the official AWS VPC module
  version = "5.1.1" # Specifying a tested version of the module

  name                 = "mayur-vpc-${var.project_name}" # A unique name for our VPC, including your project name
  cidr                 = "10.0.0.0/16" # The main IP range for our private network
  azs                  = ["${var.region}a", "${var.region}b"] # Spreading our network across two Availability Zones for high availability
  public_subnets       = ["10.0.1.0/24", "10.0.2.0/24"] # IP ranges for subnets accessible from the internet
  enable_dns_hostnames = true # Allows EC2 instances to have DNS hostnames
  enable_dns_support   = true # Ensures DNS resolution works within our VPC
}

# === Security Group for the Application ===
# This acts as a virtual firewall for your application's containers, controlling what traffic can reach them.
resource "aws_security_group" "mayur_sg" {
  name        = "mayur-sg-${var.project_name}" # A clear name for our application's security group
  description = "Allow HTTP" # What this security group is for
  vpc_id      = module.mayur_vpc.vpc_id # Attaching it to the VPC we just created

  # Inbound Rule: Allow web traffic on port 80 (standard HTTP)
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Allowing access from anywhere on the internet
  }

  # Inbound Rule: Allow traffic on the application's specific port (e.g., 3000 for React/Next.js)
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Also allowing from anywhere (Load Balancer will use this)
  }

  # Outbound Rule: Allow the application to send traffic anywhere (e.g., to external APIs, ECR)
  egress {
    from_port   = 0 # All ports
    to_port     = 0 # All ports
    protocol    = "-1" # All protocols
    cidr_blocks = ["0.0.0.0/0"] # Allowing outgoing connections to anywhere
  }
  tags = {
    Name = "${var.project_name}-sg"
  }
}

# === Local Variables for Naming Consistency ===
# These are internal Terraform variables used to create clean, consistent names for AWS resources,
# especially considering AWS naming limits.
locals {
  project_name_raw     = substr(var.project_name, 0, 16) # Take first 16 chars of project name
  project_name_trimmed = regex("^([a-zA-Z0-9-]*[a-zA-Z0-9])", local.project_name_raw)[0] # Clean it up further
}

# === Application Load Balancer (ALB) ===
# This is the entry point for users to access your web application.
# It distributes incoming traffic across your application containers.
resource "aws_lb" "mayur_lb" {
  name               = "mayur-lb-${local.project_name_trimmed}" # A unique name for our load balancer
  load_balancer_type = "application" # We're using an Application Load Balancer (for HTTP/HTTPS)
  subnets            = module.mayur_vpc.public_subnets # Placing the ALB in public subnets so it's internet-facing
  security_groups    = [aws_security_group.mayur_sg.id] # Attaching our application's security group
  tags = {
    Name = "${local.project_name_trimmed}-lb"
  }
}

# === ALB Target Group ===
# The ALB needs to know *where* to send traffic. This target group defines a collection of
# destination containers (your ECS tasks) and checks their health.
resource "aws_lb_target_group" "mayur_tg" {
  name        = "mayur-tg-${local.project_name_trimmed}" # A unique name for the target group
  port        = 3000 # The port your application container listens on inside the container
  protocol    = "HTTP" # The protocol for traffic between ALB and containers
  vpc_id      = module.mayur_vpc.vpc_id # Associated with our VPC
  target_type = "ip" # ECS Fargate tasks are registered by their IP addresses

  # Health check configuration for the target group
  # The ALB uses this to determine if your application containers are running and responsive.
  health_check {
    path                = "/index.html" # The path the ALB will request to check health (e.g., your React app's main page)
    protocol            = "HTTP"
    matcher             = "200-499" # Consider HTTP 2xx, 3xx, or 4xx responses as healthy (common for SPAs that serve index.html for all paths)
    interval            = 30 # Check every 30 seconds
    timeout             = 10 # Wait up to 10 seconds for a response
    healthy_threshold   = 2 # 2 consecutive successful checks to mark as healthy
    unhealthy_threshold = 2 # 2 consecutive failed checks to mark as unhealthy
  }
  tags = {
    Name = "${local.project_name_trimmed}-tg"
  }
}

# === ALB Listener ===
# This tells the ALB to listen for incoming requests on a specific port and forward them to a target group.
resource "aws_lb_listener" "mayur_listener" {
  load_balancer_arn = aws_lb.mayur_lb.arn # Attaching to our ALB
  port              = 80 # Listen for standard HTTP traffic on port 80
  protocol          = "HTTP"

  # Default action: Forward all incoming traffic to our application's target group
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mayur_tg.arn
  }
}

# === IAM Role for ECS Task Execution ===
# ECS tasks (your containers) need permissions to interact with other AWS services,
# like pulling images from ECR or sending logs to CloudWatch. This role grants those permissions.
resource "aws_iam_role" "mayur_ecs_exec_role" {
  name = "mayur-ecs-exec-role-${var.project_name}" # A unique name for the ECS execution role

  # The 'trust policy' defines who can assume this role (in this case, ECS tasks)
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com" # Allows ECS tasks to use this role
      }
      Action = "sts:AssumeRole"
    }]
  })
  tags = {
    Name = "${var.project_name}-ecs-exec-role"
  }
}

# === Attach Managed Policy to ECS Execution Role ===
# This attaches a standard AWS-managed policy that grants common permissions
# required by ECS tasks (e.g., pulling images, logging).
resource "aws_iam_role_policy_attachment" "mayur_ecs_exec_policy" {
  role        = aws_iam_role.mayur_ecs_exec_role.name # Our custom ECS execution role
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" # The AWS-managed policy
}

# === ECS Cluster ===
# An ECS cluster is a logical grouping of tasks or services.
# It's where your containerized applications will run.
resource "aws_ecs_cluster" "mayur_cluster" {
  name = "mayur-cluster-${var.project_name}" # A unique name for our ECS cluster
  tags = {
    Name = "${var.project_name}-cluster"
  }
}

# === ECS Task Definition ===
# A task definition is like a blueprint for your application's container(s).
# It specifies the Docker image, CPU, memory, ports, and environment variables.
resource "aws_ecs_task_definition" "mayur_task" {
  family                   = "mayur-task-${var.project_name}" # A family name for versions of this task def
  requires_compatibilities = ["FARGATE"] # We're using AWS Fargate (serverless containers)
  network_mode             = "awsvpc" # Required for Fargate, uses VPC networking mode
  cpu                      = "256" # 0.25 vCPU allocated for the container
  memory                   = "512" # 512 MiB RAM allocated for the container
  execution_role_arn       = aws_iam_role.mayur_ecs_exec_role.arn # The IAM role for ECS to run tasks

  # Define the application container within the task definition
  container_definitions = jsonencode([{
    name        = "mayur-container" # Name of the container (used in load balancer setup)
    image       = var.image_url # The Docker image URL (e.g., from ECR)
    portMappings = [{
      containerPort = var.container_port # The port your app listens on inside the container
      hostPort      = var.container_port # For Fargate, hostPort matches containerPort
    }]
    environment = [ # Environment variables passed to your application
      for k, v in jsondecode(var.app_env_vars) : {
        name  = k
        value = v
      }
    ]
  }])
  tags = {
    Name = "${var.project_name}-task-def"
  }
}

# === ECS Service ===
# An ECS service is what runs and maintains a specified number of tasks (containers)
# from a task definition in a cluster, and integrates with the load balancer.
resource "aws_ecs_service" "mayur_service" {
  name            = "mayur-service-${var.project_name}" # Name for our ECS service
  cluster         = aws_ecs_cluster.mayur_cluster.id # Associate with our ECS cluster
  launch_type     = "FARGATE" # Use Fargate for serverless container execution
  desired_count   = 1 # We want to run 1 instance of our application
  task_definition = aws_ecs_task_definition.mayur_task.arn # Link to our application's task definition

  # Network configuration for the ECS service (where tasks will run)
  network_configuration {
    subnets        = module.mayur_vpc.public_subnets # Place tasks in public subnets (Fargate requires public IPs for internet access)
    security_groups = [aws_security_group.mayur_sg.id] # Apply our application's security group
    assign_public_ip = true # Fargate tasks in public subnets need public IPs to reach the internet (e.g., ECR)
  }

  # Integrate with the Load Balancer
  load_balancer {
    target_group_arn = aws_lb_target_group.mayur_tg.arn # Connect to our ALB target group
    container_name   = "mayur-container" # The name of the container in the task definition to route traffic to
    container_port   = var.container_port # The port on that container to route traffic to
  }

  # Ensures the ALB Listener is fully set up before the ECS Service tries to register with it.
  depends_on = [aws_lb_listener.mayur_listener]
  tags = {
    Name = "${var.project_name}-service"
  }
}