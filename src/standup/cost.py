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

from . import claude_logs, gitstate, rates
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
    # above-floor Loops (ADR 0003 § the Audit): free, derived, attached by
    # attach_loops. Loop Cost is a carve-out of this session's Notional Cost,
    # never a saving.
    loops: list = field(default_factory=list)
    # the log file's mtime, kept from the stat scan_session_costs already does.
    # The staleness clock for out-of-band artifacts — deliberately *not*
    # last_activity, which counts only priced assistant turns inside the window
    # and so under-reports that the session moved on. See attach_briefs.
    log_mtime: datetime | None = None

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
    # Brief Overhead (ADR 0003 § the Session Brief): Notional Cost of
    # generating this project's Session Briefs, kept separate from `cost` so it
    # is never folded in silently.
    brief_overhead: float = 0.0
    brief_count: int = 0
    # Audit Overhead (ADR 0003 § the Audit): same move for the Expert Panel's
    # own cost.
    audit_overhead: float = 0.0
    audit_count: int = 0

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


def _scan_file(path: Path, window_start: datetime) -> SessionCost | None:
    session = Session(session_id=path.stem, log_path=str(path))
    sc = SessionCost(session=session)
    latest: datetime | None = None
    with open(path, errors="replace") as fh:
        for line in fh:
            has_usage = '"usage"' in line
            # title handling is claude_logs' (the inbox's scanner): this view
            # reads per-turn usage, which that one does not, but the two must
            # agree about what a Session is *called*.
            has_meta = '"cwd"' in line or claude_logs.title_hint(line)
            if not (has_usage or has_meta):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if session.cwd is None and obj.get("cwd"):
                session.cwd = obj["cwd"]
            claude_logs.apply_title_fields(session, obj)
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
            sc.log_mtime = mtime
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
    """Attach each session's above-floor Loops (ADR 0003 § the Audit).
    Free and derived:
    served from the Derived Cache; only changed session files are re-read."""
    from . import loops as loops_mod
    for sc in session_costs:
        scan = loops_mod.for_session(Path(sc.session.log_path), cache)
        sc.loops = loops_mod.significant(scan)


def attach_briefs(projects: list[ProjectCost]) -> None:
    """Attach each Session's Brief and price it as Brief Overhead
    (ADR 0003 § the Session Brief).

    One pass, because the drill-down needs both halves of the same file: the
    objective (rendered as a `~`-marked claim under the session's title, which
    it augments and never replaces) and the generation's own usage — attributed
    to the repo whose sessions the Briefs summarise, read-only and deliberately
    not folded into `cost`.

    Staleness is stamped against the log's mtime, never this view's
    last_activity: the question is whether the session advanced past the Brief,
    and last_activity here sees only priced assistant turns inside the window.
    A view-local clock would let the same Brief read `(stale)` in the inbox and
    unhedged here.
    """
    from . import brief as brief_mod
    for proj in projects:
        for sc in proj.sessions:
            b = brief_mod.load_one(sc.session.session_id)
            if b is None:
                continue
            brief_mod.stamp_staleness(b, sc.log_mtime)
            sc.session.brief = b
            proj.brief_overhead += brief_mod.overhead_cost(b)
            proj.brief_count += 1


def attach_audit_overhead(projects: list[ProjectCost]) -> None:
    """Price each project's Audits (ADR 0003 § the Audit) as Audit Overhead
    — the Expert
    Panel's own recorded usage, attributed to the repo whose session was
    audited. Separate and labelled, never folded into Notional Cost."""
    from . import audit as audit_mod
    for proj in projects:
        for sc in proj.sessions:
            a = audit_mod.load_one(sc.session.session_id)
            if a is None:
                continue
            proj.audit_overhead += a.overhead_cost
            proj.audit_count += 1
