#!/usr/bin/env bash
# Build frontends, build & (re)start containers. Run from deploy/ on the EC2 box.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Pulling latest"
git pull --ff-only

echo "==> Building farmos frontend"
( cd frontend && npm ci && npm run build )

echo "==> Building shopping_mall frontend"
( cd shopping_mall/frontend && npm ci && npm run build )

echo "==> Building & starting containers"
cd deploy
docker compose build
docker compose up -d

echo "==> Reloading nginx"
docker compose exec -T nginx nginx -s reload || true

echo "==> Status"
docker compose ps
