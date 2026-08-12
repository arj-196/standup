from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Brief:
    """A Session Brief (ADR 0003 § the Session Brief): an LLM-authored,
    out-of-band account of a
    Session's objective. A *claim*, never a derived fact — always rendered
    marked as such, and hedged (`stale`) when the log advanced past `generated`.
    Read from ~/.standup/briefs/<sessionId>.brief.md; standup never writes it at
    render time.
    """
    session_id: str
    objective: str
    status: str | None = None
    generated: datetime | None = None
    model: str | None = None
    body: str = ""
    stale: bool = False
    # the generation's own token usage (from `claude -p --output-format json`),
    # priced as Brief Overhead (ADR 0003 § the Session Brief). Shape matches
    # rates.turn_cost's input.
    gen_usage: dict | None = None


@dataclass
class Session:
    session_id: str
    log_path: str
    cwd: str | None = None
    custom_title: str | None = None
    ai_title: str | None = None
    slug: str | None = None
    last_prompt: str | None = None
    # The Session log's mtime: when the log last grew, and the *only* meaning
    # this field carries as the log reader produces it
    # (ADR 0001 § the one log reader).
    # It is the staleness clock for out-of-band artifacts, which ask
    # "did the session move on after this Brief was written" — a question only
    # the file's own clock answers. "When the model last spoke" is a different
    # question with its own name: `claude_logs.ParsedLog.last_turn`, and its
    # window-bounded form is `cost.SessionCost.last_turn`. No view overwrites
    # this field with either of them.
    last_activity: datetime | None = None
    brief: "Brief | None" = None
    branches: set[str] = field(default_factory=set)
    # absolute file path -> timestamp of most recent Edit/Write
    edited_files: dict[str, datetime] = field(default_factory=dict)
    # short commit hashes captured from git commit stdout, hash -> timestamp
    commit_hashes: dict[str, datetime] = field(default_factory=dict)

    @property
    def title(self) -> str:
        for t in (self.custom_title, self.ai_title, self.slug):
            if t:
                return t
        if self.last_prompt:
            p = " ".join(self.last_prompt.split())
            return p[:57] + "..." if len(p) > 60 else p
        return self.session_id[:8]


@dataclass
class Attribution:
    tier: str  # "exact" | "likely"
    session_id: str
    title: str
    when: datetime | None = None


@dataclass
class Rollup:
    """A Session's uncommitted footprint in one repo (session_id None = unattributed).

    Footprints may overlap: a multi-attributed file appears in every plausible
    Session's Rollup — the RepoEntry header carries the true git totals.
    """
    session_id: str | None
    title: str
    files: list[tuple[str, "PendingFile"]] = field(default_factory=list)  # (branch, file)
    last_activity: datetime | None = None

    @property
    def handle(self) -> str | None:
        """The Session Handle — 8-char sessionId prefix used to address this
        Session on the CLI (`standup session <handle>`). None when unattributed."""
        return self.session_id[:8] if self.session_id else None


@dataclass
class PendingFile:
    code: str  # porcelain status code, e.g. " M", "??"
    path: str  # relative to checkout toplevel
    attributions: list[Attribution] = field(default_factory=list)


@dataclass
class Commit:
    sha: str
    short: str
    subject: str
    when: datetime
    author_email: str = ""    # the Done filter's key: "my recent work"
    author_name: str = ""     # display only — what the commit header prints
    attributions: list[Attribution] = field(default_factory=list)


@dataclass
class Checkout:
    path: str  # toplevel
    branch: str
    is_main: bool
    pending: list[PendingFile] = field(default_factory=list)
    unpushed: list[Commit] = field(default_factory=list)


@dataclass
class RepoEntry:
    name: str
    main_path: str
    checkouts: list[Checkout] = field(default_factory=list)  # main first
    # terminal-state commits within the Recent Window: pushed, or — in a
    # Remoteless Repo — merely committed (ADR 0006)
    done: list[Commit] = field(default_factory=list)
    has_remote: bool = True

    @property
    def needs_decision(self) -> bool:
        return any(c.pending or c.unpushed for c in self.checkouts)

    @property
    def latest_activity(self) -> datetime | None:
        times = [c.when for co in self.checkouts for c in co.unpushed]
        times += [a.when for co in self.checkouts for f in co.pending for a in f.attributions if a.when]
        times += [c.when for c in self.done]
        return max(times) if times else None
