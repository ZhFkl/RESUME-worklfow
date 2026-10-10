#!/usr/bin/env bash
# Run on a fresh Ubuntu server: sudo bash deploy/install_docker_ubuntu.sh
# Uses Docker's official APT repository; never run this inside the application image.
set -euo pipefail
docker_repo=https://download.docker.com/linux/ubuntu
case "${1:-}" in
  "") ;;
  --aliyun-mirror) docker_repo=https://mirrors.aliyun.com/docker-ce/linux/ubuntu ;;
  *) echo 'Usage: sudo bash deploy/install_docker_ubuntu.sh [--aliyun-mirror]' >&2; exit 2 ;;
esac
if [[ $EUID -ne 0 ]]; then
  echo 'Run with sudo: sudo bash deploy/install_docker_ubuntu.sh' >&2
  exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu ]]; then
  echo 'This installer supports Ubuntu only.' >&2
  exit 1
fi
if command -v docker >/dev/null 2>&1; then
  echo 'Docker already exists; no packages or repository configuration were changed.'
  docker --version
  docker compose version
  exit 0
fi
apt-get update
apt-get install -y ca-certificates curl python3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL --connect-timeout 15 --max-time 120 --retry 2 \
  "$docker_repo/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: $docker_repo
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version
docker compose version
