"""Terminal output primitives, shared by every static view.

The Triage Inbox (`render`), the Transcript (`transcript`) and the `audit` view
(`cli`) all need the same four things before they can print anything: whether
colour is on, how wide the terminal is, how to keep a line inside that width,
and how to mark a short hex so a Session Handle can never read as a commit
hash. They used to reach into `render`'s privates for them — a renderer being
imported for its plumbing, which also dragged `render`'s own imports (and its
`cost` view types) into every consumer's import graph.

Textual-free and rich-free, like `toolcalls` and `diffrows`: the Watch has its
own colour system (`theme`) and shares none of this. What it *does* share is
the claim vocabulary below.

## The `~`-claim renderer

An out-of-band Artifact is a claim, never a derived fact, so it is rendered
`~`-marked and hedged when its Session moved on (ADR 0003 § the shared model).
That rule has one implementation here — `claim_hedges` for the vocabulary, plus
the two layouts a claim appears in:

- `claim_line` — the inbox and the `cost` drill-down: one clamped line under a
  Session's title line, hedges in a dim `(…)` suffix;
- `claim_rule` — the Transcript's Brief header and the `audit` header: a labelled
  rule across the view, which has room to spell the hedge out.

Two layouts, one vocabulary: the surfaces differ in how much room they have,
never in whether they hedge. Three spellings of "stale" is how two views start
to disagree about an artifact's trustworthiness — the failure the `~`-marking
exists to prevent.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# The honesty glyph, shared with the `likely` attribution tier: what follows is
# a claim someone made, not something Standup derived (CONTEXT.md → Attribution
# Tier, Session Brief).
CLAIM = "~"

# One hedge, two spellings — picked by the room the layout has, never by the
# surface's own opinion of the artifact (ADR 0003 § the shared model).
STALE_SHORT = "stale"
STALE_LONG = "may be stale — the session continued past it"


class Style:
    def __init__(self, enabled: bool):
        c = lambda code: (lambda s: f"\033[{code}m{s}\033[0m") if enabled else (lambda s: s)
        self.bold = c("1")
        self.dim = c("2")
        self.red = c("31")
        self.green = c("32")
        self.yellow = c("33")
        self.cyan = c("36")


def style() -> Style:
    enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return Style(enabled)


def term_width() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def visible_len(line: str) -> int:
    return len(ANSI_RE.sub("", line))


def clamp(line: str, width: int) -> str:
    """Guarantee the line fits; a clamped line loses styling rather than wrap."""
    if visible_len(line) <= width:
        return line
    plain = ANSI_RE.sub("", line)
    return plain[: max(0, width - 1)].rstrip() + "…"


def session_ref(handle: str, st: Style) -> str:
    """A Session Handle — the only short hex in Standup you can actually type.

    It gets the *stronger* of the two treatments (see `commit_ref`) because it
    outranks a commit hash: it is an address, not a reference. Cyan is the
    colour Standup already gives the human's side of a session.
    """
    return st.cyan(handle)


def agent_tag(agent: str, st: Style) -> str:
    """The word that says which agent a Session belongs to, dim, for the
    Sessions that are not Claude Code's — `codex` after a title. A fact read off
    the log's shape (ADR 0001 § two dialects, one reading), so it is printed
    bare, never `~`-marked; Claude Code's own carry nothing, because a tag on
    every line says less than a tag on the exception."""
    return "" if agent in ("claude", "") else "  " + st.dim(agent)


def commit_ref(short: str, st: Style) -> str:
    """A commit's short hash, marked so it can never read as a Session Handle.

    The Session Handle borrows the git-short-hash idiom deliberately
    (CONTEXT.md), so both are short lowercase hex in a leading column — and a
    bare hash here invites `standup session 6a4eeef`, which addresses nothing.
    Grey is the point: a commit hash recedes behind the handle beside it. The
    `@` sigil carries the distinction on its own when colour cannot (piped
    output, NO_COLOR).
    """
    return st.dim("@" + short)


# ── the `~`-claim renderer ─────────────────────────────────────────────────


def claim_hedges(artifact, *, verbose: bool = False) -> list[str]:
    """The hedges one out-of-band Artifact carries, in reading order.

    Read off the artifact, not off the view: a `status` the author gave it
    (`done` is the terminal one and says nothing, so it is dropped) and
    staleness as the Artifact store stamped it. `verbose` picks the roomier
    spelling of the same hedge — a rule of layout, not of trust.

    Duck-typed on purpose: a Brief has a `status`, an Audit does not, and a
    third kind of Artifact should need no edit here to be hedged correctly.
    """
    hedges = []
    status = getattr(artifact, "status", None)
    if status and status != "done":
        hedges.append(status)
    if getattr(artifact, "stale", False):
        hedges.append(STALE_LONG if verbose else STALE_SHORT)
    return hedges


def claim_line(text: str, st: Style, width: int, *, indent: str = "",
               hedges: list[str] | tuple[str, ...] = ()) -> str:
    """A claim as one clamped line: `~ text  (hedge, hedge)`.

    The inbox's and the `cost` drill-down's shape — it sits under a title line
    it augments and never replaces, so it obeys the never-wrap rule and the
    hedges ride in a dim suffix rather than costing a line of their own.
    """
    suffix = st.dim("  (" + ", ".join(hedges) + ")") if hedges else ""
    return clamp(f"{indent}{st.dim(CLAIM)} {text}{suffix}", width)


def claim_rule(label: str, st: Style, width: int, *,
               hedges: list[str] | tuple[str, ...] = ()) -> str:
    """A claim's header as a labelled rule: `── ~ brief · in-progress ─────`.

    The Transcript's and the `audit` view's shape: the claim is a whole block
    below it, so the mark and its hedges get the width of the view — which is
    what `claim_hedges(verbose=True)` is for.
    """
    text = f"── {CLAIM} {label}"
    if hedges:
        text += " · " + " · ".join(hedges)
    text += " "
    rule = "─" * width
    return st.dim(text + rule[len(text):] if len(text) < width else text)
