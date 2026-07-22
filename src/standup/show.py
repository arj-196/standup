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
from pathlib import Path

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


def _assistant_parts(content, show_thinking: bool) -> tuple[list[str], list[str]]:
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
            tools.append(_tool_line(b))
    return texts, tools


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

    header_done = False
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
        for tl in block["tools"]:
            out.append(st.dim(f"  ⏺ {tl}"))
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
                out.append(st.bold(f"{path.stem[:8]}  {Path(obj['cwd']).name}"))
                out.append("")
                header_done = True

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
                texts, tools = _assistant_parts(msg.get("content"), show_thinking)
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

    if not out:
        return st.dim("(no readable turns in this session)")
    return "\n".join(out)
