"""Read Codex's rollout JSONL — the one module that knows its schema
(ADR 0001 § two dialects, one reading).

Codex writes one file per thread, `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-
<id>.jsonl` (`archived_sessions/` once archived), every line
`{timestamp, ordinal, type, payload}`. What Standup reads out of it, and the
line it lives on:

| reading | line |
| --- | --- |
| cwd, branch, the thread's kind | `session_meta` (`payload.cwd`, `payload.git.branch`, `payload.thread_source`) |
| the model | `turn_context.payload.model`, restated per turn — a usage line never names it |
| a prompt | `response_item` / `message` / `role: user`, its `input_text` items minus the injected ones |
| an assistant's prose, its thinking | `response_item` / `message` / `role: assistant`; `reasoning.summary` (the content itself is encrypted) |
| a tool call | `response_item` / `function_call` (`name`, `namespace`, JSON `arguments`), `custom_tool_call` (`exec`, `apply_patch`: a string `input`), `web_search_call` |
| its result | `function_call_output` / `custom_tool_call_output`, joined on `call_id` |
| per-turn usage | `event_msg` / `token_count`, `info.last_token_usage` |
| an interrupt | `event_msg` / `turn_aborted` |
| a turn's edges | `event_msg` / `task_started`, `task_complete` |

Three schema facts a consumer would otherwise have to know, hidden here:

- **`apply_patch` paths are cwd-relative** by the format's own definition, so
  the reader joins them to the cwd the log states — reading the format, not
  guessing a path. Each `@@` section is one `EditBlock`; the hunks of one call
  share its `tool_id`, exactly as a MultiEdit's do.
- **`input_tokens` includes the cached share.** OpenAI counts cached input
  inside `input_tokens`; the typed reading's `input_tokens` is the *uncached*
  input (ADR 0002 § Codex usage), so the reader subtracts.
- **not every rollout is a Session.** Codex runs its own threads beside yours —
  `thread_source` `subagent` and `guardian_review` are the auto-reviewer
  reading your session — and those get no `cwd` here, which is how a log with
  nothing to say leaves the Scan Universe (`logs.scan_sessions` drops a
  cwd-less Session). Their usage is Codex's own overhead, not your work, and is
  counted nowhere (an accepted cost, ADR 0002 § Codex usage).

A resumed thread writes a *new* rollout named `rollout-<ts>-<thread-id>_<fork
-id>.jsonl`, with the parent thread's id inside. Each file is one Session, and
the Session id is the *last* id in the file name — the one that is unique to
the file — so `standup session <handle>` names one log, never two.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from datetime import datetime
from pathlib import Path

from . import cache as cache_mod
from . import logs
from .logs import (ACTING, SETTLED, THINKING, EditBlock, Part, Prompt, ToolCall,
                   ToolResult, TurnUsage)
from .models import Session

# the tool whose call is a file event; everything else is a Call
EDIT_TOOLS = frozenset({"apply_patch"})
# the tools whose argument is a shell command — rendered `$ …` (ADR 0004
# § Calls). `exec_command` is the current one; `shell` is the older function
# whose `command` is an argv list.
SHELL_TOOLS = frozenset({"exec_command", "shell", "local_shell", "container.exec"})

# Activity State verbs (ADR 0004 § the Activity State): the pending tool call
# read as one word. An unmapped tool falls to ACTING.
ACT_VERBS = {
    "exec_command": "running", "shell": "running", "local_shell": "running",
    "write_stdin": "running", "apply_patch": "writing",
    "web_search": "reading", "view_image": "reading", "tool_search": "reading",
}

# thread kinds that are Codex's own work, not a conversation the user had
_INTERNAL_THREADS = frozenset({"subagent", "guardian_review"})

_ROLLOUT_ID = re.compile(r"\Arollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\Z")
# A user item Codex spliced in rather than one you typed: a tag-wrapped block
# (`<environment_context>`, `<recommended_plugins>`, `<INSTRUCTIONS>`,
# `<skill>`, `<image …>`, `<turn_aborted>` …), a closing tag written as its own
# item (`</image>`), or one of the plain-text headers it uses for the same job
# — the files-mentioned block behind an attachment, the Chrome extension's tab
# context, the plugin list.
_TAG_WRAPPED = re.compile(r"\A</?[A-Za-z_][\w-]*(\s[^>]*)?>")
_INJECTED_HEADS = ("# AGENTS.md instructions", "# Files mentioned by the user",
                   "# Chrome tabs:", "Here is a list of plugins")
# the exit status a shell result states, in the spellings the tools use
_EXIT_RE = re.compile(r"(?:Process exited with code|Exit code:?|exit code)\s*(\d+)",
                      re.IGNORECASE)
_EXIT_JSON_RE = re.compile(r'"exit_code":\s*(\d+)')


def session_id_of(path: Path) -> str:
    """The id in the rollout's name — the last one when a fork suffixed its own,
    so every file has an id no other file shares (see the module docstring).
    A name that is not a rollout's falls back to the stem, as Claude's do."""
    m = _ROLLOUT_ID.match(path.name.removesuffix(".jsonl"))
    if not m:
        return path.stem
    return m.group(1).rsplit("_", 1)[-1]


def cache_id(path: Path) -> str:
    """A rollout's stem is unique on its own: timestamp plus every id."""
    return path.stem


def session_logs(root: Path) -> list[Path]:
    """Every rollout under a Codex home: the dated tree, plus the archive."""
    out = sorted(root.glob("sessions/*/*/*/rollout-*.jsonl"))
    out += sorted(root.glob("archived_sessions/rollout-*.jsonl"))
    return out


def readable_logs(root: Path) -> list[Path]:
    """Codex keeps no transcript beside a rollout that `read_log` would be
    handed, so the readable set is the Session set."""
    return session_logs(root)


def peek_cwd(path: Path) -> str | None:
    """The cwd from the `session_meta` head line, None for an internal thread
    (which has nothing a Repo Entry should hear about)."""
    try:
        with open(path, errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta":
                    meta = obj.get("payload") or {}
                    if meta.get("thread_source") in _INTERNAL_THREADS:
                        return None
                    return meta.get("cwd") or None
    except OSError:
        pass
    return None


def _text_of(output) -> str:
    """A tool output's text: a string, or the `input_text` items joined."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(item.get("text", "") for item in output
                         if isinstance(item, dict))
    return ""


def _verdict(text: str) -> bool:
    """Did the tool succeed, as far as its output says? An exit status decides
    when one is stated; a result that states none is not a failure."""
    m = _EXIT_RE.search(text) or _EXIT_JSON_RE.search(text)
    if m:
        return m.group(1) == "0"
    return "Script failed" not in text


def _injected(text: str) -> bool:
    t = text.lstrip()
    return bool(_TAG_WRAPPED.match(t)) or t.startswith(_INJECTED_HEADS)


def parse_patch(patch: str, cwd: str | None) -> list[tuple[str, str, str, bool]]:
    """An `apply_patch` body as `(path, new, old, path_only)` hunks.

    `*** Update File: p` opens a file whose `@@` sections are each a hunk of
    ` `/`-`/`+` lines (context rows dropped: an EditBlock holds what changed);
    `*** Add File: p` is one hunk of `+` lines with nothing taken out;
    `*** Delete File: p` names a file with no text to show, so it is path-only;
    `*** Move to:` renames the file being updated — the edit stays filed under
    the path it was made to. Relative paths join `cwd`; an absolute one stands.
    """
    hunks: list[tuple[str, str, str, bool]] = []
    path: str | None = None
    mode: str | None = None          # update | add
    added: list[str] = []
    removed: list[str] = []
    open_hunk = False

    def resolve(p: str) -> str:
        p = p.strip()
        if os.path.isabs(p) or not cwd:
            return p
        return os.path.normpath(os.path.join(cwd, p))

    def flush() -> None:
        nonlocal added, removed, open_hunk
        if path is not None and open_hunk and (added or removed):
            hunks.append((path, "\n".join(added), "\n".join(removed), False))
        added, removed, open_hunk = [], [], False

    for line in patch.splitlines():
        if line.startswith("*** Update File: "):
            flush()
            path, mode = resolve(line[len("*** Update File: "):]), "update"
        elif line.startswith("*** Add File: "):
            flush()
            path, mode, open_hunk = resolve(line[len("*** Add File: "):]), "add", True
        elif line.startswith("*** Delete File: "):
            flush()
            path, mode = resolve(line[len("*** Delete File: "):]), None
            hunks.append((path, "", "", True))
        elif line.startswith("*** Move to: "):
            continue
        elif line.startswith("*** "):          # Begin Patch / End Patch
            flush()
            path = None
        elif line.startswith("@@") and mode == "update":
            flush()
            open_hunk = True
        elif path is not None and mode is not None:
            if line.startswith("+"):
                added.append(line[1:])
                open_hunk = True
            elif line.startswith("-"):
                removed.append(line[1:])
                open_hunk = True
            # a ` ` context row, or a bare row: not part of the change
    flush()
    return hunks


class Reader:
    """One rollout's lines, in order (`logs.LineReader`). Stateful, because a
    line rarely says everything about itself: the model comes from the last
    `turn_context`, and a patch's paths from the cwd the thread declared.

    Every public method absorbs those facts from the line it is handed before
    answering, so a consumer that asks only some of the questions — the Watch
    never calls `note_session` — still reads a patch against the right cwd and
    a usage line against the right model. Absorbing twice is harmless: the
    facts are set once and then held.
    """

    edit_tools = EDIT_TOOLS

    def __init__(self, path: Path):
        self.path = path
        self.model: str | None = None
        self.cwd: str | None = None
        self.internal = False        # a Codex-internal thread: no Session here

    def _absorb(self, obj: dict) -> None:
        etype = obj.get("type")
        payload = obj.get("payload") or {}
        if etype == "session_meta":
            if payload.get("thread_source") in _INTERNAL_THREADS:
                self.internal = True
            if payload.get("cwd"):
                self.cwd = self.cwd or payload["cwd"]
        elif etype == "turn_context":
            if payload.get("model"):
                self.model = payload["model"]
            if payload.get("cwd"):
                self.cwd = self.cwd or payload["cwd"]
        elif etype == "event_msg" and payload.get("type") == "thread_settings_applied":
            settings = payload.get("thread_settings") or {}
            if isinstance(settings, dict) and settings.get("model"):
                self.model = settings["model"]

    # -- session facts ------------------------------------------------------

    def timestamp(self, obj: dict) -> datetime | None:
        self._absorb(obj)
        return logs.parse_ts(obj.get("timestamp"))

    def note_session(self, session: Session, obj: dict) -> None:
        self._absorb(obj)
        session.agent = "codex"
        if obj.get("type") == "session_meta":
            git = (obj.get("payload") or {}).get("git") or {}
            if isinstance(git, dict) and git.get("branch"):
                session.branches.add(git["branch"])
        if not self.internal and session.cwd is None and self.cwd:
            session.cwd = self.cwd
        prompt = self.prompt_in(obj)
        if prompt is not None:
            # a rollout carries no title, so the first prompt is the title of
            # last resort (models.Session.title) — the last one is where a
            # session says `y`
            session.first_prompt = session.first_prompt or prompt.text
            session.last_prompt = prompt.text

    # -- the conversation ---------------------------------------------------

    @staticmethod
    def _item(obj: dict, kind: str) -> dict | None:
        if obj.get("type") != "response_item":
            return None
        payload = obj.get("payload") or {}
        return payload if payload.get("type") == kind else None

    @staticmethod
    def is_interrupt(obj: dict) -> bool:
        """The turn was cut short: Codex writes a `turn_aborted` event, and the
        `<turn_aborted>` note it later splices into the user's side is injected
        text the prompt reading already drops."""
        return (obj.get("type") == "event_msg"
                and (obj.get("payload") or {}).get("type") == "turn_aborted")

    def prompt_in(self, obj: dict, ts: datetime | None = None) -> Prompt | None:
        msg = self._item(obj, "message")
        if msg is None or msg.get("role") != "user":
            return None
        parts = [item.get("text", "") for item in msg.get("content") or []
                 if isinstance(item, dict) and item.get("type") == "input_text"]
        typed = [p.strip() for p in parts if p.strip() and not _injected(p)]
        if not typed:
            return None
        return Prompt("\n".join(typed), ts)

    def tool_calls_in(self, obj: dict, ts: datetime | None = None) -> list[ToolCall]:
        self._absorb(obj)
        if obj.get("type") != "response_item":
            return []
        payload = obj.get("payload") or {}
        kind = payload.get("type")
        meta = payload.get("internal_chat_message_metadata_passthrough") or {}
        turn = meta.get("turn_id") or "" if isinstance(meta, dict) else ""
        if kind == "function_call":
            name = payload.get("name") or ""
            ns = payload.get("namespace")
            if isinstance(ns, str) and ns:
                # an MCP tool, spelled the way Claude's logs spell one so the
                # shared renderer shortens both alike (ADR 0004 § Calls)
                name = f"{ns}__{name}" if ns.startswith("mcp__") else f"mcp__{ns}__{name}"
            raw = payload.get("arguments")
            inp: dict
            if isinstance(raw, dict):
                inp = raw
            else:
                try:
                    decoded = json.loads(raw) if isinstance(raw, str) else None
                except json.JSONDecodeError:
                    decoded = None
                inp = decoded if isinstance(decoded, dict) else (
                    {"arguments": raw} if raw else {})
            return [ToolCall(name=name, input=inp, tool_id=payload.get("call_id") or "",
                             turn_uuid=turn, when=ts)]
        if kind == "custom_tool_call":
            name = payload.get("name") or ""
            body = payload.get("input")
            key = "patch" if name == "apply_patch" else "input"
            inp = {key: body} if isinstance(body, str) else {}
            return [ToolCall(name=name, input=inp, tool_id=payload.get("call_id") or "",
                             turn_uuid=turn, when=ts)]
        if kind == "web_search_call":
            action = payload.get("action")
            return [ToolCall(name="web_search",
                             input=action if isinstance(action, dict) else {},
                             tool_id=payload.get("id") or "", turn_uuid=turn, when=ts)]
        if kind == "tool_search_call":
            args = payload.get("arguments")
            return [ToolCall(name="tool_search",
                             input=args if isinstance(args, dict) else {},
                             tool_id=payload.get("call_id") or payload.get("id") or "",
                             turn_uuid=turn, when=ts)]
        return []

    def edits_of(self, call: ToolCall) -> list[EditBlock]:
        # resolved against the cwd absorbed so far — the thread's, declared on
        # its first line, so a tailer that started mid-file still has it once
        # any `turn_context` has passed
        if call.name not in EDIT_TOOLS:
            return []
        patch = call.input.get("patch")
        if not isinstance(patch, str):
            return []
        return [EditBlock("apply_patch", path, new, old, call.when, call.tool_id,
                          path_only=only)
                for path, new, old, only in parse_patch(patch, self.cwd)]

    @staticmethod
    def shell_command(call: ToolCall) -> str | None:
        if call.name not in SHELL_TOOLS:
            return None
        cmd = call.input.get("cmd")
        if isinstance(cmd, str) and cmd.strip():
            return cmd
        argv = call.input.get("command")
        if isinstance(argv, list) and argv:
            return shlex.join(str(a) for a in argv)
        if isinstance(argv, str) and argv.strip():
            return argv
        return None

    @staticmethod
    def is_silent(name: str) -> bool:
        """Codex has no local-read tool apart from the shell, so nothing is
        silent: every call earns a row (ADR 0004 § Calls)."""
        return False

    def tool_results_in(self, obj: dict) -> list[ToolResult]:
        out = self._output(obj)
        if out is None:
            return []
        call_id, text = out
        return [ToolResult(call_id, _verdict(text))] if call_id else []

    def commit_shas_in(self, obj: dict) -> list[str]:
        out = self._output(obj)
        if out is None:
            return []
        return [m.group(1) for m in logs.COMMIT_LINE_RE.finditer(out[1])]

    def _output(self, obj: dict) -> tuple[str, str] | None:
        payload = (self._item(obj, "function_call_output")
                   or self._item(obj, "custom_tool_call_output"))
        if payload is None:
            return None
        return payload.get("call_id") or "", _text_of(payload.get("output"))

    def turn_usage(self, obj: dict, ts: datetime | None = None) -> TurnUsage | None:
        self._absorb(obj)
        if obj.get("type") != "event_msg":
            return None
        payload = obj.get("payload") or {}
        if payload.get("type") != "token_count":
            return None
        info = payload.get("info")
        u = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(u, dict):
            return None
        cached = u.get("cached_input_tokens", 0) or 0
        # one id per usage line, so a turn's several responses are several
        # priced turns rather than one counted once
        turn_uuid = f"{obj.get('ordinal', '')}@{obj.get('timestamp', '')}"
        return TurnUsage(
            model=self.model, when=ts, turn_uuid=turn_uuid,
            input_tokens=max(0, (u.get("input_tokens", 0) or 0) - cached),
            output_tokens=u.get("output_tokens", 0) or 0,
            cache_read_tokens=cached,
            cache_write_5m_tokens=u.get("cache_write_input_tokens", 0) or 0,
        )

    def assistant_parts(self, obj: dict) -> list[Part]:
        msg = self._item(obj, "message")
        if msg is not None:
            if msg.get("role") != "assistant":
                return []
            return [Part("text", item["text"]) for item in msg.get("content") or []
                    if isinstance(item, dict) and item.get("type") == "output_text"
                    and item.get("text", "").strip()]
        reasoning = self._item(obj, "reasoning")
        if reasoning is not None:
            return [Part("thinking", item["text"])
                    for item in reasoning.get("summary") or []
                    if isinstance(item, dict) and item.get("text", "").strip()]
        return [Part("call", call=c) for c in self.tool_calls_in(obj)]

    def activity(self, obj: dict, ts: datetime) -> str | None:
        """Codex states its turn edges outright — `task_started`,
        `task_complete`, `turn_aborted` — so only the pause between a tool's
        result and the next line is inferred (ADR 0004 § the Activity State)."""
        self._absorb(obj)
        etype = obj.get("type")
        payload = obj.get("payload") or {}
        kind = payload.get("type")
        if etype == "event_msg":
            if kind in ("task_complete", "turn_aborted"):
                return SETTLED
            if kind == "task_started":
                return THINKING
            return None
        if etype != "response_item":
            return None
        if kind in ("function_call_output", "custom_tool_call_output"):
            return THINKING
        if kind == "message" and payload.get("role") == "user":
            return THINKING if self.prompt_in(obj) else None
        calls = self.tool_calls_in(obj)
        if calls:
            name = calls[-1].name
            return ACT_VERBS.get(name, ACTING)
        return None

    @staticmethod
    def hint(line: str) -> bool:
        return ('"function_call"' in line or '"custom_tool_call"' in line
                or '"token_count"' in line or '"web_search_call"' in line)


def parse_log(path: Path) -> logs.ParsedLog:
    """Read one rollout. Pure: no cache, nothing on disk but this file."""
    session = Session(session_id=session_id_of(path), log_path=str(path),
                      agent="codex", last_activity=logs.log_mtime(path))
    return logs.walk(Reader(path), path, session)


def swept_session(log: Path, stamp: cache_mod.Stamp, cache) -> Session:
    """One rollout's inbox facts, through the Derived Cache. No prefilter: a
    rollout's facts ride lines a cheap scan cannot tell apart, and the full
    reading's row is the one the other views ask for anyway."""
    sid = session_id_of(log)
    return cache.derive(
        cache_mod.SESSIONS, cache_id(log), stamp,
        compute=lambda: parse_log(log).session,
        load=lambda row: logs.session_from_cache(sid, str(log), stamp.mtime, row),
        dump=logs.session_to_cache)
