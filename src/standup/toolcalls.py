"""How a tool call reads as one line — shared by the Watch and the Transcript.

Two surfaces render a tool call as prose: a **Call** in the Watch's feed and a
tool one-liner in a **Transcript**. They must not drift, so the naming rule and
the argument digest live here, exactly as the diff row shape lives in
`diffrows` for the Watch and the Attributed Diff (ADR 0004 § the stream/UI
boundary).

Plain Python, no textual and no rich: both callers style the result themselves.
"""

from __future__ import annotations

import json
import re

# Tools that change a file are **not** Calls — they are file events carrying a
# diff body, and in a Transcript they read by their path. Imported from
# claude_logs so the two lists cannot disagree.
from .claude_logs import EDIT_TOOLS

# The only tools the Watch stays silent about (ADR 0004 § Calls): a local
# read changes nothing and answers nothing that the Activity State's `reading`
# does not already answer. Everything *not* named here earns a row — including
# a tool that ships next month — which is the same call ACT_VERBS makes when it
# falls an unmapped tool to `acting`.
SILENT_TOOLS = frozenset({
    "Read", "Grep", "Glob", "NotebookRead", "BashOutput", "KillShell",
})

# A claude.ai connector is logged as `mcp__<server>__<tool>`, and for those
# servers `<server>` is a bare UUID with no local mapping to a name — not in
# `~/.claude.json`, not in the JSONL. Printing it names nothing to the reader
# and eats 36 columns of a header that clips, so it is dropped. A server the
# log *can* name is kept, because two servers may expose the same tool
# (`claude-in-chrome·computer` vs `Claude_Browser·computer`).
_MCP_PREFIX = "mcp__"
_UUIDISH = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE)

# Argument keys worth showing bare, in preference order. Not a completeness
# claim and never a source of silence: an input matching none of them falls to
# the compact-JSON digest below, so a tool absent from this tuple loses
# readability, never its argument. (Before the fallback existed, an MCP call
# keyed on `id` rendered as its bare name and said nothing at all.)
_ARG_KEYS = ("file_path", "notebook_path", "command", "path", "pattern",
             "query", "url", "uri", "prompt", "id", "page_id", "expression")


def is_silent(name: str) -> bool:
    """Does this tool pass without a Call row? (Edit tools are file events.)"""
    return name in SILENT_TOOLS or name in EDIT_TOOLS


def display_name(raw: str) -> str:
    """`mcp__5ac0edc4-…__notion-fetch` -> `notion-fetch`;
    `mcp__claude-in-chrome__computer` -> `claude-in-chrome·computer`."""
    if not raw.startswith(_MCP_PREFIX):
        return raw
    rest = raw[len(_MCP_PREFIX):]
    server, sep, tool = rest.partition("__")
    if not sep or not tool:
        return rest or raw
    return tool if _UUIDISH.match(server) else f"{server}·{tool}"


def arg_digest(inp: object, limit: int = 160) -> str:
    """The call's input as one line: a preferred key's value bare, else the
    whole input as compact JSON. Clipped to `limit` — a header clips (ADR 0004
    § fold, don't clip), and the caller can show the untruncated form in a body.
    """
    if not isinstance(inp, dict) or not inp:
        return ""
    for k in _ARG_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return _clip(" ".join(v.split()), limit)
    try:
        text = json.dumps(inp, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(inp)
    return _clip(" ".join(text.split()), limit)


def one_liner(name: str, inp: object, limit: int = 160) -> str:
    """`notion-fetch https://app.notion.com/p/…` — the Transcript's whole line."""
    shown = display_name(name)
    arg = arg_digest(inp, limit)
    return f"{shown} {arg}" if arg else shown


def _clip(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"
