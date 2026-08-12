"""Read ~/.claude/projects JSONL session logs — the one module that knows their
schema (ADR 0001 § the one log reader).

Two entry points, one set of line readings:

- `scan_sessions(projects_dir, cache)` sweeps the Scan Universe for the Triage
  Inbox's facts (cwd, titles, branches, edited files, captured commit hashes).
  Line-level prefiltering keeps `json.loads` off the ~99% of lines that carry
  none of them.
- `read_log(path, cache)` reads *one* log completely into a typed `ParsedLog`:
  the same Session, plus the edit blocks, prompt text and per-turn usage the
  other views need. Every line is parsed, because those live on ordinary
  conversation lines.

Both are cached in the Derived Cache (ADR 0001 § the Derived Cache) keyed on
(size, mtime_ns): unchanged files are served without being opened. Parsing is
not gated by a lookback horizon — the cache makes full-history parsing cheap,
and attribution is ageless (ADR 0001 § ageless attribution).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import rates
from .models import Session

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# bump when the typed reading changes shape or meaning (invalidates cache rows)
READER_VERSION = 1
# `[branch abc1234]` / `[main (root-commit) abc1234]` / `[detached HEAD abc1234]`
COMMIT_LINE_RE = re.compile(r"^\[[^\[\]\n]{1,80} ([0-9a-f]{7,40})\]", re.MULTILINE)
# cheap hint on the raw JSON line (stdout newlines are escaped as \\n there)
COMMIT_HINT_RE = re.compile(r"\[[^\]\n]{1,80} [0-9a-f]{7,40}\]")

# the noise Claude Code injects into a user turn's text, and the tags a slash
# command arrives wrapped in
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CMD_TAG_RE = re.compile(r"</?command-[^>]*>", re.DOTALL)


def _mtime(path: Path) -> datetime | None:
    """A log's last-append time — the one meaning of a Session's
    `last_activity` (ADR 0001 § the one log reader)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


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
    the one place that knows the log's title schema — `cost` used to carry its
    own copy of the pair, and a mistyped prefilter in it silently demoted every
    session title to its last prompt (ADR 0001 § the one log reader).
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


@dataclass(frozen=True)
class EditBlock:
    """One recorded edit: the text a Session put in, and the text it took out.

    `new` is empty for a pure deletion, `old` for a Write or a create. A
    MultiEdit fans out to one EditBlock per entry in its `edits[]` — the hunks
    were one action by the agent, which `tool_id` (shared across the fan-out) is
    what remembers.

    `path` is the absolute path exactly as the log recorded it; a relative one
    attributes nothing and is dropped, never guessed at.
    """
    tool: str                      # Edit | Write | MultiEdit | NotebookEdit
    path: str
    new: str
    old: str
    when: datetime | None = None
    tool_id: str = ""


def _edit_blocks(obj: dict, ts: datetime | None) -> list[EditBlock]:
    """Every edit one assistant line recorded.

    The path aliases (`file_path`, `notebook_path`) and the new-text fallbacks
    (`new_string`, `new_source`, `content`) live here and nowhere else: the
    schema is Claude Code's, and reading it in three modules is how two of them
    end up disagreeing about what a NotebookEdit wrote.
    """
    message = obj.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    out: list[EditBlock] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = item.get("name")
        if name not in EDIT_TOOLS:
            continue
        inp = item.get("input") or {}
        fp = inp.get("file_path") or inp.get("notebook_path")
        if not fp or not os.path.isabs(fp):
            continue
        tid = item.get("id") or ""
        if name == "MultiEdit":
            hunks = [e for e in inp.get("edits") or [] if isinstance(e, dict)]
            # a MultiEdit whose hunks are missing or malformed still says the
            # Session touched this file, and path overlap is the attribution
            # that rests on that alone (CONTEXT.md → Attribution Tier). Dropping
            # the call would silently cost the file its Session Rollup.
            for e in hunks or [{}]:
                out.append(EditBlock(name, fp, e.get("new_string") or "",
                                     e.get("old_string") or "", ts, tid))
        else:
            new = (inp.get("new_string") or inp.get("new_source")
                   or inp.get("content") or "")
            out.append(EditBlock(name, fp, new, inp.get("old_string") or "",
                                 ts, tid))
    return out


def _note_edits(session: Session, blocks: list[EditBlock]) -> None:
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


def _extract_commits(session: Session, obj: dict, ts: datetime | None) -> None:
    tr = obj.get("toolUseResult")
    if tr is None:
        return
    text = tr if isinstance(tr, str) else json.dumps(tr) if not isinstance(tr, dict) else (tr.get("stdout") or "")
    for m in COMMIT_LINE_RE.finditer(text):
        sha = m.group(1)
        when = ts or datetime.now(timezone.utc)
        if sha not in session.commit_hashes or when > session.commit_hashes[sha]:
            session.commit_hashes[sha] = when


@dataclass(frozen=True)
class Prompt:
    """One user turn's typed text, and when it was typed.

    "Typed" is the whole point: injected material is not a prompt. System
    reminders, the bodies Claude Code splices in behind a slash command
    (`isMeta`), and turns that carry nothing but a tool result are all dropped
    here, so a consumer never has to know which of them exist.

    One divergence to settle when the consumers migrate: this reading is the
    Transcript's (a user line is a prompt if it holds prose), while the Watch
    additionally drops any user line carrying a `toolUseResult`. The two agree
    on every shape but a tool result that also carries prose.
    """
    text: str
    when: datetime | None = None


def _tagged(pattern: re.Pattern, text: str) -> str:
    """The first capture of `pattern` in `text`, stripped; "" when it misses."""
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _prompt_text(content) -> str | None:
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


@dataclass(frozen=True)
class TurnUsage:
    """One assistant turn's `usage`, typed as the Rate Card reads it.

    Counts *and* the per-turn modifiers: fast mode, the batch tier, US
    inference geo and web-search requests each move a turn's price (ADR 0002),
    so a reading that kept only token counts would price a fast Opus turn at
    half its weight. Cache writes are split by lifetime because they are priced
    differently; an older log's undifferentiated `cache_creation_input_tokens`
    is read as 5m, exactly as the Rate Card assumes.
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


def _turn_usage(obj: dict, ts: datetime | None) -> TurnUsage | None:
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


# ── the typed reading of one log ────────────────────────────────────────────


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
    edited files, captured commit hashes); the lists beside it carry what the
    other views used to re-parse for themselves.
    """
    session: Session
    edits: list[EditBlock] = field(default_factory=list)
    prompts: list[Prompt] = field(default_factory=list)
    turns: list[TurnUsage] = field(default_factory=list)

    @property
    def totals(self) -> UsageTotals:
        """*This log's* Notional Cost. A Session's is more: its subagent
        transcripts are separate files carrying usage the parent never echoes,
        and folding them in is the caller's job (ADR 0002 § subagent usage).

        A view with a window filters `turns` by their own timestamps first —
        the totals are derived, so there is no second meaning to keep in sync.
        """
        return usage_totals(self.turns)

    @property
    def last_turn(self) -> datetime | None:
        """The newest dated assistant turn — *not* the Session's last activity,
        which is the log file's mtime (see `models.Session.last_activity`).
        Named apart because the two answer different questions: when the model
        last spoke, versus when the file last grew."""
        stamps = [t.when for t in self.turns if t.when]
        return max(stamps) if stamps else None


def parse_log(path: Path | str) -> ParsedLog:
    """Read one Session log. Pure: no cache, nothing on disk but this file.

    Every line is parsed — the readings here need the whole conversation, so
    the line-level prefilter the inbox's sweep uses (`_interesting`) would only
    hide turns. One consequence to know about: `branches` is read off every
    line carrying `gitBranch`, so a Session that changed branch away from an
    edit or a title line lands here with a *superset* of what the prefiltered
    sweep sees. More complete, and the answer a consumer switching over gets.

    A log that cannot be opened comes back as an empty reading rather than
    raising — a log a view asked for by path may have been deleted under it,
    and a Session with nothing to say is dropped downstream by its missing
    `cwd`.
    """
    path = Path(path)
    session = Session(session_id=path.stem, log_path=str(path),
                      last_activity=_mtime(path))
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
            ts = _parse_ts(obj.get("timestamp"))
            etype = obj.get("type")
            if session.cwd is None and obj.get("cwd"):
                session.cwd = obj["cwd"]
            if obj.get("gitBranch"):
                session.branches.add(obj["gitBranch"])
            apply_title_fields(session, obj)
            if etype == "assistant":
                blocks = _edit_blocks(obj, ts)
                _note_edits(session, blocks)
                parsed.edits.extend(blocks)
                turn = _turn_usage(obj, ts)
                if turn is not None:
                    parsed.turns.append(turn)
            elif etype == "user":
                _extract_commits(session, obj, ts)
                if not obj.get("isMeta"):
                    text = _prompt_text((obj.get("message") or {}).get("content"))
                    if text:
                        parsed.prompts.append(Prompt(text, ts))
    return parsed


def read_log(path: Path | str, cache) -> ParsedLog:
    """One Session log, read through the Derived Cache.

    The whole typed reading is cached together, keyed on (size, mtime_ns) like
    every other row: a Session's log is parsed once per change, however many
    views ask for it (ADR 0001 § the one log reader).
    """
    path = Path(path)
    sid = path.stem
    try:
        st = path.stat()
    except OSError:
        return parse_log(path)
    hit = cache.get_log(sid, st.st_size, st.st_mtime_ns)
    if hit is not None:
        parsed = _log_from_cache(
            sid, str(path),
            datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), hit)
        if parsed is not None:
            return parsed
    parsed = parse_log(path)
    cache.put_log(sid, st.st_size, st.st_mtime_ns, _log_to_cache(parsed))
    return parsed


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

            ts = _parse_ts(obj.get("timestamp"))
            etype = obj.get("type")
            apply_title_fields(session, obj)
            if obj.get("gitBranch"):
                session.branches.add(obj["gitBranch"])
            if etype == "assistant":
                _note_edits(session, _edit_blocks(obj, ts))
            elif etype == "user":
                _extract_commits(session, obj, ts)


def scan_sessions(projects_dir: Path, cache) -> list[Session]:
    """Fully parse every session file, serving unchanged ones from the cache."""
    sessions: list[Session] = []
    live_ids: set[str] = set()
    for log in sorted(projects_dir.glob("*/*.jsonl")):
        try:
            st = log.stat()
        except OSError:
            continue
        sid = log.stem
        live_ids.add(sid)
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        cached = cache.get_session(sid, st.st_size, st.st_mtime_ns)
        if cached is not None:
            session = _from_cache(sid, str(log), mtime, cached)
        else:
            session = Session(session_id=sid, log_path=str(log), last_activity=mtime)
            _full_scan(session, log)
            cache.put_session(sid, st.st_size, st.st_mtime_ns, _to_cache(session))
        if session.cwd:
            sessions.append(session)
    # A subagent transcript is a log with a cache row of its own — the cost
    # view reads one per delegating Session (ADR 0002 § subagent usage) — but
    # it lives a level below this sweep's glob and is no Session, so it never
    # enters the list above. Name it live anyway: a prune that knew only the
    # ids here would drop those readings on every inbox run.
    live_ids.update(f.stem for f in projects_dir.glob("*/*/subagents/agent-*.jsonl"))
    cache.prune(live_ids)
    return sessions


# ── the Derived Cache round trip ────────────────────────────────────────────


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _to_cache(s: Session) -> dict:
    return {
        "cwd": s.cwd,
        "custom_title": s.custom_title,
        "ai_title": s.ai_title,
        "slug": s.slug,
        "last_prompt": s.last_prompt,
        "branches": sorted(s.branches),
        "edited_files": {p: t.isoformat() for p, t in s.edited_files.items()},
        "commit_hashes": {h: t.isoformat() for h, t in s.commit_hashes.items()},
    }


def _from_cache(session_id: str, log_path: str, mtime: datetime, d: dict) -> Session:
    s = Session(session_id=session_id, log_path=log_path, last_activity=mtime)
    s.cwd = d.get("cwd")
    s.custom_title = d.get("custom_title")
    s.ai_title = d.get("ai_title")
    s.slug = d.get("slug")
    s.last_prompt = d.get("last_prompt")
    s.branches = set(d.get("branches") or [])
    s.edited_files = {p: datetime.fromisoformat(t)
                      for p, t in (d.get("edited_files") or {}).items()}
    s.commit_hashes = {h: datetime.fromisoformat(t)
                       for h, t in (d.get("commit_hashes") or {}).items()}
    return s


def _log_to_cache(parsed: ParsedLog) -> dict:
    """The typed reading as one cache row. Positional, like the fragment index:
    the lists are long, and a repeated key is paid for on every entry."""
    return {
        "session": _to_cache(parsed.session),
        "edits": [[e.tool, e.path, e.new, e.old, _iso(e.when), e.tool_id]
                  for e in parsed.edits],
        "prompts": [[p.text, _iso(p.when)] for p in parsed.prompts],
        "turns": [[t.model, _iso(t.when), t.turn_uuid, t.input_tokens,
                   t.output_tokens, t.cache_read_tokens, t.cache_write_5m_tokens,
                   t.cache_write_1h_tokens, t.web_search_requests, t.speed,
                   t.service_tier, t.inference_geo]
                  for t in parsed.turns],
    }


def _log_from_cache(session_id: str, log_path: str, mtime: datetime | None,
                    d: dict) -> ParsedLog | None:
    """A stored reading back, or None when the row is malformed — a row this
    version cannot read costs a reparse and changes no output."""
    try:
        return ParsedLog(
            session=_from_cache(session_id, log_path, mtime, d["session"]),
            edits=[EditBlock(tool, path, new, old, _parse_ts(when), tid)
                   for tool, path, new, old, when, tid in d["edits"]],
            prompts=[Prompt(text, _parse_ts(when)) for text, when in d["prompts"]],
            turns=[TurnUsage(model, _parse_ts(when), *rest)
                   for model, when, *rest in d["turns"]],
        )
    except (KeyError, TypeError, ValueError):
        return None
