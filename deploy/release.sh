#!/usr/bin/env bash
# Build/test locally; publish only with an explicit --push argument.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != --push ) ]]; then
  echo 'Usage: bash deploy/release.sh REGISTRY/NAMESPACE/IMAGE:VERSION [--push]' >&2
  exit 2
fi
image_ref="$1"
if [[ "$image_ref" == -* || "${image_ref##*/}" != *:* || "$image_ref" == *:latest || "$image_ref" == *@* ]]; then
  echo 'Use an explicit version tag (not latest or a digest) for building.' >&2
  exit 2
fi
# Native platform only: run on the same CPU architecture as the target server.
docker build --pull --tag "$image_ref" .
python3 deploy/smoke_image.py --image "$image_ref"
mkdir -p dist
bundle_name="${image_ref##*/}"
bundle_name="${bundle_name//:/-}"
tar -czf "dist/${bundle_name}-deploy.tar.gz" compose.yaml compose.ssh.yaml compose.ip.yaml compose.ip-https.yaml compose.existing-proxy.yaml .env.example \
  deploy/existing-proxy.env.example deploy/nginx-resume.conf.example deploy/nginx-resumeworkflow.conf deploy/Caddyfile deploy/Caddyfile.ip deploy/ip-https.env.example deploy/deployment.env.example deploy/ssh.env.example deploy/ip.env.example \
  deploy/install_docker_ubuntu.sh deploy/backup_data.py docs/本地运行.md
echo "Server deployment files: dist/${bundle_name}-deploy.tar.gz"
if [[ "${2:-}" == --push ]]; then
  docker push "$image_ref"
else
  echo 'Local image passed. Add --push after configuring registry login to publish.'
fi
