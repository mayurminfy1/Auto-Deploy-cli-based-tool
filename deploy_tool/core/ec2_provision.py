# deploy_tool/core/ec2_provision.py
# Manages provisioning and configuring a monitoring EC2 instance with Prometheus, Grafana, and Exporters.

import paramiko
import time
import os
import subprocess
import json
import shutil
import tempfile
from pathlib import Path
import typer
import datetime

# Main function to provision and configure the EC2 instance
def provision_ec2(project_name, key_name, region, ecs_metrics_url, aws_profile="mayur-sso"):
    # Define path to EC2 Terraform configs
    ec2_tf_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "terraform_ec2"))

    # Create and copy Terraform files to a temporary directory
    temp_dir = tempfile.mkdtemp(prefix=f"ec2_tf_{project_name}_")
    working_dir = temp_dir
    shutil.copytree(ec2_tf_dir, working_dir, dirs_exist_ok=True)

    # Prepare and write Terraform variables (tfvars.json)
    tfvars = {
        "project_name": project_name,
        "region": region,
        "aws_profile": aws_profile,
        "ecs_metrics_target": ecs_metrics_url
    }
    tfvars_path = os.path.join(working_dir, "terraform.tfvars.json")
    typer.echo(f"Writing tfvars to: {tfvars_path}")
    with open(tfvars_path, "w") as f:
        json.dump(tfvars, f)

    # Set AWS profile for subprocess calls
    env = os.environ.copy()
    env["AWS_PROFILE"] = aws_profile

    public_ip = None
    absolute_private_key_path = None
    try:
        # Initialize Terraform (download providers, configure backend)
        typer.echo(f"Initializing Terraform in {working_dir}...")
        subprocess.run([
            "terraform", "init",
            "-reconfigure", # Reconfigure backend settings
            f"-backend-config=bucket=mayur-devops-cli-terraform-states-ap-south-1",
            f"-backend-config=key={project_name}/ec2-monitor.tfstate",
            f"-backend-config=region=ap-south-1",
            f"-backend-config=profile={aws_profile}",
            f"-backend-config=use_lockfile=true",
            f"-backend-config=encrypt=true"
        ], cwd=working_dir, check=True, env=env, capture_output=True, text=True)
        typer.echo("Terraform init complete.")

        # Apply Terraform configuration (create EC2 instance, VPC, etc.)
        typer.echo(f"Applying Terraform configuration in {working_dir}...")
        subprocess.run(["terraform", "apply", "-auto-approve", f"-var-file={tfvars_path}"],
                       cwd=working_dir, check=True, env=env, capture_output=True, text=True)
        typer.echo("Terraform apply complete.")

        # Retrieve outputs from Terraform state (EC2 IP, key path)
        output = subprocess.check_output(["terraform", "output", "-json"], cwd=working_dir, env=env, text=True)
        parsed_output = json.loads(output)

        public_ip = parsed_output.get("ec2_public_ip", {}).get("value")
        local_private_key_path_relative = parsed_output.get("private_key_path", {}).get("value")

        # --- Establish SSH Connection to EC2 Instance ---
        if public_ip and local_private_key_path_relative:
            # Construct absolute path to the generated private key
            absolute_private_key_path = os.path.join(working_dir, local_private_key_path_relative)
            typer.echo(f"EC2 provisioned. Public IP: {public_ip}, Private Key Path: {absolute_private_key_path}")

            # Initialize Paramiko SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            typer.echo(f"Attempting SSH connection to {public_ip} using key...")
            max_ssh_retries = 10
            retry_delay = 10
            # Retry loop for SSH connection (EC2 might not be ready immediately)
            for i in range(max_ssh_retries):
                try:
                    ssh_client.connect(public_ip, username="ec2-user", key_filename=absolute_private_key_path, timeout=60)
                    typer.echo("SSH connection successful.")
                    break
                except paramiko.AuthenticationException:
                    typer.echo(f"Authentication failed for ec2-user@{public_ip}. Check key permissions and user.")
                    raise
                except paramiko.SSHException as e:
                    if "not a valid RSA private key file" in str(e):
                        typer.echo(f"Error: Private key invalid/incorrect permissions.")
                        raise
                    typer.echo(f"SSH error on attempt {i+1}: {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                except Exception as e:
                    typer.echo(f"Connection attempt {i+1} failed: {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
            else:
                typer.echo(f"Failed to establish SSH connection after {max_ssh_retries} retries.")
                raise Exception("SSH connection failed.")

            # --- Configure Monitoring Tools via SSH ---
            typer.echo("Setting up Prometheus and Node Exporter on EC2...")
            _install_and_configure_prometheus_node_exporter(ssh_client, ecs_metrics_url)
            typer.echo("Prometheus and Node Exporter setup complete.")

            typer.echo("Setting up Grafana on EC2...")
            _install_and_configure_grafana(ssh_client)
            typer.echo("Grafana setup complete.")

            ssh_client.close() # Close SSH connection

        else:
            typer.echo("EC2 public IP or private key path not found in Terraform output. Provisioning failed.")
            public_ip = None

    except subprocess.CalledProcessError as e:
        typer.echo(f"Terraform command failed: {e.stderr}")
        raise typer.Exit(code=1)
    except json.JSONDecodeError as e:
        typer.echo(f"Failed to parse Terraform output JSON: {e}")
        public_ip = None
    except Exception as e:
        typer.echo(f"An unexpected error occurred during EC2 provisioning: {e}")
        public_ip = None
    finally:
        typer.echo(f"Temporary Terraform directory (containing .pem key) NOT deleted. It is located at: {temp_dir}")
        pass

    return public_ip


# Helper function to execute remote shell commands via SSH
def _execute_remote_commands(ssh_client: paramiko.SSHClient, commands: list):
    for cmd in commands:
        typer.echo(f"Running: {cmd.splitlines()[0]}...")
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        stdout_output = stdout.read().decode().strip()
        stderr_output = stderr.read().decode().strip()
        exit_status = stdout.channel.recv_exit_status()

        if stdout_output:
            typer.echo(f"STDOUT: {stdout_output}")
        if stderr_output:
            typer.echo(f"STDERR: {stderr_output}")
        if exit_status != 0:
            typer.echo(f"Command '{cmd.splitlines()[0]}' failed with exit status {exit_status}.")
            raise Exception(f"Remote command failed: {cmd.splitlines()[0]}")
        time.sleep(0.5)


# Helper function to SFTP file content and move remotely
def _sftp_file_and_move(ssh_client: paramiko.SSHClient, local_content: str, remote_temp_path: str, remote_final_path: str):
    sftp_client = ssh_client.open_sftp()
    try:
        with sftp_client.file(remote_temp_path, 'w') as f:
            f.write(local_content)
        typer.echo(f"File uploaded to temporary location: {remote_temp_path}")
    finally:
        sftp_client.close()

    stdin, stdout, stderr = ssh_client.exec_command(f"sudo mv {remote_temp_path} {remote_final_path}")
    exit_status = stdout.channel.recv_exit_status()
    stdout_output = stdout.read().decode().strip()
    stderr_output = stderr.read().decode().strip()

    if exit_status != 0:
        typer.echo(f"ERROR moving file to {remote_final_path}: {stderr_output}")
        raise Exception(f"Failed to move file: {stderr_output}")
    else:
        typer.echo(f"File moved successfully to {remote_final_path}.")


# Installs and configures Prometheus, Node Exporter, and Blackbox Exporter
def _install_and_configure_prometheus_node_exporter(ssh_client: paramiko.SSHClient, ecs_metrics_url: str):
    typer.echo("Installing Prometheus, Node Exporter, and Blackbox Exporter...")
    commands = [
        "sudo yum update -y",
        "sudo yum install -y wget",
        "wget https://github.com/prometheus/prometheus/releases/download/v2.51.2/prometheus-2.51.2.linux-amd64.tar.gz",
        "tar -xvf prometheus-2.51.2.linux-amd64.tar.gz",
        "sudo mv prometheus-2.51.2.linux-amd64 /usr/local/prometheus",
        "sudo useradd --no-create-home --shell /bin/false prometheus",
        "sudo cp /usr/local/prometheus/prometheus /usr/local/bin/",
        "sudo cp /usr/local/prometheus/promtool /usr/local/bin/",
        "wget https://github.com/prometheus/node_exporter/releases/download/v1.8.1/node_exporter-1.8.1.linux-amd64.tar.gz",
        "tar -xvf node_exporter-1.8.1.linux-amd64.tar.gz",
        "sudo mv node_exporter-1.8.1.linux-amd64/node_exporter /usr/local/bin/",
        "sudo useradd --no-create-home --shell /bin/false node_exporter",
        "sudo mkdir -p /var/lib/prometheus/data",
        "sudo chown -R prometheus:prometheus /var/lib/prometheus",
        "sudo chown -R prometheus:prometheus /usr/local/prometheus",
        "sudo chown node_exporter:node_exporter /usr/local/bin/node_exporter",
        """sudo bash -c 'cat <<EOF > /etc/systemd/system/prometheus.service
[Unit]
Description=Prometheus
Wants=network-online.target
After=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus --config.file=/usr/local/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus/data

[Install]
WantedBy=multi-user.target
EOF'""",
        """sudo bash -c 'cat <<EOF > /etc/systemd/system/node_exporter.service
[Unit]
Description=Node Exporter
Wants=network-online.target
After=network-online.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=multi-user.target
EOF'""",
        "wget https://github.com/prometheus/blackbox_exporter/releases/download/v0.24.0/blackbox_exporter-0.24.0.linux-amd64.tar.gz",
        "tar xvf blackbox_exporter-0.24.0.linux-amd64.tar.gz",
        "sudo mv blackbox_exporter-0.24.0.linux-amd64/blackbox_exporter /usr/local/bin/",
        "sudo useradd --no-create-home --shell /bin/false blackbox",
        "sudo rm -f prometheus-*.tar.gz node_exporter-*.tar.gz blackbox_exporter-*.tar.gz"
    ]
    _execute_remote_commands(ssh_client, commands)

    # Configure Blackbox Exporter systemd service file
    typer.echo("Configuring Blackbox Exporter systemd service...")
    blackbox_service_content = """
[Unit]
Description=Prometheus Blackbox Exporter
Wants=network-online.target
After=network-online.target

[Service]
User=blackbox
Group=blackbox
Type=simple
ExecStart=/usr/local/bin/blackbox_exporter --config.file=/usr/local/prometheus/blackbox.yml --web.listen-address=0.0.0.0:9115
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
    _sftp_file_and_move(ssh_client, blackbox_service_content, "/tmp/blackbox_exporter.service", "/etc/systemd/system/blackbox_exporter.service")

    # Reload systemd daemon and start/enable services
    _execute_remote_commands(ssh_client, [
        "sudo systemctl daemon-reload",
        "sudo systemctl enable prometheus",
        "sudo systemctl enable node_exporter",
        "sudo systemctl enable blackbox_exporter",
        "sudo systemctl start prometheus",
        "sudo systemctl start node_exporter",
        "sudo systemctl start blackbox_exporter",
    ])
    typer.echo("Prometheus, Node Exporter, and Blackbox Exporter services enabled and started.")

    # Configure Blackbox Exporter's modules (blackbox.yml)
    blackbox_cfg = """
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_http_versions: ["HTTP/1.1","HTTP/2"]
      method: GET
      valid_status_codes: []
"""
    _sftp_file_and_move(ssh_client, blackbox_cfg, "/tmp/blackbox.yml", "/usr/local/prometheus/blackbox.yml")
    _execute_remote_commands(ssh_client, ["sudo systemctl restart blackbox_exporter"])
    typer.echo("Blackbox Exporter configured and restarted to load new config.")

    # Configure Prometheus's scrape jobs (prometheus.yml)
    prometheus_config = f"""
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'node_exporter'
    static_configs:
      - targets: ['localhost:9100']

  - job_name: 'your_ecs_app_http_probe'
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets: ['{ecs_metrics_url}']
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 'localhost:9115'
"""
    _sftp_file_and_move(ssh_client, prometheus_config, "/tmp/prometheus.yml", "/usr/local/prometheus/prometheus.yml")
    _execute_remote_commands(ssh_client, ["sudo systemctl restart prometheus"])
    typer.echo("Prometheus restarted.")


# Installs and configures Grafana
def _install_and_configure_grafana(ssh_client: paramiko.SSHClient):
    typer.echo("Downloading and installing Grafana...")
    grafana_commands = [
        "sudo yum install -y https://dl.grafana.com/oss/release/grafana-11.0.0-1.x86_64.rpm",
        "sudo systemctl daemon-reload",
        "sudo systemctl enable grafana-server",
        "sudo systemctl start grafana-server"
    ]
    _execute_remote_commands(ssh_client, grafana_commands)

    # Configure Grafana data source (Prometheus)
    typer.echo("Configuring Grafana data source...")
    grafana_datasource_config = """
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://localhost:9090
    access: proxy
    isDefault: true
    version: 1
    editable: true
"""
    _sftp_file_and_move(
        ssh_client,
        grafana_datasource_config,
        "/tmp/prometheus-datasource.yaml",
        "/etc/grafana/provisioning/datasources/prometheus-datasource.yaml"
    )

    # Configure Grafana dashboard provisioning (telling Grafana where to find dashboards)
    typer.echo("Configuring Grafana dashboard provisioning...")
    grafana_dashboard_provisioning_config = """
apiVersion: 1
providers:
  - name: 'node_exporter_dashboard'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
"""
    _sftp_file_and_move(
        ssh_client,
        grafana_dashboard_provisioning_config,
        "/tmp/node-exporter-prov.yaml",
        "/etc/grafana/provisioning/dashboards/node-exporter-dashboard-provisioning.yaml"
    )

    # Upload Node Exporter dashboard JSON content
    typer.echo("Uploading Node Exporter dashboard JSON (basic example)...")
    node_exporter_dashboard_json = """
{
  "annotations": {
    "list": []
  },
  "editable": true,
  "gnetId": null,
  "graphTooltip": 1,
  "id": null,
  "links": [],
  "panels": [
    {
      "datasource": "Prometheus",
      "fieldConfig": {
        "defaults": {
          "custom": {},
          "max": null,
          "min": 0,
          "unit": "percent"
        },
        "overrides": []
      },
      "gridPos": {
        "h": 9,
        "w": 12,
        "x": 0,
        "y": 0
      },
      "id": 2,
      "options": {
        "reduceOptions": {
          "calcs": [
            "lastNotNull"
          ],
          "fields": "/.*/",
          "values": false
        },
        "showThresholdLabels": false,
        "showThresholdMarkers": true
      },
      "pluginVizId": "gauge",
      "targets": [
        {
          "expr": "100 - (avg by (instance) (rate(node_cpu_seconds_total{mode='idle'}[$__interval])) * 100)",
          "refId": "A"
        }
      ],
      "title": "CPU Usage",
      "type": "gauge"
    },
    {
      "datasource": "Prometheus",
      "fieldConfig": {
        "defaults": {
          "custom": {},
          "max": null,
          "min": 0,
          "unit": "bytes"
        },
        "overrides": []
      },
      "gridPos": {
        "h": 9,
        "w": 12,
        "x": 12,
        "y": 0
      },
      "id": 4,
      "options": {
        "legend": {
          "calcs": [],
          "displayMode": "list",
          "placement": "right",
          "showSeriesCount": false
        }
      },
      "pluginVizId": "graph",
      "targets": [
        {
          "expr": "node_memory_MemTotal_bytes - node_memory_MemFree_bytes - node_memory_Buffers_bytes - node_memory_Cached_bytes",
          "refId": "A",
          "legendFormat": "Used"
        },
        {
          "expr": "node_memory_MemTotal_bytes",
          "refId": "B",
          "legendFormat": "Total"
        }
      ],
      "title": "Memory Usage (Bytes)",
      "type": "graph"
    }
  ],
  "schemaVersion": 38,
  "style": "dark",
  "tags": [],
  "templating": {
    "list": []
  },
  "time": {
    "from": "now-6h",
    "to": "now"
  },
  "timepicker": {},
  "timezone": "",
  "title": "Basic Node Exporter Metrics",
  "uid": "node-exporter-basic",
  "version": 1
}
    """
    _sftp_file_and_move(
        ssh_client,
        node_exporter_dashboard_json,
        "/tmp/node-exporter-basic-dashboard.json",
        "/etc/grafana/provisioning/dashboards/node-exporter-basic-dashboard.json"
    )

    typer.echo("Starting and enabling Grafana server...")
    _execute_remote_commands(ssh_client, ["sudo systemctl start grafana-server"])
    typer.echo("Grafana setup complete. You may need to wait a moment for dashboards to appear.")