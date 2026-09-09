"""`standup session <handle>` — render a Session's Transcript.

Your prompts and the agent's responses in reading order — a Claude Code
session or a Codex one, read through the same `logs.LineReader` face so this
view never learns which (ADR 0001 § two dialects, one reading). Tool calls
collapse to one-liners — `--tools` prints each one's whole input beneath it,
never its result; thinking is hidden unless asked for; injected noise (system
reminders, hook output, tool results, Codex's environment blocks) is stripped
so "you" is what you typed, and an interrupted turn is marked as interrupted
rather than credited to you. Each assistant turn is annotated with its
per-turn Notional Cost.

This is the one view exempt from the inbox's never-wrap rule (CONTEXT.md):
prose wraps to the terminal width.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from . import artifacts
from . import brief as brief_mod
from . import logs
from . import loops as loops_mod
from . import toolcalls
from .termout import (agent_tag, claim_hedges, claim_rule, session_ref, style,
                      term_width)

_HEXISH = re.compile(r"[0-9a-f]{4,40}\Z")


class HandleError(Exception):
    pass


def resolve_handle(roots, handle: str) -> Path:
    """A Session Handle — any unambiguous prefix of a Session id — to its log,
    over every root (`logs.Roots`, or one Claude Code root as a bare path)."""
    where = logs.as_roots(roots).present()
    matches = [p for p in where.session_logs()
               if logs.session_id_of(p).startswith(handle)]
    if not matches:
        # A git commit hash and a Session Handle are both short lowercase hex,
        # so the most likely miss here is a hash pasted out of a commit line.
        # Name the distinction rather than claim which one this was.
        hint = ""
        if _HEXISH.match(handle):
            hint = ("\n  a Session Handle is the cyan 8-char id on a Session's title line;"
                    "\n  a git commit hash (rendered @" + handle + ") addresses nothing here;"
                    "\n  to read one, name its repo: standup <repo> diff @" + handle)
        raise HandleError(f"standup: no session matches {handle!r}{hint}")
    # a full session id can appear under more than one project dir; dedup by id
    ids = {logs.session_id_of(p) for p in matches}
    if len(ids) > 1:
        listing = "\n".join(f"  {logs.session_id_of(p)[:8]}  {p.parent.name}"
                            for p in sorted(matches))
        raise HandleError(f"standup: {handle!r} is ambiguous:\n{listing}")
    return matches[0]


def _tool_line(call: logs.ToolCall) -> str:
    """One tool call as one line, through the renderer the Watch's Calls use —
    so the two surfaces cannot drift (`toolcalls`)."""
    return toolcalls.one_liner(call.name or "tool", call.input, 60)


def _tool_input_lines(call: logs.ToolCall) -> list[str]:
    """`--tools`: the call's whole input beneath its one-liner — the static
    counterpart to expanding a **Call** in the Watch, through the same renderer,
    and bound by the same refusal to show a result (ADR 0004 § Calls)."""
    rows = toolcalls.input_rows(call.input)
    return [f"      {path}  {text}" if path else f"      {text}"
            for path, text in rows]


def _visible(parts: list[logs.Part], show_thinking: bool,
             looped_ids: frozenset[str] | set[str] = frozenset(),
             show_tools: bool = False,
             ) -> tuple[list[str], list[tuple[str, bool, list[str]]]]:
    """(prose texts, [(tool one-liner, is part of an above-floor Loop, input rows)])
    — what one assistant line shows, in its own order."""
    texts, tools = [], []
    for p in parts:
        if p.kind == "text":
            texts.append(p.text)
        elif p.kind == "thinking" and show_thinking:
            texts.append("[thinking] " + p.text)
        elif p.kind == "call" and p.call is not None:
            tools.append((_tool_line(p.call), p.call.tool_id in looped_ids,
                          _tool_input_lines(p.call) if show_tools else []))
    return texts, tools


def _lines(path: Path):
    """The parsed lines of one log, in order, with the file's reader."""
    reader = logs.reader(path)
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield reader, obj


# What an over-budget digest gets instead of its middle. Marked rather than
# silent: a summariser that reads a truncated session should say so.
ELIDED = "\n… [middle elided] …\n"


def digest(path: Path, *, max_chars: int, head_chars: int,
           looped_ids: frozenset[str] | set[str] = frozenset(),
           numbered: bool = False) -> str:
    """A compact plain-text rendering of one Session for an LLM pass — the one
    body behind a Session Brief's digest and an Audit's
    (ADR 0003 § the LLM-pass seam).

    The same reading the Transcript renders, minus the colour: your prompts
    through the one prompt reading (ADR 0001 § the one log reader), the agent's
    prose, and each tool call as a one-liner.

    `numbered=True` is what an Audit needs on top: `t<N>` turn markers — the
    evidence coordinates a Handoff Prompt is written in — each turn's per-turn
    Notional Cost, and a `⟳` on the tool calls of a detected Loop.

    A turn is a line with something to show; its cost is that line's own usage
    plus any usage line with nothing else on it that follows before the next
    turn — Codex writes its usage a line after the response it prices, Claude
    on the same line (ADR 0001 § two dialects, one reading).

    Capped: when over budget, keep the head (where the objective is usually set)
    plus the tail (where it landed), with the elision marked.
    """
    who = logs.dialect_of(path).name.upper()
    # each entry: ("text", line) | ("turn", texts, tools, cost)
    entries: list = []
    last_turn: list | None = None
    try:
        for reader, obj in _lines(path):
            if reader.is_interrupt(obj):
                # kept, and labelled as itself: an Expert judging the human's
                # side of a session reads an abandoned turn as evidence, and
                # reading it as a prompt would make Esc look like a question
                entries.append(("text", "INTERRUPTED BY USER"))
                last_turn = None
                continue
            prompt = reader.prompt_in(obj)
            if prompt is not None:
                entries.append(("text", "USER: " + prompt.text))
                last_turn = None
                continue
            parts = reader.assistant_parts(obj)
            usage = reader.turn_usage(obj)
            texts, tools = _visible(parts, False, looped_ids)
            if texts or tools:
                last_turn = ["turn", texts, tools,
                             usage.cost if usage is not None else None]
                entries.append(last_turn)
            elif not parts and usage is not None and last_turn is not None:
                c = usage.cost
                if c is not None:
                    last_turn[3] = (last_turn[3] or 0.0) + c
    except OSError:
        return ""

    parts_out: list[str] = []
    turn = 0
    for e in entries:
        if e[0] == "text":
            parts_out.append(e[1])
            continue
        _, texts, tools, cost = e
        turn += 1
        head = f"{who}:"
        if numbered:
            tag = f" (${cost:.2f})" if cost else ""
            head = f"[t{turn}{tag}] {who}:"
        for t in texts:
            parts_out.append(f"{head} {t}")
            if numbered:
                head = f"[t{turn}] {who}:"
        for tl, looped, _rows in tools:
            parts_out.append(f"  {'⟳' if looped else '·'} {tl}")
    text = "\n".join(parts_out)
    if len(text) > max_chars:
        text = text[:head_chars] + ELIDED + text[-(max_chars - head_chars):]
    return text


def _brief_block(brief, st, width: int, wrap) -> list[str]:
    """The Session Brief header shown at the very top of a Transcript.

    A *claim*, not a derived fact (ADR 0003 § the shared model): marked and
    hedged through the one claim renderer the inbox and the `audit` view also
    use (`termout.claim_rule`), in its roomy form — a whole view's width has
    space to say what "stale" means. Leads with the objective, then the freeform
    body; a dim provenance line closes it. Carries no cost tag — Brief Overhead
    stays a `cost`-view concern.
    """
    header = claim_rule("brief", st, width,
                        hedges=claim_hedges(brief, verbose=True))
    lines = [header, st.bold(wrap(brief.objective))]
    if brief.body:
        lines.append("")
        lines.append(wrap(brief.body))
    prov = []
    if brief.generated:
        prov.append(brief.generated.date().isoformat())
    if brief.model:
        prov.append(brief.model)
    if prov:
        lines.append("")
        lines.append(st.dim("  " + " · ".join(prov)))
    lines.append("")
    return lines


def render_transcript(path: Path, show_thinking: bool = False, raw: bool = False,
                      show_tools: bool = False) -> str:
    if raw:
        return path.read_text(errors="replace")

    st = style()
    width = min(term_width(), 100)
    agent = logs.dialect_of(path).name
    sid = logs.session_id_of(path)

    def rule(label: str) -> str:
        return f"── {label} " + "─" * max(0, width - len(label) - 4)

    def wrap(s: str) -> str:
        # wrap each source line on its own so intentional (markdown) breaks survive,
        # but runs of spaces within a line collapse — no leading-space artifacts
        lines = []
        for ln in s.split("\n"):
            lines.append(textwrap.fill(ln, width=width) if ln.strip() else "")
        return "\n".join(lines)

    # Session Brief (ADR 0003 § the Session Brief): read-only. Shown at the top
    # so the reader gets an instant understanding before the conversation.
    # Absent/body-less → silent.
    brief = brief_mod.load_one(sid)

    # Loops (ADR 0003 § the Audit): gutter-mark the tool calls of above-floor
    # Loops so the evidence is visible where you'd eyeball it. One extra pass
    # over one file.
    looped_ids = {tid for l in loops_mod.significant(loops_mod.detect(path))
                  for tid in l.tool_ids}

    out: list[str] = []
    cwd = logs.peek_cwd(path)
    if cwd:
        out.append(session_ref(sid[:8], st) + "  " + st.bold(Path(cwd).name)
                   + agent_tag(agent, st))
        out.append("")
    brief_insert_idx = len(out)

    # buffer consecutive assistant events into one response block (summed cost)
    block: dict | None = None

    def flush() -> None:
        nonlocal block
        if block is None:
            return
        tag = st.dim(f"  ${block['cost']:.2f}") if block["has_cost"] else ""
        out.append(st.yellow(rule(agent)) + tag)
        for t in block["texts"]:
            out.append(wrap(t))
        for tl, looped, inp_lines in block["tools"]:
            out.append(st.dim(f"  ⟳ {tl}") if looped else st.dim(f"  ⏺ {tl}"))
            out.extend(st.dim(ln) for ln in inp_lines)
        out.append("")
        block = None

    def price(usage) -> None:
        c = usage.cost if usage is not None else None
        if c is not None and block is not None:
            block["cost"] += c
            block["has_cost"] = True

    for reader, obj in _lines(path):
        # An interrupt is not a prompt — its text is text nobody typed — but it
        # *is* why the turn above stops mid-sentence, so it gets a rule of its
        # own rather than being credited to you (ADR 0001 § the one log reader).
        if reader.is_interrupt(obj):
            flush()
            out.append(st.dim(rule("interrupted")))
            out.append("")
            continue
        # what you typed, through the one reading the Watch also shows
        # (ADR 0001 § the one log reader): injected bodies, system reminders
        # and bare tool results are none of them prompts
        prompt = reader.prompt_in(obj)
        if prompt is not None:
            flush()
            out.append(st.cyan(rule("you")))
            out.append(wrap(prompt.text))
            out.append("")
            continue
        parts = reader.assistant_parts(obj)
        usage = reader.turn_usage(obj)
        texts, tools = _visible(parts, show_thinking, looped_ids, show_tools)
        if texts or tools:
            if block is None:
                block = {"texts": [], "tools": [], "cost": 0.0, "has_cost": False}
            block["texts"].extend(texts)
            block["tools"].extend(tools)
            price(usage)
        elif not parts:
            # a usage line with nothing else on it prices the block it follows
            price(usage)
    flush()

    if brief is not None:
        # Hedged by the same comparison the inbox uses, against the same clock —
        # the store owns both, so no surface can invent its own notion of
        # "the session moved on" (ADR 0003 § the shared model).
        artifacts.stamp_staleness(brief, path)
        out[brief_insert_idx:brief_insert_idx] = _brief_block(brief, st, width, wrap)

    if not out:
        return st.dim("(no readable turns in this session)")
    return "\n".join(out)
