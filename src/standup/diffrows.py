"""The shared diff body-row renderer: one home for the row shape.

Two surfaces render changed code — the **Watch**'s live feed and the
**Attributed Diff** (`standup <repo> diff`). They disagree about almost
everything else (one animates, one paginates; one gutters by session lane, one
by line number) but they must agree, exactly, about what a changed line *looks
like*. That agreement lives here.

Three rules are implemented once, in `sign_rows`, and are the reason this module
exists rather than being duplicated on both sides:

- **the three channels** — the gutter says *what changed* (`+` / `−`), the code
  colors say *what it is* (syntax, foreground-only), and a removed row's
  surface says *what changed* a second time (ADR 0004 § the removed-row field).
  Redundant by construction, so nothing lives in color alone;
- **the wash's bounds** (same section) — it spans the sign column to `width`,
  because a block is only findable if it is a rectangle and a diff's line
  lengths are ragged; and it never reaches the caller's gutter, whose identity
  hue needs clean surface. It has no other background to yield to: the Watch's
  selection lives in the lane, not on the body
  (ADR 0004 § selection in the lane);
- **fold, don't clip** (ADR 0004 § fold, don't clip) — a body line is the
  changed code itself, so it continues onto further rows rather than vanishing
  off the right edge. Continuation rows carry a faint `↳` where the sign would
  be: the sign states a change once, and a fold is the same source line, not
  another one.

This module is Textual-free by construction (rich only), which is what lets the
static view use it without an app running — the boundary of
ADR 0004 § the stream/UI boundary, applied to rendering rather than to the
event stream.
"""

from __future__ import annotations

from pygments.lexers import get_lexer_for_filename
from pygments.lexers.shell import BashLexer
from pygments.util import ClassNotFound
from rich.console import Console
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Span, Text

from .theme import Theme

SYNTAX_THEME = "monokai"   # per-token, foreground-only; a licensed third system
WRAP_ROWS = 40             # rows one wrapped body line may occupy; rest counted

SIGN_ADDED = "+"
SIGN_REMOVED = "−"         # U+2212, not a hyphen: it is a glyph, not a lexeme
SIGN_FOLD = "↳"

_BASH_LEXER = BashLexer()

# rich needs a console to wrap against; this one exists to measure and never
# prints — the fixed width keeps it from touching the real terminal at all
_MEASURE = Console(width=200, quiet=True)


def token_style(style: Style | str, no_color: bool) -> Style | str:
    """Token styles keep foreground only: monokai's page background is
    stripped so the surface and the selection band show through. Under
    NO_COLOR the attributes (bold/italic) remain — never the hues."""
    if not isinstance(style, Style):
        return style
    if style.bgcolor is None and not no_color:
        return style
    return Style(color=None if no_color else style.color, bold=style.bold,
                 italic=style.italic, underline=style.underline)


def styled_lines(code: str, path: str | None, no_color: bool) -> list[Text]:
    """Syntax-highlighted lines of a diff block. Change semantics live in the
    gutter, never in these colors. Plain lines when no lexer fits the path or
    the lex round-trip doesn't reproduce the text exactly."""
    raw = code.split("\n")
    plain = [Text(ln) for ln in raw]
    if not code or not path:
        return plain
    try:
        lexer = get_lexer_for_filename(path)
    except ClassNotFound:
        return plain
    lines = list(Syntax("", lexer, theme=SYNTAX_THEME).highlight(code).split("\n"))
    # highlight() drops trailing blank lines; session Write events end in "\n"
    while len(lines) < len(raw) and raw[len(lines)] == "":
        lines.append(Text())
    if [t.plain for t in lines] != raw:   # animation slices by char count
        return plain
    for t in lines:
        t.style = ""                      # the line-level monokai page wash
        t.spans = [Span(s.start, s.end, token_style(s.style, no_color))
                   for s in t.spans]
    return list(lines)


def wrap_lines(line: Text, width: int) -> list[Text]:
    """One body line as the rows it folds into, styles intact.

    The fold is word-aware and only breaks inside a token when the token itself
    is longer than the row — a path or a long string then continues rather than
    disappearing off the right edge."""
    if width <= 0 or line.cell_len <= width:
        return [line]
    return list(line.wrap(_MEASURE, width, overflow="fold"))


def lexed_command(cmd: str, no_color: bool) -> Text:
    """One bash command, monokai-lexed like any diff body."""
    line = Syntax("", _BASH_LEXER, theme=SYNTAX_THEME).highlight(cmd)
    t = next(iter(line.split("\n")))
    if t.plain != cmd:
        return Text(cmd)
    t.style = ""                          # the line-level monokai page wash
    t.spans = [Span(s.start, s.end, token_style(s.style, no_color))
               for s in t.spans]
    return t


def path_text(t: Theme, path: str) -> Text:
    """dir/ muted · basename bold · .ext in the address family."""
    d, _, base = path.rpartition("/")
    stem, dot, ext = base.rpartition(".")
    if not stem:
        stem, dot, ext = base, "", ""
    out = Text()
    if d:
        out.append(d + "/", style=t.style("muted"))
    out.append(stem, style=t.style("file", bold=True))
    if dot:
        out.append("." + ext, style=t.style("ext"))
    return out


def counts(t: Theme, added: int, removed: int) -> Text:
    out = Text()
    out.append(f"+{added}", style=t.style("added"))
    if removed:
        out.append(f" {SIGN_REMOVED}{removed}", style=t.style("removed"))
    return out


def fold(t: Theme, line: Text, avail: int, wrap: bool = True,
         wrap_rows: int = WRAP_ROWS) -> list[Text]:
    """A body line as the rows it occupies (ADR 0004 § fold, don't clip).

    Wrapping — the default — continues the line onto further rows, because a
    body line you cannot read the end of is the one thing a diff owes you: it is
    the changed code itself. It is bounded at `wrap_rows` with the tail
    *counted* rather than dropped silently (a minified file is one line and
    would otherwise fill the view). Wrap off is one row clipped at the right
    edge — a fixed row count per line, for reading the shape of a change rather
    than its content.
    """
    if not wrap:
        return [line]
    rows = wrap_lines(line, avail)
    if len(rows) <= wrap_rows:
        return rows
    dropped = sum(len(r.plain) for r in rows[wrap_rows:])
    return rows[:wrap_rows] + [Text(f"… +{dropped} chars", style=t.style("faint"))]


def sign_rows(t: Theme, sign: str | None, code: Text, *,
              gutter: Text, cont_gutter: Text | None = None,
              width: int, wrap: bool = True,
              wrap_rows: int = WRAP_ROWS) -> list[Text]:
    """One diff body line as the rows it occupies.

    `gutter` is whatever the caller puts left of the sign column — a session
    lane here, a line number there. Its width sets the sign column, and the
    wash starts there, so the caller's own columns are never washed (ADR 0004 §
    the removed-row field). `cont_gutter` is used on folded rows; it defaults
    to blanks of the same width, which keeps the code column aligned so
    indentation still reads down the page.

    `sign` is a *diff* sign — `"+"`, `"-"`, or None for a context line — never a
    glyph: the renderer owns the glyph, so the removed marker is U+2212 on both
    surfaces or on neither.

    `wrap=False` is one row clipped at the right edge — a fixed row count per
    line, for reading the shape of a change rather than its content. Wrapping
    is bounded at `wrap_rows` with the tail *counted* rather than dropped
    silently: a minified file is one line and would otherwise fill the view.
    """
    removed = sign in ("-", SIGN_REMOVED)
    if sign in ("+", SIGN_ADDED):
        sign_cell = (SIGN_ADDED + " ", t.style("added", bold=True))
    elif removed:
        sign_cell = (SIGN_REMOVED + " ", t.style("removed", bold=True))
    else:
        sign_cell = ("  ", None)
    sign_col = gutter.cell_len
    code_col = sign_col + 2
    blank = Text(" " * sign_col)
    chunks = fold(t, code, width - code_col, wrap, wrap_rows)

    out: list[Text] = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            row = gutter.copy()
            row.append(sign_cell[0], style=sign_cell[1])
        else:
            row = (cont_gutter.copy() if cont_gutter is not None else blank.copy())
            row.append(SIGN_FOLD + " ", style=t.style("faint"))
        row.append_text(chunk)
        if removed:
            bg = t.background("removed_bg")
            if bg is not None:
                row.append(" " * max(0, width - row.cell_len))
                row.stylize(bg, sign_col)
        out.append(row)
    return out
