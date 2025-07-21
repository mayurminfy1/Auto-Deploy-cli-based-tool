# deploy_tool/terraform_ec2/outputs.tf

output "ec2_public_ip" {
  description = "The public IP address of the monitoring EC2 instance."
  value       = aws_instance.monitor_ec2.public_ip
}

output "ec2_instance_id" {
  description = "The ID of the monitoring EC2 instance."
  value       = aws_instance.monitor_ec2.id
}

output "private_key_path" {
  description = "The local path to the generated private key file for SSH access to the EC2."
  value       = local_file.private_key.filename
}

output "generated_key_pair_name" {
  description = "The name of the generated EC2 key pair used for the monitoring EC2 instance."
  value       = aws_key_pair.generated.key_name
}