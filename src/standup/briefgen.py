"""Session Brief generator (ADR 0003 § the Session Brief) — the
`standup _brief` code path.

Invoked by the Claude Code Stop hook (installed via `standup install`), NOT by
the interactive `standup` display command. It reads the hook's stdin JSON and,
if the session is worth summarising, spawns a detached background job that runs
one Brief pass over a digest of the transcript and writes the Brief to
~/.standup/briefs/<sessionId>.brief.md.

Design (ADR 0003 § the Session Brief):
- **out-of-band**: double-forks so the interactive turn is never blocked, even
  if the hook's `async: true` is unsupported by the running Claude Code;
- **gated**: only sessions with a code footprint (an Edit/Write) get a Brief;
- **debounced**: the store's freshness check + a lockfile keep it to ~once per
  idle gap, on the one tolerance a reader hedges past;
- **turnkey**: the pass reuses the user's existing Claude Code auth — no API
  key (verified 2026-07-22, CC 2.1.201);
- **cost-tracked**: the pass reports its own `usage`, which is recorded in the
  Brief as Brief Overhead and priced by the Rate Card.

Three things this module does *not* own. The location, frontmatter and atomic
write are the Artifact store's (`artifacts.py`), reached through `brief.save`.
The digest is `transcript.digest` — one rendering behind both artifacts'
generators (ADR 0003 § the LLM-pass seam). Reaching a model at all is
`llmpass`'s, which owns the empty-result and timeout rules. What is left here is
the decision to generate, the prompt, and the reply format.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import brief, llmpass, transcript
from .claude_logs import EDIT_TOOLS
from .models import Brief

MODEL = "claude-haiku-4-5"
GEN_TIMEOUT = 120                 # seconds for the Brief pass
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

STATUSES = ("done", "in-progress", "blocked", "abandoned")


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


def _take_lock(lock: Path) -> bool:
    """Claim the generation lock, or decline. A lock older than two generation
    timeouts is stale and may be stolen; anything younger means a generation is
    genuinely in flight (ADR 0003 § the Artifact store)."""
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                lock.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return False
        return age.total_seconds() >= GEN_TIMEOUT * 2
    except OSError:
        return False


# ── the reply format ───────────────────────────────────────────────────────

def parse_output(text: str) -> tuple[str, str | None, str]:
    """Parse the OBJECTIVE/STATUS/SUMMARY reply into (objective, status, body).

    Read leniently — casing and surrounding prose are not the claim — but never
    invented: a status outside `STATUSES` is no status at all, and an
    objective-less reply yields `""`, which the caller stores as nothing.
    """
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
            status = val if val in STATUSES else None
            section = None
        elif upper.startswith("SUMMARY:"):
            section = "summary"
        elif section == "summary" and line.strip():
            body_lines.append(line)
    return objective, status, "\n".join(body_lines).strip()


# ── the write ──────────────────────────────────────────────────────────────

def generate(session_id: str, log_path: Path, cwd: str | None = None,
             transport: llmpass.Transport | None = None) -> Path | None:
    """Lock, digest, one Brief pass, parse, store; return the Brief's path.

    `None` means there was nothing to claim — a generation already in flight, an
    unreadable session, a failed pass, or a reply with no objective. None of
    those is an error: a summariser problem must never break the session it
    summarises, and a Session with no Brief renders title-only.
    """
    lock = brief.STORE.lock_for(session_id)
    if not _take_lock(lock):
        return None
    try:
        digest = transcript.digest(log_path, max_chars=MAX_DIGEST,
                                   head_chars=HEAD_DIGEST)
        if not digest.strip():
            return None
        try:
            res = llmpass.run(llmpass.Pass(
                label="brief", model=MODEL, prompt=PROMPT, context=digest,
                cwd=cwd, timeout=GEN_TIMEOUT), transport)
        except llmpass.PassError:
            return None
        objective, status, body = parse_output(res.text)
        if not objective:
            return None
        return brief.save(Brief(session_id=session_id, objective=objective,
                                status=status, generated=datetime.now(timezone.utc),
                                model=res.model, body=body, gen_usage=res.usage))
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
    log = data.get("transcript_path")
    cwd = data.get("cwd")
    if not session_id or not log:
        return 0
    log_path = Path(log)
    if not log_path.exists() or not _has_footprint(log_path):
        return 0

    if brief.STORE.written_within_tolerance(session_id):
        return 0  # debounce

    if not _detach():
        return 0  # original process returns immediately; grandchild does the work
    try:
        generate(session_id, log_path, cwd)
    finally:
        os._exit(0)
