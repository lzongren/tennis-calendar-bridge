"""Process-local audit for replay benchmarks; not a production OS sandbox."""

from __future__ import annotations

import socket
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit


def install_model_only_guard(
    endpoint: str, *, denied_frames: list[list[str]] | None = None
) -> dict[str, int]:
    """Limit subsequent DNS/connect calls to the configured model, deny subprocesses."""
    parsed = urlsplit(endpoint)
    host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = {row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    counts = {
        "model_connections": 0,
        "blocked_connections": 0,
        "blocked_dns": 0,
        "blocked_processes": 0,
    }

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event == "socket.getaddrinfo":
            name = args[0].decode() if isinstance(args[0], bytes) else args[0]
            if name != host and name not in addresses:
                counts["blocked_dns"] += 1
                raise PermissionError("Unexpected benchmark DNS blocked")
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or address[0] not in addresses or address[1] != port:
                counts["blocked_connections"] += 1
                raise PermissionError("Unexpected benchmark connection blocked")
            counts["model_connections"] += 1
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
            counts["blocked_processes"] += 1
            if denied_frames is not None:
                denied_frames.append(
                    [
                        f"{Path(frame.filename).name}:{frame.name}"
                        for frame in traceback.extract_stack(limit=12)
                    ]
                )
            raise PermissionError("Benchmark subprocess blocked")

    sys.addaudithook(audit)
    return counts
