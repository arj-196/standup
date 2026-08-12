"""Notional Cost aggregation: per-Session, grouped per-project (Repo Entry).

Spans *all* sessions (any git footprint or none) — a wider population than the
Triage Inbox. Groups by Repo Entry identity (worktrees fold into their parent
checkout), falling back to the raw cwd for sessions whose dir is not a git repo.

A Session's usage includes its subagent transcripts
(`<project>/<sessionId>/subagents/agent-*.jsonl`): their per-turn `usage` is
never echoed into the parent log, so the parent alone under-counts
subagent-heavy work. Folded into the parent Session's own figures, never a
separate row (ADR 0002 § subagent usage).

Stateless: recomputed from the logs each run — through the one log reader, so
the parse is the same one the inbox pays for and the Derived Cache serves an
unchanged log to both (ADR 0001 § the one log reader). The window (default: the
current calendar month) bounds which files are read by mtime; individual turns
are then filtered by their own timestamp so sessions straddling the edge count
exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import claude_logs
from .models import Session

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class SessionCost:
    session: Session
    # the priced fold of every turn this Session counted in the window — its
    # own and its subagents'. Held rather than re-derived field by field: the
    # arithmetic is `claude_logs.usage_totals`', and a second copy of it here
    # is how the two ways to price a session start to disagree.
    usage: claude_logs.UsageTotals = field(
        default_factory=claude_logs.UsageTotals)
    # subagent transcripts that contributed at least one in-window turn to the
    # figures above (ADR 0002 § subagent usage). The fold's visible mark: a
    # session line whose tokens include delegated work says so.
    subagents: int = 0
    # the newest counted turn — what `--recent` ranks by, and what the views
    # show as this row's recency. Deliberately *not* `session.last_activity`,
    # which is the log file's mtime and nothing else
    # (ADR 0001 § the one log reader): this one is window-bounded and answers
    # "when did the work these figures price happen".
    last_turn: datetime | None = None
    # above-floor Loops (ADR 0003 § the Audit): free, derived, attached by
    # attach_loops. Loop Cost is a carve-out of this session's Notional Cost,
    # never a saving.
    loops: list = field(default_factory=list)

    @property
    def cost(self) -> float:
        return self.usage.cost

    @property
    def by_model(self) -> dict[str, float]:
        return self.usage.by_model

    @property
    def tokens(self) -> dict[str, int]:
        return self.usage.tokens

    @property
    def turns(self) -> int:
        """Priced turns. An unpriced one is counted apart, never at $0
        (ADR 0002)."""
        return self.usage.turns

    @property
    def unpriced_turns(self) -> int:
        return self.usage.unpriced_turns

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
    def last_turn(self) -> datetime | None:
        """The newest counted turn across this project's sessions — the
        project-level reading of `SessionCost.last_turn`, and what `--recent`
        ranks projects by."""
        times = [s.last_turn for s in self.sessions if s.last_turn]
        return max(times) if times else None


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _in_window(turns, window_start: datetime) -> list:
    """The turns a windowed figure counts.

    A turn whose line carried no timestamp counts: it is still work the
    Session did, and the alternative — dropping what cannot be dated — would
    under-price it silently.
    """
    return [t for t in turns if t.when is None or t.when >= window_start]


def _subagent_logs(parent_log: Path, window_start: datetime) -> list[Path]:
    """This Session's subagent transcripts (ADR 0002 § subagent usage).

    Separate files, so separate readings — the parent's log carries none of
    their usage. Each is mtime-gated like the parent, a pure read saver since
    turns are timestamp-filtered anyway.
    """
    agents_dir = parent_log.parent / parent_log.stem / "subagents"
    live = ((f, _mtime(f)) for f in sorted(agents_dir.glob("agent-*.jsonl")))
    return [f for f, m in live if m is not None and m >= window_start]


def _session_cost(log: Path, window_start: datetime, cache) -> SessionCost | None:
    """One Session priced: its own in-window turns plus its subagents',
    folded into one row. None when nothing counted in the window.

    Usage is all a subagent transcript contributes — titles and cwd are the
    parent's business, and a subagent log carries neither.
    """
    parsed = claude_logs.read_log(log, cache)
    counted = _in_window(parsed.turns, window_start)
    subagents = 0
    for f in _subagent_logs(log, window_start):
        delegated = _in_window(claude_logs.read_log(f, cache).turns, window_start)
        if delegated:
            subagents += 1
            counted += delegated
    if not counted:
        return None
    return SessionCost(
        session=parsed.session,
        # one fold over the concatenated turns, never a sum of two folds:
        # pricing is per-turn (ADR 0002), so the turns of one Session are one
        # list however many files they were read from
        usage=claude_logs.usage_totals(counted),
        subagents=subagents,
        last_turn=max((t.when for t in counted if t.when), default=None),
    )


def scan_session_costs(projects_dir: Path, window_start: datetime,
                       cache) -> list[SessionCost]:
    """Price every Session of the Scan Universe over one window.

    `cache` is the open Derived Cache — the same rows the inbox's reading
    fills, so a log unchanged since any earlier view read it is not opened
    (ADR 0001 § the Derived Cache).
    """
    out: list[SessionCost] = []
    for log in sorted(projects_dir.glob("*/*.jsonl")):
        mtime = _mtime(log)
        if mtime is None or mtime < window_start:
            continue
        sc = _session_cost(log, window_start, cache)
        if sc and sc.session.cwd:
            out.append(sc)
    return out


def group_by_project(session_costs: list[SessionCost],
                     order: str = "cost") -> list[ProjectCost]:
    """Bucket sessions by Repo Entry identity (`universe.owner_of` — worktrees
    fold into their main checkout), the directory itself for a non-repo cwd.

    `order` ranks both levels the same way: "cost" (the default — where the
    load concentrates) or "recent" (newest counted turn first — what the
    last few sessions cost, however cheap). A session that counted no dated
    turn sorts last under "recent" rather than borrowing a rank.
    """
    # imported here, not at module scope: `universe` reaches `render`, which
    # reads this module's ProjectCost — a top-level import would close the loop.
    from . import universe

    cwds = list(dict.fromkeys(sc.session.cwd for sc in session_costs if sc.session.cwd))
    owners = {cwd: universe.owner_of(cwd) for cwd in cwds}

    projects: dict[str, ProjectCost] = {}
    for sc in session_costs:
        owner = owners.get(sc.session.cwd)
        if owner is None:
            continue    # unreachable: scan_session_costs drops a cwd-less session
        proj = projects.get(owner.key)
        if proj is None:
            proj = projects[owner.key] = ProjectCost(key=owner.key, name=owner.name,
                                                     path=owner.path)
        proj.sessions.append(sc)

    if order == "recent":
        session_key = lambda s: s.last_turn or _EPOCH
        project_key = lambda p: p.last_turn or _EPOCH
    else:
        session_key = lambda s: s.cost
        project_key = lambda p: p.cost
    for proj in projects.values():
        proj.sessions.sort(key=session_key, reverse=True)
    return sorted(projects.values(), key=project_key, reverse=True)


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

    Hedging is the store's, against the log the Session was read from: this
    view never gets to pick its own clock, or the same Brief could read
    `(stale)` in the inbox and unhedged here (ADR 0003 § the shared model).
    """
    from . import artifacts
    from . import brief as brief_mod
    for proj in projects:
        for sc in proj.sessions:
            b = brief_mod.load_one(sc.session.session_id)
            if b is None:
                continue
            artifacts.stamp_staleness(b, sc.session.log_path)
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
