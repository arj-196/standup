"""Read ~/.claude/projects JSONL session logs — the one module that knows their
schema (ADR 0001 § the one log reader; its place beside the Codex reader is
ADR 0001 § two dialects, one reading).

Two entry points, one set of line readings:

- `scan_sessions(projects_dir, cache)` sweeps this root for the Triage
  Inbox's facts (cwd, titles, branches, edited files, captured commit hashes).
  Line-level prefiltering keeps `json.loads` off the ~99% of lines that carry
  none of them. `logs.scan_sessions` is the sweep over every root, and it
  reaches this one through `swept_session`.
- `parse_log(path)` reads *one* log completely into a typed `ParsedLog`
  (`logs.ParsedLog`): the same Session, plus the edit blocks, prompt text and
  per-turn usage the other views need. Every line is parsed, because those live
  on ordinary conversation lines. `logs.read_log` is the cached form.

The **line readings** those two are built from are public in their own right —
`tool_calls_in`, `edits_of`/`edits_in`, `prompt_in`/`prompt_text`,
`turn_usage`, `apply_title_fields`, `is_interrupt` — because a whole-file
reading is the wrong shape for a consumer that never holds the whole file: the
Watch tails a log as it grows, and the Loop detector prefilters lines it will
not count. `Reader` wraps them into the `logs.LineReader` face both dialects
present, so those consumers read a Claude log and a Codex log through one call
and never learn which is which. The wrapped functions read a line the same way
`parse_log` does, so a streaming consumer is a projection of the same reading
rather than a rival one.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import cache as cache_mod
from . import logs, rates, toolcalls
from .logs import (ACTING, COMMIT_LINE_RE, READER_VERSION, SETTLED, THINKING, EditBlock, ParsedLog,
                   Part, Prompt, ToolCall, ToolResult, TurnUsage, UsageTotals,
                   log_mtime, usage_totals)
from .models import Session

__all__ = [
    "EDIT_TOOLS", "ACT_VERBS", "COMMIT_LINE_RE", "READER_VERSION", "SUBAGENT_GLOB",
    "Reader",
    "EditBlock", "ParsedLog", "Prompt", "ToolCall", "TurnUsage", "UsageTotals",
    "usage_totals", "log_mtime", "title_hint", "apply_title_fields",
    "tool_calls_in", "edits_of", "edits_in", "prompt_text", "is_interrupt",
    "prompt_in", "turn_usage", "parse_log", "read_log", "scan_sessions",
    "session_id_of", "cache_id", "session_logs", "readable_logs", "peek_cwd",
    "session_log_ids", "readable_log_ids", "swept_session",
]

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Activity State verbs (ADR 0004 § the Activity State): the pending tool call
# read as one word. An unmapped tool falls to `acting` — true of anything, so a
# new or MCP tool never needs a table entry to stay honest.
ACT_VERBS = {
    "Read": "reading", "Grep": "reading", "Glob": "reading",
    "NotebookRead": "reading", "WebFetch": "reading", "WebSearch": "reading",
    "Write": "writing", "Edit": "writing", "MultiEdit": "writing",
    "NotebookEdit": "writing",
    "Bash": "running", "BashOutput": "running", "KillShell": "running",
}

# A subagent transcript lives under its parent Session's directory
# (<proj>/<parent-session-id>/subagents/agent-<id>.jsonl), one level below where
# top-level Session logs sit — which is why the ordinary `*/*.jsonl` glob can
# never find one (ADR 0004 § the worktree lane).
SUBAGENT_GLOB = "*/*/subagents/agent-*.jsonl"

# cheap hint on the raw JSON line (stdout newlines are escaped as \\n there)
COMMIT_HINT_RE = re.compile(r"\[[^\]\n]{1,80} [0-9a-f]{7,40}\]")

# the noise Claude Code injects into a user turn's text, and the tags a slash
# command arrives wrapped in
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CMD_TAG_RE = re.compile(r"</?command-[^>]*>", re.DOTALL)
# the whole text of an interrupted turn's user line — `[Request interrupted by
# user]`, and the `… for tool use]` variant a refused tool call writes
_INTERRUPT_RE = re.compile(r"^\[Request interrupted by user[^\]]*\]$")


# ── one line of the log ─────────────────────────────────────────────────────
#
# Everything below knows the log's schema; nothing above or beside it does.
# The readings were each re-derived in two or three other modules once, which
# is how a mistyped prefilter in one of the copies could silently demote every
# session title to its last prompt (ADR 0001 § the one log reader).


def title_hint(line: str) -> bool:
    """Does this raw JSONL line plausibly carry one of a Session's title fields?

    The cheap prefilter that lets the inbox's sweep skip `json.loads` on the
    ~99% of lines that hold no title. Paired with `apply_title_fields`, this is
    the one place that knows the log's title schema.
    """
    return '"custom-title"' in line or '"ai-title"' in line or '"last-prompt"' in line


def apply_title_fields(session: Session, obj: dict) -> None:
    """Fold a parsed line's title fields into the Session, honouring precedence.

    Later lines win (a session retitled mid-run keeps the newer name), but a
    field is never overwritten with an empty one. `slug` rides on ordinary
    lines rather than a type of its own, so it is picked up opportunistically
    from whatever lines the prefilter already admitted — never by widening the
    prefilter to every line that mentions it.
    """
    etype = obj.get("type")
    if etype == "custom-title":
        session.custom_title = obj.get("customTitle") or session.custom_title
    elif etype == "ai-title":
        session.ai_title = obj.get("aiTitle") or session.ai_title
    elif etype == "last-prompt":
        session.last_prompt = obj.get("lastPrompt") or session.last_prompt
    if obj.get("slug"):
        session.slug = obj["slug"]


def tool_calls_in(obj: dict, ts: datetime | None = None) -> list[ToolCall]:
    """Every tool call one assistant line recorded, in log order.

    Where the `message.content` walk lives for anything that *acts* on a call —
    the Loop detector's shapes, the Watch's Calls and file events — so no two
    of them re-derive it and disagree about malformed content.
    """
    message = obj.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    uuid = obj.get("uuid") or ""
    out: list[ToolCall] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        inp = item.get("input")
        out.append(ToolCall(name=item.get("name") or "", tool_id=item.get("id") or "",
                            input=inp if isinstance(inp, dict) else {},
                            turn_uuid=uuid, when=ts))
    return out


def edits_of(call: ToolCall) -> list[EditBlock]:
    """The edits one tool call recorded — empty for a call that edits no file.

    The path aliases (`file_path`, `notebook_path`) and the new-text fallbacks
    (`new_string`, `new_source`, `content`) live here and nowhere else: the
    schema is Claude Code's, and reading it in three modules is how two of them
    end up disagreeing about what a NotebookEdit wrote. A relative path
    attributes nothing and is dropped, never guessed at.
    """
    name = call.name
    if name not in EDIT_TOOLS:
        return []
    inp = call.input
    fp = inp.get("file_path") or inp.get("notebook_path")
    if not fp or not os.path.isabs(fp):
        return []
    if name == "MultiEdit":
        hunks = [e for e in inp.get("edits") or [] if isinstance(e, dict)]
        if not hunks:
            # a MultiEdit whose hunks are missing or malformed still says the
            # Session touched this file, and path overlap is the attribution
            # that rests on that alone (CONTEXT.md → Attribution Tier). Dropping
            # the call would silently cost the file its Session Rollup.
            return [EditBlock(name, fp, "", "", call.when, call.tool_id,
                              path_only=True)]
        return [EditBlock(name, fp, e.get("new_string") or "",
                          e.get("old_string") or "", call.when, call.tool_id)
                for e in hunks]
    new = (inp.get("new_string") or inp.get("new_source")
           or inp.get("content") or "")
    return [EditBlock(name, fp, new, inp.get("old_string") or "",
                      call.when, call.tool_id)]


def edits_in(obj: dict, ts: datetime | None = None) -> list[EditBlock]:
    """Every edit one assistant line recorded, in log order."""
    return [e for call in tool_calls_in(obj, ts) for e in edits_of(call)]


def _result_text(obj: dict) -> str:
    """The text of a user line's `toolUseResult`, "" when it carries none."""
    tr = obj.get("toolUseResult")
    if tr is None:
        return ""
    if isinstance(tr, str):
        return tr
    if isinstance(tr, dict):
        return tr.get("stdout") or ""
    return json.dumps(tr)


def _tagged(pattern: re.Pattern, text: str) -> str:
    """The first capture of `pattern` in `text`, stripped; "" when it misses."""
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def prompt_text(content) -> str | None:
    """A user turn's typed text, or None when the turn carried none.

    A slash command arrives as `<command-name>`/`<command-args>` around a body
    nobody typed, and reads back as the command line the user actually entered.
    """
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # a tool_result-only turn carries no user prose
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
    else:
        return None
    if "<command-name>" in text:
        text = f"{_tagged(_CMD_NAME_RE, text)} {_tagged(_CMD_ARGS_RE, text)}".strip()
    text = _CMD_TAG_RE.sub("", _REMINDER_RE.sub("", text)).strip()
    return text or None


def is_interrupt(obj: dict) -> bool:
    """Is this line a turn *cut short* rather than one finished or continued?

    `Esc` mid-turn, the session quitting mid-turn, and a refused tool call all
    arrive as an ordinary `user` line whose whole text is
    `[Request interrupted by user]` — text nobody typed, which a reader looking
    for prompts would take for a question you asked (ADR 0004 § the Activity
    State).

    **The text is the only marker actually written.** Claude Code sometimes
    also tagged the line — `interruptedMessageId` (Esc),
    `interruptedByShutdown` (quit mid-turn) — but rarely: 2 of 79 interrupt
    lines across this machine's logs (2.1.205 … 2.1.258) carried either, and
    none of the recent ones do. The tags are still read, because a log that has
    one is not wrong, but a reading that needs one settles almost nothing.
    """
    if obj.get("type") != "user":
        return False
    if "interruptedMessageId" in obj or "interruptedByShutdown" in obj:
        return True
    text = prompt_text((obj.get("message") or {}).get("content"))
    return bool(text and _INTERRUPT_RE.match(text))


def prompt_in(obj: dict, ts: datetime | None = None) -> Prompt | None:
    """One log line as a Prompt, or None when it is not one.

    The whole rule in one call — the line must be a user turn, must not be
    `isMeta`, must not be an interrupt, and must hold prose — so a consumer
    reading a log line by line asks the same question `parse_log` asks, rather
    than half of it. The rule is **prose makes a prompt**, whatever else rides
    the line: a tool result with prose beside it is what you typed while a call
    was in flight, and that is a real prompt (ADR 0001 § the one log reader).
    """
    if obj.get("type") != "user" or obj.get("isMeta") or is_interrupt(obj):
        return None
    text = prompt_text((obj.get("message") or {}).get("content"))
    return Prompt(text, ts) if text else None


def turn_usage(obj: dict, ts: datetime | None = None) -> TurnUsage | None:
    """One assistant line's per-turn usage, None when the line carried none."""
    msg = obj.get("message") or {}
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    w5, w1 = rates.cache_write_split(u)
    stu = u.get("server_tool_use") or {}
    return TurnUsage(
        model=msg.get("model"),
        when=ts,
        turn_uuid=obj.get("uuid") or "",
        input_tokens=u.get("input_tokens", 0),
        output_tokens=u.get("output_tokens", 0),
        cache_read_tokens=u.get("cache_read_input_tokens", 0),
        cache_write_5m_tokens=w5,
        cache_write_1h_tokens=w1,
        web_search_requests=stu.get("web_search_requests", 0),
        speed=u.get("speed") or "standard",
        service_tier=u.get("service_tier") or "standard",
        inference_geo=u.get("inference_geo") or "",
    )


class Reader:
    """The line readings above, presented as one `logs.LineReader`.

    Stateless apart from one fact about the *file*: whether it is a subagent
    transcript, which decides how `isSidechain` lines are read for the Activity
    State (ADR 0004 § the worktree lane).
    """

    edit_tools = EDIT_TOOLS

    def __init__(self, path: Path):
        self.path = path
        self.subagent = "/subagents/" in str(path)

    @staticmethod
    def timestamp(obj: dict) -> datetime | None:
        return logs.parse_ts(obj.get("timestamp"))

    @staticmethod
    def note_session(session: Session, obj: dict) -> None:
        if session.cwd is None and obj.get("cwd"):
            session.cwd = obj["cwd"]
        if obj.get("gitBranch"):
            session.branches.add(obj["gitBranch"])
        apply_title_fields(session, obj)

    is_interrupt = staticmethod(is_interrupt)
    prompt_in = staticmethod(prompt_in)
    tool_calls_in = staticmethod(tool_calls_in)
    edits_of = staticmethod(edits_of)
    turn_usage = staticmethod(turn_usage)

    @staticmethod
    def shell_command(call: ToolCall) -> str | None:
        """Bash is the one tool whose argument is a shell command
        (ADR 0004 § Calls)."""
        if call.name != "Bash":
            return None
        cmd = call.input.get("command")
        return cmd if isinstance(cmd, str) and cmd.strip() else None

    @staticmethod
    def is_silent(name: str) -> bool:
        return name in toolcalls.SILENT_TOOLS

    @staticmethod
    def tool_results_in(obj: dict) -> list[ToolResult]:
        if obj.get("type") != "user":
            return []
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        return [ToolResult(b.get("tool_use_id") or "", not bool(b.get("is_error")))
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
                and b.get("tool_use_id")]

    @staticmethod
    def commit_shas_in(obj: dict) -> list[str]:
        if obj.get("type") != "user":
            return []
        return [m.group(1) for m in COMMIT_LINE_RE.finditer(_result_text(obj))]

    @staticmethod
    def assistant_parts(obj: dict) -> list[Part]:
        """Prose, thinking and tool calls in the order the line holds them."""
        if obj.get("type") != "assistant":
            return []
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        uuid = obj.get("uuid") or ""
        ts = logs.parse_ts(obj.get("timestamp"))
        parts: list[Part] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                parts.append(Part("text", b["text"].strip()))
            elif t == "thinking" and b.get("thinking", "").strip():
                parts.append(Part("thinking", b["thinking"].strip()))
            elif t == "tool_use":
                inp = b.get("input")
                parts.append(Part("call", call=ToolCall(
                    name=b.get("name") or "", tool_id=b.get("id") or "",
                    input=inp if isinstance(inp, dict) else {},
                    turn_uuid=uuid, when=ts)))
        return parts

    def activity(self, obj: dict, ts: datetime) -> str | None:
        """Advance the Activity State from one log line (CONTEXT.md).

        Four transitions, in the order they have to be tested:

        - an **interrupt** settles the session. It arrives as a plain user
          line whose text is `[Request interrupted by user]`, so this must be
          checked before the prompt reading — that text would otherwise look
          like you asking a question, and the state would read `thinking` for
          as long as the Watch stays open.
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
            return None
        etype = obj.get("type")
        message = obj.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None

        if etype == "assistant":
            if message.get("stop_reason") != "tool_use":
                return SETTLED
            calls = tool_calls_in(obj)
            name = calls[-1].name if calls else ""     # parallel: the last one
            if not name:
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
                return None
            return ACT_VERBS.get(name, ACTING)

        if etype != "user" or obj.get("isMeta"):
            return None
        if is_interrupt(obj):
            return SETTLED
        returned = obj.get("toolUseResult") is not None or (
            isinstance(content, list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content))
        if returned or prompt_text(content):
            return THINKING
        return None

    @staticmethod
    def hint(line: str) -> bool:
        return '"tool_use"' in line or '"usage"' in line


# ── the typed reading of one log ────────────────────────────────────────────


def session_id_of(path: Path) -> str:
    """A Claude Code log is named by its `sessionId`."""
    return path.stem


def parse_log(path: Path | str) -> ParsedLog:
    """Read one Session log. Pure: no cache, nothing on disk but this file.

    Every line is parsed — the readings need the whole conversation, so the
    line-level prefilter the inbox's sweep uses (`_interesting`) would only
    hide turns. One consequence to know about: `branches` is read off every
    line carrying `gitBranch`, so a Session that changed branch away from an
    edit or a title line lands here with a *superset* of what the prefiltered
    sweep sees. More complete, and the answer a consumer switching over gets.
    """
    path = Path(path)
    session = Session(session_id=session_id_of(path), log_path=str(path),
                      last_activity=log_mtime(path))
    return logs.walk(Reader(path), path, session)


def read_log(path: Path | str, cache) -> ParsedLog:
    """One log through the Derived Cache — `logs.read_log`, kept here so the
    name every consumer once imported still answers."""
    return logs.read_log(path, cache)


def cache_id(path: Path) -> str:
    """The Derived Cache row id for a log — its name in the cache, which is not
    always the Session id inside it.

    A Session's own log is named by its `sessionId` and that is unique across
    the Scan Universe. A **subagent transcript** is named `agent-<id>` and is
    unique only inside its parent's directory, so it is qualified by the
    parent. Unqualified, two parents' identically-named transcripts share one
    row the moment their size and mtime agree, and one Session is priced with
    the other's turns (ADR 0002 § subagent usage).
    """
    if path.parent.name == "subagents":
        return f"{path.parent.parent.name}/{path.stem}"
    return path.stem


def session_logs(root: Path) -> list[Path]:
    """Every Session log under a projects directory."""
    return sorted(Path(root).glob("*/*.jsonl"))


def readable_logs(root: Path) -> list[Path]:
    """The Session logs, plus the subagent transcripts a level below them
    (ADR 0002 § subagent usage) — everything `read_log` can be handed."""
    return session_logs(root) + sorted(Path(root).glob(SUBAGENT_GLOB))


def _claude_root(where) -> Path | None:
    """This dialect's root out of what a caller holds — the roots, or one
    Claude Code directory as a bare path."""
    if isinstance(where, logs.Roots):
        return where.claude
    return Path(where)


def session_log_ids(where) -> set[str]:
    """Every Session log's Derived Cache id under this dialect's root — a
    liveness enumerator in the cache's own shape (ADR 0001 § the accelerator
    protocol), so it may be handed the roots or the one directory."""
    root = _claude_root(where)
    return set() if root is None else {cache_id(p) for p in session_logs(root)}


def readable_log_ids(where) -> set[str]:
    """Every readable log's Derived Cache id under this dialect's root, spelled
    as `cache_id` spells them."""
    root = _claude_root(where)
    return set() if root is None else {cache_id(p) for p in readable_logs(root)}


def peek_cwd(log: Path) -> str | None:
    """A log's `cwd` from its first lines that carry one."""
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
                if isinstance(obj, dict) and obj.get("cwd"):
                    return obj["cwd"]
    except OSError:
        pass
    return None


# ── the Triage Inbox's sweep ────────────────────────────────────────────────


def _interesting(line: str) -> bool:
    if '"tool_use"' in line and any(f'"{t}"' in line for t in EDIT_TOOLS):
        return True
    if title_hint(line):
        return True
    if '"toolUseResult"' in line and COMMIT_HINT_RE.search(line):
        return True
    return False


def _full_scan(session: Session, path: Path) -> None:
    reader = Reader(path)
    with open(path, errors="replace") as f:
        for line in f:
            if session.cwd is None and '"cwd"' in line:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("cwd"):
                    session.cwd = obj["cwd"]
                if obj.get("gitBranch"):
                    session.branches.add(obj["gitBranch"])
                if not _interesting(line):
                    continue
            elif not _interesting(line):
                continue
            else:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

            ts = logs.parse_ts(obj.get("timestamp"))
            etype = obj.get("type")
            apply_title_fields(session, obj)
            if obj.get("gitBranch"):
                session.branches.add(obj["gitBranch"])
            if etype == "assistant":
                logs.note_edits(session, edits_in(obj, ts))
            elif etype == "user":
                logs.note_commits(session, reader.commit_shas_in(obj), ts)


def swept_session(log: Path, stamp: cache_mod.Stamp, cache) -> Session:
    """One log's prefiltered sweep, through the Derived Cache."""
    sid = session_id_of(log)

    def scan() -> Session:
        session = Session(session_id=sid, log_path=str(log),
                          last_activity=stamp.mtime)
        _full_scan(session, log)
        return session

    return cache.derive(
        cache_mod.SESSIONS, cache_id(log), stamp, compute=scan,
        load=lambda row: logs.session_from_cache(sid, str(log), stamp.mtime, row),
        dump=logs.session_to_cache)


def scan_sessions(projects_dir: Path, cache) -> list[Session]:
    """Sweep one Claude Code root — `logs.scan_sessions` over that root alone."""
    return logs.scan_sessions(logs.Roots(claude=Path(projects_dir)), cache)
