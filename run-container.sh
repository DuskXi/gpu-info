#!/usr/bin/env bash
# Rebuild the image and (re)start the systemd-managed container (Podman Quadlet, rootful).
# Unit definition: deploy/gpu-info.container -> /etc/containers/systemd/gpu-info.container
set -euo pipefail
cd "$(dirname "$0")"
podman build -t gpu-info .
podman save localhost/gpu-info | sudo -n podman load -q >/dev/null
sudo -n install -m 644 deploy/gpu-info.container /etc/containers/systemd/gpu-info.container
sudo -n systemctl daemon-reload
sudo -n systemctl restart gpu-info.service
systemctl --no-pager --lines=0 status gpu-info.service
