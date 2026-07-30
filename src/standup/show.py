"""`standup show <handle>` — render a Session's Transcript.

Your prompts and Claude's responses in reading order. Tool calls collapse to
one-liners; thinking is hidden unless asked for; injected noise (system
reminders, hook output, tool results) is stripped so "you" is what you typed.
Each assistant turn is annotated with its per-turn Notional Cost.

This is the one view exempt from the inbox's never-wrap rule (CONTEXT.md):
prose wraps to the terminal width.
"""

from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime
from pathlib import Path

from . import brief as brief_mod
from . import loops as loops_mod
from . import rates
from .render import _style, _term_width

_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CMD_TAG_RE = re.compile(r"</?command-[^>]*>", re.DOTALL)
_TOOL_KEYS = ("file_path", "notebook_path", "command", "path", "pattern", "query", "url", "prompt")


class HandleError(Exception):
    pass


def resolve_handle(projects_dir: Path, handle: str) -> Path:
    matches = [p for p in projects_dir.glob("*/*.jsonl") if p.stem.startswith(handle)]
    if not matches:
        raise HandleError(f"standup: no session matches {handle!r}")
    # a full session id can appear under more than one project dir; dedup by stem
    stems = {p.stem for p in matches}
    if len(stems) > 1:
        listing = "\n".join(f"  {p.stem[:8]}  {p.parent.name}" for p in sorted(matches))
        raise HandleError(f"standup: {handle!r} is ambiguous:\n{listing}")
    return matches[0]


def _user_text(content) -> str | None:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # tool_result-only turns carry no user prose — skip them
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
    else:
        return None
    if "<command-name>" in text:  # a slash-command invocation: show the command + args
        name = (_CMD_NAME_RE.search(text) or [None, ""])[1].strip()
        args = (_CMD_ARGS_RE.search(text) or [None, ""])[1].strip()
        text = f"{name} {args}".strip()
    text = _CMD_TAG_RE.sub("", _REMINDER_RE.sub("", text)).strip()
    return text or None


def _tool_line(block: dict) -> str:
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    for k in _TOOL_KEYS:
        if k in inp and isinstance(inp[k], str):
            arg = " ".join(inp[k].split())
            if len(arg) > 60:
                arg = arg[:59] + "…"
            return f"{name} {arg}"
    return name


def _assistant_parts(content, show_thinking: bool,
                     looped_ids: set[str]) -> tuple[list[str], list[tuple[str, bool]]]:
    """(prose texts, [(tool one-liner, is part of an above-floor Loop)])."""
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
            tools.append((_tool_line(b), b.get("id") in looped_ids))
    return texts, tools


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


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


def render_transcript(path: Path, show_thinking: bool = False, raw: bool = False) -> str:
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

    # Session Brief (ADR 0006): read-only. Shown at the top so the reader gets an
    # instant understanding before the conversation. Absent/body-less → silent.
    brief = brief_mod.load_one(path.stem)
    last_ts: datetime | None = None

    # Loops (ADR 0007): gutter-mark the tool calls of above-floor Loops so the
    # evidence is visible where you'd eyeball it. One extra pass over one file.
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
        for tl, looped in block["tools"]:
            out.append(st.dim(f"  ⟳ {tl}") if looped else st.dim(f"  ⏺ {tl}"))
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

            ts = _parse_ts(obj.get("timestamp"))
            if ts and (last_ts is None or ts > last_ts):
                last_ts = ts

            if not header_done and obj.get("cwd"):
                out.append(st.bold(f"{path.stem[:8]}  {Path(obj['cwd']).name}"))
                out.append("")
                header_done = True
                brief_insert_idx = len(out)

            if etype == "user":
                if obj.get("isMeta"):  # injected skill/command bodies, not typed
                    continue
                text = _user_text(msg.get("content"))
                if text:
                    flush()
                    out.append(st.cyan(f"── you {body[7:]}"))
                    out.append(wrap(text))
                    out.append("")
            elif etype == "assistant":
                texts, tools = _assistant_parts(msg.get("content"), show_thinking, looped_ids)
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
        # Stamp staleness the way the inbox does, but against the session's own
        # last-activity timestamp scanned from this JSONL (show has no Session).
        try:
            if brief.generated and last_ts and last_ts > brief.generated + brief_mod.STALE_TOLERANCE:
                brief.stale = True
        except TypeError:  # naive vs aware in a hand-edited brief — don't crash show
            pass
        block_lines = _brief_block(brief, st, width, wrap)
        idx = brief_insert_idx if brief_insert_idx is not None else 0
        out[idx:idx] = block_lines

    if not out:
        return st.dim("(no readable turns in this session)")
    return "\n".join(out)
