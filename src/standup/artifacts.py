"""The Artifact store (ADR 0003 § the Artifact store).

A **Session Brief** and an **Audit** are the same object on disk: an
LLM-authored claim about one Session, produced out of band, kept in the durable
`~/.standup` root as markdown behind a small frontmatter block, written
atomically, *staled* rather than refreshed, and pruned when its session log
dies. That shape lives here once. `brief.py` and `audit.py` are its two
adapters and own only what differs — which frontmatter fields their kind
carries, and how it renders.

Nothing in this module runs a model or knows what a Brief means; the read side
is pure filesystem work, which is what keeps the render path deterministic
(ADR 0003 § the shared model).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Protocol

# The durable root. Read at *call* time, never captured in a default argument,
# so a test (or a future `--root`) can rebind it in one place instead of once
# per artifact kind.
ROOT = Path(os.path.expanduser("~/.standup"))

# One number, two jobs (ADR 0003 § the Artifact store): the generator refuses to
# rewrite an artifact younger than this, so an artifact may lag its session by
# at most this much — which is exactly the point past which a reader must hedge.
# Held together by identity here, not by a comment in two modules.
TOLERANCE = timedelta(minutes=5)


class Claim(Protocol):
    """What staleness stamping needs of an artifact: when it was generated, and
    somewhere to record that its Session has moved on."""
    generated: datetime | None
    stale: bool


# ── frontmatter ────────────────────────────────────────────────────────────

class Frontmatter:
    """A parsed `--- ... ---` block: `key: value` lines, read by type.

    Stdlib only — the project has no third-party deps, and a YAML dependency
    would buy nothing for a handful of scalar keys. Every accessor answers
    `None`/`0`/`[]` rather than raising: an artifact is hand-editable, and a
    reader that crashes on a typo is worse than one that shows less.
    """

    def __init__(self, fields: Mapping[str, str] | None = None) -> None:
        self._fields: dict[str, str] = dict(fields or {})

    def text(self, key: str) -> str | None:
        return (self._fields.get(key) or "").strip() or None

    def timestamp(self, key: str) -> datetime | None:
        raw = self.text(key)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def count(self, key: str, default: int = 0) -> int:
        raw = self.text(key)
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    def mapping(self, key: str) -> dict | None:
        value = self._json(key)
        return value if isinstance(value, dict) else None

    def records(self, key: str) -> list[dict]:
        value = self._json(key)
        return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []

    def _json(self, key: str):
        raw = self.text(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None


@dataclass(frozen=True)
class Document:
    """One artifact as read: its frontmatter and its markdown body."""
    frontmatter: Frontmatter
    body: str          # the markdown beneath the block, stripped of its separator


def _split(text: str) -> Document:
    """Split a leading `--- ... ---` block off the body.

    Anything the parser does not understand is ignored rather than fatal, and a
    file with no frontmatter at all is all body.
    """
    if not text.startswith("---"):
        return Document(Frontmatter(), text.strip())
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return Document(Frontmatter(), text.strip())
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, val = line.partition(":")
        if not sep:
            continue
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            fields[key] = val
    return Document(Frontmatter(fields), "\n".join(lines[end + 1:]).strip())


def _render(fields: Mapping[str, object]) -> str:
    """The write side of the frontmatter contract, inverse of `_split`.

    `None` values are omitted — an absent key and an empty one read the same,
    and omitting keeps hand-inspection honest. Timestamps go out ISO, objects
    and lists as one line of compact JSON (a frontmatter value is one line).
    """
    out = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, datetime):
            rendered = value.isoformat()
        elif isinstance(value, (dict, list)):
            rendered = json.dumps(value, separators=(",", ":"))
        else:
            rendered = str(value)
        out.append(f"{key}: {rendered}")
    out.append("---")
    return "\n".join(out)


# ── the store ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Store:
    """One kind of Artifact, on disk: `~/.standup/<dirname>/<sessionId><suffix>`.

    Keyed by the Session it describes, which is what makes orphan pruning and
    staleness answerable without an index.
    """

    dirname: str
    suffix: str

    @property
    def dir(self) -> Path:
        return ROOT / self.dirname

    def path_for(self, session_id: str) -> Path:
        return self.dir / f"{session_id}{self.suffix}"

    def lock_for(self, session_id: str) -> Path:
        """Path a generator may take exclusively while it works, beside the
        artifact it is about to write.

        The directory is made here, not at write time: the lock is the *first*
        thing a generation touches, so on a machine that has produced no
        artifact yet an absent directory would fail every run before any write
        could create it. Best-effort — a mkdir that fails leaves the caller's
        `O_EXCL` open to report the problem.
        """
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        path = self.path_for(session_id)
        return path.with_name(path.name + ".lock")

    def read(self, session_id: str) -> Document | None:
        """The artifact as frontmatter + body, or None if absent/unreadable.
        A missing artifact is never an error (ADR 0003 § the shared model)."""
        try:
            return _split(self.path_for(session_id).read_text(errors="replace"))
        except OSError:
            return None

    def write(self, session_id: str, fields: Mapping[str, object], body: str) -> Path:
        """Write atomically: a reader racing a generator sees the old artifact
        or the new one, never half a frontmatter block. The scratch file is
        per-process, so two generators racing cannot interleave into one."""
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(_render(fields) + "\n\n" + body.strip() + "\n")
        tmp.replace(path)
        return path

    def written_within_tolerance(self, session_id: str) -> bool:
        """The generation debounce: an artifact this young is current enough
        that regenerating would buy nothing a reader could see
        (ADR 0003 § the Artifact store).

        Reads the artifact's *mtime* against wall-clock now — deliberately not
        the `generated` field against the log, which is the reader's question:
        the debounce must answer before the file is parsed, and its job is only
        to rate-limit the generator. The two meet at the one tolerance, so an
        artifact the generator declines to rewrite is never one a reader hedges.
        """
        try:
            written = datetime.fromtimestamp(
                self.path_for(session_id).stat().st_mtime, tz=timezone.utc)
        except OSError:
            return False
        return (datetime.now(timezone.utc) - written) < TOLERANCE

    def prune_orphans(self, live_ids: Iterable[str]) -> None:
        """Delete artifacts whose Session log is gone — an orphan goes the way
        of a cache row (ADR 0003 § the shared model). Best-effort, never raises:
        a failed unlink costs disk, not correctness."""
        live = set(live_ids)
        try:
            entries = list(self.dir.glob(f"*{self.suffix}"))
        except OSError:
            return
        for path in entries:
            if path.name[: -len(self.suffix)] not in live:
                try:
                    path.unlink()
                except OSError:
                    pass


# ── staleness ──────────────────────────────────────────────────────────────

def _utc(when: datetime) -> datetime:
    """A naive timestamp is read as UTC. Every writer here stamps UTC, so a
    naive one came from a hand edit that dropped the offset; assuming UTC keeps
    the comparison answerable, where refusing it would leave a hand-edited
    artifact permanently unhedged."""
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def log_advanced_past(generated: datetime | None, log_path: str | Path | None) -> bool:
    """Did the Session advance past the claim?

    The clock is chosen *here*, not by the caller: the session log's mtime, on
    every surface (ADR 0003 § the shared model). "Did the file grow after the
    artifact was written?" is the literal question staleness asks, and a
    view-local clock always answers a narrower one — which is how the same Brief
    would come to render `(stale)` in one view and unhedged in another.

    Exactly at TOLERANCE is not yet stale: the generator is allowed to lag by
    that much, so hedging there would hedge every artifact it wrote on time.
    """
    if generated is None:
        return False
    try:
        mtime = datetime.fromtimestamp(Path(log_path).stat().st_mtime, tz=timezone.utc)
    except (OSError, TypeError, ValueError):
        return False  # no log to compare against: nothing says the claim moved
    return mtime > _utc(generated) + TOLERANCE


def stamp_staleness(artifact: Claim | None, log_path: str | Path | None) -> None:
    """Hedge an artifact whose Session continued past it. Never *un*-stales:
    staleness is one-way, since nothing regenerates at render time."""
    if artifact is not None and log_advanced_past(artifact.generated, log_path):
        artifact.stale = True
