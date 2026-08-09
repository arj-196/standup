"""Scan ~/.claude/projects JSONL session logs.

Every session file is fully parsed (line-level prefiltering to extract edits,
captured commit hashes, and titles) and the result is cached in the Derived
Cache (ADR 0001 § the Derived Cache), keyed on (size, mtime_ns). Unchanged
files are served from the cache without being opened; only files that actually
changed are reparsed.

Parsing is no longer gated by a lookback horizon — the cache makes full-history
parsing cheap, and attribution is ageless (ADR 0001 § ageless attribution).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Session

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# `[branch abc1234]` / `[main (root-commit) abc1234]` / `[detached HEAD abc1234]`
COMMIT_LINE_RE = re.compile(r"^\[[^\[\]\n]{1,80} ([0-9a-f]{7,40})\]", re.MULTILINE)
# cheap hint on the raw JSON line (stdout newlines are escaped as \\n there)
COMMIT_HINT_RE = re.compile(r"\[[^\]\n]{1,80} [0-9a-f]{7,40}\]")


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def title_hint(line: str) -> bool:
    """Does this raw JSONL line plausibly carry one of a Session's title fields?

    The cheap prefilter that lets a scanner skip `json.loads` on the ~99% of
    lines that hold no title. Paired with `apply_title_fields`, this is the one
    place that knows the log's title schema: `cost` runs a second scanner (it
    reads per-turn `usage`, which this one does not), and when it carried its
    own copy of the pair the two drifted — a mistyped prefilter there silently
    demoted every session title to its last prompt.
    """
    return '"custom-title"' in line or '"ai-title"' in line or '"last-prompt"' in line


def apply_title_fields(session: Session, obj: dict) -> None:
    """Fold a parsed line's title fields into the Session, honouring precedence.

    Later lines win (a session retitled mid-run keeps the newer name), but a
    field is never overwritten with an empty one. `slug` rides on ordinary
    lines rather than a type of its own, so it is picked up opportunistically
    from whatever lines the prefilter already admitted — never by widening the
    prefilter to every line that mentions it.
    """
    etype = obj.get("type")
    if etype == "custom-title":
        session.custom_title = obj.get("customTitle") or session.custom_title
    elif etype == "ai-title":
        session.ai_title = obj.get("aiTitle") or session.ai_title
    elif etype == "last-prompt":
        session.last_prompt = obj.get("lastPrompt") or session.last_prompt
    if obj.get("slug"):
        session.slug = obj["slug"]


def _interesting(line: str) -> bool:
    if '"tool_use"' in line and any(f'"{t}"' in line for t in EDIT_TOOLS):
        return True
    if title_hint(line):
        return True
    if '"toolUseResult"' in line and COMMIT_HINT_RE.search(line):
        return True
    return False


def _extract_edits(session: Session, obj: dict, ts: datetime | None) -> None:
    message = obj.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        if item.get("name") not in EDIT_TOOLS:
            continue
        inp = item.get("input") or {}
        fp = inp.get("file_path") or inp.get("notebook_path")
        if not fp or not os.path.isabs(fp):
            continue
        prev = session.edited_files.get(fp)
        if ts and (prev is None or ts > prev):
            session.edited_files[fp] = ts
        elif prev is None and ts is None:
            session.edited_files[fp] = datetime.now(timezone.utc)


def _extract_commits(session: Session, obj: dict, ts: datetime | None) -> None:
    tr = obj.get("toolUseResult")
    if tr is None:
        return
    text = tr if isinstance(tr, str) else json.dumps(tr) if not isinstance(tr, dict) else (tr.get("stdout") or "")
    for m in COMMIT_LINE_RE.finditer(text):
        sha = m.group(1)
        when = ts or datetime.now(timezone.utc)
        if sha not in session.commit_hashes or when > session.commit_hashes[sha]:
            session.commit_hashes[sha] = when


def _full_scan(session: Session, path: Path) -> None:
    with open(path, errors="replace") as f:
        for line in f:
            if session.cwd is None and '"cwd"' in line:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("cwd"):
                    session.cwd = obj["cwd"]
                if obj.get("gitBranch"):
                    session.branches.add(obj["gitBranch"])
                if not _interesting(line):
                    continue
            elif not _interesting(line):
                continue
            else:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

            ts = _parse_ts(obj.get("timestamp"))
            etype = obj.get("type")
            apply_title_fields(session, obj)
            if obj.get("gitBranch"):
                session.branches.add(obj["gitBranch"])
            if etype == "assistant":
                _extract_edits(session, obj, ts)
            elif etype == "user":
                _extract_commits(session, obj, ts)


def _to_cache(s: Session) -> dict:
    return {
        "cwd": s.cwd,
        "custom_title": s.custom_title,
        "ai_title": s.ai_title,
        "slug": s.slug,
        "last_prompt": s.last_prompt,
        "branches": sorted(s.branches),
        "edited_files": {p: t.isoformat() for p, t in s.edited_files.items()},
        "commit_hashes": {h: t.isoformat() for h, t in s.commit_hashes.items()},
    }


def _from_cache(session_id: str, log_path: str, mtime: datetime, d: dict) -> Session:
    s = Session(session_id=session_id, log_path=log_path, last_activity=mtime)
    s.cwd = d.get("cwd")
    s.custom_title = d.get("custom_title")
    s.ai_title = d.get("ai_title")
    s.slug = d.get("slug")
    s.last_prompt = d.get("last_prompt")
    s.branches = set(d.get("branches") or [])
    s.edited_files = {p: datetime.fromisoformat(t)
                      for p, t in (d.get("edited_files") or {}).items()}
    s.commit_hashes = {h: datetime.fromisoformat(t)
                       for h, t in (d.get("commit_hashes") or {}).items()}
    return s


def scan_sessions(projects_dir: Path, cache) -> list[Session]:
    """Fully parse every session file, serving unchanged ones from the cache."""
    sessions: list[Session] = []
    live_ids: set[str] = set()
    for log in sorted(projects_dir.glob("*/*.jsonl")):
        try:
            st = log.stat()
        except OSError:
            continue
        sid = log.stem
        live_ids.add(sid)
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        cached = cache.get_session(sid, st.st_size, st.st_mtime_ns)
        if cached is not None:
            session = _from_cache(sid, str(log), mtime, cached)
        else:
            session = Session(session_id=sid, log_path=str(log), last_activity=mtime)
            _full_scan(session, log)
            cache.put_session(sid, st.st_size, st.st_mtime_ns, _to_cache(session))
        if session.cwd:
            sessions.append(session)
    cache.prune(live_ids)
    return sessions
