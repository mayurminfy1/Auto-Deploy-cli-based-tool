# deploy_tool/terraform_ec2/main.tf
# This file sets up a dedicated EC2 instance to run our monitoring tools:
# Prometheus (for collecting metrics), Grafana (for visualizing them),
# Node Exporter (for EC2 host metrics), and Blackbox Exporter (for app availability checks).

# === Terraform Configuration ===
# This part tells Terraform which AWS services it needs to interact with
# and where it should store its 'state' file, which is like a record of
# all the AWS resources it has created.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0" # We're specifying a compatible version of the AWS provider plugin
    }
  }

  # We're storing Terraform's state file in an S3 bucket. This is super important
  # for collaboration and reliability, as it keeps track of our infrastructure.
  backend "s3" {
    bucket       = "mayur-devops-cli-terraform-states-ap-south-1" # Your centralized S3 bucket for all Terraform states
    region       = "ap-south-1" # The AWS region where your state bucket is located
    profile      = "mayur-sso" # The AWS CLI profile used to access this S3 bucket
    key          = "ec2-monitor/default-project.tfstate" # A unique path for *this* monitoring EC2's state file
    use_lockfile = true # Prevents multiple people/processes from making conflicting changes at the same time
    encrypt      = true # Keeps your state file secure in S3
  }
}

# === SSH Key Pair Generation ===
# We need a secure way to log into our EC2 instance via SSH.
# Terraform will generate a new SSH key pair for us.
resource "tls_private_key" "generated" {
  algorithm = "RSA"
  rsa_bits  = 4096 # A strong encryption standard for our SSH key
}

# This takes the public part of our newly generated SSH key and registers it with AWS.
# This is what AWS uses to allow our private key to log into the EC2 instance.
resource "aws_key_pair" "generated" {
  key_name  = "${var.project_name}-key-${formatdate("YYYYMMDDhhmmss", timestamp())}" # A unique name for our key pair, including a timestamp
  public_key = tls_private_key.generated.public_key_openssh # The public key generated above
}

# This step saves the *private* part of our SSH key to your local machine.
# You'll need this .pem file to SSH into the EC2 instance later.
resource "local_file" "private_key" {
  content          = tls_private_key.generated.private_key_pem # The private key content
  filename         = "${path.module}/${aws_key_pair.generated.key_name}.pem" # Saves it in the same directory as this main.tf file
  file_permission = "0600" # Sets secure permissions (read-only for owner)
}

# === AWS Provider Configuration ===
# This block tells Terraform which AWS account and region to deploy these monitoring resources into.
provider "aws" {
  region  = var.region # The AWS region for our monitoring EC2 (from our inputs)
  profile = var.aws_profile # Your AWS CLI profile for authentication
}

# === VPC (Virtual Private Cloud) Setup for Monitoring ===
# We're creating a simple, dedicated network for our monitoring EC2 instance.
# This keeps our monitoring infrastructure isolated from our application's VPC.
resource "aws_vpc" "main_vpc" {
  cidr_block           = "10.0.0.0/16" # The main private IP range for this monitoring network
  enable_dns_support   = true # Essential for DNS resolution within the VPC
  enable_dns_hostnames = true # Allows EC2 instances to have DNS hostnames
  tags = {
    Name = "${var.project_name}-vpc" # A clear name for our monitoring VPC
  }
}

# === Public Subnet for Monitoring EC2 ===
# This is a segment of our VPC where our EC2 instance will live.
# It's 'public' because it needs to be accessible from the internet (for SSH, Grafana UI).
resource "aws_subnet" "main_subnet" {
  vpc_id                  = aws_vpc.main_vpc.id # Attaching it to our monitoring VPC
  cidr_block              = "10.0.1.0/24" # A smaller IP range within the VPC for this subnet
  map_public_ip_on_launch = true # This is key: it automatically assigns a public IP to our EC2 instance
  availability_zone       = "${var.region}a" # Placing it in a specific Availability Zone
  tags = {
    Name = "${var.project_name}-subnet" # A clear name for our monitoring subnet
  }
}

# === Internet Gateway ===
# This acts as a bridge between our VPC and the public internet, allowing traffic in and out.
resource "aws_internet_gateway" "main_igw" {
  vpc_id = aws_vpc.main_vpc.id # Attaching it to our monitoring VPC
  tags = {
    Name = "${var.project_name}-igw"
  }
}

# === Route Table for Public Internet Access ===
# This tells traffic within our subnet how to reach the internet.
resource "aws_route_table" "main_route_table" {
  vpc_id = aws_vpc.main_vpc.id # Associated with our monitoring VPC

  route {
    cidr_block = "0.0.0.0/0" # This means 'all internet traffic'
    gateway_id = aws_internet_gateway.main_igw.id # Directs all internet traffic through our Internet Gateway
  }

  tags = {
    Name = "${var.project_name}-route-table"
  }
}

# === Associate Route Table with Subnet ===
# This links our public subnet to the route table we just created,
# ensuring instances in this subnet can communicate with the internet.
resource "aws_route_table_association" "route_assoc" {
  subnet_id      = aws_subnet.main_subnet.id
  route_table_id = aws_route_table.main_route_table.id
}

# === Security Group for Monitoring EC2 ===
# This is a virtual firewall specifically for our monitoring EC2 instance.
# It controls which ports are open for incoming and outgoing traffic.
resource "aws_security_group" "monitor_sg" {
  name        = "${var.project_name}-monitor-sg" # A clear name for our monitoring security group
  description = "Allow SSH, Prometheus, Node Exporter, and Grafana" # What this security group allows
  vpc_id      = aws_vpc.main_vpc.id # Attaching it to our monitoring VPC

  # Inbound Rule: Allow SSH access (port 22) from anywhere
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Allowing access from any IP address (for management)
  }

  # Inbound Rule: Allow Prometheus UI access (port 9090) from anywhere
  ingress {
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # So you can access Prometheus in your browser
  }

  # Inbound Rule: Allow Node Exporter metrics access (port 9100) from anywhere
  ingress {
    from_port   = 9100
    to_port     = 9100
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # For direct access to Node Exporter metrics (if needed)
  }

  # Inbound Rule: Allow Grafana UI access (port 3000) from anywhere
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # So you can access Grafana in your browser
  }

  # Outbound Rule: Allow the EC2 instance to connect to anywhere on the internet
  egress {
    from_port   = 0 # All ports
    to_port     = 0 # All ports
    protocol    = "-1" # All protocols
    cidr_blocks = ["0.0.0.0/0"] # Allowing outgoing connections to anywhere (e.g., download software)
  }
  tags = {
    Name = "${var.project_name}-monitor-sg"
  }
}

# === Monitoring EC2 Instance ===
# This is the actual virtual server where our monitoring tools will run.
resource "aws_instance" "monitor_ec2" {
  ami                         = var.ami_id # The Amazon Machine Image (OS) to use for the EC2 (from variables)
  instance_type               = var.instance_type # The size of the EC2 instance (e.g., t2.micro)
  subnet_id                   = aws_subnet.main_subnet.id # Placing it in our public monitoring subnet
  vpc_security_group_ids      = [aws_security_group.monitor_sg.id] # Attaching our monitoring security group
  associate_public_ip_address = true # Ensures it gets a public IP address
  key_name                    = aws_key_pair.generated.key_name # Attaching the SSH key pair we generated

  # User data script: This script runs automatically when the EC2 instance first starts up.
  # We're using a template file to pass dynamic values (like the app's URL for Prometheus).
  user_data = templatefile(
    "${path.module}/monitor_ec2_bootstrap.sh.tpl", # Path to our bootstrap script template
    {
      ecs_metrics_target = var.ecs_metrics_target # Passing the app's URL to the script
    }
  )

  tags = {
    Name = "${var.project_name}-monitor-ec2" # A clear name for our monitoring EC2 instance
  }
}