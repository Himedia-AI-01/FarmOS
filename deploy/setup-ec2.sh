#!/usr/bin/env bash
# Run once on a fresh Ubuntu 24.04 EC2 instance (t3.small or larger, 30GB+ EBS).
# Usage:
#   ssh ubuntu@<elastic-ip>
#   sudo bash setup-ec2.sh
set -euo pipefail

# --- Docker ---
apt-get update
apt-get install -y ca-certificates curl gnupg git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

usermod -aG docker ubuntu

# --- Node (for building frontends on the box) ---
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

# --- Certbot (host-installed; we copy certs into nginx via bind-mount) ---
apt-get install -y certbot

echo
echo "Done. Next steps:"
echo "  1. Log out and back in so docker group takes effect (or run: newgrp docker)"
echo "  2. git clone your repo to /home/ubuntu/FarmOS-v2"
echo "  3. Copy .env files into backend/ and shopping_mall/backend/"
echo "  4. Edit deploy/nginx.conf with your real domains"
echo "  5. Issue certs:"
echo "       sudo certbot certonly --standalone -d farmos.example.com -d shop.example.com"
echo "  6. cd /home/ubuntu/FarmOS-v2/deploy && bash deploy.sh"
