#!/usr/bin/env bash
set -eu
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
sudo apt-get update
sudo apt-get install -y --no-install-recommends libreoffice-calc
sudo python3 -m venv /opt/venv
sudo chown -R "$(id -u):$(id -g)" /opt/venv
/opt/venv/bin/pip install --no-cache-dir -r requirements-dev.txt
curl -fsSL https://claude.ai/install.sh | bash
echo "Setup complete. Open a new terminal and run: claude"
