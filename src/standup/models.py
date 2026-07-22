from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Session:
    session_id: str
    log_path: str
    cwd: str | None = None
    custom_title: str | None = None
    ai_title: str | None = None
    slug: str | None = None
    last_prompt: str | None = None
    last_activity: datetime | None = None
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
    author_email: str = ""
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
    done: list[Commit] = field(default_factory=list)  # pushed within the Recent Window

    @property
    def needs_decision(self) -> bool:
        return any(c.pending or c.unpushed for c in self.checkouts)

    @property
    def latest_activity(self) -> datetime | None:
        times = [c.when for co in self.checkouts for c in co.unpushed]
        times += [a.when for co in self.checkouts for f in co.pending for a in f.attributions if a.when]
        times += [c.when for c in self.done]
        return max(times) if times else None
