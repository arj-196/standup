"""Scan ~/.claude/projects JSONL session logs.

Two-speed scan:
- every session file gets a cheap header parse (first lines) to learn its cwd,
  which seeds the Scan Universe (ADR 0001);
- files modified within the lookback horizon get a full parse with line-level
  prefiltering to extract edits, captured commit hashes, and titles.
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


def _interesting(line: str) -> bool:
    if '"tool_use"' in line and any(f'"{t}"' in line for t in EDIT_TOOLS):
        return True
    if '"custom-title"' in line or '"ai-title"' in line or '"last-prompt"' in line:
        return True
    if '"toolUseResult"' in line and COMMIT_HINT_RE.search(line):
        return True
    return False


def _header_scan(session: Session, path: Path) -> None:
    """Read the first lines only, to learn cwd (repo discovery)."""
    with open(path, errors="replace") as f:
        for _ in range(25):
            line = f.readline()
            if not line:
                return
            if '"cwd"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cwd = obj.get("cwd")
            if cwd:
                session.cwd = cwd
                return


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
            if etype == "custom-title":
                session.custom_title = obj.get("customTitle") or session.custom_title
            elif etype == "ai-title":
                session.ai_title = obj.get("aiTitle") or session.ai_title
            elif etype == "last-prompt":
                session.last_prompt = obj.get("lastPrompt") or session.last_prompt
            if obj.get("slug"):
                session.slug = obj["slug"]
            if obj.get("gitBranch"):
                session.branches.add(obj["gitBranch"])
            if etype == "assistant":
                _extract_edits(session, obj, ts)
            elif etype == "user":
                _extract_commits(session, obj, ts)


def scan_sessions(projects_dir: Path, horizon: datetime) -> list[Session]:
    """Return all sessions; ones with recent mtime are fully parsed."""
    sessions: list[Session] = []
    for log in sorted(projects_dir.glob("*/*.jsonl")):
        try:
            mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        session = Session(session_id=log.stem, log_path=str(log), last_activity=mtime)
        if mtime >= horizon:
            _full_scan(session, log)
        else:
            _header_scan(session, log)
        if session.cwd:
            sessions.append(session)
    return sessions
