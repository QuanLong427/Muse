"""Connection policy for native disks and Docker bind mounts.

Rollback journals avoid WAL's shared-memory requirement. WAL is an explicit
opt-in on compatible local disks. Never delete sidecars to hide I/O failures.
"""
import sqlite3
from pathlib import Path

from config import settings


def connect_database(path: str | Path, *, timeout: float = 30) -> sqlite3.Connection:
    mode = settings.SQLITE_JOURNAL_MODE.strip().upper()
    if mode not in {"DELETE", "WAL"}:
        raise ValueError("SQLITE_JOURNAL_MODE must be DELETE or WAL")
    connection = sqlite3.connect(str(path), timeout=timeout)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        current = connection.execute("PRAGMA journal_mode").fetchone()[0].upper()
        if current != mode:
            actual = connection.execute(f"PRAGMA journal_mode={mode}").fetchone()[0].upper()
            if actual != mode:
                raise RuntimeError(f"SQLite journal transition failed: {current} -> {mode}")
        return connection
    except Exception:
        connection.close()
        raise
