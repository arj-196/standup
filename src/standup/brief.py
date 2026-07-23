"""Session Brief reader (ADR 0006).

A Session Brief is an LLM-authored, out-of-band account of a Session's objective,
written *during* the session by a Claude Code Stop hook (installed via
`standup install`). Standup only ever *reads* it here — generation lives in the
separate `_brief` code path — so rendering stays deterministic and the
"never LLM-generated at render time" rule (CONTEXT.md → Resume) holds.

Briefs live in the *durable* root of ~/.standup (never the disposable `cache/`,
see cache.py), keyed by the session they describe:

    ~/.standup/briefs/<sessionId>.brief.md

Format is markdown with a small frontmatter contract, parsed with the stdlib
only (the project has no third-party deps):

    ---
    objective: <one declarative line — what the session set out to do>
    status: in-progress        # done | in-progress | blocked | abandoned
    generated: 2026-07-22T21:40:00Z
    model: claude-haiku-4-5
    ---
    <freeform body — shown only in `standup show`>

A missing, unreadable, or objective-less Brief is never an error: the Session
simply renders title-only, exactly as before Briefs existed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import rates
from .models import Brief, Session

BRIEFS_DIR = Path(os.path.expanduser("~/.standup")) / "briefs"

# A Brief generated within this slack of the log's last activity is treated as
# current; beyond it the session advanced past the Brief, so it is marked stale.
# Mirrors the hook's debounce tolerance (ADR 0006).
STALE_TOLERANCE = timedelta(minutes=5)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError:
        return None


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `--- ... ---` block into {key: value} + body.

    Stdlib only — no YAML dependency. Parses simple `key: value` lines and
    strips surrounding quotes; anything it doesn't understand is ignored rather
    than fatal, so a hand-edited Brief never crashes the reader.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, val = line.partition(":")
        if not sep:
            continue
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            fm[key] = val
    return fm, "\n".join(lines[end + 1:])


def _parse(path: Path, session_id: str) -> Brief | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    fm, body = _split_frontmatter(text)
    objective = (fm.get("objective") or "").strip()
    if not objective:
        return None  # nothing worth showing
    gen_usage = None
    if fm.get("gen_usage"):
        try:
            parsed = json.loads(fm["gen_usage"])
            if isinstance(parsed, dict):
                gen_usage = parsed
        except (ValueError, TypeError):
            gen_usage = None
    return Brief(
        session_id=session_id,
        objective=objective,
        status=(fm.get("status") or "").strip() or None,
        generated=_parse_ts(fm.get("generated")),
        model=(fm.get("model") or "").strip() or None,
        body=body.strip(),
        gen_usage=gen_usage,
    )


def overhead_cost(brief: Brief) -> float:
    """Notional Cost of generating this Brief (Brief Overhead, ADR 0006), priced
    from the recorded generation usage by the Rate Card. 0.0 if unknown/unpriced.
    """
    if not brief.gen_usage:
        return 0.0
    return rates.turn_cost(brief.model, brief.gen_usage) or 0.0


def load_one(session_id: str) -> Brief | None:
    """Read a single Session's Brief by id, or None if absent/unreadable."""
    path = BRIEFS_DIR / f"{session_id}.brief.md"
    if not path.exists():
        return None
    return _parse(path, session_id)


def load_for_sessions(sessions: list[Session]) -> dict[str, Brief]:
    """Attach each Session's Brief (if any) and stamp staleness against the
    Session's last activity. Returns {session_id: Brief} for the ones found.
    """
    briefs: dict[str, Brief] = {}
    for s in sessions:
        path = BRIEFS_DIR / f"{s.session_id}.brief.md"
        if not path.exists():
            continue
        b = _parse(path, s.session_id)
        if b is None:
            continue
        if b.generated and s.last_activity and s.last_activity > b.generated + STALE_TOLERANCE:
            b.stale = True
        s.brief = b
        briefs[s.session_id] = b
    return briefs


def prune_orphans(live_ids: set[str]) -> None:
    """Delete Brief files whose Session log no longer exists — the Brief analogue
    of the cache's prune(live_ids). Best-effort; never raises.
    """
    try:
        entries = list(BRIEFS_DIR.glob("*.brief.md"))
    except OSError:
        return
    for p in entries:
        sid = p.name[: -len(".brief.md")]
        if sid not in live_ids:
            try:
                p.unlink()
            except OSError:
                pass
