"""The Watch's event stream: JSONL tail (claims) + git observation (ground truth).

Plain Python by design (ADR 0004 § the stream/UI boundary): this module never
imports textual. The watch UI is just one consumer of the typed Feed Events
produced here.

The split mirrors Attribution: the session log claims *who and what* (the exact
Edit text, the Bash command, the prompt); git confirms tree state and alone
reveals live Unattributed Changes. Live Session is a recency claim — a log
appended within the Live window (LIVE_THRESHOLD by default, widened per run by
`standup watch --since`) — never a process fact (CONTEXT.md).
"""

from __future__ import annotations

import difflib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brief as brief_mod
from . import claude_logs, gitstate, handles, toolcalls, unidiff, universe
from .models import Session

LIVE_THRESHOLD = timedelta(minutes=30)  # Live Session recency claim (CONTEXT.md)
GIT_POLL_INTERVAL = 2.0        # seconds between git status/HEAD polls
PUSH_POLL_EVERY = 5            # push check once per N git polls
DISCOVERY_INTERVAL = 10.0      # seconds between scans for new session logs
# A subagent transcript, in Claude Code's layout: it lives under its parent
# Session's directory (<proj>/<parent-session-id>/subagents/agent-<id>.jsonl),
# one level below where top-level Session logs sit — which is why the ordinary
# `*/*.jsonl` glob can never find one (ADR 0004 § the worktree lane).
SUBAGENT_GLOB = "*/*/subagents/*.jsonl"
BACKFILL_CAP = 400             # newest events replayed at launch, all sessions

# Activity State verbs (CONTEXT.md): the pending tool call read as one word.
# An unmapped tool falls to "acting" — true of anything, so a new or MCP tool
# never needs a table entry to stay honest.
ACT_VERBS = {
    "Read": "reading", "Grep": "reading", "Glob": "reading",
    "NotebookRead": "reading", "WebFetch": "reading", "WebSearch": "reading",
    "Write": "writing", "Edit": "writing", "MultiEdit": "writing",
    "NotebookEdit": "writing",
    "Bash": "running", "BashOutput": "running", "KillShell": "running",
}
ACT_FALLBACK = "acting"
ACT_THINKING = "thinking"
# Display floor for a tool verb (ADR 0004 § the Activity State). A local Read
# returns in ~25ms, so bound to its own execution window `reading` was never on
# screen long enough to be read by a human — measured over eight of this repo's
# sessions, `reading` held the state for 7 seconds in total and `writing` for
# 8, against 6740 for `thinking`. A tool verb therefore holds the band for at
# least this long before `thinking` may replace it. It never delays a *settled*
# session going blank, and never delays another tool verb.
ACT_FLOOR = timedelta(seconds=1.0)

_COMMIT_RE = claude_logs.COMMIT_LINE_RE


class WatchError(Exception):
    pass


@dataclass
class CommitFile:
    """One file's contribution to a commit: the same added/removed shape a file
    event carries, so the Watch renders committed and uncommitted change the
    same way. Committing must not make a change unreadable."""
    path: str
    change: str                  # create | modify | delete | rename
    added: str = ""
    removed: str = ""


@dataclass
class FeedEvent:
    """One entry in the Watch (CONTEXT.md → Feed Event)."""
    kind: str                    # file | call | call_result | prompt | commit |
                                 # branch | push | unattributed | session | worktree
    when: datetime
    session_id: str | None = None
    title: str = ""              # session title (display context)
    path: str | None = None      # repo-relative, for file events
    change: str | None = None    # create | modify | delete
    added: str = ""              # text written (drives the animation)
    removed: str = ""            # text replaced
    # A Call (CONTEXT.md → Call): `tool` is the display name, `command` is set
    # for `Bash` alone — the one tool whose argument is a shell command and so
    # renders `$ …` with shell lexing — and `args` is every other tool's input
    # digest. `command` and `args` are never both set.
    #
    # `tool_input` is the input **itself**, kept because a digest cannot be
    # expanded back into what it summarised: `args` used to be the only copy the
    # feed held, so `{page_id, command, content_updates}` reached the UI as the
    # 14-character string `update_content` and `enter` had nothing to open. The
    # header still reads from `args`; the body reads from here (ADR 0004 § Calls).
    tool: str | None = None
    command: str | None = None
    args: str = ""
    tool_input: dict | None = None
    ok: bool | None = None       # call_result verdict
    tool_id: str | None = None   # joins call -> call_result; on a file event, the
                                 # tool call it came from, so a Change Run can
                                 # count calls rather than hunks (one MultiEdit
                                 # spans several events but is one call)
    # Does this file event *replace* the picture of its path, or add to it? A
    # Session claims hunks, which accumulate; the git watcher states the whole
    # delta of a dirty file, which supersedes what it last said (ADR 0004 § the
    # Change Run). The Watch needs the distinction to fold a Change Run without
    # lying about the counts, and it is a property of the witness, not a UI
    # guess.
    restates: bool = False
    message: str = ""            # prompt text / commit subject / free text
    sha: str | None = None
    files: list[CommitFile] = field(default_factory=list)  # commit events' diff
    backfill: bool = False

    @property
    def handle(self) -> str | None:
        return self.session_id[:8] if self.session_id else None


@dataclass
class Activity:
    """A Session's Activity State (CONTEXT.md): what the agent is doing *now*.

    Only ever constructed for a session mid-turn — a settled session has no
    Activity at all, which is what lets the Watch say nothing about it. `verb`
    is a fact read off the log for every value but `thinking`, which is
    inferred from *silence*: the log records a tool call and its result, never
    the pause between them, so the pause is all there is to read.

    `since` is the timestamp of the line that put the session in this state, so
    the age is right at launch too — backfill walks the whole file and leaves
    the state at the true tail.

    A tool verb is subject to a display floor (`ACT_FLOOR`) applied when the
    state is read, not when it is tracked: `Read` returns in milliseconds, and a
    verb no one can see answers nothing. `WatchStream._activity_of` holds it.
    """
    verb: str
    since: datetime


@dataclass
class LiveSessionInfo:
    session_id: str
    title: str
    objective: str | None        # Session Brief claim, when one exists
    last_append: datetime
    num: int = 0                 # stable lane number, assigned first-seen —
                                 # never re-sorted, so `[2]` stays session 2
    activity: Activity | None = None   # None once the turn is over

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
    # the most recently appended Session even when nothing is live, so the
    # quiet header can state the absence with its last handle ("no live
    # session · last log append 42m ago")
    last: LiveSessionInfo | None = None


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Tailer:
    """Incremental reader of one Session's JSONL: new complete lines -> Feed Events."""

    def __init__(self, session: Session, repo_paths: list[str]):
        self.session = session
        self.path = Path(session.log_path)
        self.offset = 0
        # The WatchStream's own root list, shared by reference and mutated in
        # place when a worktree appears or vanishes mid-watch — a tailer that
        # copied it would keep resolving against the launch-time checkouts.
        # Already realpath'd (agent-written paths and git toplevels may disagree
        # about symlinks — macOS /var vs /private/var — so `_rel` realpaths its
        # side too) and ordered longest-first, so a file in a worktree nested
        # under the main checkout resolves against the worktree, not as a
        # `.claude/worktrees/…` path relative to main.
        self.repo_paths = repo_paths
        # A subagent transcript (…/<parent-session>/subagents/<agent>.jsonl) is
        # one sidechain, tailed as its own lane — its lines are all marked
        # `isSidechain`, and for *this* tailer they are not someone else's.
        self.subagent = "/subagents/" in session.log_path
        self.pending_calls: dict[str, datetime] = {}  # tool_use id -> when issued
        self.claimed_shas: set[str] = set()           # commit hashes seen in results
        self.edited_paths: set[str] = set(session.edited_files)
        self.last_append: datetime = session.last_activity or _now()
        # Activity State: the verb the tail leaves us in, None when settled
        self.act_verb: str | None = None
        self.act_since: datetime = self.last_append
        # the last tool verb announced in this turn, kept alongside the truth so
        # the display floor has something to hold; cleared when the turn settles
        self.act_tool: str | None = None
        self.act_tool_since: datetime = self.last_append
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
        return "create" if gitstate.is_untracked(root, rel) else "modify"

    def _track_activity(self, obj: dict, ts: datetime) -> None:
        """Advance the Activity State from one log line (CONTEXT.md).

        Four transitions, in the order they have to be tested:

        - an **interrupt** settles the session. `interruptedMessageId` (Esc) and
          `interruptedByShutdown` (the session quit mid-turn) both arrive as
          plain user lines, so this must be checked before the prompt reading —
          the line's text is `[Request interrupted by user]`, which would
          otherwise look like you asking a question. Without it the state would
          read `thinking` for as long as the Watch stays open.
        - `stop_reason == "tool_use"` *and* a `tool_use` block on the line names
          the call about to run: its verb. The stop reason alone is not enough —
          see the block comment below.
        - any other `stop_reason` ends the turn — the agent handed control back.
        - a tool result, or your prompt, leaves the model composing: `thinking`,
          the one verb no line ever states.

        Sidechain lines inside a *parent* log are skipped: those reads are not
        this session's, and several running at once have no single answer. A
        subagent tailer's whole log is one sidechain, so there the flag carries
        no such ambiguity and tracking proceeds (ADR 0004 § the worktree lane).
        """
        if obj.get("isSidechain") and not self.subagent:
            return
        etype = obj.get("type")
        message = obj.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None

        if etype == "assistant":
            if message.get("stop_reason") != "tool_use":
                self.act_verb, self.act_since = None, ts
                self.act_tool = None      # settled: nothing may be held over
                return
            name = None
            if isinstance(content, list):
                for b in content:      # parallel calls: the last one announced
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name")
            if name is None:
                # `stop_reason` belongs to the whole assistant *message*, but the
                # message's blocks are flushed as separate lines — a preamble
                # `text` block and an extended `thinking` block each land on
                # their own line carrying the same `tool_use` stop reason. So the
                # stop reason says "a tool comes later in this message", not
                # "this line announces one". Over eight of this repo's own
                # sessions, 315 of 838 such lines named no tool (231 thinking,
                # 84 text) and every one of them was read as `acting` — a verb
                # reserved for a tool absent from the table. A line that names no
                # tool is the model still composing, so leave the state (and its
                # age) exactly where the previous line left it.
                return
            self.act_verb = ACT_VERBS.get(name, ACT_FALLBACK)
            self.act_since = ts
            self.act_tool, self.act_tool_since = self.act_verb, ts
            return

        if etype != "user" or obj.get("isMeta"):
            return
        if "interruptedMessageId" in obj or "interruptedByShutdown" in obj:
            self.act_verb, self.act_since = None, ts
            self.act_tool = None          # cut short: nothing may be held over
            return
        returned = obj.get("toolUseResult") is not None or (
            isinstance(content, list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content))
        if returned or claude_logs.prompt_text(content):
            self.act_verb, self.act_since = ACT_THINKING, ts

    def _events_from_obj(self, obj: dict) -> list[FeedEvent]:
        sid, title = self.session.session_id, self.session.title
        ts = _parse_ts(obj.get("timestamp")) or _now()
        etype = obj.get("type")
        events: list[FeedEvent] = []
        # every line advances the state, including on the backfill pass — which
        # walks the whole file, so a session mid-turn at launch is already in
        # the right state before its first live line arrives
        self._track_activity(obj, ts)

        if etype == "user":
            tr = obj.get("toolUseResult")
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id")
                        if tid in self.pending_calls:
                            del self.pending_calls[tid]
                            events.append(FeedEvent(
                                kind="call_result", when=ts, session_id=sid, title=title,
                                tool_id=tid, ok=not bool(b.get("is_error"))))
            if tr is not None:
                text = tr if isinstance(tr, str) else (tr.get("stdout") or "") if isinstance(tr, dict) else ""
                for m in _COMMIT_RE.finditer(text):
                    self.claimed_shas.add(m.group(1))
            prompt = claude_logs.prompt_in(obj, ts)
            if prompt is not None:
                events.append(FeedEvent(kind="prompt", when=ts, session_id=sid,
                                        title=title, message=prompt.text))
            return events

        if etype != "assistant":
            return events
        for call in claude_logs.tool_calls_in(obj, ts):
            name, inp = call.name, call.input
            if not name or name in toolcalls.SILENT_TOOLS:
                continue      # a local read narrates nothing the feed can show
            if name not in claude_logs.EDIT_TOOLS:
                # a Call (ADR 0004 § Calls): every tool call that changes no
                # file, Bash included — it is the one whose argument is a shell
                # command, so it carries `command` and the rest carry `args`
                self.pending_calls[call.tool_id] = ts
                cmd = inp.get("command") if name == "Bash" else None
                cmd = cmd if isinstance(cmd, str) and cmd.strip() else None
                events.append(FeedEvent(
                    kind="call", when=ts, session_id=sid, title=title,
                    tool_id=call.tool_id, tool=toolcalls.display_name(name),
                    command=cmd,
                    # generous: the header clips at its own width and the
                    # expanded body folds, so the event carries more than one
                    # row's worth rather than deciding the display's limit here
                    args="" if cmd else toolcalls.arg_digest(inp, 2000),
                    # unclipped and unflattened — the body is the request, not a
                    # summary of it. Median 173 bytes across this machine's logs,
                    # p99 4KB, so a full BACKFILL_CAP of Calls costs ~200KB.
                    tool_input=inp))
                continue
            events.extend(self._file_events(call, ts))
        return events

    def _file_events(self, call: claude_logs.ToolCall,
                     ts: datetime) -> list[FeedEvent]:
        """One file-touching call as Feed Events — a projection of the reader's
        edit blocks (ADR 0001 § the one log reader), never a second reading of
        the `tool_use` block.

        One block per hunk, so a MultiEdit lands as several events; they carry
        the same `tool_id`, which is the only thing that remembers they were a
        single action by the agent.
        """
        sid, title = self.session.session_id, self.session.title
        events: list[FeedEvent] = []
        for e in claude_logs.edits_of(call):
            loc = self._rel(e.path)
            if loc is None:
                continue  # the session touched a file outside this Repo Entry
            root, rel = loc
            self.edited_paths.add(os.path.realpath(e.path))
            if e.tool == "MultiEdit" and not e.new and not e.old:
                # the reader's path-only block: a MultiEdit whose hunks it could
                # not read still attributes the file, but there is no text for
                # the feed to narrate and an empty diff body would state a
                # change nobody made
                continue
            change = "modify"
            if e.tool == "Write":
                # during backfill the tree has long moved on — don't ask git
                change = ("modify" if self._backfilling
                          else self._write_change(root, rel))
            events.append(FeedEvent(kind="file", when=ts, session_id=sid,
                                    title=title, path=rel, change=change,
                                    tool_id=call.tool_id or None,
                                    added=e.new, removed=e.old))
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


MAX_SNAPSHOT_BYTES = 200_000   # dirty files beyond this aren't content-diffed
MAX_COMMIT_DIFF_BYTES = 400_000  # commits beyond this aren't diffed (header only)


class _GitWatcher:
    """Polls git for ground truth: content deltas of dirty files, HEAD moves,
    branch switches, pushes. When no tailed Session explains a change, the
    watcher itself supplies the content: it snapshots every dirty file and
    emits a real file event with the *cumulative* line-diff against a reference
    snapshot — so a repo with no Claude sessions at all still narrates (status
    codes alone can't: a file that is already ` M` and changes again never
    changes code). Binary or oversized files degrade to a one-line Unattributed
    Change.

    Cumulative, not incremental (ADR 0004 § the Change Run). Polling every
    GIT_POLL_INTERVAL chops one burst of writing into one delta per window, and
    a run of `+8 +4 +1 +1` says more about the poll rate than about the change.
    Each event therefore restates the whole delta of the path, and the Watch
    folds the run into one Change Run whose counts are the true net figure — a
    line added and then removed inside the run cancels instead of being counted
    twice."""

    def __init__(self, checkouts: list[str]):
        # the WatchStream's own list, shared by reference: it appends/removes
        # as worktrees appear and vanish mid-watch, bracketed by adopt/forget
        self.checkouts = checkouts
        self.status: dict[str, dict[str, str]] = {}    # checkout -> path -> code
        self.head: dict[str, str] = {}
        self.branch: dict[str, str] = {}
        self.unpushed: dict[str, int] = {}
        self._polls = 0
        # (checkout, path) -> last-seen content / stat fingerprint of dirty files
        self._content: dict[tuple[str, str], str | None] = {}   # None = undiffable
        self._fp: dict[tuple[str, str], tuple[int, int] | None] = {}
        # (checkout, path) -> the snapshot a cumulative diff is measured *from*.
        # HEAD's version for a file that dirties while the Watch runs; the
        # adoption snapshot for dirt that predates it, because pre-watch dirt is
        # old news and replaying it as one giant event would bury the live
        # narrative. Costs a second copy of each dirty file's text alongside
        # `_content`, bounded by MAX_SNAPSHOT_BYTES per path.
        self._base: dict[tuple[str, str], str | None] = {}
        for co in checkouts:
            self.adopt(co)

    def adopt(self, co: str) -> None:
        """Start watching a checkout, seeding silently: dirt that predates the
        adoption is old news, no events. A worktree adopted mid-watch (ADR 0004
        § the worktree lane) is at most one discovery interval old, so what the
        seed swallows is bounded — and its Session's claims narrate it anyway."""
        self.status[co] = gitstate.status(co, untracked_all=True)
        self.head[co] = gitstate.head_sha(co)
        self.branch[co] = gitstate.branch(co)
        self.unpushed[co] = gitstate.unpushed_count(co)
        for p in self.status[co]:
            key = (co, p)
            self._fp[key] = self._stat(co, p)
            self._content[key] = self._read(co, p)
            self._base[key] = self._content[key]

    def forget(self, co: str) -> None:
        """Drop a checkout that no longer exists (worktrees are removed or
        auto-cleaned). State only — the caller owns the `checkouts` list."""
        for d in (self.status, self.head, self.branch, self.unpushed):
            d.pop(co, None)
        for key in [k for k in self._fp if k[0] == co]:
            del self._fp[key]
            self._content.pop(key, None)
            self._base.pop(key, None)

    @staticmethod
    def _stat(co: str, p: str) -> tuple[int, int] | None:
        try:
            st = os.stat(os.path.join(co, p))
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None    # gone (deleted, or a rename's old path)

    @staticmethod
    def _read(co: str, p: str) -> str | None:
        """Current content of a dirty file; None when it can't be diffed
        (binary, oversized, unreadable). A deleted file reads as ''."""
        fp = os.path.join(co, p)
        if not os.path.exists(fp):
            return ""
        try:
            if os.path.getsize(fp) > MAX_SNAPSHOT_BYTES:
                return None
            with open(fp, errors="replace") as f:
                text = f.read()
        except OSError:
            return None
        return None if "\x00" in text else text

    def _baseline(self, co: str, p: str, code: str) -> str | None:
        """What the file looked like before it went dirty: HEAD's version for
        tracked files, empty for untracked (brand-new) ones."""
        if code.startswith("?"):
            return ""
        out = gitstate.file_at_head(co, p)
        if out is None:
            return ""
        return None if "\x00" in out or len(out) > MAX_SNAPSHOT_BYTES else out

    @staticmethod
    def _line_diff(old: str, new: str) -> tuple[str, str]:
        """(added, removed) line blocks between two snapshots — the dirty-file
        counterpart of `_commit_files`, and the same flat shape
        (ADR 0004 § the stream/UI boundary).

        Read off the matcher's **opcodes**, never off a formatted diff. This
        used to call `difflib.unified_diff` and strain `---`/`+++` back out of
        its text, which is the same mistake the Watch's own git-diff parser
        made: those are file headers before the first hunk and ordinary rows
        inside one, so a line reading `-- x` vanished — and a change consisting
        only of such lines produced no Feed Event at all. Formatting a diff in
        order to parse it back is what created the bug; the ranges were already
        there. Same opcodes `unified_diff` would have grouped, so the blocks are
        unchanged for every other input.
        """
        a, b = old.splitlines(), new.splitlines()
        added: list[str] = []
        removed: list[str] = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
            if tag in ("replace", "delete"):
                removed += a[i1:i2]
            if tag in ("replace", "insert"):
                added += b[j1:j2]
        return "\n".join(added), "\n".join(removed)

    @staticmethod
    def _commit_files(co: str, sha: str) -> list[CommitFile]:
        """One commit's per-file added/removed blocks, **projected** from the
        shared unified-diff parser rather than re-read here.

        `-U0` is what makes the projection flat: with no context rows, every
        row of every hunk is an addition or a deletion, which is the same shape
        `_line_diff` produces for dirty files — so a committed change reads
        exactly like an uncommitted one. Hunk boundaries and line numbers are
        dropped on the way through; the Attributed Diff is where those are read
        (ADR 0005 § two grammars).

        Returns `[]` when there is nothing to show rather than guessing: a merge
        commit (git's default `show` prints no combined diff), a diff past
        MAX_COMMIT_DIFF_BYTES, or an unreadable object. The Watch then renders
        the commit as a bare header line, which is honest — no diff was read."""
        out = gitstate.commit_diff(co, sha, context=0)
        if not out or len(out) > MAX_COMMIT_DIFF_BYTES:
            return []
        files: list[CommitFile] = []
        for fd in unidiff.parse(out):
            added, removed = fd.added_lines, fd.removed_lines
            # A CommitFile *is* its two blocks, with no field to say why it has
            # neither — so a binary blob, a mode change and an empty new file
            # stay out of the list rather than render as a `+0` that claims an
            # empty change. A rename is the exception: the move is the change.
            if added or removed or fd.change == unidiff.RENAME:
                files.append(CommitFile(path=fd.path, change=fd.change,
                                        added="\n".join(added),
                                        removed="\n".join(removed)))
        # deliberately uncapped: MAX_COMMIT_DIFF_BYTES already bounds the work,
        # and a truncated list would make the header's "N files" a lie
        return files

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
            if not os.path.isdir(co):
                continue   # a just-removed worktree; the next refresh forgets it
            new_status = gitstate.status(co, untracked_all=True)
            plain: list[str] = []    # changed but undiffable -> one-line event
            for p, code in new_status.items():
                key = (co, p)
                fp = self._stat(co, p)
                known = key in self._fp
                if known and fp == self._fp[key]:
                    continue          # dirty but untouched since last poll
                self._fp[key] = fp
                if not known:
                    self._base[key] = self._baseline(co, p, code)
                old = self._content[key] if known else self._base[key]
                base = self._base[key]
                cur = self._read(co, p)
                self._content[key] = cur
                if os.path.realpath(os.path.join(co, p)) in explained:
                    continue          # a tailed Session already narrated this
                if old == cur:
                    continue          # e.g. only staged/unstaged flip, same bytes
                if cur is None:
                    plain.append(p)   # can't be read at all right now
                    continue
                if base is None:
                    # the *reference* is undiffable (binary or oversized when it
                    # was first seen), so no cumulative delta exists to state.
                    # Say a change happened and adopt the current snapshot, so
                    # the next one is measurable rather than stranded forever.
                    self._base[key] = cur
                    plain.append(p)
                    continue
                # the whole delta since the reference snapshot, not since the last
                # poll: the event restates the path rather than adding to it
                added, removed = self._line_diff(base, cur)
                if not added and not removed:
                    # back to the reference snapshot. Nothing is emitted, so the
                    # Change Run on screen keeps its last figures — it states the
                    # delta *as of its last update*, which is what every other
                    # entry in the feed does too (ADR 0004 § the Change Run)
                    continue
                change = ("delete" if cur == "" and code.strip().startswith("D")
                          else "create" if base == "" and code.startswith("?")
                          else "modify")
                events.append(FeedEvent(
                    kind="file", when=now, session_id=None, restates=True,
                    path=p, change=change, added=added, removed=removed))
            for key in [k for k in self._fp if k[0] == co and k[1] not in new_status]:
                del self._fp[key]     # went clean (committed/restored): drop and
                del self._content[key]  # re-baseline from HEAD if it dirties again
                del self._base[key]
            if plain:
                events.append(FeedEvent(
                    kind="unattributed", when=now,
                    message=", ".join(sorted(plain)[:5]) +
                            (f" +{len(plain) - 5} more" if len(plain) > 5 else "")))
            self.status[co] = new_status

            new_branch = gitstate.branch(co)
            if new_branch != self.branch[co]:
                events.append(FeedEvent(kind="branch", when=now,
                                        message=f"{self.branch[co]} → {new_branch}"))
                self.branch[co] = new_branch

            new_head = gitstate.head_sha(co)
            if new_head and new_head != self.head[co]:
                commits = (gitstate.commits_between(co, self.head[co], new_head)
                           if self.head[co] else [])
                if not commits:
                    # rebase/amend/reset: old..new is empty or unwalkable, so
                    # the new HEAD is all there is to report
                    one = gitstate.commit_meta(co, new_head)
                    commits = [one] if one else []
                for c in commits:
                    sid = attribute(c.sha)
                    events.append(FeedEvent(kind="commit", when=now, session_id=sid,
                                            sha=c.short, message=c.subject,
                                            files=self._commit_files(co, c.sha)))
                self.head[co] = new_head

            if self._polls % PUSH_POLL_EVERY == 0:
                n = gitstate.unpushed_count(co)
                if n < self.unpushed[co] and self.head[co] == new_head:
                    pushed = self.unpushed[co] - n
                    # the branch, never "origin/…": the count comes from
                    # --not --remotes, which doesn't say *which* remote took it
                    events.append(FeedEvent(
                        kind="push", when=now, sha=new_head[:7],
                        message=f"{self.branch[co]}  "
                                f"{pushed} commit{'s' if pushed != 1 else ''}"))
                self.unpushed[co] = n
        return events


def _resolve_target(repo_arg: str, u: universe.Universe) -> tuple[str, list[str]]:
    """Project Handle / name / filesystem path -> (name, checkout toplevels).

    A path is resolved through git directly, so a repo with no sessions yet is
    still watchable — the Watch is the one view that does not need the Scan
    Universe to have heard of a repo. A bare word goes through the Universe's
    resolver, so `pm` means here exactly what it means in the inbox — over its
    *git* Repo Entries only: this view's ground truth is git, so a Session's
    non-repo directory is a name it could never narrate.
    """
    if handles.looks_like_path(repo_arg):
        path = os.path.abspath(os.path.expanduser(repo_arg))
        if not os.path.isdir(path):
            raise WatchError(f"standup watch: no such directory: {repo_arg}")
        owner = universe.owner_of(path)
        if owner is None or not owner.is_repo:
            raise WatchError(f"standup watch: {repo_arg!r} is not inside a git repo")
        main = owner.path
    else:
        try:
            main = u.resolve_repo(repo_arg, u.targets(repos_only=True),
                                  prog="standup watch").path
        except handles.HandleError as e:
            raise WatchError(str(e)) from None
    checkouts = [w for w in (gitstate.worktrees(main) or []) if os.path.isdir(w)]
    if not checkouts:
        # a Repo Entry the Scan Universe still remembers, whose checkout is gone
        raise WatchError(
            f"standup watch: {handles.shorten_home(main)} is not on disk any more")
    return os.path.basename(checkouts[0].rstrip("/")), checkouts


class WatchStream:
    """Orchestrates tailers + git watcher for one Repo Entry.

    The UI drives it by calling poll() on a short interval; internal rate
    limiting keeps git subprocesses and discovery scans on their own cadence.
    """

    def __init__(self, u: universe.Universe, repo_arg: str, quiet: bool = False,
                 live_window: timedelta | None = None):
        # The Universe is read here and not kept: a Watch runs for minutes and
        # has no business holding the Derived Cache open for them. Everything it
        # needs afterwards is the log directory, which it globs directly for
        # sessions and subagent transcripts that appear mid-run.
        self.projects_dir = u.projects_dir
        self.quiet = quiet
        # The recency claim, widenable per run (`--since`): it decides both which
        # Sessions this Watch picks up and which ones the header still calls
        # live. One window for both, because a lane in the feed that has no row
        # in the header is a Session you can filter to but cannot see.
        self.live_window = live_window or LIVE_THRESHOLD
        sessions = u.sessions()
        self.name, self.checkouts = _resolve_target(repo_arg, u)
        # longest-first, so a path inside a worktree nested under the main
        # checkout (`.claude/worktrees/…`) resolves to the worktree's root, not
        # to a `.claude/…`-relative path under main. Shared by reference with
        # every tailer and mutated in place by `_refresh_checkouts`.
        self._roots = sorted((os.path.realpath(c).rstrip("/") for c in self.checkouts),
                             key=len, reverse=True)
        self.tailers: dict[str, _Tailer] = {}
        self._nums: dict[str, int] = {}        # session_id -> stable lane number
        self._known_logs: set[str] = {s.log_path for s in sessions}
        self._briefs: dict[str, str] = {}
        self._counts: dict[str, int] = {}      # parting-snapshot tallies
        self._files_touched: set[str] = set()
        self._started = time.monotonic()
        self._last_git = 0.0
        self._last_discovery = time.monotonic()

        now = _now()
        here = [s for s in sessions
                if s.cwd and self._in_repo(s.cwd)
                and s.last_activity and now - s.last_activity <= self.live_window]
        here += self._scan_subagents(now)
        # oldest first: lane numbers are first-seen and never re-sorted, so the
        # longest-running session is [1] and stays [1]
        here.sort(key=lambda s: s.last_activity)
        for s in here:
            self._add_tailer(s)
        self.git = _GitWatcher(self.checkouts)

    # -- setup helpers -------------------------------------------------------

    def _in_repo(self, cwd: str) -> bool:
        cwd = os.path.realpath(cwd).rstrip("/")
        return any(cwd == r or cwd.startswith(r + "/") for r in self._roots)

    def _scan_subagents(self, now: datetime) -> list[Session]:
        """Subagent transcripts already on disk that belong to this Repo Entry
        and are inside the Live window — the same recency claim top-level
        Sessions answer to. Every transcript found is marked known, lane or
        not, so `_discover` never replays a long-finished agent as brand new."""
        found: list[Session] = []
        for log in self.projects_dir.glob(SUBAGENT_GLOB):
            lp = str(log)
            if lp in self._known_logs:
                continue
            self._known_logs.add(lp)
            try:
                mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if now - mtime > self.live_window:
                continue          # long settled: not even worth peeking into
            s = self._subagent_session(log, mtime)
            if s and self._in_repo(s.cwd):
                found.append(s)
        return found

    def _subagent_session(self, log: Path,
                          mtime: datetime | None = None) -> Session | None:
        """A subagent transcript as a Session lane (ADR 0004 § the worktree
        lane). Identity is the agent id — the `agent-` file prefix dropped, so
        the lane handle reads like any Session Handle — and the title is the
        spawn description from the sibling `.meta.json`, the one place the
        parent's intent for this agent is written down. The cwd is peeked from
        the log itself: a worktree agent's cwd *is* its worktree, which is all
        the repo membership check needs."""
        cwd = self._peek_cwd(log)
        if not cwd:
            return None
        if mtime is None:
            try:
                mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
            except OSError:
                mtime = _now()
        s = Session(session_id=log.stem.removeprefix("agent-"),
                    log_path=str(log), cwd=cwd, last_activity=mtime)
        try:
            with open(log.with_suffix(".meta.json"), errors="replace") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            meta = {}
        if isinstance(meta.get("description"), str) and meta["description"]:
            s.custom_title = meta["description"]
        return s

    def _add_tailer(self, session: Session) -> _Tailer:
        t = _Tailer(session, self._roots)
        self.tailers[session.session_id] = t
        self._nums[session.session_id] = len(self._nums) + 1
        b = brief_mod.load_one(session.session_id)
        if b:
            self._briefs[session.session_id] = b.objective
        return t

    def session_num(self, session_id: str) -> int:
        """The session's stable lane number (1-based, first-seen order)."""
        return self._nums.get(session_id, 0)

    # -- the stream ----------------------------------------------------------

    def start(self) -> list[FeedEvent]:
        """Backfill: *every* Live Session replays its current chapter — the
        events since its own latest user prompt — interleaved chronologically.

        Per-session rather than newest-only, so filtering to `[2]` has something
        to show: a session that committed its work and went quiet still has a
        chapter, and its edits are the only place that change is legible once
        the tree is clean. Oldest events past BACKFILL_CAP are dropped."""
        events: list[FeedEvent] = []
        for t in self.tailers.values():
            events.extend(t.backfill())
        events.sort(key=lambda e: e.when)
        if len(events) > BACKFILL_CAP:
            events = events[-BACKFILL_CAP:]
        return self._filtered(events)

    def poll(self) -> list[FeedEvent]:
        now_m = time.monotonic()
        events: list[FeedEvent] = []
        for t in list(self.tailers.values()):
            events.extend(t.read_new())

        if now_m - self._last_discovery >= DISCOVERY_INTERVAL:
            self._last_discovery = now_m
            # checkouts first: a worktree that appeared alongside its agent must
            # be a root before the agent's cwd is checked against the roots
            events.extend(self._refresh_checkouts())
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

    def _refresh_checkouts(self) -> list[FeedEvent]:
        """Re-ask git for the worktree list and fold the answer into the
        watched set (ADR 0004 § the worktree lane). Claude Code creates its
        worktrees mid-run (`.claude/worktrees/…`), so a launch-time list goes
        stale exactly when an agent starts working. The shared root list is
        mutated in place — every live tailer resolves against the current set —
        and adoption seeds silently, so only what happens *after* counts."""
        answer = gitstate.worktrees(self.checkouts[0])
        if answer is None:
            return []      # a transient git failure must not read as removals
        listed = {w for w in map(os.path.realpath, answer) if os.path.isdir(w)}
        events: list[FeedEvent] = []
        for w in sorted(listed - {os.path.realpath(c) for c in self.checkouts}):
            self.checkouts.append(w)
            self._roots.append(w.rstrip("/"))
            self._roots.sort(key=len, reverse=True)   # keep deepest-first matching
            self.git.adopt(w)
            events.append(FeedEvent(
                kind="worktree", when=_now(),
                message=f"{os.path.basename(w)} appeared — {gitstate.branch(w)}"))
        for c in list(self.checkouts[1:]):            # main never leaves
            if os.path.realpath(c) in listed:
                continue
            self.checkouts.remove(c)
            r = os.path.realpath(c).rstrip("/")
            if r in self._roots:
                self._roots.remove(r)
            self.git.forget(c)
            events.append(FeedEvent(kind="worktree", when=_now(),
                                    message=f"{os.path.basename(c)} removed"))
        return events

    def _discover(self) -> list[FeedEvent]:
        """Notice brand-new session logs in this repo — top-level Sessions and
        subagent transcripts alike — and start tailing them."""
        events: list[FeedEvent] = []
        for log in (*self.projects_dir.glob("*/*.jsonl"),
                    *self.projects_dir.glob(SUBAGENT_GLOB)):
            lp = str(log)
            if lp in self._known_logs:
                continue
            self._known_logs.add(lp)
            if "/subagents/" in lp:
                s = self._subagent_session(log, _now())
            else:
                cwd = self._peek_cwd(log)
                s = (Session(session_id=log.stem, log_path=lp, cwd=cwd,
                             last_activity=_now()) if cwd else None)
            if not s or not self._in_repo(s.cwd):
                continue
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

    @staticmethod
    def _activity_of(t: _Tailer) -> Activity | None:
        """The tail's Activity State, with the tool verb's display floor applied.

        Resolved here rather than in the tailer because the floor is a *reading*
        of the state against the clock, not a transition in the log — the truth
        the tailer holds stays untouched, and the age shown is always the real
        one (a held `reading` reads `0s`, never an inflated figure).

        The floor yields to everything that matters: a settled or interrupted
        turn clears `act_tool`, so the band still goes blank the instant the
        agent hands control back, and a fresh tool verb overwrites immediately.
        It only ever holds a tool verb against `thinking` (ADR 0004 § the
        Activity State).
        """
        if t.act_verb is None:
            return None
        if (t.act_verb == ACT_THINKING and t.act_tool is not None
                and _now() - t.act_tool_since < ACT_FLOOR):
            return Activity(t.act_tool, t.act_tool_since)
        return Activity(t.act_verb, t.act_since)

    def _info(self, sid: str) -> LiveSessionInfo:
        t = self.tailers[sid]
        return LiveSessionInfo(session_id=sid, title=t.session.title,
                               objective=self._briefs.get(sid),
                               last_append=t.last_append,
                               num=self._nums.get(sid, 0),
                               activity=self._activity_of(t))

    def vitals(self) -> Vitals:
        now = _now()
        # stable order (first-seen lane number), never recency-sorted: the
        # header's [n] is an address the feed's lane digits reuse, and an
        # address that re-sorts under you is no address at all
        live = [self._info(sid) for sid, t in self.tailers.items()
                if now - t.last_append <= self.live_window]
        last_sid = max(self.tailers, key=lambda s: self.tailers[s].last_append,
                       default=None)
        return Vitals(repo=self.name, path=self.checkouts[0],
                      branch=self.git.main_branch(),
                      dirty=self.git.dirty_count(), live=live,
                      last=self._info(last_sid) if last_sid else None)

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
