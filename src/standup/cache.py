"""The Derived Cache (ADR 0003): a pure accelerator at ~/.standup/cache/cache.db.

The `cache/` subdirectory is deliberate: the `~/.standup` root is durable and
holds non-recomputable data (Session Briefs, see brief.py), so only `cache/` is
disposable. `rm -rf ~/.standup/cache` is always safe; the root is not.

Holds results derived deterministically from the session logs — one row per
session file (the fully parsed Session) plus an immutable commit_files(sha)
table. Keyed on (size, mtime_ns) so a stale entry is always detected and
reparsed; output is byte-identical whether the cache is warm, cold, or deleted.

The cache is disposable. Any read error, a schema/parser version mismatch, or a
future-version DB triggers a silent cold rebuild; if ~/.standup can't be used at
all, a NullCache keeps the CLI working with zero caching. A cache problem is
never a user-visible error.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2
PARSER_VERSION = 1  # bump when session parse logic changes (invalidates rows)

CACHE_PATH = Path(os.path.expanduser("~/.standup")) / "cache" / "cache.db"


def loops_detector_version() -> int:
    from .loops import DETECTOR_VERSION  # the detector owns its own version
    return DETECTOR_VERSION


class NullCache:
    """No-op fallback when the on-disk cache is unusable."""

    def get_session(self, session_id, size, mtime_ns):
        return None

    def put_session(self, session_id, size, mtime_ns, data):
        pass

    def get_commit_files(self, sha):
        return None

    def put_commit_files(self, sha, files):
        pass

    def get_loops(self, session_id, size, mtime_ns):
        return None

    def put_loops(self, session_id, size, mtime_ns, data):
        pass

    def prune(self, live_ids):
        pass

    def flush(self):
        pass


class Cache:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._sessions: dict[str, tuple[int, int, dict]] = {}
        self._commits: dict[str, list[str]] = {}
        self._loops: dict[str, tuple[int, int, dict]] = {}
        self._live: set[str] | None = None

    # --- sessions -------------------------------------------------------

    def get_session(self, session_id: str, size: int, mtime_ns: int) -> dict | None:
        try:
            row = self._conn.execute(
                "SELECT data FROM sessions "
                "WHERE session_id=? AND size=? AND mtime_ns=? AND parser_version=?",
                (session_id, size, mtime_ns, PARSER_VERSION),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None

    def put_session(self, session_id: str, size: int, mtime_ns: int, data: dict) -> None:
        self._sessions[session_id] = (size, mtime_ns, data)

    # --- commit files (immutable by sha) --------------------------------

    def get_commit_files(self, sha: str) -> list[str] | None:
        if sha in self._commits:
            return self._commits[sha]
        try:
            row = self._conn.execute(
                "SELECT files FROM commit_files WHERE sha=?", (sha,)
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None

    def put_commit_files(self, sha: str, files: list[str]) -> None:
        self._commits[sha] = files

    # --- loops (ADR 0007: derived Loop detection per session file) -------

    def get_loops(self, session_id: str, size: int, mtime_ns: int) -> dict | None:
        try:
            row = self._conn.execute(
                "SELECT data FROM loops "
                "WHERE session_id=? AND size=? AND mtime_ns=? AND detector_version=?",
                (session_id, size, mtime_ns, loops_detector_version()),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None

    def put_loops(self, session_id: str, size: int, mtime_ns: int, data: dict) -> None:
        self._loops[session_id] = (size, mtime_ns, data)

    # --- lifecycle ------------------------------------------------------

    def prune(self, live_ids: set[str]) -> None:
        self._live = live_ids

    def flush(self) -> None:
        """Apply all buffered writes and pruning in one transaction, then close."""
        try:
            if self._sessions:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO sessions(session_id,size,mtime_ns,parser_version,data) "
                    "VALUES(?,?,?,?,?)",
                    [(sid, sz, mt, PARSER_VERSION, json.dumps(data))
                     for sid, (sz, mt, data) in self._sessions.items()],
                )
            if self._commits:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO commit_files(sha,files) VALUES(?,?)",
                    [(sha, json.dumps(files)) for sha, files in self._commits.items()],
                )
            if self._loops:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO loops(session_id,size,mtime_ns,detector_version,data) "
                    "VALUES(?,?,?,?,?)",
                    [(sid, sz, mt, loops_detector_version(), json.dumps(data))
                     for sid, (sz, mt, data) in self._loops.items()],
                )
            if self._live is not None:
                for table in ("sessions", "loops"):
                    existing = {r[0] for r in self._conn.execute(f"SELECT session_id FROM {table}")}
                    stale = existing - self._live
                    if stale:
                        self._conn.executemany(
                            f"DELETE FROM {table} WHERE session_id=?", [(s,) for s in stale]
                        )
            self._conn.commit()
        except sqlite3.Error:
            pass
        finally:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id     TEXT PRIMARY KEY,
            size           INTEGER NOT NULL,
            mtime_ns       INTEGER NOT NULL,
            parser_version INTEGER NOT NULL,
            data           TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS commit_files (
            sha   TEXT PRIMARY KEY,
            files TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS loops (
            session_id       TEXT PRIMARY KEY,
            size             INTEGER NOT NULL,
            mtime_ns         INTEGER NOT NULL,
            detector_version INTEGER NOT NULL,
            data             TEXT NOT NULL
        );
        """
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _delete_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix)
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def open_cache(path: Path = CACHE_PATH):
    """Open (or rebuild) the cache. Never raises — falls back to NullCache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return NullCache()

    for rebuild in (False, True):
        conn = None
        try:
            if rebuild:
                _delete_db(path)
            conn = sqlite3.connect(str(path))
            conn.execute("PRAGMA journal_mode=WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                _init_schema(conn)
            # sanity-check the schema is present and readable
            conn.execute("SELECT 1 FROM sessions LIMIT 1")
            conn.execute("SELECT 1 FROM commit_files LIMIT 1")
            conn.execute("SELECT 1 FROM loops LIMIT 1")
            return Cache(conn)
        except sqlite3.Error:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
    return NullCache()
