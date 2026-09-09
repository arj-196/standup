"""The one log reading, in the shape every view consumes — and where the logs
live (ADR 0001 § two dialects, one reading).

Standup reads two agents' Session logs: Claude Code's
(`~/.claude/projects/<slug>/<sessionId>.jsonl`, read by `claude_logs`) and
Codex's (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, read by
`codex_logs`). The two schemas share nothing but the fact that they are JSONL,
so each has a **dialect** module that knows its lines — and *only* that
module. What they share is here:

- the **typed reading**: `ToolCall`, `EditBlock`, `Prompt`, `TurnUsage`, the
  `ParsedLog` a whole file becomes, and the `UsageTotals` fold over turns. A
  consumer that holds one of these cannot tell which agent wrote the log, and
  that is the point: attribution, pricing, the Attributed Diff's matcher and
  the Derived Cache all work on the shape, never on the schema;
- the **`LineReader` contract** a dialect fulfils, for the consumers that never
  hold a whole file — the Watch tails a log as it grows, the Loop detector
  prefilters, the Transcript lays lines out in order. A reader is made *per
  file* (`reader(path)`) because a Codex line does not name its model or its
  cwd; an earlier line did, and the reader remembers;
- **`Roots`**: which directories are read, and the enumerations over them the
  Universe, the cache's prune and the Watch's discovery all share;
- the **dispatch**: `dialect_of(path)` by the file's own shape, and
  `read_log`/`parse_log`/`reader` resolved through it, plus the one Derived
  Cache round trip of the typed reading.

`claude_logs` keeps every line reading it always had as public functions — a
streaming consumer is a projection of the same reading rather than a rival one
(ADR 0001 § the one log reader) — and wraps them in a `Reader` so both dialects
present one face.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import cache as cache_mod
from . import rates
from .models import Session

# bump when the typed reading changes shape or meaning (invalidates cache rows
# of both dialects: the row is the shape, whichever schema it was read from)
READER_VERSION = 4

DEFAULT_PROJECTS_DIR = "~/.claude/projects"
DEFAULT_CODEX_DIR = "~/.codex"

# `[branch abc1234]` / `[main (root-commit) abc1234]` / `[detached HEAD abc1234]`
# — the line `git commit` prints, which both agents' shell tools echo back
COMMIT_LINE_RE = re.compile(r"^\[[^\[\]\n]{1,80} ([0-9a-f]{7,40})\]", re.MULTILINE)

# Activity State transitions a reader reports (ADR 0004 § the Activity State):
# the turn settled, the model is composing, or a tool verb. `None` from
# `LineReader.activity` means the line moves the state nowhere.
SETTLED = "settled"
THINKING = "thinking"
ACTING = "acting"        # the verb an unmapped tool falls to — true of anything


def log_mtime(path: Path) -> datetime | None:
    """A log's last-append time — the one meaning of a Session's
    `last_activity` (ADR 0001 § the one log reader), and what a windowed view
    gates a file on before opening it. None when the file cannot be stat'd."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ── the typed reading ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCall:
    """One tool call: what the Session called, and with what.

    The typed reading of a tool call *before* anything is made of it — the Loop
    detector reads its shape, the Watch renders it as a **Call** or projects it
    into file events, and the reader's `edits_of` turns the file-touching ones
    into `EditBlock`s. `input` is the call's own input dict, unclipped: a
    consumer that needs a digest makes one (`toolcalls`), and one that needs
    the request itself still has it. `name` is the raw tool name as the log
    spelled it — an MCP tool as `mcp__<server>__<tool>` in both dialects, so
    `toolcalls.display_name` shortens it the same way for both.
    """
    name: str
    input: dict
    tool_id: str = ""
    turn_uuid: str = ""
    when: datetime | None = None


@dataclass(frozen=True)
class EditBlock:
    """One recorded edit: the text a Session put in, and the text it took out.

    `new` is empty for a pure deletion, `old` for a Write or a create. A call
    that lands several hunks (Claude's MultiEdit, a Codex `apply_patch` with
    several `@@` sections) fans out to one EditBlock per hunk — the hunks were
    one action by the agent, which `tool_id` (shared across the fan-out) is what
    remembers.

    `path` is absolute: Claude Code records absolute paths and a relative one
    attributes nothing, so it is dropped; Codex's patch format is defined as
    cwd-relative, so its reader joins the path to the cwd the log states — that
    is reading the format, not guessing (ADR 0001 § two dialects, one reading).

    `path_only` marks the block a call yields when the log records *that* it
    edited a file but not *what* it wrote. The path still attributes the file,
    so the block exists; there is no text in it, so a consumer that shows
    change (the Watch) has nothing to show.
    """
    tool: str                      # Edit | Write | MultiEdit | NotebookEdit | apply_patch
    path: str
    new: str
    old: str
    when: datetime | None = None
    tool_id: str = ""
    path_only: bool = False


@dataclass(frozen=True)
class Prompt:
    """One user turn's typed text, and when it was typed.

    "Typed" is the whole point: injected material is not a prompt. Both agents
    splice text into the user's side of the conversation — Claude Code its
    system reminders and `isMeta` bodies, Codex its `<environment_context>`,
    plugin lists and `AGENTS.md` instructions — and both write a line when a
    turn is cut short that reads as prose nobody typed. A reader drops all of
    it, so a consumer never has to know which of them exist.
    """
    text: str
    when: datetime | None = None


@dataclass(frozen=True)
class ToolResult:
    """A tool's return, as much of it as the feed may know: which call it
    answers and whether it succeeded. Never its content — the Watch shows what
    was asked, not what came back (ADR 0004 § Calls)."""
    tool_id: str
    ok: bool


@dataclass(frozen=True)
class Part:
    """One piece of an assistant line in reading order — prose, hidden thinking,
    or a tool call — for the Transcript, which lays all three out together."""
    kind: str                      # text | thinking | call
    text: str = ""
    call: ToolCall | None = None


@dataclass(frozen=True)
class TurnUsage:
    """One assistant turn's usage, typed as the Rate Card reads it.

    Counts *and* the per-turn modifiers: fast mode, the batch tier, US
    inference geo and web-search requests each move a turn's price (ADR 0002),
    so a reading that kept only token counts would price a fast Opus turn at
    half its weight. Cache writes are split by lifetime because they are priced
    differently; an older log's undifferentiated `cache_creation_input_tokens`
    is read as 5m, exactly as the Rate Card assumes.

    `input_tokens` is the **uncached** input. Claude's logs already count it
    so; Codex's `input_tokens` includes the cached share, and its reader
    subtracts it (ADR 0002 § Codex usage), so the four buckets mean the same
    thing whichever agent's turn this was.
    """
    model: str | None = None
    when: datetime | None = None
    turn_uuid: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0
    speed: str = "standard"
    service_tier: str = "standard"
    inference_geo: str = ""

    def as_usage(self) -> dict:
        """The `usage` shape `rates` reads — the typed reading's round trip."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_tokens,
            "cache_creation_input_tokens": (self.cache_write_5m_tokens
                                            + self.cache_write_1h_tokens),
            "cache_creation": {
                "ephemeral_5m_input_tokens": self.cache_write_5m_tokens,
                "ephemeral_1h_input_tokens": self.cache_write_1h_tokens,
            },
            "speed": self.speed,
            "service_tier": self.service_tier,
            "inference_geo": self.inference_geo,
            "server_tool_use": {"web_search_requests": self.web_search_requests},
        }

    @property
    def cost(self) -> float | None:
        """This turn's Notional Cost, None when the Rate Card has no row for
        the model — never zero, which would read as a free turn."""
        return rates.turn_cost(self.model, self.as_usage())

    @property
    def tokens(self) -> dict[str, int]:
        """The four display buckets for this turn."""
        return rates.turn_tokens(self.as_usage())


@dataclass
class UsageTotals:
    """Summed per-turn usage: Notional Cost, its split by model, and the four
    display buckets. `turns` counts *priced* turns; an unpriced one is counted
    apart rather than folded in at zero dollars."""
    turns: int = 0
    unpriced_turns: int = 0
    cost: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, int] = field(
        default_factory=lambda: {b: 0 for b in rates.BUCKETS})


def usage_totals(turns) -> UsageTotals:
    """Price an iterable of TurnUsage turn by turn.

    Turn by turn, never bucket by bucket: the modifiers are per-turn, so
    summing tokens first and pricing once would mis-price any session that
    mixed fast and standard turns.
    """
    totals = UsageTotals()
    for t in turns:
        c = t.cost
        if c is None:
            totals.unpriced_turns += 1
            continue
        totals.turns += 1
        totals.cost += c
        totals.by_model[t.model] = totals.by_model.get(t.model, 0.0) + c
        for b, n in t.tokens.items():
            totals.tokens[b] += n
    return totals


@dataclass
class ParsedLog:
    """One Session log, read once and typed.

    `session` carries the facts the Triage Inbox needs (cwd, titles, branches,
    edited files, captured commit hashes, which agent); the lists beside it
    carry what the other views used to re-parse for themselves.
    """
    session: Session
    edits: list[EditBlock] = field(default_factory=list)
    prompts: list[Prompt] = field(default_factory=list)
    turns: list[TurnUsage] = field(default_factory=list)

    @property
    def totals(self) -> UsageTotals:
        """*This log's* Notional Cost. A Session's is more: its subagent
        transcripts are separate files carrying usage the parent never echoes,
        and folding them in is the caller's job (ADR 0002 § subagent usage)."""
        return usage_totals(self.turns)

    @property
    def last_turn(self) -> datetime | None:
        """The newest dated assistant turn — *not* the Session's last activity,
        which is the log file's mtime (see `models.Session.last_activity`)."""
        stamps = [t.when for t in self.turns if t.when]
        return max(stamps) if stamps else None


# ── the contract a dialect fulfils ──────────────────────────────────────────


class LineReader(Protocol):
    """One file's lines, read one at a time, in order.

    Every method takes a parsed JSON line (`obj`). A reader may keep state
    between calls — Codex names the model on one line and the usage on a later
    one — which is why a reader is made per file and fed the file in order. It
    must never *need* the whole file: the Watch feeds it a tail.
    """

    edit_tools: frozenset[str]      # tool names whose call is a file event

    def timestamp(self, obj: dict) -> datetime | None: ...
    def note_session(self, session: Session, obj: dict) -> None:
        """Fold what this line says about the Session itself — cwd, branch,
        titles, the agent — into `session`."""
    def is_interrupt(self, obj: dict) -> bool: ...
    def prompt_in(self, obj: dict, ts: datetime | None = None) -> Prompt | None: ...
    def tool_calls_in(self, obj: dict, ts: datetime | None = None) -> list[ToolCall]: ...
    def edits_of(self, call: ToolCall) -> list[EditBlock]: ...
    def shell_command(self, call: ToolCall) -> str | None:
        """The shell command a call runs, for the one tool per dialect whose
        argument is one (Claude's `Bash`, Codex's `exec_command`) — None for
        every other tool. The Watch renders it `$ …` with shell lexing."""
    def is_silent(self, name: str) -> bool:
        """A local read the Watch stays silent about (ADR 0004 § Calls)."""
    def tool_results_in(self, obj: dict) -> list[ToolResult]: ...
    def commit_shas_in(self, obj: dict) -> list[str]: ...
    def turn_usage(self, obj: dict, ts: datetime | None = None) -> TurnUsage | None: ...
    def assistant_parts(self, obj: dict) -> list[Part]: ...
    def activity(self, obj: dict, ts: datetime) -> str | None:
        """SETTLED, THINKING, a tool verb, or None to leave the state as it
        was (ADR 0004 § the Activity State)."""
    def hint(self, line: str) -> bool:
        """Could this raw line carry a tool call or usage? The Loop detector's
        prefilter, so `json.loads` is skipped on lines that carry neither."""


def note_edits(session: Session, blocks: list[EditBlock]) -> None:
    """Fold edit blocks into the Session's path -> latest-edit index.

    An undated block (a log line with no timestamp) is stamped `now` only when
    the path is new: the index answers "did this session touch this file, and
    how recently", and a missing stamp must not overwrite a real one.
    """
    for e in blocks:
        prev = session.edited_files.get(e.path)
        if e.when and (prev is None or e.when > prev):
            session.edited_files[e.path] = e.when
        elif prev is None and e.when is None:
            session.edited_files[e.path] = datetime.now(timezone.utc)


def note_commits(session: Session, shas: list[str], ts: datetime | None) -> None:
    for sha in shas:
        when = ts or datetime.now(timezone.utc)
        if sha not in session.commit_hashes or when > session.commit_hashes[sha]:
            session.commit_hashes[sha] = when


def walk(reader, path: Path, session: Session) -> ParsedLog:
    """The full reading of one log through one reader — the same walk for both
    dialects, so what a `ParsedLog` *is* is decided once.

    Every line is parsed: the readings here need the whole conversation. A log
    that cannot be opened comes back as an empty reading rather than raising —
    a log a view asked for by path may have been deleted under it, and a
    Session with nothing to say is dropped downstream by its missing `cwd`.
    """
    import json
    parsed = ParsedLog(session=session)
    try:
        fh = open(path, errors="replace")
    except OSError:
        return parsed
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            ts = reader.timestamp(obj)
            reader.note_session(session, obj)
            calls = reader.tool_calls_in(obj, ts)
            if calls:
                blocks = [e for c in calls for e in reader.edits_of(c)]
                note_edits(session, blocks)
                parsed.edits.extend(blocks)
            note_commits(session, reader.commit_shas_in(obj), ts)
            prompt = reader.prompt_in(obj, ts)
            if prompt is not None:
                parsed.prompts.append(prompt)
            turn = reader.turn_usage(obj, ts)
            if turn is not None:
                parsed.turns.append(turn)
    return parsed


# ── the dialects, and where their logs live ─────────────────────────────────


@dataclass(frozen=True)
class Dialect:
    """One agent's log schema: its name (the `Session.agent` value, and the
    word a view prints) and the module that reads it. The module is looked up
    by name at call time, so a test that monkeypatches `claude_logs.parse_log`
    still intercepts a read dispatched from here."""
    name: str
    module_name: str

    @property
    def module(self):
        return importlib.import_module(f".{self.module_name}", __package__)


CLAUDE = Dialect("claude", "claude_logs")
CODEX = Dialect("codex", "codex_logs")

_ROLLOUT = re.compile(r"\Arollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-")


def dialect_of(path: Path | str) -> Dialect:
    """Which agent wrote this log, from the file's own shape: Codex names every
    rollout `rollout-<timestamp>-<id>.jsonl`; nothing under `~/.claude` does.
    Decided by the name rather than the root it was found under, so a path
    handed straight to a view (a Session Handle's log) needs no root context.
    """
    return CODEX if _ROLLOUT.match(Path(path).name) else CLAUDE


def session_id_of(path: Path | str) -> str:
    """The Session id a log carries — its file stem for Claude Code, the id in
    the rollout's name for Codex (`codex_logs.session_id_of`)."""
    return dialect_of(path).module.session_id_of(Path(path))


def cache_id(path: Path | str) -> str:
    """The Derived Cache row id for a log (ADR 0001 § the accelerator
    protocol) — unique across the whole Scan Universe, which a Session id is
    not always (a Claude subagent transcript is qualified by its parent)."""
    return dialect_of(path).module.cache_id(Path(path))


def peek_cwd(path: Path) -> str | None:
    """A log's cwd from its head, without reading the file — what the Watch's
    discovery asks of a log that just appeared."""
    return dialect_of(path).module.peek_cwd(Path(path))


def reader(path: Path | str):
    """A fresh `LineReader` for one file, in that file's dialect."""
    return dialect_of(path).module.Reader(Path(path))


def parse_log(path: Path | str) -> ParsedLog:
    """Read one Session log. Pure: no cache, nothing on disk but this file."""
    return dialect_of(path).module.parse_log(Path(path))


@dataclass(frozen=True)
class Roots:
    """Where the logs live: the Claude Code projects directory and the Codex
    home. Either may be absent — a machine with one agent installed has one
    root — and every enumeration here reads the roots that exist.

    Passed around instead of a bare `projects_dir` so that "every Session log"
    has one answer for the Universe's scan, the cache's prune, the cost view's
    sweep and the Watch's discovery (ADR 0001 § two dialects, one reading).
    """
    claude: Path | None = None
    codex: Path | None = None

    @classmethod
    def default(cls, projects_dir: str | Path | None = None,
                codex_dir: str | Path | None = None) -> "Roots":
        """The roots a command reads: each override, or its default under
        `$HOME` (expanded at call time, so a repointed home is honoured)."""
        c = Path(os.path.expanduser(DEFAULT_PROJECTS_DIR) if projects_dir is None
                 else projects_dir)
        x = Path(os.path.expanduser(DEFAULT_CODEX_DIR) if codex_dir is None
                 else codex_dir)
        return cls(claude=c, codex=x)

    def present(self) -> "Roots":
        """Only the roots that are directories on disk."""
        return Roots(claude=self.claude if self.claude and self.claude.is_dir() else None,
                     codex=self.codex if self.codex and self.codex.is_dir() else None)

    def __bool__(self) -> bool:
        return self.claude is not None or self.codex is not None

    def session_logs(self) -> list[Path]:
        """Every Session log under the present roots, in a stable order."""
        out: list[Path] = []
        if self.claude is not None:
            out += CLAUDE.module.session_logs(self.claude)
        if self.codex is not None:
            out += CODEX.module.session_logs(self.codex)
        return out

    def readable_logs(self) -> list[Path]:
        """Every log `read_log` can be handed: the Session logs, plus the
        transcripts a level below them that are no Session (ADR 0002
        § subagent usage)."""
        out: list[Path] = []
        if self.claude is not None:
            out += CLAUDE.module.readable_logs(self.claude)
        if self.codex is not None:
            out += CODEX.module.readable_logs(self.codex)
        return out

    def describe(self) -> str:
        """The roots as a message names them."""
        parts = []
        if self.claude is not None:
            parts.append(f"Claude Code logs at {self.claude}")
        if self.codex is not None:
            parts.append(f"Codex logs at {self.codex}")
        return " or ".join(parts) or "no log roots"


def as_roots(where: "Roots | Path | str") -> Roots:
    """A `Roots`, or the Claude Code root alone when handed a bare directory —
    the shape every consumer took before Codex, kept so a fixture that builds a
    temp `projects_dir` still reads as a Scan Universe."""
    if isinstance(where, Roots):
        return where
    return Roots(claude=Path(where))


def session_log_ids(roots: "Roots | Path | str") -> set[str]:
    """Every Session log's Derived Cache id — the keys of a derivation keyed on
    a Session (ADR 0001 § the accelerator protocol), which is what its rows are
    pruned against."""
    return {cache_id(p) for p in as_roots(roots).present().session_logs()}


def readable_log_ids(roots: "Roots | Path | str") -> set[str]:
    """Every id `read_log` can be handed, spelled as `cache_id` spells them —
    the liveness of a derivation over *readings*, wider than one over Sessions."""
    return {cache_id(p) for p in as_roots(roots).present().readable_logs()}


# ── the Derived Cache round trip ────────────────────────────────────────────


def read_log(path: Path | str, cache) -> ParsedLog:
    """One Session log, read through the Derived Cache.

    The whole typed reading is cached together, keyed on (size, mtime_ns) like
    every other row: a Session's log is parsed once per change, however many
    views ask for it (ADR 0001 § the one log reader). One row shape for both
    dialects — the row *is* the typed reading, and `READER_VERSION` is shared.
    """
    path = Path(path)
    stamp = cache_mod.Stamp.of(path)
    if stamp is None:
        return parse_log(path)
    sid = session_id_of(path)
    return cache.derive(
        cache_mod.LOGS, cache_id(path), stamp,
        compute=lambda: parse_log(path),
        load=lambda row: log_from_cache(sid, str(path), stamp.mtime, row),
        dump=log_to_cache)


def scan_sessions(roots: "Roots | Path | str", cache) -> list[Session]:
    """Every Session of the Scan Universe, each log's inbox facts served from
    the cache when unchanged — the sweep the Triage Inbox is built on."""
    roots = as_roots(roots).present()
    sessions: list[Session] = []
    for log in roots.session_logs():
        stamp = cache_mod.Stamp.of(log)
        if stamp is None:
            continue
        session = dialect_of(log).module.swept_session(log, stamp, cache)
        if session.cwd:
            sessions.append(session)
    # The sweep is where the cache learns the logs are on disk; *which* of its
    # rows that makes live is each derivation's own declaration to answer
    # (ADR 0001 § the accelerator protocol).
    cache.prune(roots)
    return sessions


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def session_to_cache(s: Session) -> dict:
    return {
        "cwd": s.cwd,
        "agent": s.agent,
        "custom_title": s.custom_title,
        "ai_title": s.ai_title,
        "slug": s.slug,
        "first_prompt": s.first_prompt,
        "last_prompt": s.last_prompt,
        "branches": sorted(s.branches),
        "edited_files": {p: t.isoformat() for p, t in s.edited_files.items()},
        "commit_hashes": {h: t.isoformat() for h, t in s.commit_hashes.items()},
    }


def session_from_cache(session_id: str, log_path: str, mtime: datetime | None,
                       d: dict) -> Session:
    s = Session(session_id=session_id, log_path=log_path, last_activity=mtime)
    s.cwd = d.get("cwd")
    s.agent = d["agent"]         # a row without it predates the field: reparse
    s.custom_title = d.get("custom_title")
    s.ai_title = d.get("ai_title")
    s.slug = d.get("slug")
    s.first_prompt = d.get("first_prompt")
    s.last_prompt = d.get("last_prompt")
    s.branches = set(d.get("branches") or [])
    s.edited_files = {p: datetime.fromisoformat(t)
                      for p, t in (d.get("edited_files") or {}).items()}
    s.commit_hashes = {h: datetime.fromisoformat(t)
                       for h, t in (d.get("commit_hashes") or {}).items()}
    return s


def log_to_cache(parsed: ParsedLog) -> dict:
    """The typed reading as one cache row. Positional: the lists are long, and
    a repeated key is paid for on every entry."""
    return {
        "session": session_to_cache(parsed.session),
        "edits": [[e.tool, e.path, e.new, e.old, _iso(e.when), e.tool_id,
                   e.path_only]
                  for e in parsed.edits],
        "prompts": [[p.text, _iso(p.when)] for p in parsed.prompts],
        "turns": [[t.model, _iso(t.when), t.turn_uuid, t.input_tokens,
                   t.output_tokens, t.cache_read_tokens, t.cache_write_5m_tokens,
                   t.cache_write_1h_tokens, t.web_search_requests, t.speed,
                   t.service_tier, t.inference_geo]
                  for t in parsed.turns],
    }


def log_from_cache(session_id: str, log_path: str, mtime: datetime | None,
                   d: dict) -> ParsedLog | None:
    """A stored reading back, or None when the row is malformed — a row this
    version cannot read costs a reparse and changes no output."""
    try:
        return ParsedLog(
            session=session_from_cache(session_id, log_path, mtime, d["session"]),
            edits=[EditBlock(tool, path, new, old, parse_ts(when), tid, only)
                   for tool, path, new, old, when, tid, only in d["edits"]],
            prompts=[Prompt(text, parse_ts(when)) for text, when in d["prompts"]],
            turns=[TurnUsage(model, parse_ts(when), *rest)
                   for model, when, *rest in d["turns"]],
        )
    except (KeyError, TypeError, ValueError):
        return None
