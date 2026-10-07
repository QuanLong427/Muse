import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from services import sqlite_runtime as runtime, memory_store
from scripts.migrate_sqlite_journal import migrate, restore_snapshot


def test_journal_policy_and_rollback_preserve_records(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.settings, "SQLITE_JOURNAL_MODE", "DELETE")
    path = tmp_path / "store.db"
    with closing(runtime.connect_database(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('preserved')")
        connection.commit()
        connection.execute("INSERT INTO evidence VALUES ('rolled back')")
        connection.rollback()
    monkeypatch.setattr(runtime.settings, "SQLITE_JOURNAL_MODE", "WAL")
    with closing(runtime.connect_database(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT value FROM evidence").fetchall()[0][0] == "preserved"


def test_invalid_policy_does_not_create_a_database(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.settings, "SQLITE_JOURNAL_MODE", "OFF")
    path = tmp_path / "store.db"
    with pytest.raises(ValueError):
        runtime.connect_database(path)
    assert not path.exists()


def test_offline_migration_retains_data_and_verified_backup(tmp_path):
    path = tmp_path / "store.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('用户数据')")
        connection.commit()
    backup_dir = tmp_path / "backup"
    result = migrate([path], backup_dir)
    assert result[0]["counts"] == {"evidence": 1}
    for db in (path, backup_dir / path.name):
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "用户数据"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    with pytest.raises(FileExistsError):
        migrate([path], backup_dir)
    with pytest.raises(FileNotFoundError):
        migrate([tmp_path / "missing.db"], tmp_path / "other-backup")


def test_rollback_journal_supports_parallel_memory_writers(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.settings, "SQLITE_JOURNAL_MODE", "DELETE")
    monkeypatch.setattr(memory_store, "MEMORY_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory_store, "MEMORY_DB_PATH", tmp_path / "memory.db")
    session = memory_store.ensure_session("s", "u", "跑步")
    def write(i):
        return memory_store.add_message(session_id=session, user_id="u", role="user", content=f"记录{i}", scenario="跑步")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(24)))
    assert len(memory_store.get_session_history(session, "u")) == 24
    assert memory_store.check_memory_health()["status"] == "ok"


def test_snapshot_recovery_archives_original_without_losing_rows(tmp_path):
    original = tmp_path / "memory.db"
    backup = tmp_path / "backup.db"
    with closing(sqlite3.connect(original)) as source:
        source.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, content TEXT)")
        source.execute("INSERT INTO events VALUES (237,'原有记录')")
        source.commit()
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    archive = tmp_path / "archive"
    result = restore_snapshot(original, backup, archive)
    assert result["counts"] == {"events": 1}
    for path in (original, archive / "memory.db", backup):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT * FROM events").fetchall() == [(237, "原有记录")]
    assert original.is_file()


def test_snapshot_recovery_refuses_same_counts_with_changed_content(tmp_path):
    original = tmp_path / "memory.db"
    backup = tmp_path / "backup.db"
    for path, value in [(original, "最新用户数据"), (backup, "旧备份")]:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, content TEXT)")
            connection.execute("INSERT INTO events VALUES(1, ?)", (value,))
            connection.commit()
    with pytest.raises(RuntimeError, match="does not match"):
        restore_snapshot(original, backup, tmp_path / "archive")
    with closing(sqlite3.connect(original)) as connection:
        assert connection.execute("SELECT content FROM events").fetchone()[0] == "最新用户数据"
