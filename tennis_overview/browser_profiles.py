"""Keep interactive login state out of the browser worker's writable profile."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def profile_snapshot(source: Path, scratch: Path | None = None) -> Iterator[Path]:
    # Chromium can delete cookies it cannot decrypt. Never launch against the
    # human login profile, including when a read is expected to fail.
    if source.is_symlink() or os.path.lexists(source / "SingletonLock"):
        raise ValueError("Login profile must be closed and must not be a symlink")
    source = source.resolve()
    with tempfile.TemporaryDirectory(
        prefix="profile-read-", dir=scratch or source.parent
    ) as directory:
        target = Path(directory)
        for relative in ("Local State", "Default/Cookies", "Default/Network/Cookies"):
            path = source / relative
            if any(item.is_symlink() for item in (path, *path.parents)):
                raise ValueError("Login state symlinks are not allowed")
            if not path.exists():
                continue
            if not path.is_file() or path.stat().st_size > 32_000_000:
                raise ValueError("Login state exceeds the snapshot limit")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.name == "Cookies":
                # SQLite backup includes committed WAL data without decrypting
                # values or copying a potentially inconsistent database file.
                def bounded(status: int, remaining: int, total: int) -> None:
                    if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                        raise ValueError("Cookie database is busy")
                    if total * page_size > 32_000_000:
                        raise ValueError("Cookie database exceeds the snapshot limit")

                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as original:
                    page_size = original.execute("PRAGMA page_size").fetchone()[0]
                    with closing(sqlite3.connect(destination)) as copy:
                        original.backup(copy, pages=256, progress=bounded)
            else:
                shutil.copyfile(path, destination)
            destination.chmod(0o600)
        yield target
