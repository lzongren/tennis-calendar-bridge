from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_uses_a_placeholder_host():
    inventory = yaml.safe_load((ROOT / "ansible/inventory.example.yml").read_text())
    assert inventory["tennis_servers"]["hosts"] == {
        "tennis_vps": {"ansible_host": "your-vps-hostname", "ansible_user": "ubuntu"}
    }


def test_shell_deployment_requires_explicit_target():
    env = {key: value for key, value in os.environ.items() if key != "SSH_TARGET"}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/deploy_vps.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert "Set SSH_TARGET" in result.stderr
    assert "Deploying to" not in result.stdout
