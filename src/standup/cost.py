"""Notional Cost aggregation: per-Session, grouped per-project (Repo Entry).

Spans *all* sessions (any git footprint or none) — a wider population than the
Triage Inbox. Groups by Repo Entry identity (worktrees fold into their parent
checkout), falling back to the raw cwd for sessions whose dir is not a git repo.

Stateless: recomputed from the logs each run. The window (default: the current
calendar month) bounds which files are read by mtime; individual turns are then
filtered by their own timestamp so sessions straddling the edge count exactly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gitstate, rates
from .models import Session

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
BUCKETS = ("input", "output", "cache_write", "cache_read")


@dataclass
class SessionCost:
    session: Session
    cost: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=lambda: {b: 0 for b in BUCKETS})
    turns: int = 0
    unpriced_turns: int = 0
    # above-floor Loops (ADR 0007): free, derived, attached by attach_loops.
    # Loop Cost is a carve-out of this session's Notional Cost, never a saving.
    loops: list = field(default_factory=list)

    @property
    def loop_cost(self) -> float:
        return sum(l.cost for l in self.loops)

    @property
    def title(self) -> str:
        return self.session.title

    @property
    def handle(self) -> str:
        return self.session.session_id[:8]

    @property
    def dominant_model(self) -> str | None:
        return max(self.by_model, key=self.by_model.get) if self.by_model else None

    @property
    def why(self) -> str:
        """One-word hint at what makes this session heavy."""
        total_tok = sum(self.tokens.values()) or 1
        if self.cost and self.by_model.get("claude-fable-5", 0) / self.cost > 0.6:
            return "fable"
        if self.tokens["cache_read"] / total_tok > 0.75:
            return "cache-heavy"
        # output tokens are the priciest per-token, so weight by a rough 5x
        if self.tokens["output"] * 5 > (self.tokens["input"] + self.tokens["cache_write"]):
            return "out-heavy"
        return ""


@dataclass
class ProjectCost:
    key: str
    name: str
    path: str
    sessions: list[SessionCost] = field(default_factory=list)
    # Brief Overhead (ADR 0006): Notional Cost of generating this project's
    # Session Briefs, kept separate from `cost` so it is never folded in silently.
    brief_overhead: float = 0.0
    brief_count: int = 0

    @property
    def cost(self) -> float:
        return sum(s.cost for s in self.sessions)

    @property
    def by_model(self) -> dict[str, float]:
        merged: dict[str, float] = {}
        for s in self.sessions:
            for m, c in s.by_model.items():
                merged[m] = merged.get(m, 0.0) + c
        return merged

    @property
    def last_activity(self) -> datetime | None:
        times = [s.session.last_activity for s in self.sessions if s.session.last_activity]
        return max(times) if times else None


def _short_title_fields(session: Session, obj: dict, etype: str) -> None:
    if etype == "custom-title":
        session.custom_title = obj.get("customTitle") or session.custom_title
    elif etype == "ai-title":
        session.ai_title = obj.get("aiTitle") or session.ai_title
    elif etype == "last-prompt":
        session.last_prompt = obj.get("lastPrompt") or session.last_prompt
    if obj.get("slug"):
        session.slug = obj["slug"]


def _scan_file(path: Path, window_start: datetime) -> SessionCost | None:
    session = Session(session_id=path.stem, log_path=str(path))
    sc = SessionCost(session=session)
    latest: datetime | None = None
    with open(path, errors="replace") as fh:
        for line in fh:
            has_usage = '"usage"' in line
            has_meta = ('"cwd"' in line or '"-title"' in line
                        or '"slug"' in line or '"lastPrompt"' in line)
            if not (has_usage or has_meta):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if session.cwd is None and obj.get("cwd"):
                session.cwd = obj["cwd"]
            _short_title_fields(session, obj, obj.get("type", ""))
            if not has_usage or obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            ts = _parse_ts(obj.get("timestamp"))
            if ts and ts < window_start:
                continue
            if ts and (latest is None or ts > latest):
                latest = ts
            model = msg.get("model")
            c = rates.turn_cost(model, u)
            if c is None:
                sc.unpriced_turns += 1
                continue
            sc.turns += 1
            sc.cost += c
            sc.by_model[model] = sc.by_model.get(model, 0.0) + c
            for b, n in rates.turn_tokens(u).items():
                sc.tokens[b] += n
    session.last_activity = latest
    if sc.turns == 0 and sc.unpriced_turns == 0:
        return None
    return sc


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def scan_session_costs(projects_dir: Path, window_start: datetime) -> list[SessionCost]:
    out: list[SessionCost] = []
    for log in sorted(projects_dir.glob("*/*.jsonl")):
        try:
            mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < window_start:
            continue
        sc = _scan_file(log, window_start)
        if sc and sc.session.cwd:
            out.append(sc)
    return out


def group_by_project(session_costs: list[SessionCost]) -> list[ProjectCost]:
    """Bucket sessions by Repo Entry identity, cwd-slug fallback for non-repos."""
    cwds = list(dict.fromkeys(sc.session.cwd for sc in session_costs if sc.session.cwd))
    resolved = {cwd: gitstate._resolve(cwd) for cwd in cwds}

    projects: dict[str, ProjectCost] = {}
    for sc in session_costs:
        cwd = sc.session.cwd
        res = resolved.get(cwd)
        if res:
            toplevel, key = res
            name, path = os.path.basename(toplevel), toplevel
        else:
            key = os.path.realpath(cwd)
            name, path = os.path.basename(cwd.rstrip("/")) or cwd, cwd
        proj = projects.get(key)
        if proj is None:
            proj = projects[key] = ProjectCost(key=key, name=name, path=path)
        proj.sessions.append(sc)

    for proj in projects.values():
        proj.sessions.sort(key=lambda s: s.cost, reverse=True)
    return sorted(projects.values(), key=lambda p: p.cost, reverse=True)


def attach_loops(session_costs: list[SessionCost], cache) -> None:
    """Attach each session's above-floor Loops (ADR 0007). Free and derived:
    served from the Derived Cache; only changed session files are re-read."""
    from . import loops as loops_mod
    for sc in session_costs:
        scan = loops_mod.for_session(Path(sc.session.log_path), cache)
        sc.loops = loops_mod.significant(scan)


def attach_brief_overhead(projects: list[ProjectCost]) -> None:
    """Price each project's Session Briefs and record it as Brief Overhead (ADR
    0006) — attributed to the repo whose sessions the Briefs summarise. Read-only
    and separate from Notional Cost; deliberately not folded into `cost`.
    """
    from . import brief as brief_mod
    for proj in projects:
        for sc in proj.sessions:
            b = brief_mod.load_one(sc.session.session_id)
            if b is None:
                continue
            proj.brief_overhead += brief_mod.overhead_cost(b)
            proj.brief_count += 1
