"""Install the isolated browser worker; routing belongs to the infrastructure operator."""

from __future__ import annotations

import argparse
import ipaddress
import os
import plistlib
import secrets
import subprocess
import sys
from pathlib import Path

LABEL = "org.tennis-calendar.browser"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "login", "start", "stop"))
    parser.add_argument("--port", type=int, default=8134)
    parser.add_argument("--url", default="")
    parser.add_argument("--allowed-peer", action="append", default=[])
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Requires macOS")
    root = Path.home() / ".config" / "tennis-browser"
    domain = f"gui/{os.getuid()}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if args.action in {"stop", "login"}:
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        if args.action == "login":
            from urllib.parse import urlsplit
            import re

            parsed = urlsplit(args.url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "app.courtreserve.com"
                or parsed.query
                or parsed.fragment
                or not re.fullmatch(r"/Online/Portal/Index/\d+", parsed.path)
            ):
                parser.error("Provide the approved CourtReserve portal URL")
            subprocess.run(
                [
                    "open",
                    "-na",
                    "Google Chrome",
                    "--args",
                    "--user-data-dir=" + str(root / "profile"),
                    "--no-first-run",
                    "--no-default-browser-check",
                    args.url,
                ],
                check=True,
            )
            print(
                "Dedicated login browser opened. Complete login yourself, then quit this browser and start the worker. No cookies are exported."
            )
        return
    if args.action == "install":
        if not args.allowed_peer:
            parser.error("Provide the authorized VPS Tailnet IP addresses")
        for peer in args.allowed_peer:
            address = ipaddress.ip_address(peer)
            network = ipaddress.ip_network(
                "100.64.0.0/10" if address.version == 4 else "fd7a:115c:a1e0::/48"
            )
            if address not in network:
                parser.error("Allowed peers must be Tailnet addresses")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        (root / "profile").mkdir(exist_ok=True, mode=0o700)
        key = root / "api-key"
        if not key.exists():
            fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(48) + "\n")
        key.chmod(0o600)
        plist.parent.mkdir(parents=True, exist_ok=True)
        spec = {
            "Label": LABEL,
            "ProgramArguments": [
                sys.executable,
                "-I",
                "-m",
                "tennis_overview.browser_worker",
                "serve",
                "--port",
                str(args.port),
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 30,
            "WorkingDirectory": str(root),
            "StandardOutPath": str(root / "service.log"),
            "StandardErrorPath": str(root / "service-error.log"),
            "EnvironmentVariables": {
                "HOME": str(Path.home()),
                "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
            },
        }
        for peer in args.allowed_peer:
            spec["ProgramArguments"].extend(["--allowed-peer", peer])
        with plist.open("wb") as stream:
            plistlib.dump(spec, stream)
        plist.chmod(0o600)
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print(
        f"Started {LABEL} on loopback:{args.port}; dedicated key remains private; no routes changed."
    )


if __name__ == "__main__":
    main()
