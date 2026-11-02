from __future__ import annotations

import socket
import sys

import pytest

from benchmarks.network_guard import install_model_only_guard


def test_benchmark_network_audit_blocks_non_model_network_and_processes(monkeypatch):
    hooks = []
    monkeypatch.setattr(sys, "addaudithook", hooks.append)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("100.64.0.8", 443))]
    )
    counts = install_model_only_guard("https://model.example.ts.net/v1")
    audit = hooks[0]
    audit("socket.getaddrinfo", ("model.example.ts.net", 443))
    audit("socket.connect", (None, ("100.64.0.8", 443)))
    for event, args in [
        ("socket.getaddrinfo", ("collector.invalid", 443)),
        ("socket.connect", (None, ("100.64.0.9", 443))),
        ("socket.connect", (None, ("100.64.0.8", 8443))),
        ("subprocess.Popen", ("shell",)),
    ]:
        with pytest.raises(PermissionError):
            audit(event, args)
    assert counts == {
        "model_connections": 1,
        "blocked_dns": 1,
        "blocked_connections": 2,
        "blocked_processes": 1,
    }
