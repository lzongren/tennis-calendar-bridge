"""Install an authenticated, loopback-only Rapid-MLX LaunchAgent (no routing changes)."""

from __future__ import annotations

import argparse
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "org.tennis-calendar.inference"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "run"])
    parser.add_argument("--executable", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()
    root = Path.home() / ".config" / "tennis-inference"
    key_path = root / "api-key"
    if args.action == "run":
        env = os.environ.copy()
        env["RAPID_MLX_API_KEY"] = key_path.read_text().strip()
        env["HF_HUB_OFFLINE"] = "1"
        os.execve(
            args.executable,
            [
                args.executable,
                "serve",
                args.model,
                "--served-model-name",
                "tennis-discovery",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--max-num-seqs",
                "1",
                "--max-concurrent-requests",
                "2",
                "--max-tokens",
                "2048",
                "--cache-memory-mb",
                "2048",
                "--gpu-memory-utilization",
                "0.65",
                "--timeout",
                "180",
                "--log-level",
                "WARNING",
            ],
            env,
        )
    if sys.platform != "darwin":
        parser.error("Installation requires macOS")
    if not Path(args.executable).is_file() or not Path(args.model).is_dir():
        parser.error("Provide an existing Rapid-MLX executable and local model directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    if not key_path.exists():
        fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(48) + "\n")
    key_path.chmod(0o600)
    script = root / "service.py"
    shutil.copyfile(__file__, script)
    script.chmod(0o700)
    logs = root / "logs"
    logs.mkdir(exist_ok=True, mode=0o700)
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / f"{LABEL}.plist"
    spec = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(script),
            "run",
            "--executable",
            args.executable,
            "--model",
            args.model,
            "--port",
            str(args.port),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / "stdout.log"),
        "StandardErrorPath": str(logs / "stderr.log"),
        "EnvironmentVariables": {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
    }
    with plist.open("wb") as handle:
        plistlib.dump(spec, handle)
    plist.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print(
        f"Installed {LABEL}; loopback port {args.port}; API key stored privately (not displayed)."
    )


if __name__ == "__main__":
    main()
