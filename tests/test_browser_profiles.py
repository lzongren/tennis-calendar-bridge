from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from tennis_overview.browser_profiles import profile_snapshot


def seed_profile(tmp_path):
    source = tmp_path / "login"
    (source / "Default").mkdir(parents=True)
    (source / "Local State").write_text("{}")
    (source / "Default/Login Data").write_text("must-not-copy-password-store")
    database = source / "Default/Cookies"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE cookies (encrypted_value BLOB)")
        connection.execute("INSERT INTO cookies VALUES (?)", (b"synthetic-not-a-secret",))
        connection.commit()
    return source, database


def test_failed_reader_cannot_delete_original_cookie_state(tmp_path):
    source, database = seed_profile(tmp_path)
    before = database.read_bytes()
    with pytest.raises(RuntimeError):
        with profile_snapshot(source) as copy:
            assert copy != source
            assert copy.stat().st_mode & 0o077 == 0
            assert not (copy / "Default/Login Data").exists()
            assert (copy / "Default/Cookies").stat().st_mode & 0o077 == 0
            with closing(sqlite3.connect(copy / "Default/Cookies")) as connection:
                connection.execute("DELETE FROM cookies")
                connection.commit()
            raise RuntimeError("simulated browser decryption failure")
    assert database.read_bytes() == before
    assert not copy.exists()


def test_snapshot_refuses_active_login_browser(tmp_path):
    source, database = seed_profile(tmp_path)
    (source / "SingletonLock").symlink_to("host-123")
    with pytest.raises(ValueError, match="closed"):
        with profile_snapshot(source):
            pytest.fail("Opened an active login profile")
    assert list(tmp_path.iterdir()) == [source]


def test_snapshot_refuses_linked_cookie_file(tmp_path):
    source, database = seed_profile(tmp_path)
    external = tmp_path / "other"
    external.write_bytes(database.read_bytes())
    database.unlink()
    database.symlink_to(external)
    with pytest.raises(ValueError, match="symlinks"):
        with profile_snapshot(source):
            pytest.fail("Followed a cookie symlink")
    assert not list(tmp_path.glob("profile-read-*"))


def test_snapshot_preserves_committed_wal_data(tmp_path):
    source, database = seed_profile(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO cookies VALUES (?)", (b"second-synthetic-record",))
        connection.commit()
        with profile_snapshot(source) as copy:
            with closing(sqlite3.connect(copy / "Default/Cookies")) as copied:
                assert copied.execute("SELECT COUNT(*) FROM cookies").fetchone()[0] == 2
