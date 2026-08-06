"""Session Brief generator (ADR 0003 § the Session Brief) — the
`standup _brief` code path.

Invoked by the Claude Code Stop hook (installed via `standup install`), NOT by
the interactive `standup` display command. It reads the hook's stdin JSON and,
if the session is worth summarising, spawns a detached background job that runs
`claude -p` (Haiku) over a digest of the transcript and writes the Brief to
~/.standup/briefs/<sessionId>.brief.md.

Design (ADR 0003 § the Session Brief):
- **out-of-band**: double-forks so the interactive turn is never blocked, even
  if the hook's `async: true` is unsupported by the running Claude Code;
- **gated**: only sessions with a code footprint (an Edit/Write) get a Brief;
- **debounced**: a freshness check + a lockfile keep it to ~once per idle gap;
- **turnkey**: `claude -p` reuses the user's existing Claude Code auth — no API
  key, no `--bare` (verified 2026-07-22, CC 2.1.201);
- **cost-tracked**: `--output-format json` returns the run's own `usage`, which
  is recorded in the Brief as Brief Overhead and priced by the Rate Card.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import transcript
from .brief import BRIEFS_DIR
from .claude_logs import EDIT_TOOLS

MODEL = "claude-haiku-4-5"
DEBOUNCE = timedelta(minutes=5)   # regenerate at most this often per session
GEN_TIMEOUT = 120                 # seconds for the claude -p call
MAX_DIGEST = 60_000               # chars of transcript fed to the summariser
HEAD_DIGEST = 40_000              # when over budget: keep this much head + the rest tail

PROMPT = (
    "You are summarising a Claude Code coding session for a triage tool. "
    "Below is a digest of the session (the user's prompts and the assistant's "
    "actions). Identify the session's PRIMARY objective — what it set out to "
    "achieve — as one short declarative line. If several objectives ran, pick "
    "the dominant through-line. Then judge its status.\n\n"
    "Reply in EXACTLY this format, nothing else:\n"
    "OBJECTIVE: <one line, no trailing period, <= 90 chars>\n"
    "STATUS: <one of: done | in-progress | blocked | abandoned>\n"
    "SUMMARY:\n"
    "- <what happened, one bullet>\n"
    "- <optional further bullets / secondary objectives>\n"
)


# ── gating & debounce ──────────────────────────────────────────────────────

def _has_footprint(path: Path) -> bool:
    """Cheap, LLM-free check: did the session make an Edit/Write? Matches the
    population standup actually displays (footprint sessions)."""
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if '"tool_use"' in line and any(f'"{t}"' in line for t in EDIT_TOOLS):
                    return True
    except OSError:
        return False
    return False


def _fresh(brief_path: Path, now: datetime) -> bool:
    """True if a Brief was written within DEBOUNCE — skip to rate-limit."""
    try:
        mtime = datetime.fromtimestamp(brief_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    return (now - mtime) < DEBOUNCE


# ── transcript digest ──────────────────────────────────────────────────────

def _digest(path: Path) -> str:
    """A compact plain-text rendering of the session for the summariser, reusing
    transcript.py's extraction. Capped; when over budget, keep the head (where the
    objective is usually set) plus the tail (where it landed)."""
    parts: list[str] = []
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = obj.get("type")
                msg = obj.get("message") or {}
                if etype == "user" and not obj.get("isMeta"):
                    text = transcript._user_text(msg.get("content"))
                    if text:
                        parts.append("USER: " + text)
                elif etype == "assistant":
                    texts, tools = transcript._assistant_parts(msg.get("content"), show_thinking=False)
                    for t in texts:
                        parts.append("CLAUDE: " + t)
                    for tl, _looped in tools:
                        parts.append("  · " + tl)
    except OSError:
        return ""
    text = "\n".join(parts)
    if len(text) > MAX_DIGEST:
        text = text[:HEAD_DIGEST] + "\n…\n" + text[-(MAX_DIGEST - HEAD_DIGEST):]
    return text


def _parse_output(text: str) -> tuple[str, str | None, str]:
    """Parse the OBJECTIVE/STATUS/SUMMARY reply into (objective, status, body)."""
    objective, status, body_lines = "", None, []
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        upper = line.strip().upper()
        if upper.startswith("OBJECTIVE:"):
            objective = line.split(":", 1)[1].strip()
            section = None
        elif upper.startswith("STATUS:"):
            val = line.split(":", 1)[1].strip().lower()
            status = val if val in ("done", "in-progress", "blocked", "abandoned") else None
            section = None
        elif upper.startswith("SUMMARY:"):
            section = "summary"
        elif section == "summary" and line.strip():
            body_lines.append(line)
    return objective, status, "\n".join(body_lines).strip()


# ── the write ──────────────────────────────────────────────────────────────

def _write_brief(brief_path: Path, objective: str, status: str | None,
                 body: str, usage: dict | None, now: datetime) -> None:
    fm = [
        "---",
        f"objective: {objective}",
    ]
    if status:
        fm.append(f"status: {status}")
    fm.append(f"generated: {now.isoformat()}")
    fm.append(f"model: {MODEL}")
    if isinstance(usage, dict):
        fm.append("gen_usage: " + json.dumps(usage, separators=(",", ":")))
    fm.append("---")
    text = "\n".join(fm) + "\n\n" + (body or "") + "\n"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = brief_path.with_name(brief_path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(brief_path)  # atomic


def _generate(session_id: str, transcript: Path, cwd: str | None,
              brief_path: Path) -> None:
    lock = brief_path.with_name(brief_path.name + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        # a stale lock (older than a generation timeout) may be stolen
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                lock.stat().st_mtime, tz=timezone.utc)
            if age.total_seconds() < GEN_TIMEOUT * 2:
                return  # another generation is in flight
        except OSError:
            return
    except OSError:
        return

    try:
        digest = _digest(transcript)
        if not digest.strip():
            return
        proc = subprocess.run(
            ["claude", "-p", PROMPT, "--model", MODEL,
             "--output-format", "json", "--no-session-persistence"],
            input=digest, capture_output=True, text=True,
            cwd=cwd or None, timeout=GEN_TIMEOUT,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return
        try:
            data = json.loads(proc.stdout)
        except (ValueError, TypeError):
            return
        objective, status, body = _parse_output(data.get("result", ""))
        if not objective:
            return
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        _write_brief(brief_path, objective, status, body, usage,
                     datetime.now(timezone.utc))
    except (subprocess.SubprocessError, OSError):
        return
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


# ── entry point (called by cli for `standup _brief`) ───────────────────────

def _detach() -> bool:
    """Double-fork so the heavy work outlives the hook. Returns True only in the
    fully detached grandchild; the original process gets False and must exit."""
    try:
        if os.fork() > 0:
            return False
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
    except OSError:
        return True  # fork unavailable: just run inline rather than lose the Brief
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        try:
            os.dup2(devnull, fd)
        except OSError:
            pass
    return True


def run_from_hook_stdin() -> int:
    """`standup _brief`: read the Stop hook's JSON from stdin and maybe generate.
    Always returns 0 fast — a summariser problem must never break a session."""
    try:
        data = json.load(sys.stdin)
    except (ValueError, TypeError):
        return 0
    session_id = data.get("session_id")
    transcript = data.get("transcript_path")
    cwd = data.get("cwd")
    if not session_id or not transcript:
        return 0
    tpath = Path(transcript)
    if not tpath.exists() or not _has_footprint(tpath):
        return 0

    brief_path = BRIEFS_DIR / f"{session_id}.brief.md"
    now = datetime.now(timezone.utc)
    if _fresh(brief_path, now):
        return 0  # debounce

    if not _detach():
        return 0  # original process returns immediately; grandchild does the work
    try:
        _generate(session_id, tpath, cwd, brief_path)
    finally:
        os._exit(0)
