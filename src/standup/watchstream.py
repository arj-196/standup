"""The Watch's event stream: JSONL tail (claims) + git observation (ground truth).

Plain Python by design (ADR 0008): this module never imports textual. The watch
UI is just one consumer of the typed Feed Events produced here.

The split mirrors Attribution: the session log claims *who and what* (the exact
Edit text, the Bash command, the prompt); git confirms tree state and alone
reveals live Unattributed Changes. Live Session is a recency claim — a log
appended within LIVE_THRESHOLD — never a process fact (CONTEXT.md).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brief as brief_mod
from . import cache as cache_mod
from . import claude_logs, gitstate
from .models import Session

LIVE_THRESHOLD = timedelta(minutes=30)  # Live Session recency claim (CONTEXT.md)
GIT_POLL_INTERVAL = 2.0        # seconds between git status/HEAD polls
PUSH_POLL_EVERY = 5            # push check once per N git polls
DISCOVERY_INTERVAL = 10.0      # seconds between scans for new session logs
EDIT_TOOLS = claude_logs.EDIT_TOOLS

_COMMIT_RE = claude_logs.COMMIT_LINE_RE
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CMD_TAG_RE = re.compile(r"</?command-[^>]*>", re.DOTALL)


class WatchError(Exception):
    pass


@dataclass
class FeedEvent:
    """One entry in the Watch (CONTEXT.md → Feed Event)."""
    kind: str                    # file | bash | bash_result | prompt | commit |
                                 # branch | push | unattributed | session
    when: datetime
    session_id: str | None = None
    title: str = ""              # session title (display context)
    path: str | None = None      # repo-relative, for file events
    change: str | None = None    # create | modify | delete
    added: str = ""              # text written (drives the animation)
    removed: str = ""            # text replaced
    command: str | None = None   # bash events
    ok: bool | None = None       # bash_result verdict
    tool_id: str | None = None   # joins bash -> bash_result
    message: str = ""            # prompt text / commit subject / free text
    sha: str | None = None
    backfill: bool = False

    @property
    def handle(self) -> str | None:
        return self.session_id[:8] if self.session_id else None


@dataclass
class LiveSessionInfo:
    session_id: str
    title: str
    objective: str | None        # Session Brief claim, when one exists
    last_append: datetime

    @property
    def handle(self) -> str:
        return self.session_id[:8]


@dataclass
class Vitals:
    """The Watch header: repo, branch, Live Sessions, dirt count."""
    repo: str
    path: str
    branch: str
    dirty: int
    live: list[LiveSessionInfo] = field(default_factory=list)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prompt_text(content) -> str | None:
    """A user turn's typed text, stripped of injected noise (the show idiom)."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
    else:
        return None
    if "<command-name>" in text:
        name = (_CMD_NAME_RE.search(text) or [None, ""])[1].strip()
        args = (_CMD_ARGS_RE.search(text) or [None, ""])[1].strip()
        text = f"{name} {args}".strip()
    text = _CMD_TAG_RE.sub("", _REMINDER_RE.sub("", text)).strip()
    return text or None


class _Tailer:
    """Incremental reader of one Session's JSONL: new complete lines -> Feed Events."""

    def __init__(self, session: Session, repo_paths: list[str]):
        self.session = session
        self.path = Path(session.log_path)
        self.offset = 0
        # realpath both sides of every comparison: agent-written paths and git
        # toplevels may disagree about symlinks (macOS /var vs /private/var)
        self.repo_paths = [os.path.realpath(p) for p in repo_paths]
        self.pending_bash: dict[str, datetime] = {}   # tool_use id -> when issued
        self.claimed_shas: set[str] = set()           # commit hashes seen in results
        self.edited_paths: set[str] = set(session.edited_files)
        self.last_append: datetime = session.last_activity or _now()
        self._backfilling = False

    def seek_to_end(self) -> None:
        try:
            self.offset = self.path.stat().st_size
        except OSError:
            pass

    def _rel(self, fp: str) -> tuple[str, str] | None:
        """Absolute path -> (checkout root, repo-relative), or None if outside."""
        fp = os.path.realpath(fp)
        for top in self.repo_paths:
            root = top.rstrip("/")
            if fp.startswith(root + "/"):
                return root, fp[len(root) + 1:]
        return None

    @staticmethod
    def _write_change(root: str, rel: str) -> str:
        """create vs modify for a Write, from git (the file itself already exists
        by the time the tail is parsed, so os.path.exists can't tell)."""
        out = gitstate.git(root, "status", "--porcelain", "--", rel)
        return "create" if (out or "").startswith("??") else "modify"

    def _events_from_obj(self, obj: dict) -> list[FeedEvent]:
        sid, title = self.session.session_id, self.session.title
        ts = _parse_ts(obj.get("timestamp")) or _now()
        etype = obj.get("type")
        events: list[FeedEvent] = []

        if etype == "user":
            tr = obj.get("toolUseResult")
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id")
                        if tid in self.pending_bash:
                            del self.pending_bash[tid]
                            events.append(FeedEvent(
                                kind="bash_result", when=ts, session_id=sid, title=title,
                                tool_id=tid, ok=not bool(b.get("is_error"))))
            if tr is not None:
                text = tr if isinstance(tr, str) else (tr.get("stdout") or "") if isinstance(tr, dict) else ""
                for m in _COMMIT_RE.finditer(text):
                    self.claimed_shas.add(m.group(1))
            if not obj.get("isMeta") and tr is None:
                text = _prompt_text((obj.get("message") or {}).get("content"))
                if text:
                    events.append(FeedEvent(kind="prompt", when=ts, session_id=sid,
                                            title=title, message=text))
            return events

        if etype != "assistant":
            return events
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return events
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name, inp = b.get("name"), b.get("input") or {}
            if name == "Bash" and inp.get("command"):
                tid = b.get("id") or ""
                self.pending_bash[tid] = ts
                events.append(FeedEvent(kind="bash", when=ts, session_id=sid, title=title,
                                        tool_id=tid, command=inp["command"]))
            elif name in EDIT_TOOLS:
                fp = inp.get("file_path") or inp.get("notebook_path")
                if not fp or not os.path.isabs(fp):
                    continue
                loc = self._rel(fp)
                if loc is None:
                    continue  # the session touched a file outside this Repo Entry
                root, rel = loc
                self.edited_paths.add(os.path.realpath(fp))
                if name == "Write":
                    # during backfill the tree has long moved on — don't ask git
                    change = "modify" if self._backfilling else self._write_change(root, rel)
                    events.append(FeedEvent(kind="file", when=ts, session_id=sid,
                                            title=title, path=rel, change=change,
                                            added=inp.get("content") or ""))
                elif name == "MultiEdit":
                    for e in inp.get("edits") or []:
                        if isinstance(e, dict):
                            events.append(FeedEvent(
                                kind="file", when=ts, session_id=sid, title=title,
                                path=rel, change="modify",
                                added=e.get("new_string") or "",
                                removed=e.get("old_string") or ""))
                else:  # Edit / NotebookEdit
                    events.append(FeedEvent(
                        kind="file", when=ts, session_id=sid, title=title,
                        path=rel, change="modify",
                        added=inp.get("new_string") or inp.get("new_source") or "",
                        removed=inp.get("old_string") or ""))
        return events

    def read_new(self) -> list[FeedEvent]:
        """Parse lines appended since the last call (complete lines only)."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:      # truncated/rotated: start over from the top
            self.offset = 0
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)
        nl = chunk.rfind(b"\n")
        if nl < 0:                  # a partial line is still being written
            return []
        self.offset += nl + 1
        self.last_append = _now()
        events: list[FeedEvent] = []
        for raw in chunk[: nl + 1].splitlines():
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            events.extend(self._events_from_obj(obj))
        return events

    def backfill(self) -> list[FeedEvent]:
        """Replay from the current user prompt: scan the whole file, keeping only
        events since the last prompt (prompts are the narrative's chapter breaks),
        then tail from EOF. Marks everything as backfill."""
        events: list[FeedEvent] = []
        self._backfilling = True
        try:
            with open(self.path, "rb") as f:
                for raw in f:
                    try:
                        obj = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    for ev in self._events_from_obj(obj):
                        if ev.kind == "prompt":
                            events = [ev]     # new chapter: drop everything before
                        else:
                            events.append(ev)
        except OSError:
            return []
        finally:
            self._backfilling = False
        self.seek_to_end()
        for ev in events:
            ev.backfill = True
        return events


class _GitWatcher:
    """Polls git for ground truth: status deltas, HEAD moves, branch switches,
    pushes. Emits Unattributed Changes for dirt no tailed Session explains."""

    def __init__(self, checkouts: list[str]):
        self.checkouts = checkouts
        self.status: dict[str, dict[str, str]] = {}    # checkout -> path -> code
        self.head: dict[str, str] = {}
        self.branch: dict[str, str] = {}
        self.unpushed: dict[str, int] = {}
        self._polls = 0
        for co in checkouts:
            self.status[co] = self._status(co)
            self.head[co] = self._head(co)
            self.branch[co] = gitstate._branch(co)
            self.unpushed[co] = self._unpushed_count(co)

    @staticmethod
    def _status(co: str) -> dict[str, str]:
        out = gitstate.git(co, "status", "--porcelain") or ""
        st = {}
        for line in out.splitlines():
            if len(line) >= 4:
                p = line[3:]
                if " -> " in p:
                    p = p.split(" -> ", 1)[1]
                st[p.strip('"')] = line[:2]
        return st

    @staticmethod
    def _head(co: str) -> str:
        return (gitstate.git(co, "rev-parse", "HEAD") or "").strip()

    @staticmethod
    def _unpushed_count(co: str) -> int:
        out = gitstate.git(co, "rev-list", "--count", "HEAD", "--not", "--remotes")
        try:
            return int((out or "").strip())
        except ValueError:
            return 0

    def dirty_count(self) -> int:
        return sum(len(s) for s in self.status.values())

    def main_branch(self) -> str:
        return self.branch.get(self.checkouts[0], "?") if self.checkouts else "?"

    def poll(self, explained: set[str], attribute) -> list[FeedEvent]:
        """One ground-truth pass. `explained` holds absolute paths narrated by
        tailed Sessions; `attribute(sha)` maps a commit to a session_id or None."""
        self._polls += 1
        now = _now()
        events: list[FeedEvent] = []
        for co in self.checkouts:
            new_status = self._status(co)
            old_status = self.status[co]
            fresh = [p for p, code in new_status.items()
                     if old_status.get(p) != code
                     and os.path.realpath(os.path.join(co, p)) not in explained]
            if fresh:
                events.append(FeedEvent(
                    kind="unattributed", when=now,
                    message=", ".join(sorted(fresh)[:5]) +
                            (f" +{len(fresh) - 5} more" if len(fresh) > 5 else "")))
            self.status[co] = new_status

            new_branch = gitstate._branch(co)
            if new_branch != self.branch[co]:
                events.append(FeedEvent(kind="branch", when=now,
                                        message=f"{self.branch[co]} → {new_branch}"))
                self.branch[co] = new_branch

            new_head = self._head(co)
            if new_head and new_head != self.head[co]:
                out = gitstate.git(co, "log", "--format=%H\x1f%h\x1f%s",
                                   f"{self.head[co]}..{new_head}") if self.head[co] else None
                lines = (out or "").splitlines()
                if not lines:   # rebase/amend/reset: old..new is empty or failed
                    out1 = gitstate.git(co, "log", "-1", "--format=%H\x1f%h\x1f%s", new_head)
                    lines = (out1 or "").splitlines()
                for line in lines:
                    parts = line.split("\x1f")
                    if len(parts) != 3:
                        continue
                    sha, short, subject = parts
                    sid = attribute(sha)
                    events.append(FeedEvent(kind="commit", when=now, session_id=sid,
                                            sha=short, message=subject))
                self.head[co] = new_head

            if self._polls % PUSH_POLL_EVERY == 0:
                n = self._unpushed_count(co)
                if n < self.unpushed[co] and self.head[co] == new_head:
                    pushed = self.unpushed[co] - n
                    events.append(FeedEvent(
                        kind="push", when=now,
                        message=f"pushed {pushed} commit{'s' if pushed != 1 else ''} ({self.branch[co]})"))
                self.unpushed[co] = n
        return events


def _resolve_target(repo_arg: str, sessions: list[Session]) -> tuple[str, list[str]]:
    """repo name / path fragment / filesystem path -> (name, checkout toplevels)."""
    if os.path.isdir(repo_arg):
        res = gitstate._resolve(os.path.abspath(repo_arg))
        if not res:
            raise WatchError(f"standup watch: {repo_arg!r} is not inside a git repo")
        toplevel, _ = res
        worktrees = [w for w in gitstate._worktrees(toplevel) if os.path.isdir(w)]
        return os.path.basename(worktrees[0]), worktrees

    # a name: resolve against the Scan Universe, same matching as the drill-down
    needle = repo_arg.rstrip("/").lower()
    by_key: dict[str, str] = {}
    order: list[str] = []
    for cwd in dict.fromkeys(s.cwd for s in sessions if s.cwd):
        res = gitstate._resolve(cwd)
        if not res:
            continue
        toplevel, key = res
        if key not in by_key:
            by_key[key] = toplevel
            order.append(key)
    # map each key to its main checkout (first worktree)
    candidates: list[tuple[str, str]] = []   # (name, main toplevel)
    for key in order:
        worktrees = gitstate._worktrees(by_key[key])
        main = worktrees[0] if worktrees else by_key[key]
        candidates.append((os.path.basename(main), main))
    matches = [(n, p) for n, p in candidates
               if needle == n.lower() or needle in p.lower()]
    if not matches:
        known = ", ".join(sorted({n for n, _ in candidates}))
        raise WatchError(f"standup watch: no scanned repo matches {repo_arg!r}\n"
                         f"known repos: {known}")
    exact = [m for m in matches if m[0].lower() == needle]
    name, main = (exact[0] if exact else matches[0])
    worktrees = [w for w in gitstate._worktrees(main) if os.path.isdir(w)]
    return name, worktrees


class WatchStream:
    """Orchestrates tailers + git watcher for one Repo Entry.

    The UI drives it by calling poll() on a short interval; internal rate
    limiting keeps git subprocesses and discovery scans on their own cadence.
    """

    def __init__(self, repo_arg: str, projects_dir: Path, quiet: bool = False):
        self.projects_dir = projects_dir
        self.quiet = quiet
        cache = cache_mod.open_cache()
        sessions = claude_logs.scan_sessions(projects_dir, cache)
        cache.flush()
        self.name, self.checkouts = _resolve_target(repo_arg, sessions)
        self._roots = [os.path.realpath(c).rstrip("/") for c in self.checkouts]
        self.tailers: dict[str, _Tailer] = {}
        self._known_logs: set[str] = {s.log_path for s in sessions}
        self._briefs: dict[str, str] = {}
        self._counts: dict[str, int] = {}      # parting-snapshot tallies
        self._files_touched: set[str] = set()
        self._started = time.monotonic()
        self._last_git = 0.0
        self._last_discovery = time.monotonic()

        now = _now()
        for s in sessions:
            if s.cwd and self._in_repo(s.cwd):
                if s.last_activity and now - s.last_activity <= LIVE_THRESHOLD:
                    self._add_tailer(s)
        self.git = _GitWatcher(self.checkouts)

    # -- setup helpers -------------------------------------------------------

    def _in_repo(self, cwd: str) -> bool:
        cwd = os.path.realpath(cwd).rstrip("/")
        return any(cwd == r or cwd.startswith(r + "/") for r in self._roots)

    def _add_tailer(self, session: Session) -> _Tailer:
        t = _Tailer(session, self.checkouts)
        self.tailers[session.session_id] = t
        b = brief_mod.load_one(session.session_id)
        if b:
            self._briefs[session.session_id] = b.objective
        return t

    # -- the stream ----------------------------------------------------------

    def start(self) -> list[FeedEvent]:
        """Backfill: the newest Live Session replays from its current prompt;
        the others tail from EOF."""
        newest: _Tailer | None = None
        for t in self.tailers.values():
            if newest is None or t.last_append > newest.last_append:
                newest = t
        events: list[FeedEvent] = []
        for t in self.tailers.values():
            if t is newest:
                events = t.backfill()
            else:
                t.seek_to_end()
        return self._filtered(events)

    def poll(self) -> list[FeedEvent]:
        now_m = time.monotonic()
        events: list[FeedEvent] = []
        for t in list(self.tailers.values()):
            events.extend(t.read_new())

        if now_m - self._last_discovery >= DISCOVERY_INTERVAL:
            self._last_discovery = now_m
            events.extend(self._discover())

        if now_m - self._last_git >= GIT_POLL_INTERVAL:
            self._last_git = now_m
            explained = {p for t in self.tailers.values() for p in t.edited_paths}
            events.extend(self.git.poll(explained, self._attribute))

        return self._filtered(events)

    def _attribute(self, sha: str) -> str | None:
        for sid, t in self.tailers.items():
            if any(sha.startswith(c) or c.startswith(sha) for c in t.claimed_shas):
                return sid
        return None

    def _discover(self) -> list[FeedEvent]:
        """Notice brand-new session logs in this repo and start tailing them."""
        events: list[FeedEvent] = []
        for log in self.projects_dir.glob("*/*.jsonl"):
            lp = str(log)
            if lp in self._known_logs:
                continue
            self._known_logs.add(lp)
            cwd = self._peek_cwd(log)
            if not cwd or not self._in_repo(cwd):
                continue
            s = Session(session_id=log.stem, log_path=lp, cwd=cwd, last_activity=_now())
            t = self._add_tailer(s)   # new log: tail from the top, it's all fresh
            events.append(FeedEvent(kind="session", when=_now(),
                                    session_id=s.session_id, title=s.title,
                                    message="new session"))
            events.extend(t.read_new())
        return events

    @staticmethod
    def _peek_cwd(log: Path) -> str | None:
        try:
            with open(log, errors="replace") as f:
                for _ in range(50):
                    line = f.readline()
                    if not line:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("cwd"):
                        return obj["cwd"]
        except OSError:
            pass
        return None

    def _filtered(self, events: list[FeedEvent]) -> list[FeedEvent]:
        if self.quiet:
            events = [e for e in events
                      if e.kind in ("file", "commit", "push", "unattributed")]
        for e in events:
            self._counts[e.kind] = self._counts.get(e.kind, 0) + 1
            if e.kind == "file" and e.path:
                self._files_touched.add(e.path)
        return events

    # -- vitals + parting snapshot -------------------------------------------

    def vitals(self) -> Vitals:
        now = _now()
        live = [LiveSessionInfo(session_id=sid, title=t.session.title,
                                objective=self._briefs.get(sid),
                                last_append=t.last_append)
                for sid, t in self.tailers.items()
                if now - t.last_append <= LIVE_THRESHOLD]
        live.sort(key=lambda l: l.last_append, reverse=True)
        return Vitals(repo=self.name, path=self.checkouts[0],
                      branch=self.git.main_branch(),
                      dirty=self.git.dirty_count(), live=live)

    def parting_snapshot(self) -> str:
        """The plain-stdout lines printed after the alt-screen closes."""
        mins = max(1, round((time.monotonic() - self._started) / 60))
        c = self._counts
        seen = []
        n_sessions = len({sid for sid, t in self.tailers.items()})
        if n_sessions:
            seen.append(f"{n_sessions} session{'s' if n_sessions != 1 else ''}")
        if self._files_touched:
            n = len(self._files_touched)
            seen.append(f"{n} file{'s' if n != 1 else ''} touched")
        if c.get("commit"):
            seen.append(f"{c['commit']} commit{'s' if c['commit'] != 1 else ''}")
        if c.get("push"):
            seen.append(f"{c['push']} push{'es' if c['push'] != 1 else ''}")
        if c.get("unattributed"):
            seen.append(f"{c['unattributed']} unattributed change{'s' if c['unattributed'] != 1 else ''}")
        v = self.vitals()
        head = f"watched {self.name} for {mins}m — " + (", ".join(seen) if seen else "no activity")
        tail = f"now: {v.dirty} dirty on {v.branch}" if v.dirty else f"now: clean on {v.branch}"
        return head + "\n" + tail
