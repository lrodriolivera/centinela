#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "[→] Building Centinela sandbox Docker image..."
docker build -t centinela-sandbox:latest -f "$PROJECT_DIR/docker/Dockerfile.sandbox" "$PROJECT_DIR/docker/"
echo "[✓] Sandbox image built: centinela-sandbox:latest"
