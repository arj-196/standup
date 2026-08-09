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

# Argument keys worth showing bare, in preference order. The tuple only decides
# what *leads* the digest — it never decides what the digest contains, because
# the remaining keys follow it as compact JSON (ADR 0004 § Calls). Ranking one
# key used to mean discarding its siblings, which cost 1108 of this machine's
# 4615 non-Bash calls their remaining arguments and rendered `notion-update-page`
# as the bare verb `update_content`: the key naming the page was dropped because
# `command` outranks `page_id`.
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
    """The call's input as one line, carrying as much of it as `limit` holds:
    the preferred key's value bare and first, then every remaining key as
    compact JSON. Clipped — a header clips (ADR 0004 § fold, don't clip) — and
    `input_rows` is the untruncated reading the caller shows in a body.

    A single-key input therefore reads exactly as it always did (`WebFetch
    https://…`); a multi-key one no longer loses its siblings to the ranking.
    """
    if not isinstance(inp, dict) or not inp:
        return ""
    lead = next((k for k in _ARG_KEYS
                 if isinstance(inp.get(k), str) and inp[k].strip()), None)
    parts = []
    if lead is not None:
        parts.append(" ".join(inp[lead].split()))
    rest = {k: v for k, v in inp.items() if k != lead}
    if rest:
        parts.append(" ".join(_json(rest).split()))
    return _clip("  ".join(parts), limit)


def input_rows(inp: object) -> list[tuple[str | None, str]]:
    """The call's input **entire**, as `(path, text)` rows a caller lays out.

    Every leaf of the input gets its dotted/indexed path (`content_updates[0]
    .new_str`) and its value; a value's own line breaks become their own rows,
    carrying `None` for the path, so multi-line text reads as text instead of
    as `\\n` escapes. Nothing is clipped and nothing is dropped — the point of
    the body is that it *is* the request (ADR 0004 § Calls).

    Path-per-leaf rather than pretty JSON: a 5KB markdown value is the thing
    being read here, and `json.dumps(indent=2)` leaves it a single escaped
    string. The structure stays recoverable from the paths, so the reshape
    loses nothing — it is presentation, like the diff row shape.
    """
    if not inp:      # a call with no arguments has no body, not a `{}` row
        return []
    rows: list[tuple[str | None, str]] = []
    for path, value in _leaves(inp, ""):
        lines = value.splitlines() or [""]
        rows.append((path, lines[0]))
        rows.extend((None, ln) for ln in lines[1:])
    return rows


def one_liner(name: str, inp: object, limit: int = 160) -> str:
    """`notion-fetch https://app.notion.com/p/…` — the Transcript's whole line."""
    shown = display_name(name)
    arg = arg_digest(inp, limit)
    return f"{shown} {arg}" if arg else shown


def _leaves(value: object, path: str):
    """Walk to every scalar, yielding `(path or None, text)`. An *empty* dict or
    list is itself a leaf (`{}`, `[]`) — a key whose value is empty must still
    appear, or the body would silently answer a question it was not asked."""
    if isinstance(value, dict) and value:
        for k, v in value.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list) and value:
        for i, v in enumerate(value):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield (path or None,
               value if isinstance(value, str) else _json(value))


def _json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _clip(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"
