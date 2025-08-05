terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "mayur-devops-cli-terraform-states-ap-south-1"
    region       = "ap-south-1"
    profile      = "mayur-sso"
    key          = "ecs-app.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
}

module "mayur_vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.1.1"

  name               = "mayur-vpc-${var.project_name}"
  cidr               = "10.0.0.0/16"
  azs                = ["${var.region}a", "${var.region}b"]
  public_subnets     = ["10.0.1.0/24", "10.0.2.0/24"]
  enable_dns_hostnames = true
  enable_dns_support   = true
}

resource "aws_security_group" "mayur_sg" {
  name        = "mayur-sg-${var.project_name}"
  description = "Allow HTTP"
  vpc_id      = module.mayur_vpc.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = {
    Name = "${var.project_name}-sg"
  }
}

locals {
  project_name_raw     = substr(var.project_name, 0, 16)
  project_name_trimmed = regex("^([a-zA-Z0-9-]*[a-zA-Z0-9])", local.project_name_raw)[0]
}

resource "aws_lb" "mayur_lb" {
  name               = "mayur-lb-${local.project_name_trimmed}"
  load_balancer_type = "application"
  subnets            = module.mayur_vpc.public_subnets
  security_groups    = [aws_security_group.mayur_sg.id]
  tags = {
    Name = "${local.project_name_trimmed}-lb"
  }
}

resource "aws_lb_target_group" "mayur_tg" {
  name        = "mayur-tg-${local.project_name_trimmed}"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = module.mayur_vpc.vpc_id
  target_type = "ip"

  health_check {
    path                = "/index.html"
    protocol            = "HTTP"
    matcher             = "200-499"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  tags = {
    Name = "${local.project_name_trimmed}-tg"
  }
}

resource "aws_lb_listener" "mayur_listener" {
  load_balancer_arn = aws_lb.mayur_lb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mayur_tg.arn
  }
}

resource "aws_iam_role" "mayur_ecs_exec_role" {
  name = "mayur-ecs-exec-role-${var.project_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = {
    Name = "${var.project_name}-ecs-exec-role"
  }
}

resource "aws_iam_role_policy_attachment" "mayur_ecs_exec_policy" {
  role       = aws_iam_role.mayur_ecs_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_cluster" "mayur_cluster" {
  name = "mayur-cluster-${var.project_name}"
  tags = {
    Name = "${var.project_name}-cluster"
  }
}

resource "aws_ecs_task_definition" "mayur_task" {
  family                   = "mayur-task-${var.project_name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.mayur_ecs_exec_role.arn

  container_definitions = jsonencode([{
    name      = "mayur-container"
    image     = var.image_url
    portMappings = [{
      containerPort = var.container_port
      hostPort      = var.container_port
    }]
    environment = [
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

resource "aws_ecs_service" "mayur_service" {
  name            = "mayur-service-${var.project_name}"
  cluster         = aws_ecs_cluster.mayur_cluster.id
  launch_type     = "FARGATE"
  desired_count   = 1
  task_definition = aws_ecs_task_definition.mayur_task.arn

  network_configuration {
    subnets         = module.mayur_vpc.public_subnets
    security_groups = [aws_security_group.mayur_sg.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.mayur_tg.arn
    container_name   = "mayur-container"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.mayur_listener]
  tags = {
    Name = "${var.project_name}-service"
  }
}
