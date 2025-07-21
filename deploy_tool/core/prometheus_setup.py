import paramiko
import time
import os

PROMETHEUS_VERSION = "2.52.0"
NODE_EXPORTER_VERSION = "1.8.1"

PROMETHEUS_URL = "https://github.com/prometheus/prometheus/releases/download/v2.52.0/prometheus-2.52.0.linux-amd64.tar.gz"
NODE_EXPORTER_URL = "https://github.com/prometheus/node_exporter/releases/download/v1.8.1/node_exporter-1.8.1.linux-amd64.tar.gz"

def setup_monitoring_on_ec2(ip_address: str):
    print("🔧 Connecting to EC2 to setup Prometheus & Node Exporter...")

    key_path = os.path.join(os.path.dirname(__file__), "terraform_ec2", "id_rsa")
    key = paramiko.RSAKey.from_private_key_file(key_path)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(hostname=ip_address, username="ubuntu", pkey=key)

        cmds = [
            "sudo apt update -y",
            "sudo apt install wget tar -y",

            f"wget {PROMETHEUS_URL}",
            "tar xvf prometheus-2.52.0.linux-amd64.tar.gz",
            "sudo mv prometheus-2.52.0.linux-amd64 /usr/local/prometheus",

            f"wget {NODE_EXPORTER_URL}",
            "tar xvf node_exporter-1.8.1.linux-amd64.tar.gz",
            "sudo mv node_exporter-1.8.1.linux-amd64 /usr/local/node_exporter",

            "nohup /usr/local/prometheus/prometheus --web.listen-address=':9090' > prometheus.log 2>&1 &",
            "nohup /usr/local/node_exporter/node_exporter > node_exporter.log 2>&1 &"
        ]

        for cmd in cmds:
            print(f"🔸 Running: {cmd}")
            stdin, stdout, stderr = ssh.exec_command(cmd)
            time.sleep(1)  # wait a bit for each command
            err = stderr.read().decode()
            if err:
                print(f"⚠️  Error: {err.strip()}")

        print("✅ Prometheus & Node Exporter installed and running!")

    except Exception as e:
        print(f"SSH connection failed: {e}")
    finally:
        ssh.close()
