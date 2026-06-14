#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INVENTORY="$ROOT_DIR/ansible/inventory.yml"
VAULT_FILE="$ROOT_DIR/ansible/group_vars/tennis_servers/vault.yml"

if [[ ! -f "$INVENTORY" ]]; then
  echo "Missing ansible/inventory.yml. Copy ansible/inventory.example.yml first." >&2
  exit 1
fi

if [[ ! -f "$VAULT_FILE" ]]; then
  echo "Missing ansible/group_vars/tennis_servers/vault.yml. Copy vault.yml.example and encrypt it." >&2
  exit 1
fi

cd "$ROOT_DIR/ansible"
ansible-playbook playbook.yml --ask-vault-pass "$@"
