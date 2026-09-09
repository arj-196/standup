"""Build a Codex Session log the way Codex writes one.

The second of the two agents Standup reads (ADR 0001 § two dialects, one
reading): a rollout under `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl`,
every line `{timestamp, ordinal, type, payload}`. `codex_logs` reads it once,
fully — there is no prefiltered sweep — so this builder has one reading to
satisfy, but the shapes it must get right are the ones a Claude-shaped fixture
would never exercise: the model on `turn_context` rather than on the usage
line, `apply_patch` hunks with cwd-relative paths, `exec_command` as the shell,
`token_count` usage that counts cached input *inside* `input_tokens`, a
`turn_aborted` event as the interrupt, and the `<environment_context>`-style
items Codex splices into the user's side of the conversation.

`fixture_codex_session()` is the canonical small one — a prompt, two patched
files, one captured commit hash, priced usage — the counterpart of
`sessions.fixture_session()`. Reach for `CodexLog` directly when a test needs a
shape it does not have.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A priced OpenAI model (see standup.rates.CARD) so a fixture's turns cost
# something other than None. Deliberately not the newest.
MODEL = "gpt-5.4"

# Timestamps are relative, never absolute, for the reason `sessions.py` gives:
# a fixture that expires is a test that fails on a Tuesday.
DEFAULT_AGE = timedelta(hours=1)
STEP = timedelta(minutes=1)


@dataclass
class CodexLog:
    """One Codex thread's rollout, built line by line. Every mutator returns
    `self`."""

    session_id: str = "01a00000-0000-7000-8000-000000000001"
    cwd: str = "/tmp/tt"
    branch: str | None = "main"
    model: str = MODEL
    # `user` is a conversation you had; `subagent` and `guardian_review` are
    # Codex's own auto-review threads, which are no Session (codex_logs)
    thread_source: str = "user"
    start: datetime | None = None
    step: timedelta = STEP
    # a fork of an earlier thread writes `rollout-<ts>-<parent>_<own>.jsonl`;
    # set this to the parent's id to build that shape
    forked_from: str | None = None
    lines: list[dict] = field(default_factory=list)
    # (model, usage-as-the-Rate-Card-reads-it) per usage line, in order — what
    # a cost test compares against without re-deriving the Rate Card
    usages: list[tuple[str, dict]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._clock = self.start or (datetime.now(timezone.utc) - DEFAULT_AGE)
        self._started_at = self._clock
        self._last_call_id: str | None = None
        self._turn = 0
        self._turn_id = self._new_turn_id()
        self._line({"type": "session_meta", "payload": {
            "id": self.session_id, "session_id": self.session_id,
            "timestamp": self._iso(), "cwd": self.cwd,
            "originator": "codex_work_desktop", "cli_version": "0.153.4",
            "source": "vscode", "thread_source": self.thread_source,
            "model_provider": "openai",
            "git": ({"commit_hash": "0" * 40, "branch": self.branch,
                     "repository_url": None} if self.branch else None),
        }})
        self._turn_context()

    # -- turn structure -----------------------------------------------------

    def _new_turn_id(self) -> str:
        self._turn += 1
        return f"{self.session_id[:8]}-turn-{self._turn:04d}"

    def _turn_context(self, model: str | None = None) -> None:
        self._line({"type": "turn_context", "payload": {
            "turn_id": self._turn_id, "cwd": self.cwd, "model": model or self.model,
            "approval_policy": "on-request"}})

    def new_turn(self, model: str | None = None) -> "CodexLog":
        """Start a fresh turn: Codex restates the model (and cwd) on a
        `turn_context` line at the top of every turn, and announces
        `task_started`. Prompts call this for you."""
        self._turn_id = self._new_turn_id()
        self._turn_context(model)
        self._event("task_started", turn_id=self._turn_id)
        return self

    # -- lines ----------------------------------------------------------

    def prompt(self, text: str, *, injected: tuple[str, ...] = ()) -> "CodexLog":
        """A user turn: your text as an `input_text` item, beside whatever
        Codex spliced in (`injected`, each its own item — an
        `<environment_context>` block, a plugin list, an AGENTS.md header)."""
        self.new_turn()
        items = [{"type": "input_text", "text": t} for t in injected]
        items.append({"type": "input_text", "text": text})
        self._item({"type": "message", "role": "user", "content": items})
        return self

    def environment(self) -> "CodexLog":
        """The `<environment_context>` item Codex sends at the top of a thread,
        as its own user message — never a prompt."""
        self._item({"type": "message", "role": "user", "content": [
            {"type": "input_text",
             "text": f"<environment_context>\n  <cwd>{self.cwd}</cwd>\n"
                     "  <shell>zsh</shell>\n</environment_context>"}]})
        return self

    def turn(self, text: str = "Working on it.", *, phase: str = "final_answer",
             usage: dict | None = None, model: str | None = None) -> "CodexLog":
        """An assistant message, followed by the `token_count` line Codex
        writes for the response that produced it — the usage rides a *separate*
        line and names no model."""
        self._item({"type": "message", "role": "assistant", "phase": phase,
                    "content": [{"type": "output_text", "text": text}]})
        return self.usage(usage, model=model)

    def reasoning(self, summary: str = "**Planning the change**") -> "CodexLog":
        """A reasoning item: its content is encrypted, only the summary reads."""
        self._item({"type": "reasoning", "summary": [
            {"type": "summary_text", "text": summary}], "encrypted_content": "gAAA…"})
        return self

    def usage(self, usage: dict | None = None, *, model: str | None = None,
              input_tokens: int = 30_000, cached: int = 20_000,
              output_tokens: int = 400, reasoning_tokens: int = 100) -> "CodexLog":
        """One `token_count` event. Codex's `input_tokens` *includes* the
        cached share, so the Rate Card reads `input_tokens - cached` as the
        uncached input (ADR 0002 § Codex usage); `usages` records that reading."""
        if model is not None and model != self.model:
            self.model = model
            self._turn_context(model)
        u = usage or {
            "input_tokens": input_tokens, "cached_input_tokens": cached,
            "cache_write_input_tokens": 0, "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        self._event("token_count", info={
            "total_token_usage": u, "last_token_usage": u,
            "model_context_window": 258_400}, rate_limits=None)
        self.usages.append((self.model, {
            "input_tokens": u["input_tokens"] - u["cached_input_tokens"],
            "output_tokens": u["output_tokens"],
            "cache_read_input_tokens": u["cached_input_tokens"],
            "cache_creation_input_tokens": u.get("cache_write_input_tokens", 0),
        }))
        return self

    def shell(self, cmd: str, *, workdir: str | None = None) -> "CodexLog":
        """An `exec_command` function call — Codex's shell, the one tool whose
        argument is a shell command (ADR 0004 § Calls)."""
        return self.call("exec_command", cmd=cmd, workdir=workdir or self.cwd,
                         max_output_tokens=12_000)

    def call(self, name: str, *, namespace: str | None = None, **args) -> "CodexLog":
        """A `function_call`: `name` with JSON-encoded `arguments`. An MCP tool
        carries its server as `namespace` (`mcp__notion`), which the reader
        folds into the `mcp__<server>__<tool>` spelling Claude's logs use."""
        call_id = f"call_{len(self.lines):016d}"
        payload = {"type": "function_call", "id": f"fc_{len(self.lines):08d}",
                   "name": name, "arguments": json.dumps(args), "call_id": call_id}
        if namespace:
            payload["namespace"] = namespace
        self._last_call_id, self._last_kind = call_id, "function"
        self._item(payload)
        return self

    def patch(self, path: str, *, old: str = "", new: str = "",
              hunks: list[tuple[str, str]] | None = None) -> "CodexLog":
        """An `apply_patch` custom tool call updating one file. `path` is
        cwd-relative unless absolute — the format's own rule. `hunks` gives
        several `(old, new)` sections under one call; `old`/`new` is the
        one-hunk shorthand."""
        sections = hunks if hunks is not None else [(old, new)]
        body = ["*** Begin Patch", f"*** Update File: {path}"]
        for o, n in sections:
            body.append("@@")
            body += [f"-{ln}" for ln in o.splitlines()]
            body += [f"+{ln}" for ln in n.splitlines()]
        body.append("*** End Patch")
        return self._custom("apply_patch", "\n".join(body))

    def add_file(self, path: str, content: str) -> "CodexLog":
        """An `apply_patch` that creates a file."""
        body = ["*** Begin Patch", f"*** Add File: {path}"]
        body += [f"+{ln}" for ln in content.splitlines()]
        body.append("*** End Patch")
        return self._custom("apply_patch", "\n".join(body))

    def result(self, output: str = "", *, exit_code: int | None = 0) -> "CodexLog":
        """The output line for the last call. A shell result states its exit
        status the way `exec_command` does; `exit_code=None` states none."""
        head = (f"Chunk ID: c1\nWall time: 0.1 seconds\n"
                f"Process exited with code {exit_code}\nOutput:\n"
                if exit_code is not None else "")
        kind = ("custom_tool_call_output" if (self._last_kind == "custom")
                else "function_call_output")
        self._item({"type": kind, "id": f"fco_{len(self.lines):08d}",
                    "call_id": self._last_call_id or "", "output": head + output})
        return self

    def commit(self, sha: str, subject: str = "A commit", *, files: int = 1) -> "CodexLog":
        """A `git commit` run through the shell, with the announcement Standup
        captures a hash from riding its result."""
        self.shell(f"git commit -m {json.dumps(subject)}")
        return self.result(f"[{self.branch or 'main'} {sha}] {subject}\n"
                           f" {files} file{'s' if files != 1 else ''} changed\n")

    def aborted(self) -> "CodexLog":
        """The `turn_aborted` event an interrupted turn ends on."""
        self._event("turn_aborted", turn_id=self._turn_id, reason="interrupted")
        return self

    def complete(self, last_message: str = "Done.") -> "CodexLog":
        """`task_complete`: the agent handed control back."""
        self._event("task_complete", turn_id=self._turn_id,
                    last_agent_message=last_message)
        return self

    # -- output ---------------------------------------------------------

    def file_name(self) -> str:
        stamp = self._started_at.strftime("%Y-%m-%dT%H-%M-%S")
        ids = (f"{self.forked_from}_{self.session_id}" if self.forked_from
               else self.session_id)
        return f"rollout-{stamp}-{ids}.jsonl"

    def to_jsonl(self) -> str:
        return "".join(json.dumps(line) + "\n" for line in self.lines)

    def save(self, codex_dir: Path, *, archived: bool = False) -> Path:
        """Write the rollout under `codex_dir` in Codex's own layout — the dated
        tree, or `archived_sessions/` — and return its path."""
        if archived:
            d = Path(codex_dir) / "archived_sessions"
        else:
            d = Path(codex_dir) / "sessions" / self._started_at.strftime("%Y/%m/%d")
        d.mkdir(parents=True, exist_ok=True)
        log = d / self.file_name()
        log.write_text(self.to_jsonl())
        return log

    # -- internals ------------------------------------------------------

    # which output line answers the last call: a `function_call` gets a
    # `function_call_output`, a custom tool (`apply_patch`) a `custom_tool_call_output`
    _last_kind: str = "function"

    def _custom(self, name: str, body: str) -> "CodexLog":
        call_id = f"call_{len(self.lines):016d}"
        self._last_call_id, self._last_kind = call_id, "custom"
        self._item({"type": "custom_tool_call", "id": f"ctc_{len(self.lines):08d}",
                    "status": "completed", "call_id": call_id, "name": name,
                    "input": body})
        return self

    def _item(self, payload: dict) -> None:
        payload.setdefault("internal_chat_message_metadata_passthrough",
                           {"turn_id": self._turn_id})
        self._line({"type": "response_item", "payload": payload})

    def _event(self, kind: str, **payload) -> None:
        self._line({"type": "event_msg", "payload": {"type": kind, **payload}})

    def _line(self, obj: dict) -> None:
        self._clock += self.step
        self.lines.append({"timestamp": self._iso(), "ordinal": len(self.lines), **obj})

    def _iso(self) -> str:
        return self._clock.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fixture_codex_session(cwd: str = "/tmp/tt", *,
                          session_id: str = "01a00000-0000-7000-8000-000000000001",
                          sha: str = "abc1234", branch: str = "main") -> CodexLog:
    """The canonical small Codex Session: a prompt, two patched files, one
    captured commit hash, and priced usage on every response.

    Edits are `<cwd>/alpha.py` and `<cwd>/beta.py` — written cwd-relative in
    the patch, as Codex writes them — so pointing this at a scratch repo that
    holds those files is all an attribution test needs.
    """
    return (
        CodexLog(session_id=session_id, cwd=cwd, branch=branch)
        .environment()
        .prompt("teach the inbox to read a rollout")
        .reasoning()
        .turn("On it.", phase="commentary")
        .patch("alpha.py", old="x = 1", new="x = 2").result("Success. Updated the following files:\nM alpha.py", exit_code=None)
        .usage()
        .add_file("beta.py", "print('two')\n").result("Success. Updated the following files:\nA beta.py", exit_code=None)
        .usage()
        .commit(sha, "Add alpha and beta", files=2)
        .turn("Committed.")
        .complete()
    )
