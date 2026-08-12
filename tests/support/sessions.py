"""Build a Session log the way Claude Code writes one.

Standup reads exactly one thing from the outside world it does not control: the
JSONL under `~/.claude/projects/<cwd-slug>/<sessionId>.jsonl` (ADR 0001 § the
Scan Universe). Two scanners read it — `claude_logs` (titles, edits, captured
commit hashes) and `cost` (per-turn `usage`) — so a fixture that satisfies only
one of them is a trap.

`SessionLog` emits the line shapes both scanners prefilter on, in the schema the
real logs use: a `type` per line, `cwd`/`gitBranch`/`timestamp` on the
conversation lines, tool calls as `tool_use` blocks inside an assistant
`message`, and a `git commit` announcement in a user line's `toolUseResult`.
Titles ride their own line types (`ai-title`, `custom-title`, `last-prompt`).

`fixture_session()` is the canonical small Session the issue asks for — titles,
two edits, one commit hash, priced per-turn usage — and is what most tests
should reach for. Reach for `SessionLog` directly when a test needs a shape
`fixture_session` does not have.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A priced model (see standup.rates.CARD) so a fixture's turns cost something
# other than None. Deliberately not the newest: a fixture should not have to be
# rewritten every time the Rate Card grows a row.
MODEL = "claude-sonnet-5"

# One assistant turn's usage, in the shape the logs actually carry (the
# differentiated `cache_creation` sub-object, not the flat legacy field).
DEFAULT_USAGE = {
    "input_tokens": 12,
    "output_tokens": 340,
    "cache_read_input_tokens": 27_996,
    "cache_creation_input_tokens": 3_500,
    "cache_creation": {"ephemeral_5m_input_tokens": 3_500,
                       "ephemeral_1h_input_tokens": 0},
    "service_tier": "standard",
    "speed": "standard",
}

# Every fixture Session starts here unless told otherwise: a fixed wall clock,
# so a test that asserts on ordering never races the one it runs beside. Recent
# enough that the default Recent Window (7 days) and the `cost` window (the
# current calendar month) both contain it is *not* something a constant can
# promise, so timestamps are anchored on `now` and step backwards.
STEP = timedelta(minutes=1)


def project_dir_name(cwd: str) -> str:
    """Claude Code's directory name for a project: the cwd with every
    non-alphanumeric character replaced by a dash
    (`/Users/arjun/x` -> `-Users-arjun-x`).

    Nothing in Standup parses this name — both scanners glob `*/*.jsonl` and
    read `cwd` from inside the file — but a fixture tree that does not look like
    the real one invites a future reader to assume the wrong thing.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


@dataclass
class SessionLog:
    """One Session's JSONL, built line by line. Every mutator returns `self`."""

    session_id: str = "1a2b3c4d-0000-4000-8000-000000000001"
    cwd: str = "/tmp/tt"
    branch: str = "main"
    version: str = "2.0.0"
    start: datetime | None = None
    lines: list[dict] = field(default_factory=list)
    # (model, usage) per priced assistant turn, in order — what a cost test
    # compares against without re-deriving the Rate Card.
    usages: list[tuple[str, dict]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._clock = self.start or (datetime.now(timezone.utc) - timedelta(hours=1))

    # -- lines ----------------------------------------------------------

    def prompt(self, text: str) -> "SessionLog":
        """A user turn, plus the `last-prompt` line Claude Code writes beside
        it — the title of last resort."""
        self._conversation("user", {"role": "user", "content": text})
        self.lines.append({"type": "last-prompt", "sessionId": self.session_id,
                           "lastPrompt": text, "leafUuid": self._uuid()})
        return self

    def ai_title(self, title: str) -> "SessionLog":
        self.lines.append({"type": "ai-title", "sessionId": self.session_id,
                           "aiTitle": title})
        return self

    def custom_title(self, title: str) -> "SessionLog":
        self.lines.append({"type": "custom-title", "sessionId": self.session_id,
                           "customTitle": title})
        return self

    def turn(self, text: str = "Working on it.", *, model: str = MODEL,
             usage: dict | None = None) -> "SessionLog":
        """A plain assistant turn, carrying per-turn `usage` like the real ones."""
        return self._assistant([{"type": "text", "text": text}], model, usage)

    def edit(self, file_path: str, *, tool: str = "Edit", model: str = MODEL,
             usage: dict | None = None, **tool_input) -> "SessionLog":
        """An assistant turn whose `tool_use` block edits `file_path`.

        `file_path` must be absolute: `claude_logs` ignores relative paths,
        because a path it cannot join to a repo attributes nothing.
        """
        block = {
            "type": "tool_use",
            "id": f"toolu_{len(self.lines):024d}",
            "name": tool,
            "input": {"file_path": file_path, **tool_input},
            "caller": {"type": "direct"},
        }
        return self._assistant([block], model, usage)

    def commit(self, sha: str, subject: str = "A commit", *,
               branch: str | None = None, files: int = 1) -> "SessionLog":
        """The `git commit` announcement Standup captures a hash from: a user
        line whose `toolUseResult.stdout` opens with `[<branch> <sha>] <subject>`.
        """
        stdout = (f"[{branch or self.branch} {sha}] {subject}\n"
                  f" {files} file{'s' if files != 1 else ''} changed, "
                  f"{files} insertion(+)\n")
        self._conversation(
            "user",
            {"role": "user", "content": [{"type": "tool_result",
                                          "content": stdout,
                                          "tool_use_id": f"toolu_{len(self.lines):024d}"}]},
            toolUseResult={"stdout": stdout, "stderr": "", "interrupted": False,
                           "isImage": False, "noOutputExpected": False},
        )
        return self

    # -- output ---------------------------------------------------------

    def to_jsonl(self) -> str:
        return "".join(json.dumps(line) + "\n" for line in self.lines)

    def save(self, projects_dir: Path) -> Path:
        """Write the log under `projects_dir` in Claude Code's own layout, and
        return its path."""
        d = Path(projects_dir) / project_dir_name(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        log = d / f"{self.session_id}.jsonl"
        log.write_text(self.to_jsonl())
        return log

    # -- internals ------------------------------------------------------

    def _assistant(self, content: list[dict], model: str,
                   usage: dict | None) -> "SessionLog":
        u = DEFAULT_USAGE if usage is None else usage
        self._conversation("assistant", {
            "role": "assistant", "type": "message", "model": model,
            "id": f"msg_{len(self.lines):024d}", "content": content,
            "stop_reason": "end_turn", "usage": u,
        })
        self.usages.append((model, u))
        return self

    def _conversation(self, etype: str, message: dict, **extra) -> None:
        self._clock += STEP
        self.lines.append({
            "type": etype,
            "message": message,
            "cwd": self.cwd,
            "gitBranch": self.branch,
            "sessionId": self.session_id,
            "uuid": self._uuid(),
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "timestamp": self._clock.isoformat().replace("+00:00", "Z"),
            "version": self.version,
            **extra,
        })

    def _uuid(self) -> str:
        return f"{self.session_id[:24]}{len(self.lines):012d}"


def fixture_session(cwd: str = "/tmp/tt", *,
                    session_id: str = "1a2b3c4d-0000-4000-8000-000000000001",
                    sha: str = "abc1234", branch: str = "main") -> SessionLog:
    """The canonical small Session: a title, two edits, one captured commit
    hash, and priced per-turn usage on every assistant line.

    Edits are `<cwd>/alpha.py` and `<cwd>/beta.py`, so pointing this at a
    scratch repo that holds those files is all an attribution test needs.
    """
    return (
        SessionLog(session_id=session_id, cwd=cwd, branch=branch)
        .prompt("teach the inbox to read a fixture")
        .ai_title("Teach the inbox to read")
        .edit(f"{cwd}/alpha.py")
        .edit(f"{cwd}/beta.py", tool="Write", content="print('two')\n")
        .turn("Committing.")
        .commit(sha, "Add alpha and beta", branch=branch, files=2)
    )
