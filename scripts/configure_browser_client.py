"""Receive the dedicated browser key on stdin without logging or overwriting secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from tennis_overview.private_model import PrivateModelConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    endpoint = PrivateModelConfig.private_endpoint(args.url)
    key = sys.stdin.read(129).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
        parser.error("Invalid dedicated browser credential")
    content = f"TENNIS_DISCOVERY_BROWSER_URL={json.dumps(endpoint)}\nTENNIS_DISCOVERY_BROWSER_KEY={json.dumps(key)}\n"
    fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print("Created owner-only browser settings; credentials not displayed; agent not enabled.")


if __name__ == "__main__":
    main()
