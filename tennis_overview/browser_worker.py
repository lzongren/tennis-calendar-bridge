"""Authenticated loopback service for one bounded browser job at a time."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .browser_protocol import (
    MAX_RESPONSE,
    SEARCH_UNAVAILABLE,
    SOURCE_ERRORS,
    BrowserLink,
    BrowserReply,
    BrowserRequest,
)

ROOT = Path.home() / ".config" / "tennis-browser"
ACTIVE: set[subprocess.Popen[bytes]] = set()
ACTIVE_LOCK = threading.Lock()


class WorkerServer(ThreadingHTTPServer):
    """Bound slow/unauthenticated connections as well as the single browser job."""

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        self.connections = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        if not self.connections.acquire(blocking=False):
            self.close_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def execute(request: BrowserRequest, root: Path) -> dict[str, Any]:
    """Isolate the browser from the service key and bound the entire process lifetime."""
    env = {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "TMPDIR") if key in os.environ}
    with (
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryDirectory(prefix="browser-job-", dir=root) as scratch,
    ):
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "tennis_overview.browser_worker",
                "child",
                "--root",
                str(root),
                "--scratch",
                scratch,
            ],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        with ACTIVE_LOCK:
            ACTIVE.add(process)
        try:
            process.communicate(request.model_dump_json().encode(), timeout=50)
        except subprocess.TimeoutExpired:
            terminate(process)
            return {"error": "timeout"}
        finally:
            terminate(process)
            with ACTIVE_LOCK:
                ACTIVE.discard(process)
        output.seek(0)
        raw = output.read(MAX_RESPONSE + 1)
    if process.returncode or len(raw) > MAX_RESPONSE:
        return {"error": "worker_unavailable"}
    try:
        value = json.loads(raw)
        expected = SOURCE_ERRORS if request.operation == "read" else {SEARCH_UNAVAILABLE}
        if isinstance(value.get("error"), str) and value["error"] in expected | {
            "worker_unavailable"
        }:
            return {"error": value["error"]}
        return BrowserReply.model_validate(value).model_dump(mode="json")
    except (ValueError, AttributeError):
        return {"error": "worker_unavailable"}


def child_reply(request: BrowserRequest, root: Path, scratch: Path | None) -> dict[str, Any]:
    """Keep page failures distinct from worker faults without leaking exception text."""
    from ddgs.exceptions import DDGSException

    from .browser_runtime import BrowserReadError, read_browser
    from .public_web import search_courses

    try:
        if request.operation == "search":
            assert request.year is not None
            links = search_courses(request.city, request.level, request.year, request.focus)
            reply = BrowserReply(
                kind="search", links=tuple(BrowserLink.model_validate(link) for link in links)
            )
        else:
            reply = asyncio.run(read_browser(request, root / "profile", scratch=scratch))
        return reply.model_dump(mode="json")
    except BrowserReadError as error:
        code = str(error)
        return {
            "error": code
            if request.operation == "read" and code in SOURCE_ERRORS
            else "worker_unavailable"
        }
    except DDGSException:
        return {
            "error": SEARCH_UNAVAILABLE if request.operation == "search" else "worker_unavailable"
        }
    except Exception:
        return {"error": "worker_unavailable"}


def handler_class(
    key: str, root: Path, allowed_peers: frozenset[str] = frozenset()
) -> type[BaseHTTPRequestHandler]:
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "TennisBrowser"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Never log URLs, credentials, browser text or caller bodies.

        def reply(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            values = self.headers.get_all("Authorization", [])
            return len(values) == 1 and hmac.compare_digest(
                values[0].encode(), ("Bearer " + key).encode()
            )

        def peer_allowed(self) -> bool:
            values = self.headers.get_all("X-Forwarded-For", [])
            # Only the local Tailscale Serve proxy may reach this loopback listener.
            # Serve replaces this header with the verified Tailnet source IP.
            # Direct local administration still requires the independent bearer key.
            return not values or (len(values) == 1 and values[0] in allowed_peers)

        def do_GET(self) -> None:
            if not self.peer_allowed():
                self.reply(403, {"error": "forbidden"})
            elif not self.authorized():
                self.reply(401, {"error": "unauthorized"})
            elif self.path == "/healthz":
                self.reply(
                    200,
                    {
                        "status": "ready",
                        "policy": "read-only",
                        "profile": "isolated",
                        "authentication": "not_verified",
                    },
                )
            else:
                self.reply(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self.peer_allowed():
                self.reply(403, {"error": "forbidden"})
                return
            if not self.authorized():
                self.reply(401, {"error": "unauthorized"})
                return
            if self.path != "/v1/read":
                self.reply(404, {"error": "not_found"})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (
                self.headers.get("Transfer-Encoding")
                or len(lengths) != 1
                or not lengths[0].isdigit()
                or not 0 < int(lengths[0]) <= 4096
            ):
                self.reply(400, {"error": "invalid_request"})
                return
            try:
                request = BrowserRequest.model_validate_json(self.rfile.read(int(lengths[0])))
            except ValueError:
                self.reply(400, {"error": "invalid_request"})
                return
            if not lock.acquire(blocking=False):
                self.reply(503, {"error": "busy"})
                return
            try:
                payload = execute(request, root)
                self.reply(503 if "error" in payload else 200, payload)
            except (OSError, subprocess.SubprocessError):
                self.reply(503, {"error": "worker_unavailable"})
            finally:
                lock.release()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "child"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8134)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--allowed-peer", action="append", default=[])
    args = parser.parse_args()
    if args.action == "child":
        try:
            request = BrowserRequest.model_validate_json(sys.stdin.buffer.read(4097))
            payload = child_reply(request, args.root, args.scratch)
        except ValueError:
            payload = {"error": "worker_unavailable"}
        print(json.dumps(payload))
        return
    key_file = args.root / "api-key"
    if not args.allowed_peer:
        parser.error("Configure the authorized VPS Tailnet IP addresses")
    if key_file.stat().st_mode & 0o077:
        parser.error("Worker key must have owner-only permissions")
    key = key_file.read_text().strip()
    if len(key) < 32:
        parser.error("Worker key is missing or too short")
    server = WorkerServer(
        ("127.0.0.1", args.port), handler_class(key, args.root, frozenset(args.allowed_peer))
    )

    def shutdown(signum: int, frame: Any) -> None:
        with ACTIVE_LOCK:
            active = tuple(ACTIVE)
        for process in active:
            terminate(process)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
