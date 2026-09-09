"""The Scan Universe: the one module that answers "what does Standup see"
(ADR 0001 § one module owns the scan).

Every view asks the same four questions — what Sessions exist, what Repo
Entries they discovered, which Repo Entry owns a directory, and which Session
here is newest — and each used to answer them itself. This module owns them,
and hides the three things a view has no business knowing:

* **the Derived Cache's lifecycle.** Opened once per command and flushed on
  every path out, error paths included (ADR 0001 § the Derived Cache). A view
  that forgets to flush loses no output — the cache is a pure accelerator — but
  it pays the parse again on the next run, silently.
* **where the logs live, and the one way looking for them fails.** The hidden
  `--projects-dir`/`--codex-dir` overrides and the "no Claude Code logs found"
  message are declared once here rather than copied into every subcommand. Two
  agents' logs make one Scan Universe (ADR 0001 § two dialects, one reading):
  a `cwd` is a `cwd` whichever wrote it.
* **Repo Entry identity.** `git rev-parse --git-common-dir`, the rule that
  folds worktrees into their main checkout (CONTEXT.md → Repo Entry).

Path resolution lives here too, which is why `handles` imports no git: a
Project Handle is pure name matching over a list of Targets, and turning a
*path* into one of those Targets is a question about the Universe.
"""

from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import cache as cache_mod
from . import gitstate, handles, join, logs, transcript
from .models import RepoEntry, Session

DEFAULT_PROJECTS_DIR = logs.DEFAULT_PROJECTS_DIR
DEFAULT_CODEX_DIR = logs.DEFAULT_CODEX_DIR


class UniverseError(Exception):
    """The Scan Universe cannot be read: no Claude Code or Codex logs where
    they live."""


@dataclass(frozen=True)
class Owner:
    """The Repo Entry a directory belongs to — the answer to "which Repo Entry
    owns this cwd", not a domain term of its own (CONTEXT.md names no *owner*).

    `key` is the identity a Repo Entry is folded on — the realpath of
    `git rev-parse --git-common-dir`, which worktrees share with their main
    checkout and independent clones never do (CONTEXT.md → Repo Entry).

    A directory outside git has no Repo Entry, but still has to be *grouped*
    somewhere (the `cost` view spans Sessions with no git footprint at all), so
    it owns itself: `key` is its realpath and `is_repo` is False. Callers that
    only speak in Repo Entries drop those; nobody has to reinvent the fallback.
    """

    key: str
    path: str
    is_repo: bool

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/")) or self.path

    def as_target(self) -> handles.Target:
        return handles.Target(self.name, self.path)


def _main_checkout(toplevel: str, common: str) -> str:
    """The Repo Entry's main checkout, given any of its checkouts.

    Derived from the common dir rather than asked of `git worktree list`: the
    common dir of a worktree *is* the main checkout's `.git`, so the main
    checkout is its parent — free, where a worktree walk would be one more git
    subprocess per repo on paths (shell completion) that were tuned to avoid
    exactly that. Anything unusual (a separate git dir, a bare repo) fails the
    shape test and keeps the checkout git actually reported.
    """
    if os.path.basename(common) != ".git":
        return toplevel
    parent = os.path.dirname(common)
    return parent if os.path.isdir(parent) else toplevel


def owner_of(cwd: str | None) -> Owner | None:
    """Which Repo Entry owns this directory — the one implementation of the rule.

    None only when there is no directory to ask about; a directory that exists
    but is not in a repo owns itself (see `Owner`).
    """
    if not cwd:
        return None
    res = gitstate.resolve_checkout(cwd)
    if res is None:
        return Owner(key=os.path.realpath(cwd), path=cwd, is_repo=False)
    toplevel, key = res
    return Owner(key=key, path=_main_checkout(toplevel, key), is_repo=True)


def add_projects_dir_argument(parser: argparse.ArgumentParser) -> None:
    """The hidden `--projects-dir` and `--codex-dir` overrides, declared in one
    place — one per log root (ADR 0001 § two dialects, one reading).

    Hidden is a decision, not an omission (CLAUDE.md § CLI help): they are test
    and debugging entry points, and listing them would invite them to be
    configured.
    """
    parser.add_argument("--projects-dir", default=os.path.expanduser(DEFAULT_PROJECTS_DIR),
                        help=argparse.SUPPRESS)
    parser.add_argument("--codex-dir", default=os.path.expanduser(DEFAULT_CODEX_DIR),
                        help=argparse.SUPPRESS)


class Universe:
    """One command's view of the Scan Universe, over one open Derived Cache.

    Every answer is computed once and reused: a command that resolves a repo,
    reads its Sessions and then names the newest one parses the logs a single
    time. Construct it with `open_universe()`, which owns the cache's lifecycle.
    """

    def __init__(self, roots, cache):
        # the log roots this command reads — `logs.Roots`, or one Claude Code
        # root handed as a bare path, the shape every caller used before Codex
        self.roots: logs.Roots = logs.as_roots(roots)
        self.cache = cache
        self._sessions: list[Session] | None = None
        self._owners: dict[str, Owner | None] = {}
        self._entries: tuple[datetime, list[RepoEntry]] | None = None

    # --- the scan pipeline ---------------------------------------------------

    def sessions(self) -> list[Session]:
        """Every Session of the Scan Universe, newest parse served warm."""
        if self._sessions is None:
            self._sessions = logs.scan_sessions(self.roots, self.cache)
        return self._sessions

    @property
    def projects_dir(self) -> Path | None:
        """The Claude Code root alone — for the one consumer whose layout is
        Claude's (subagent transcripts, ADR 0004 § the worktree lane)."""
        return self.roots.claude

    def entries(self, since: datetime) -> list[RepoEntry]:
        """The Repo Entries, with their git state attributed to Sessions — the
        whole pipeline (scan → discover → attribute) in the one place it lives.

        `since` bounds the Done retrospective only; Needs-Decision Items and
        their attribution stay ageless (ADR 0001 § ageless attribution).
        """
        if self._entries is None or self._entries[0] != since:
            sessions = self.sessions()
            entries = gitstate.discover_repos([s.cwd for s in sessions if s.cwd], since)
            join.attribute(entries, sessions, self.cache)
            self._entries = (since, entries)
        return self._entries[1]

    # --- identity ------------------------------------------------------------

    def owner(self, cwd: str | None) -> Owner | None:
        """`owner_of`, memoized: a Session scan asks about the same handful of
        directories hundreds of times, and each miss is two git subprocesses."""
        if not cwd:
            return None
        if cwd not in self._owners:
            self._owners[cwd] = owner_of(cwd)
        return self._owners[cwd]

    def targets(self, repos_only: bool = False) -> list[handles.Target]:
        """The Scan Universe as resolvable Project Handles, one per Repo Entry.

        Built from the Session scan, not from git discovery: resolving a handle
        needs names and paths, and must not pay for a status walk of every repo.

        A Session can have run in a directory that is no repo at all, and it is
        still addressable — `standup session --in <it>` and the completion
        candidates both name it. `repos_only` drops those, for the caller whose
        answer comes from git: a name the Watch cannot produce a single git
        event for is not something it can watch.
        """
        seen: dict[str, handles.Target] = {}
        for cwd in dict.fromkeys(s.cwd for s in self.sessions() if s.cwd):
            owner = self.owner(cwd)
            if owner is None or (repos_only and not owner.is_repo):
                continue
            seen.setdefault(owner.key, owner.as_target())
        return list(seen.values())

    def sessions_in(self, key: str) -> list[Session]:
        """Every Session that ran in one Repo Entry, worktrees included."""
        out = []
        for s in self.sessions():
            owner = self.owner(s.cwd)
            if owner is not None and owner.key == key:
                out.append(s)
        return out

    # --- resolution ----------------------------------------------------------

    def resolve_repo(self, arg: str, targets=None, prog: str = "standup") -> handles.Target:
        """A `<repo>` argument → one project. A path (`.`, `../x`, `~/y`) is
        resolved through git; a bare word is a Project Handle (ADR 0005
        § Project Handles)."""
        candidates = list(self.targets() if targets is None else targets)
        if not handles.looks_like_path(arg):
            return handles.resolve(arg, candidates, prog)

        p = os.path.abspath(os.path.expanduser(arg))
        if not os.path.isdir(p):
            raise handles.HandleError(f"{prog}: no such directory: {arg}")
        owner = self.owner(p)
        if owner is None or not owner.is_repo:
            raise handles.HandleError(f"{prog}: {arg!r} is not inside a git repo")
        for t in candidates:
            if t.path == owner.path or os.path.realpath(t.path) == os.path.realpath(owner.path):
                return t
        # The Universe knows this repo under a path that is not its main
        # checkout — a layout the common-dir shape test cannot name (a separate
        # git dir). Fold on the identity key, which every checkout shares.
        for t in candidates:
            other = self.owner(t.path)
            if other is not None and other.key == owner.key:
                return t
        raise handles.HandleError(
            f"{prog}: {handles.shorten_home(owner.path)} has no recorded sessions")

    def resolve_session(self, handle: str) -> Path:
        """A Session Handle (any unambiguous `sessionId` prefix) → its log."""
        return transcript.resolve_handle(self.roots, handle)

    def check_session_in(self, log_path: Path, repo: str,
                         prog: str = "standup session") -> None:
        """A Session Handle *and* a repo: confirm the handle belongs to that
        Repo Entry.

        `standup st session <handle>` names a repo it does not strictly need — a
        Session Handle already implies its repo. Rather than reject the pair
        (which made the object-first spelling useless the moment you pasted a
        handle into it) or ignore the repo (which would answer about a different
        project without saying so), the repo becomes a *constraint*: the same
        rule ADR 0005 § two grammars applies to `@<hash>`, where naming a repo
        means the answer must come from it.
        """
        hit = self.resolve_repo(repo, prog=prog)
        want = self.owner(hit.path)
        if want is None or not want.is_repo:
            raise UniverseError(f"{prog}: {hit.name} is not a git repo")

        sid = logs.session_id_of(log_path)
        session = next((s for s in self.sessions() if s.session_id == sid), None)
        found = self.owner(session.cwd) if session else None
        if found is not None and found.key == want.key:
            return
        where = (found.name if found is not None and found.is_repo
                 else handles.shorten_home(session.cwd) if (session and session.cwd)
                 else "an unknown directory")
        raise UniverseError(
            f"{prog}: {sid[:8]} is not a session of {hit.name} — it ran in {where}\n"
            f"  a Session Handle already names its repo: `standup session {sid[:8]}` reads it")

    def newest_session_in(self, repo: str | None,
                          prog: str = "standup session") -> tuple[Session, str]:
        """The most recently appended Session of one Repo Entry, and the name to
        call it by — what `standup session` means with no handle (`repo=None`:
        the one you are standing in), and what `standup <repo> session` means
        with one.

        Deliberately no fallback to "newest session anywhere": handing back a
        transcript from an unrelated repo is the kind of silent misdirection
        every other view is built to avoid. Standing outside the Scan Universe
        is an error, and says so.
        """
        if repo is None:
            here = self.owner(os.getcwd())
            if here is None or not here.is_repo:
                raise UniverseError(
                    f"{prog}: not inside a git repo — name a session handle, "
                    "or a repo with `standup <repo> session`")
            key, where = here.key, handles.shorten_home(os.getcwd())
        else:
            hit = self.resolve_repo(repo, prog=prog)
            owner = self.owner(hit.path)
            if owner is None or not owner.is_repo:
                raise UniverseError(f"{prog}: {hit.name} is not a git repo")
            key, where = owner.key, hit.name

        mine = [s for s in self.sessions_in(key) if s.last_activity]
        if not mine:
            raise UniverseError(f"{prog}: no sessions recorded for {where}")
        return max(mine, key=lambda s: s.last_activity), where


@contextmanager
def open_universe(projects_dir: str | Path | None = None,
                  codex_dir: str | Path | None = None):
    """Open the Scan Universe over a Derived Cache, and flush it on the way out
    — including when the view raised (ADR 0001 § the Derived Cache).

    Either root may be missing — a machine with one agent installed has one —
    and the Universe reads the ones that exist. Raises `UniverseError` only
    when *neither* is there, so the message is written once instead of once per
    subcommand.
    """
    wanted = logs.Roots.default(projects_dir, codex_dir)
    present = wanted.present()
    if not present:
        raise UniverseError(
            f"standup: no Claude Code logs found at {wanted.claude}, "
            f"and no Codex logs at {wanted.codex}")
    cache = cache_mod.open_cache()
    try:
        yield Universe(present, cache)
    finally:
        cache.flush()
