#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-vps_us}"
REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/projects/tennis-calendar-bridge}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing .env. Copy .env.example to .env and fill in tokens/credentials first." >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/config/clubs.toml" ]]; then
  echo "Missing config/clubs.toml. Copy config/clubs.example.toml first." >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "Missing rsync on this machine." >&2
  exit 1
fi

quote() {
  printf "%q" "$1"
}

REMOTE_DIR_QUOTED="$(quote "$REMOTE_DIR")"

echo "Deploying to ${SSH_TARGET}:${REMOTE_DIR}"

ssh "$SSH_TARGET" "mkdir -p $REMOTE_DIR_QUOTED"

rsync -az --delete \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  --exclude ".pytest_cache/" \
  --exclude "*.egg-info/" \
  --exclude "data/" \
  --exclude "ansible/inventory.yml" \
  --exclude "ansible/group_vars/**/vault.yml" \
  --exclude "ansible/group_vars/**/vault.yaml" \
  --exclude "ansible/group_vars/**/local.yml" \
  --exclude "ansible/group_vars/**/local.yaml" \
  --exclude "ansible/group_vars/**/zz-local.yml" \
  --exclude "ansible/group_vars/**/zz-local.yaml" \
  "$ROOT_DIR/" "$SSH_TARGET:$REMOTE_DIR/"

ssh "$SSH_TARGET" "cd $REMOTE_DIR_QUOTED && docker compose up -d --build && docker compose ps"

echo
echo "Deployment complete."
echo "Service URL, if port 8080 is open: http://${SSH_TARGET}:8080"
