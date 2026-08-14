"""`standup session <handle>` — render a Session's Transcript.

Your prompts and Claude's responses in reading order. Tool calls collapse to
one-liners — `--tools` prints each one's whole input beneath it, never its
result; thinking is hidden unless asked for; injected noise (system
reminders, hook output, tool results) is stripped so "you" is what you typed.
Each assistant turn is annotated with its per-turn Notional Cost.

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
from . import claude_logs
from . import loops as loops_mod
from . import rates, toolcalls
from .render import _session_ref, _style, _term_width

_HEXISH = re.compile(r"[0-9a-f]{4,40}\Z")


class HandleError(Exception):
    pass


def resolve_handle(projects_dir: Path, handle: str) -> Path:
    matches = [p for p in projects_dir.glob("*/*.jsonl") if p.stem.startswith(handle)]
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
    # a full session id can appear under more than one project dir; dedup by stem
    stems = {p.stem for p in matches}
    if len(stems) > 1:
        listing = "\n".join(f"  {p.stem[:8]}  {p.parent.name}" for p in sorted(matches))
        raise HandleError(f"standup: {handle!r} is ambiguous:\n{listing}")
    return matches[0]


def _tool_line(block: dict) -> str:
    """One tool call as one line, through the renderer the Watch's Calls use —
    so the two surfaces cannot drift (`toolcalls`)."""
    return toolcalls.one_liner(block.get("name", "tool"), block.get("input"), 60)


def _tool_input_lines(block: dict) -> list[str]:
    """`--tools`: the call's whole input beneath its one-liner — the static
    counterpart to expanding a **Call** in the Watch, through the same renderer,
    and bound by the same refusal to show a result (ADR 0004 § Calls)."""
    rows = toolcalls.input_rows(block.get("input"))
    return [f"      {path}  {text}" if path else f"      {text}"
            for path, text in rows]


def _assistant_parts(content, show_thinking: bool,
                     looped_ids: frozenset[str] | set[str] = frozenset(),
                     show_tools: bool = False,
                     ) -> tuple[list[str], list[tuple[str, bool, list[str]]]]:
    """(prose texts, [(tool one-liner, is part of an above-floor Loop, input rows)])."""
    texts, tools = [], []
    if not isinstance(content, list):
        return texts, tools
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            texts.append(b["text"].strip())
        elif t == "thinking" and show_thinking and b.get("thinking", "").strip():
            texts.append("[thinking] " + b["thinking"].strip())
        elif t == "tool_use":
            tools.append((_tool_line(b), b.get("id") in looped_ids,
                          _tool_input_lines(b) if show_tools else []))
    return texts, tools


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
    through the one prompt reading (ADR 0001 § the one log reader), Claude's
    prose, and each tool call as a one-liner.

    `numbered=True` is what an Audit needs on top: `t<N>` turn markers — the
    evidence coordinates a Handoff Prompt is written in — each turn's per-turn
    Notional Cost, and a `⟳` on the tool calls of a detected Loop.

    Capped: when over budget, keep the head (where the objective is usually set)
    plus the tail (where it landed), with the elision marked.
    """
    parts: list[str] = []
    turn = 0
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = obj.get("type")
                msg = obj.get("message") or {}
                if etype == "user":
                    prompt = claude_logs.prompt_in(obj)
                    if prompt is not None:
                        parts.append("USER: " + prompt.text)
                elif etype == "assistant":
                    texts, tools = _assistant_parts(msg.get("content"),
                                                    show_thinking=False,
                                                    looped_ids=looped_ids)
                    if not texts and not tools:
                        continue
                    turn += 1
                    head = "CLAUDE:"
                    if numbered:
                        u = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
                        c = rates.turn_cost(msg.get("model"), u) if u else None
                        tag = f" (${c:.2f})" if c else ""
                        head = f"[t{turn}{tag}] CLAUDE:"
                    for t in texts:
                        parts.append(f"{head} {t}")
                        if numbered:
                            head = f"[t{turn}] CLAUDE:"
                    for tl, looped, _rows in tools:
                        parts.append(f"  {'⟳' if looped else '·'} {tl}")
    except OSError:
        return ""
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:head_chars] + ELIDED + text[-(max_chars - head_chars):]
    return text


def _brief_block(brief, st, width: int, wrap) -> list[str]:
    """The Session Brief header shown at the very top of a Transcript.

    A *claim*, not a derived fact (CONTEXT.md → Session Brief): marked with the
    `~` idiom and hedged when stale. Leads with the objective, then the freeform
    body; a dim provenance line closes it. Carries no cost tag — Brief Overhead
    stays a `cost`-view concern.
    """
    rule = "─" * width
    tags = []
    if brief.status and brief.status != "done":
        tags.append(brief.status)
    if brief.stale:
        tags.append("may be stale")
    label = "── ~ brief"
    if tags:
        label += " · " + " · ".join(tags)
    label += " "
    header = st.dim(label + rule[len(label):] if len(label) < width else label)
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

    st = _style()
    width = min(_term_width(), 100)
    body = "─" * width

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
    brief = brief_mod.load_one(path.stem)

    # Loops (ADR 0003 § the Audit): gutter-mark the tool calls of above-floor
    # Loops so the evidence is visible where you'd eyeball it. One extra pass
    # over one file.
    looped_ids = {tid for l in loops_mod.significant(loops_mod.detect(path))
                  for tid in l.tool_ids}

    header_done = False
    brief_insert_idx: int | None = None
    out: list[str] = []
    # buffer consecutive assistant events into one response block (summed cost)
    block: dict | None = None

    def flush() -> None:
        nonlocal block
        if block is None:
            return
        tag = st.dim(f"  ${block['cost']:.2f}") if block["has_cost"] else ""
        out.append(st.yellow(f"── claude {body[10:]}") + tag)
        for t in block["texts"]:
            out.append(wrap(t))
        for tl, looped, inp_lines in block["tools"]:
            out.append(st.dim(f"  ⟳ {tl}") if looped else st.dim(f"  ⏺ {tl}"))
            out.extend(st.dim(ln) for ln in inp_lines)
        out.append("")
        block = None

    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = obj.get("type")
            msg = obj.get("message") or {}

            if not header_done and obj.get("cwd"):
                out.append(_session_ref(path.stem[:8], st) + "  "
                           + st.bold(Path(obj["cwd"]).name))
                out.append("")
                header_done = True
                brief_insert_idx = len(out)

            if etype == "user":
                # what you typed, through the one reading the Watch also shows
                # (ADR 0001 § the one log reader): injected bodies, system
                # reminders and bare tool results are none of them prompts
                prompt = claude_logs.prompt_in(obj)
                if prompt is not None:
                    flush()
                    out.append(st.cyan(f"── you {body[7:]}"))
                    out.append(wrap(prompt.text))
                    out.append("")
            elif etype == "assistant":
                texts, tools = _assistant_parts(msg.get("content"), show_thinking,
                                                looped_ids, show_tools)
                if not texts and not tools:
                    continue
                if block is None:
                    block = {"texts": [], "tools": [], "cost": 0.0, "has_cost": False}
                block["texts"].extend(texts)
                block["tools"].extend(tools)
                u = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
                c = rates.turn_cost(msg.get("model"), u) if u else None
                if c is not None:
                    block["cost"] += c
                    block["has_cost"] = True
    flush()

    if brief is not None:
        # Hedged by the same comparison the inbox uses, against the same clock —
        # the store owns both, so no surface can invent its own notion of
        # "the session moved on" (ADR 0003 § the shared model).
        artifacts.stamp_staleness(brief, path)
        block_lines = _brief_block(brief, st, width, wrap)
        idx = brief_insert_idx if brief_insert_idx is not None else 0
        out[idx:idx] = block_lines

    if not out:
        return st.dim("(no readable turns in this session)")
    return "\n".join(out)
