"""The Derived Cache (ADR 0001 § the Derived Cache): a pure accelerator at
~/.standup/cache/cache.db.

The `cache/` subdirectory is deliberate: the `~/.standup` root is durable and
holds non-recomputable data (Session Briefs, see brief.py), so only `cache/` is
disposable. `rm -rf ~/.standup/cache` is always safe; the root is not.

Holds results derived deterministically from the session logs — one row per
session file (the fully parsed Session), the typed full reading beside it
(ADR 0001 § the one log reader), an immutable commit_files(sha) table, and
detected Loops. What hunk attribution matches against (ADR 0007 § Decision) is
a projection of the typed reading, so it is served by that row rather than
stored a second time. Keyed on (size, mtime_ns) so a stale entry is always
detected and reparsed; output is byte-identical whether the cache is warm,
cold, or deleted.

Each of those is one **`Derivation`**, declared once in `DERIVED` — kind, codec,
whose version invalidates it, and how its live keys are enumerated. Everything
else follows from the declaration: the table, the stat key, the buffered write,
the prune, and the whole get/compute/put dance, which callers ask for as a
single `derive()` (ADR 0001 § the accelerator protocol). Nothing here knows
what a Session, a Loop or a commit *is*; the owning module keeps its own typed
round-trip and hands it over as `load`/`dump`.

*Derivation*, not *Artifact*: an *Artifact* is the durable out-of-band kind
(ADR 0003 § the Artifact store), which is the opposite of disposable.

The cache is disposable, at three depths and never as an error the user sees: a
row whose declared version moved on, or that cannot be read, costs a recompute;
a DB that cannot be opened, or that a newer Standup stamped with a schema this
one may not know, is deleted and rebuilt cold; and if ~/.standup cannot be used
at all, a NullCache keeps the CLI working with zero caching.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 5
PARSER_VERSION = 1  # bump when session parse logic changes (invalidates rows)

CACHE_PATH = Path(os.path.expanduser("~/.standup")) / "cache" / "cache.db"

# The ceiling on one compressed blob row (the typed reading). A session that
# wrote a hundred large files should not be allowed to
# grow the DB without bound, and declining the write costs a reparse and
# changes no output — which is exactly what a pure accelerator may do.
MAX_BLOB_BYTES = 8 * 1024 * 1024


# ── the stat key ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stamp:
    """What a stat-keyed row is keyed on: a log's size and mtime_ns, so an
    appended turn is always detected (ADR 0001 § the Derived Cache).

    `mtime` rides along, from the same `stat` — it is not part of the key, but
    it is what the caller dates its reading by, and a second `stat` to get it
    could disagree with the one the row was keyed on.
    """

    size: int
    mtime_ns: int
    mtime: datetime

    @classmethod
    def of(cls, path: Path | str) -> "Stamp | None":
        """The stamp of a file, or None when it cannot be stat'd — a log a view
        asked for by path may have been deleted under it, and an uncacheable
        read is still a read."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        return cls(st.st_size, st.st_mtime_ns,
                   datetime.fromtimestamp(st.st_mtime, tz=timezone.utc))


# ── codecs ──────────────────────────────────────────────────────────────────


def _to_json(data) -> str | None:
    try:
        return json.dumps(data)
    except (ValueError, TypeError):
        return None


def _from_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _compress(data) -> bytes | None:
    """A cache blob, or None when it cannot or should not be stored
    (see MAX_BLOB_BYTES)."""
    try:
        blob = zlib.compress(json.dumps(data).encode(), 6)
    except (ValueError, TypeError, zlib.error):
        return None
    return blob if len(blob) <= MAX_BLOB_BYTES else None


def _decompress(blob):
    """A stored blob back, or None when the row is unreadable (→ reparse)."""
    try:
        return json.loads(zlib.decompress(blob).decode())
    except (ValueError, TypeError, zlib.error, UnicodeDecodeError):
        return None


@dataclass(frozen=True)
class Codec:
    """How a row's value crosses the SQLite boundary. Both directions may
    answer None — "cannot or should not be stored", and "unreadable, reparse" —
    and neither is an error, because refusing a row changes no output."""

    column_type: str
    encode: Callable[[Any], Any | None]
    decode: Callable[[Any], Any | None]


TEXT_JSON = Codec("TEXT", _to_json, _from_json)
# zlib-compressed, for a reading that carries the literal text of every edit and
# every prompt, which compresses several-fold as source.
ZLIB_JSON = Codec("BLOB", _compress, _decompress)


# ── the declarations ────────────────────────────────────────────────────────


def _owned_version(module: str, attr: str) -> Callable[[], int]:
    """The version a derivation's *owner* keeps, read at query time.

    The cache holds no copy: a detector or reader that changes its output bumps
    its own constant and its rows fall out, with nothing here to keep in step.
    Imported lazily because those modules read the cache.
    """
    def read() -> int:
        return getattr(importlib.import_module(f".{module}", __package__), attr)
    return read


def _live(module: str, attr: str) -> Callable[[Path], set[str]]:
    """A derivation's liveness enumerator, resolved the same lazy way."""
    def keys(root: Path) -> set[str]:
        return getattr(importlib.import_module(f".{module}", __package__), attr)(root)
    return keys


@dataclass(frozen=True)
class Derivation:
    """One derived artifact the cache accelerates, declared whole.

    * `kind` — its name, and its table, whose DDL follows from the rest;
    * `codec` — how its value is stored, and its right to refuse a row;
    * `version_column`/`version` — what invalidates every row of it, read from
      the owning module at query time (`_owned_version`). A pair: declare both
      or neither;
    * `stat_keyed` — keyed on `(key, size, mtime_ns)` like a log, or on the key
      alone for something immutable by construction (a commit's file list);
    * `live_keys` — how its keys are *enumerated*, which is what `prune`
      deletes against. Declared here rather than passed in, because liveness is
      a rule about which files exist and a derivation keyed on something the
      Session sweep never produces (a subagent transcript, ADR 0002 § subagent
      usage) would otherwise have to be remembered somewhere else. None means
      the rows are never pruned.
    """

    kind: str
    codec: Codec = TEXT_JSON
    key_column: str = "session_id"
    data_column: str = "data"
    version_column: str | None = None
    version: Callable[[], int] | None = None
    stat_keyed: bool = True
    live_keys: Callable[[Path], set[str]] | None = None


SESSIONS = Derivation(
    kind="sessions",
    version_column="parser_version", version=lambda: PARSER_VERSION,
    live_keys=_live("claude_logs", "session_log_ids"),
)
# immutable by sha, so no version and no stat: a commit's file list cannot
# change under its own hash, and a sha nobody asks for again costs one row
COMMIT_FILES = Derivation(
    kind="commit_files", key_column="sha", data_column="files", stat_keyed=False,
)
LOOPS = Derivation(
    kind="loops",
    version_column="detector_version",
    version=_owned_version("loops", "DETECTOR_VERSION"),
    live_keys=_live("claude_logs", "session_log_ids"),
)
# the typed full reading (ADR 0001 § the one log reader) — the one derivation
# whose keys include logs the Session sweep never enumerates
LOGS = Derivation(
    kind="logs", codec=ZLIB_JSON,
    version_column="reader_version",
    version=_owned_version("claude_logs", "READER_VERSION"),
    live_keys=_live("claude_logs", "readable_log_ids"),
)

DERIVED: tuple[Derivation, ...] = (SESSIONS, COMMIT_FILES, LOOPS, LOGS)


class NullCache:
    """No-op fallback when the on-disk cache is unusable."""

    def derive(self, spec, key, stamp, compute, load=None, dump=None):
        return compute()

    def prune(self, projects_dir):
        pass

    def flush(self):
        pass


class Cache:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        # kind → key → (stamp, encoded value), applied in one transaction by
        # flush(). Read back before the DB is, so one command derives a value
        # once however many views ask for it.
        self._buffer: dict[str, dict[str, tuple[Stamp | None, Any]]] = {
            spec.kind: {} for spec in DERIVED}
        self._prune_root: Path | None = None

    # --- the one caller-side call ---------------------------------------

    def derive(self, spec: Derivation, key: str, stamp: Stamp | None,
               compute: Callable[[], Any],
               load: Callable[[Any], Any | None] | None = None,
               dump: Callable[[Any], Any | None] | None = None) -> Any:
        """The whole get/compute/put dance for one derived value.

        `load`/`dump` are the owning module's typed round-trip over the stored
        shape — omitted, the stored shape *is* the value. Either side may bow
        out and cost a recompute and nothing else: `load` returning None (or
        raising on a row it cannot read) means "unreadable row", `dump`
        returning None means "do not store this".
        """
        row = self._buffered(spec, key, stamp)
        if row is None:
            row = self._stored(spec, key, stamp)
        if row is not None:
            value = self._load(spec, row, load)
            if value is not None:
                return value
        value = compute()
        self._record(spec, key, stamp, value, dump)
        return value

    def _buffered(self, spec: Derivation, key: str, stamp: Stamp | None):
        hit = self._buffer[spec.kind].get(key)
        if hit is None:
            return None
        written, row = hit
        return row if written == stamp else None

    def _stored(self, spec: Derivation, key: str, stamp: Stamp | None):
        where, params = [f"{spec.key_column}=?"], [key]
        if spec.stat_keyed:
            if stamp is None:
                return None
            where += ["size=?", "mtime_ns=?"]
            params += [stamp.size, stamp.mtime_ns]
        if spec.version_column:
            where.append(f"{spec.version_column}=?")
            params.append(spec.version())
        try:
            row = self._conn.execute(
                f"SELECT {spec.data_column} FROM {spec.kind} "
                f"WHERE {' AND '.join(where)}", params).fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None

    @staticmethod
    def _load(spec: Derivation, row, load):
        stored = spec.codec.decode(row)
        if stored is None or load is None:
            return stored
        try:
            return load(stored)
        except (KeyError, TypeError, ValueError):
            return None      # a row this version cannot read costs a recompute

    def _record(self, spec: Derivation, key: str, stamp: Stamp | None,
                value, dump) -> None:
        if stamp is None and spec.stat_keyed:
            return               # nothing to key it on: the read was uncacheable
        stored = value if dump is None else dump(value)
        if stored is None:
            return
        encoded = spec.codec.encode(stored)
        if encoded is None:
            return
        self._buffer[spec.kind][key] = (stamp, encoded)

    # --- lifecycle ------------------------------------------------------

    def prune(self, projects_dir: Path | str) -> None:
        """Drop the rows of logs that are gone, each derivation against its own
        enumerator. A root that is not there enumerates nothing, which would
        read as "everything is dead" — so it prunes nothing instead."""
        root = Path(projects_dir)
        self._prune_root = root if root.is_dir() else None

    def flush(self) -> None:
        """Apply all buffered writes and pruning in one transaction, then close."""
        try:
            for spec in DERIVED:
                rows = self._buffer[spec.kind]
                if rows:
                    self._conn.executemany(
                        self._insert(spec),
                        [self._values(spec, key, stamp, row)
                         for key, (stamp, row) in rows.items()])
            self._apply_prune()
            self._conn.commit()
        except sqlite3.Error:
            pass
        finally:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    @staticmethod
    def _columns(spec: Derivation) -> list[str]:
        cols = [spec.key_column]
        if spec.stat_keyed:
            cols += ["size", "mtime_ns"]
        if spec.version_column:
            cols.append(spec.version_column)
        return cols + [spec.data_column]

    def _insert(self, spec: Derivation) -> str:
        cols = self._columns(spec)
        return (f"INSERT OR REPLACE INTO {spec.kind}({','.join(cols)}) "
                f"VALUES({','.join('?' * len(cols))})")

    @staticmethod
    def _values(spec: Derivation, key: str, stamp: Stamp | None, row) -> list:
        values: list = [key]
        if spec.stat_keyed:
            values += [stamp.size, stamp.mtime_ns]
        if spec.version_column:
            values.append(spec.version())
        return values + [row]

    def _apply_prune(self) -> None:
        if self._prune_root is None:
            return
        for spec in DERIVED:
            if spec.live_keys is None:
                continue
            live = spec.live_keys(self._prune_root)
            existing = {r[0] for r in
                        self._conn.execute(f"SELECT {spec.key_column} FROM {spec.kind}")}
            stale = existing - live
            if stale:
                self._conn.executemany(
                    f"DELETE FROM {spec.kind} WHERE {spec.key_column}=?",
                    [(k,) for k in stale])


def _ddl(spec: Derivation) -> str:
    """One declaration's table. Adding a derivation adds its table with it."""
    cols = [f"{spec.key_column} TEXT PRIMARY KEY"]
    if spec.stat_keyed:
        cols += ["size INTEGER NOT NULL", "mtime_ns INTEGER NOT NULL"]
    if spec.version_column:
        cols.append(f"{spec.version_column} INTEGER NOT NULL")
    cols.append(f"{spec.data_column} {spec.codec.column_type} NOT NULL")
    return f"CREATE TABLE IF NOT EXISTS {spec.kind} ({', '.join(cols)});"


def _init_schema(conn: sqlite3.Connection) -> None:
    """The declared schema, applied to any DB. Idempotent, and run on *every*
    open: a derivation declared since this DB was written then gains its table
    in place rather than costing a rebuild of the rows beside it.

    Write-free once the DB already matches, which is the common case — the
    `IF [NOT] EXISTS` clauses and the version stamp all no-op.
    """
    conn.executescript("\n".join(
        [_ddl(spec) for spec in DERIVED]
        # the fragment index is a projection of `logs` now (ADR 0007
        # § Decision); an upgraded DB drops the rows it no longer reads
        + ["DROP TABLE IF EXISTS fragments;"]))
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
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
            if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                # written by a newer Standup, whose columns this one may not
                # know: start over rather than degrade to a permanent miss
                raise sqlite3.DatabaseError("cache is from a newer schema")
            _init_schema(conn)
            # sanity-check every declared table is present and readable
            for spec in DERIVED:
                conn.execute(f"SELECT 1 FROM {spec.kind} LIMIT 1")
            return Cache(conn)
        except sqlite3.Error:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
    return NullCache()
