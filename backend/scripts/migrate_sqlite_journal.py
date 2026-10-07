"""Offline journal migration with verified, non-overwriting SQLite backups.

Stop all database users first. Run on the host that can still read the WAL.
Never delete -wal/-shm files: SQLite checkpoints committed pages itself.
"""
import argparse
import json
import sqlite3
import hashlib
from contextlib import closing
from pathlib import Path
from uuid import uuid4


def _counts(connection):
    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names}


def _digest(connection):
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def migrate(databases: list[Path], backup_dir: Path) -> list[dict]:
    if len(set(databases)) != len(databases):
        raise ValueError("Duplicate database paths")
    backup_dir = backup_dir.resolve()
    paths = [path.resolve(strict=True) for path in databases]
    if len({path.name for path in paths}) != len(paths):
        raise ValueError("Backup filenames would collide")
    if any(path == backup_dir / path.name for path in paths):
        raise ValueError("Backup must not overwrite its source")
    backup_dir.mkdir(parents=True, exist_ok=False)
    records = []
    # Back up ALL targets before changing any journal mode.
    for path in paths:
        with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30)) as source:
            if source.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"Integrity check failed: {path.name}")
            counts = _counts(source)
            backup = backup_dir / path.name
            with closing(sqlite3.connect(str(backup))) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)] or _counts(destination) != counts:
                    raise RuntimeError(f"Backup verification failed: {path.name}")
            records.append({"path": str(path), "backup": str(backup), "counts": counts})
    for record in records:
        connection = sqlite3.connect(Path(record["path"]).as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            # Offline exclusive access avoids consulting cross-host WAL-index
            # shared memory during the transition. This is connection-local.
            connection.execute("PRAGMA locking_mode=EXCLUSIVE").fetchone()
            if connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint[0] != 0:
                    raise RuntimeError("Database still in use; stop all writers before migration")
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if mode != "delete" or _counts(connection) != record["counts"]:
                raise RuntimeError("Journal migration verification failed")
            record["journal_mode"] = mode
        except sqlite3.Error as exc:
            raise RuntimeError(f"Migration failed for {record['path']}; verified backup: {record['backup']}: {exc}") from exc
        finally:
            connection.close()
    return records


def restore_snapshot(database: Path, backup: Path, archive_dir: Path) -> dict:
    """Offline, explicit recovery when cross-host WAL locks cannot be changed.

    Keep the original database AND sidecars together for recovery. Publish a
    SQLite-cloned snapshot only after integrity/record-count verification.
    """
    database = database.resolve(strict=True)
    backup = backup.resolve(strict=True)
    if database == backup:
        raise ValueError("Backup must be separate from source")
    staged = database.with_name(database.name + ".recovered-" + uuid4().hex)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        counts = _counts(source)
        expected_digest = _digest(source)
    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source:
        if source.execute("PRAGMA quick_check").fetchall() != [("ok",)] or _counts(source) != counts or _digest(source) != expected_digest:
            raise RuntimeError("Backup does not match the offline source; recovery refused")
        with closing(sqlite3.connect(staged)) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA journal_mode=DELETE").fetchall() != [("delete",)] or _counts(destination) != counts or _digest(destination) != expected_digest:
                raise RuntimeError("Recovered snapshot verification failed")
            if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError("Recovered snapshot is not valid")
    archive_dir = archive_dir.resolve()
    archive_dir.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for path in [database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]:
            if path.exists():
                target = archive_dir / path.name
                path.rename(target)
                moved.append((path, target))
        staged.rename(database)
    except Exception:
        for original, archived in reversed(moved):
            archived.rename(original)
        raise
    return {"path": str(database), "archive": str(archive_dir), "backup": str(backup), "counts": counts, "journal_mode": "delete"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="append", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--restore-snapshot", type=Path, help="Explicitly recover one database from a previously verified backup")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--offline", action="store_true", required=True, help="All applications accessing the databases have been stopped")
    args = parser.parse_args()
    if args.restore_snapshot:
        if len(args.database) != 1 or not args.archive_dir:
            parser.error("Recovery requires one --database and --archive-dir")
        result = restore_snapshot(args.database[0], args.restore_snapshot, args.archive_dir)
    else:
        if not args.backup_dir:
            parser.error("Migration requires --backup-dir")
        result = migrate(args.database, args.backup_dir)
    print(json.dumps(result, ensure_ascii=False))
