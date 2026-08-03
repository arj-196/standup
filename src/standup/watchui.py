"""The Watch UI: a Textual app over the watchstream Feed Events (ADR 0008).

This is the only module that imports textual. Layout and color follow the
"Watch Redesigned" specs (pass 1: layout; pass 2: color): prompts are chapter
rules, the clock is a gap gutter, sessions get a lane (digit + bar), and every
color is a role from theme.py with a glyph or attribute carrier that survives
NO_COLOR. Diff bodies are the one saturated register: per-token monokai at
full strength on added and removed lines alike, backfilled or live — the
±gutter alone carries change-semantics, backgrounds are reserved for the
header/status bands and the selection.

The typing animation is presentation-only under a hard staleness bound: the
display may lag the log by at most STALENESS_BOUND seconds — typing speed
compresses (down to instant landing) to honor it. Delight never outranks
truth (CONTEXT.md → Watch).
"""

from __future__ import annotations

import subprocess
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pygments.lexers import get_lexer_for_filename
from pygments.lexers.shell import BashLexer
from pygments.util import ClassNotFound
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Span, Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from .theme import Theme
from .watchstream import FeedEvent, LiveSessionInfo, WatchStream

POLL_INTERVAL = 0.25       # seconds between stream polls
FPS = 30                   # animation frames per second
STALENESS_BOUND = 2.5      # max seconds the display may lag the log
BASE_SPEED = 160.0         # chars/sec at rest — the leisurely default
SPEED_MIN, SPEED_MAX = 40.0, 2000.0
SNAP_SPEED = 8000.0        # beyond this, blocks land instantly (flash, no typing)
HEAD_LINES = 12            # animated head of a large block; rest collapses
REMOVED_LINES = 4          # removed-text lines shown collapsed
COMMIT_FILE_LINES = 6      # commit's file list shown collapsed; rest counted
MAX_EVENTS = 500           # DOM cap; oldest events beyond it are dropped
TRIM_SLACK = 200           # extra events tolerated while reading scrollback
SYNTAX_THEME = "monokai"   # per-token, foreground-only; a licensed third system
GAP_SHOW = 5               # gaps below this many seconds stay quiet
FRESH = 30                 # recency younger than this reads in live-green
STRIP_CELLS = 8            # header activity strip: 8 cells, one minute each
STRIP_BLOCKS = "▁▂▃▄▅▆▇█"
NARROW = 100               # below this width: strips, briefs, hint labels drop
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"      # Activity State spinner frames
SPIN_FPS = 4               # spinner frame rate — wall-clock driven, so an
                           # ad-hoc status refresh can't jitter it; matched to
                           # 1/POLL_INTERVAL so no frame is skipped
SPIN_STALL = "⠿"           # frozen spinner: nothing appended for FRESH seconds
ACTS_SHOWN = 3             # acting sessions named in the footer; rest counted

_MARKS = {
    "file": "✎", "bash": "⏺", "commit": "⚑", "push": "⇧",
    "branch": "⑂", "unattributed": "~", "session": "●",
}

_BASH_LEXER = BashLexer()


def _hm(when: datetime) -> str:
    return when.astimezone().strftime("%H:%M")


def _hms(when: datetime) -> str:
    return when.astimezone().strftime("%H:%M:%S")


def _ago(when: datetime, now: datetime) -> str:
    s = max(0, int((now - when).total_seconds()))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m ago"


def _dur(seconds: float) -> str:
    """A bare compact duration — `_ago` without the "ago", for an Activity State
    that is still running rather than something that already happened."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _one_line(s: str, limit: int = 120) -> str:
    s = " ".join(s.split())
    return s[: limit - 1] + "…" if len(s) > limit else s


def _elapsed(seconds: float, wide: bool) -> str:
    s = int(seconds)
    if wide:
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}m" if s < 5400 else f"{s // 3600}h{s % 3600 // 60:02d}m"


def _token_style(style: Style | str, no_color: bool) -> Style | str:
    """Token styles keep foreground only: monokai's page background is
    stripped so the surface and the selection band show through. Under
    NO_COLOR the attributes (bold/italic) remain — never the hues."""
    if not isinstance(style, Style):
        return style
    if style.bgcolor is None and not no_color:
        return style
    return Style(color=None if no_color else style.color, bold=style.bold,
                 italic=style.italic, underline=style.underline)


def _styled_lines(code: str, path: str | None, no_color: bool) -> list[Text]:
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
        t.spans = [Span(s.start, s.end, _token_style(s.style, no_color))
                   for s in t.spans]
    return list(lines)


def _lexed_command(cmd: str, no_color: bool) -> Text:
    """One bash command, monokai-lexed like any diff body."""
    line = Syntax("", _BASH_LEXER, theme=SYNTAX_THEME).highlight(cmd)
    t = next(iter(line.split("\n")))
    if t.plain != cmd:
        return Text(cmd)
    t.style = ""                          # the line-level monokai page wash
    t.spans = [Span(s.start, s.end, _token_style(s.style, no_color))
               for s in t.spans]
    return t


class EventWidget(Static):
    """One Feed Event: a header line behind the gap gutter and session lane,
    plus (for files, commits, prompts, bash) an optional body. File events own
    a typed-animation body; commits expand in two steps — header → file list →
    every file's diff."""

    def __init__(self, event: FeedEvent, theme: Theme, num: int) -> None:
        super().__init__()
        self.event = event
        self.t = theme
        self.num = num                    # stable session lane number, 0 = none
        self.expand_level = 0             # files/prompts/bash: 0|1 · commits: 0|1|2
        self.bash_ok: bool | None = None
        self.gap_seconds: float | None = None   # set by the app (visible-chain gap)
        self.lane_head = True                   # digit vs bar — set by the app
        # animation state: how many chars of the (collapsed) body are visible
        self.head_text, self.hidden_lines = self._split_body()
        no_color = theme.depth == "none"
        if event.kind == "file":
            self.added_lines = (_styled_lines(event.added, event.path, no_color)
                                if event.added else [])
            self.removed_lines = (_styled_lines(event.removed, event.path, no_color)
                                  if event.removed else [])
        else:
            self.added_lines, self.removed_lines = [], []
        self.head_lines = self.added_lines[:HEAD_LINES]
        self.total_chars = len(self.head_text)
        self.shown_chars = self.total_chars  # instant by default; app may reset to 0
        # commit diffs are highlighted lazily: a commit can carry many files, and
        # lexing them all at mount time would stall the feed for a body nobody
        # has asked to see yet
        self._commit_lines: list[tuple[str, str, list[Text], list[Text]]] | None = None
        # backfill is *not* faded: replayed code must read as clearly as live
        # code. The boundary rule and the missing animation carry the
        # distinction instead of a wash that muddies every monokai token.
        if event.kind == "prompt":
            self.add_class("chapter")     # 1 blank row above the rule

    @property
    def expanded(self) -> bool:
        return self.expand_level > 0

    # -- body construction ----------------------------------------------------

    def _split_body(self) -> tuple[str, int]:
        """(the animated head of the added text, count of collapsed lines)."""
        if self.event.kind != "file" or not self.event.added:
            return "", 0
        lines = self.event.added.split("\n")
        head = lines[:HEAD_LINES]
        return "\n".join(head), max(0, len(lines) - HEAD_LINES)

    def commit_lines(self) -> list[tuple[str, str, list[Text], list[Text]]]:
        """(path, change, added lines, removed lines) per file of a commit,
        syntax-highlighted on first use and cached thereafter."""
        if self._commit_lines is None:
            no_color = self.t.depth == "none"
            self._commit_lines = [
                (f.path, f.change,
                 _styled_lines(f.added, f.path, no_color) if f.added else [],
                 _styled_lines(f.removed, f.path, no_color) if f.removed else [])
                for f in self.event.files
            ]
        return self._commit_lines

    # -- the left columns -------------------------------------------------------

    def _gap_cell(self) -> Text:
        """Cols 1–6: the gap gutter. Quiet below GAP_SHOW; second-gaps faint,
        minute-gaps muted — the least-read text is the least-visible text.
        Chapters leave it blank: their rule carries the absolute time."""
        t = self.t
        if (self.event.kind == "prompt" or self.gap_seconds is None
                or self.gap_seconds < GAP_SHOW):
            return Text(" " * 6)
        s = int(self.gap_seconds)
        if s < 60:
            return Text(f"+{s}s".rjust(5) + " ", style=t.style("faint"))
        m = s // 60
        label = f"+{m}m" if m < 60 else f"+{m // 60}h"
        return Text(label.rjust(5) + " ", style=t.style("muted"))

    def _lane_cell(self, head: bool) -> Text:
        """Cols 7–8: the session lane. Digit at run start, then a bar; git-only
        rows get ·· — the digit, not the hue, is the NO_COLOR carrier."""
        t = self.t
        if self.event.session_id is None:
            return Text("··" if head else "· ", style=t.style("faint"))
        cell = Text()
        if head and 1 <= self.num <= 9:
            cell.append(str(self.num), style=t.session(self.num, bold=True))
        else:
            cell.append(" ")
        cell.append("▏", style=t.session(self.num))
        return cell

    def _prefix(self, first: bool) -> Text:
        p = self._gap_cell() if first else Text(" " * 6)
        p.append_text(self._lane_cell(head=first and self.lane_head))
        p.append(" ")
        return p

    # -- header content -----------------------------------------------------------

    def _path_text(self, path: str) -> Text:
        """dir/ muted · basename bold · .ext in the address family."""
        t = self.t
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

    def _counts(self, added: int, removed: int) -> Text:
        t, out = self.t, Text()
        out.append(f"+{added}", style=t.style("added"))
        if removed:
            out.append(f" −{removed}", style=t.style("removed"))
        return out

    def _header(self) -> tuple[Text, Text | None]:
        """(header content from the mark column on, right-edge disclosure)."""
        e, t = self.event, self.t
        out = Text()
        right: Text | None = None

        if e.kind == "file":
            plus = len(e.added.split("\n")) if e.added else 0
            minus = len(e.removed.split("\n")) if e.removed else 0
            if e.session_id is None:      # git is the only witness — a claim gap
                out.append("~", style=t.style("claim", bold=True))
            else:
                out.append("✎", style=t.style("file"))
            out.append("  ")
            out.append_text(self._path_text(e.path or "?"))
            out.append(f"  {e.change}  ", style=t.style("muted"))
            out.append_text(self._counts(plus, minus))
            if e.session_id is None:
                out.append("  ~unattributed", style=t.style("claim"))
            body = plus + minus
            if self.expanded:
                right = Text("▾ ", style=t.style("primary"))
            elif body > (len(self.removed_lines[:REMOVED_LINES]) + len(self.head_lines)):
                right = Text(f"▸ {body} lines ", style=t.style("faint"))
        elif e.kind == "bash":
            out.append("⏺", style=t.style("muted"))
            out.append("  $ ", style=t.style("faint"))
            out.append_text(_lexed_command(_one_line(e.command or "", 100),
                                           t.depth == "none"))
            if self.bash_ok is True:
                out.append("  ✓", style=t.style("added", bold=True))
            elif self.bash_ok is False:
                out.append("  ✗", style=t.style("removed", bold=True))
        elif e.kind == "commit":
            out.append("⚑", style=t.style("git", bold=True))
            out.append("  ")
            out.append("commit ", style=t.style("git"))
            out.append(f"@{e.sha}", style=t.style("reference"))
            out.append("  ")
            out.append(_one_line(e.message, 80), style=t.style("primary"))
            if e.files:   # only when a diff was actually read
                plus = sum(len(f.added.split("\n")) for f in e.files if f.added)
                minus = sum(len(f.removed.split("\n")) for f in e.files if f.removed)
                n = len(e.files)
                out.append(f"  {n} file{'s' if n != 1 else ''} ",
                           style=t.style("muted"))
                out.append_text(self._counts(plus, minus))
                n_txt = f"{n} file{'s' if n != 1 else ''}"
                if self.expand_level == 0:
                    right = Text(f"▸ {n_txt} ", style=t.style("faint"))
                elif self.expand_level == 1:
                    right = Text(f"▾ {n_txt} ", style=t.style("primary"))
                else:
                    right = Text("▾ ", style=t.style("primary"))
        elif e.kind == "push":
            out.append("⇧", style=t.style("git", bold=True))
            out.append("  ")
            out.append("push ", style=t.style("git"))
            branch, _, rest = e.message.partition("  ")
            out.append(branch, style=t.style("primary"))
            if rest:
                out.append(f"  {rest}", style=t.style("muted"))
            if e.sha:
                out.append(f"  @{e.sha}", style=t.style("reference"))
        elif e.kind == "branch":
            out.append("⑂", style=t.style("chapter", bold=True))
            out.append("  ")
            out.append("branch ", style=t.style("chapter"))
            old, arrow, new = e.message.partition(" → ")
            out.append(old, style=t.style("primary"))
            if arrow:
                out.append(" → ", style=t.style("muted"))
                out.append(new, style=t.style("primary"))
        elif e.kind == "unattributed":
            out.append("~", style=t.style("claim", bold=True))
            out.append("  ")
            out.append(_one_line(e.message, 80), style=t.style("primary"))
            out.append("  ~unattributed", style=t.style("claim"))
            out.append("  (git cannot diff)", style=t.style("faint"))
        elif e.kind == "session":
            out.append("●", style=t.style("address"))
            out.append("  ")
            out.append("session ", style=t.style("muted"))
            out.append(e.handle or "?", style=t.style("address"))
            out.append(" appeared", style=t.style("muted"))
            if e.title:
                out.append(" — ", style=t.style("muted"))
                out.append(_one_line(e.title, 60), style=t.style("primary"))
        else:
            out.append("·  ", style=t.style("muted"))
            out.append(_one_line(e.message, 90), style=t.style("primary"))
        return out, right

    def _chapter_rule(self, width: int) -> Text:
        """── you: prompt ─ handle ────…──── HH:MM ── across the full row."""
        e, t = self.event, self.t
        line = self._prefix(first=True)
        line.append("── ", style=t.style("chapter", bold=True))
        line.append("you: ", style=t.style("chapter"))
        clock = _hm(e.when)
        # room = width − prefix − fixed glyphs; keep ≥3 fill dashes
        fixed = line.cell_len + len(" ─ ") + 8 + 1 + len(clock) + len(" ──") + 3
        text = _one_line(e.message, max(10, width - fixed))
        line.append(text, style=t.style("primary", bold=True))
        line.append(" ─ ", style=t.style("faint"))
        line.append(e.handle or "········", style=t.style("address"))
        fill = max(3, width - line.cell_len - len(clock) - len(" ──") - 2)
        line.append(" " + "─" * fill + " ", style=t.style("faint"))
        line.append(clock, style=t.style("muted"))
        line.append(" ──", style=t.style("faint"))
        return line

    # -- rendering --------------------------------------------------------------

    def _rline(self, left: Text, right: Text | None, width: int) -> Text:
        if right is not None:
            pad = width - left.cell_len - right.cell_len
            if pad > 0:
                left.append(" " * pad)
            else:
                left.append("  ")
            left.append_text(right)
        return left

    def _sign_row(self, out: Text, sign: str | None, code: Text) -> None:
        """One body line: cols 15–16 carry the ± sign, code starts at col 17.
        The gutter says what changed; the code colors say what it is."""
        t = self.t
        out.append("\n")
        out.append_text(self._prefix(first=False))
        if sign == "+":
            out.append("     + ", style=t.style("added", bold=True))
        elif sign == "-":
            out.append("     − ", style=t.style("removed", bold=True))
        else:
            out.append("       ")
        out.append_text(code)

    def _more_row(self, out: Text, n: int, noun: str = "lines") -> None:
        out.append("\n")
        out.append_text(self._prefix(first=False))
        out.append(f"     … ▸ {n} more {noun}", style=self.t.style("faint"))

    def _commit_body(self, out: Text) -> Text:
        """Two shallow levels: the file list (level 1), then every file's diff
        (level 2) — same gutter and same highlighting as a live file event; the
        only difference is that git, not a session, supplied it."""
        t, blocks = self.t, self.event.files
        if self.expand_level == 1:
            pad = max(len(f.path) for f in blocks[:COMMIT_FILE_LINES]) + 2
            for f in blocks[:COMMIT_FILE_LINES]:
                plus = len(f.added.split("\n")) if f.added else 0
                minus = len(f.removed.split("\n")) if f.removed else 0
                out.append("\n")
                out.append_text(self._prefix(first=False))
                out.append("      ")
                out.append_text(self._path_text(f.path))
                out.append(" " * max(1, pad - len(f.path)))
                out.append(f"{f.change}  ", style=t.style("muted"))
                out.append_text(self._counts(plus, minus))
            if len(blocks) > COMMIT_FILE_LINES:
                self._more_row(out, len(blocks) - COMMIT_FILE_LINES, "files")
            return out
        for path, change, added, removed in self.commit_lines():
            out.append("\n")
            out.append_text(self._prefix(first=False))
            out.append("      ")
            out.append_text(self._path_text(path))
            out.append(f"  {change}", style=t.style("muted"))
            for ln in removed:
                self._sign_row(out, "-", ln)
            for ln in added:
                self._sign_row(out, "+", ln)
        return out

    def render_event(self, stat_mode: bool, width: int) -> Text:
        e, t = self.event, self.t
        if e.kind == "prompt":
            out = self._chapter_rule(width)
            out.no_wrap = True
            # expandable when the rule visibly truncated it (same budget as
            # _chapter_rule's) or the original had line structure to show
            long = ("\n" in e.message.strip()
                    or len(" ".join(e.message.split())) > max(10, width - 45))
            if self.expanded and long and not stat_mode:
                for raw in e.message.splitlines():
                    out.append("\n")
                    out.append_text(self._prefix(first=False))
                    out.append("   ")
                    out.append(raw, style=t.style("primary"))
            return out

        head, right = self._header()
        if stat_mode:
            right = (Text("▸ ", style=t.style("faint"))
                     if (e.kind == "file" and (e.added or e.removed))
                     or (e.kind == "commit" and e.files) else None)
        line = self._prefix(first=True)
        line.append_text(head)
        out = self._rline(line, right, width)
        out.no_wrap = True
        if stat_mode or e.kind not in ("file", "bash", "commit"):
            return out
        if e.kind == "commit":
            return self._commit_body(out) if self.expanded and e.files else out
        if e.kind == "bash":
            if self.expanded and e.command and len(_one_line(e.command)) < len(e.command):
                for raw in e.command.splitlines():
                    self._sign_row(out, None, _lexed_command(raw, t.depth == "none"))
            return out

        # file event body: the gutter says what changed, the colors say what it is
        if self.expanded:
            for ln in self.removed_lines:
                self._sign_row(out, "-", ln)
            for ln in self.added_lines:
                self._sign_row(out, "+", ln)
            return out

        if self.removed_lines:
            for ln in self.removed_lines[:REMOVED_LINES]:
                self._sign_row(out, "-", ln)
            if len(self.removed_lines) > REMOVED_LINES:
                self._more_row(out, len(self.removed_lines) - REMOVED_LINES)
        shown = int(self.shown_chars)
        animating = self.shown_chars < self.total_chars
        for ln in self.head_lines:
            if shown <= 0:
                break
            self._sign_row(out, "+",
                           ln if shown >= len(ln.plain) else ln.divide([shown])[0])
            shown -= len(ln.plain) + 1   # +1 spends the newline
        if animating:
            out.append("▌", style=t.style("added", bold=True) + Style(blink=True))
        elif self.hidden_lines:
            self._more_row(out, self.hidden_lines)
        return out

    def refresh_event(self) -> None:
        app = self.app
        self.update(self.render_event(getattr(app, "stat_mode", False),
                                      getattr(app, "content_width", lambda: 98)()))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        app = self.app
        if isinstance(app, WatchApp):
            app.click_select(self)


class VitalsWidget(Static):
    """The raised header band. Clicking a session row toggles its filter."""

    def on_click(self, event: events.Click) -> None:
        event.stop()
        app = self.app
        if isinstance(app, WatchApp):
            app.click_vitals_row(event.y)


class WatchApp(App):
    """`standup watch` — see CONTEXT.md (Watch, Feed Event, Live Session)."""

    # real CSS is built per-theme in _css(); this default only anchors the names
    CSS = ""

    BINDINGS = [
        Binding("q", "quit", "quit", priority=True),
        Binding("escape,0", "filter_all", "all sessions", show=False, priority=True),
        Binding("tab", "cycle_filter", "next session", show=False, priority=True),
        Binding("enter", "toggle_expand", "expand", show=False, priority=True),
        Binding("d", "toggle_stat", "stat", priority=True),
        Binding("s", "open_show", "show transcript", priority=True),
        Binding("plus,equals_sign", "faster", "faster", show=False, priority=True),
        Binding("minus", "slower", "slower", show=False, priority=True),
        Binding("up,k", "select_prev", "scrollback", show=False, priority=True),
        Binding("down,j", "select_next", show=False, priority=True),
        Binding("pageup", "page_up", show=False, priority=True),
        Binding("pagedown", "page_down", show=False, priority=True),
        Binding("end,G", "go_live", "live", show=False, priority=True),
        Binding("home,g", "go_top", "top", show=False, priority=True),
        Binding("left_square_bracket", "chapter_prev", "prev chapter",
                show=False, priority=True),
        Binding("right_square_bracket", "chapter_next", "next chapter",
                show=False, priority=True),
        Binding("question_mark", "toggle_keymap", "keys", show=False, priority=True),
    ] + [Binding(str(i), f"filter_n({i})", show=False, priority=True) for i in range(1, 10)]

    def __init__(self, stream: WatchStream, theme_: Theme) -> None:
        super().__init__()
        self.stream = stream
        self.t = theme_
        self.stat_mode = False
        self.speed = BASE_SPEED
        self.filter_sid: str | None = None
        self.anim_queue: list[EventWidget] = []
        self.bash_widgets: dict[str, EventWidget] = {}
        self.selected: EventWidget | None = None
        self._t0 = time.monotonic()
        self._last_event_at: datetime | None = None
        self._new_below = 0                      # events landed while scrolled
        self._tail_meta: tuple[datetime, str | None] | None = None
        self._activity: dict[str, deque[datetime]] = {}
        self._vitals_rows: list[str] = []        # session ids by header row
        self._backfill_events = stream.start()

    # -- layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield VitalsWidget(id="vitals")
        yield Static(id="rule")
        yield VerticalScroll(id="feed")
        yield Static(id="keymap")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#keymap", Static).display = False
        for ev in self._backfill_events:
            self._add_event(ev, animate=False)
        if self._backfill_events:
            self._mount_boundary(len(self._backfill_events))
        self._refresh_vitals()
        self._refresh_status()
        self.set_interval(POLL_INTERVAL, self._poll)
        self.set_interval(1 / FPS, self._tick_animation)
        self.set_interval(1.0, self._tick_second)
        # the feed follows the bottom while you're there; any scroll up releases
        # it, and returning to the bottom (or G) re-engages it — nothing pauses
        self.query_one("#feed", VerticalScroll).anchor()

    def content_width(self) -> int:
        w = self.size.width - 2                      # feed padding
        if not self._following():
            w -= 1                                   # the scrolled-only scrollbar
        return max(40, w)

    def on_resize(self, event: events.Resize) -> None:
        self._refresh_vitals()
        self._refresh_status()
        for w in self.query(EventWidget):
            w.refresh_event()

    def _mount_boundary(self, count: int) -> None:
        """The ┈ rule that names both sides of launch: replay above, live below."""
        t, width = self.t, self.content_width()
        clock = _hms(datetime.now(timezone.utc))
        line = Text(no_wrap=True)
        line.append("┈┈ ", style=t.style("faint"))
        line.append("backfill", style=t.style("claim"))
        line.append(f" · {count} events replayed from log ", style=t.style("muted"))
        fill = max(3, width - line.cell_len - len(clock) - len(" ┈ live below ┈┈") - 2)
        line.append("┈" * fill + " ", style=t.style("faint"))
        line.append(clock, style=t.style("muted"))
        line.append(" ┈ ", style=t.style("faint"))
        line.append("live below", style=t.style("live", bold=True))
        line.append(" ┈┈", style=t.style("faint"))
        boundary = Static(line, id="boundary")
        self.query_one("#feed", VerticalScroll).mount(boundary)

    # -- the feed ---------------------------------------------------------------

    def _poll(self) -> None:
        for ev in self.stream.poll():
            self._add_event(ev, animate=True)
        self._refresh_status()

    def _visible_to(self, ev: FeedEvent) -> bool:
        if self.filter_sid is None:
            return True
        # the filter hides other sessions' work but never repo facts: commits,
        # pushes, branch switches, and Unattributed Changes stay — the feed
        # must not hide dirt (ground truth is never filtered away)
        if ev.kind in ("commit", "push", "branch", "unattributed"):
            return True
        return ev.session_id is None or ev.session_id == self.filter_sid

    def _add_event(self, ev: FeedEvent, animate: bool) -> None:
        if ev.kind == "bash_result":
            w = self.bash_widgets.pop(ev.tool_id or "", None)
            if w is not None:
                w.bash_ok = ev.ok
                w.refresh_event()
            return
        self._last_event_at = ev.when
        if ev.session_id:
            self._activity.setdefault(ev.session_id, deque()).append(ev.when)
        feed = self.query_one("#feed", VerticalScroll)
        w = EventWidget(ev, self.t, self.stream.session_num(ev.session_id or ""))
        if ev.kind == "bash" and ev.tool_id:
            self.bash_widgets[ev.tool_id] = w
        if not self._visible_to(ev):
            w.display = False
        else:
            self._place_after_tail(w)
        if animate and not self._following():
            self._new_below += 1
        if animate and ev.kind == "file" and w.total_chars > 0 and not ev.backfill:
            w.shown_chars = 0
            self.anim_queue.append(w)
        feed.mount(w)
        w.refresh_event()
        self._trim(feed)

    def _place_after_tail(self, w: EventWidget) -> None:
        """Gap gutter and lane run-start, relative to the previous *visible*
        event — the chain the reader actually sees."""
        prev = self._tail_meta
        w.gap_seconds = (w.event.when - prev[0]).total_seconds() if prev else None
        w.lane_head = prev is None or prev[1] != w.event.session_id
        self._tail_meta = (w.event.when, w.event.session_id)

    def _relayout(self) -> None:
        """Recompute every visible event's gap and lane run-start (after a
        filter change reshapes the visible chain)."""
        self._tail_meta = None
        for w in self.query(EventWidget):
            if not w.display:
                continue
            self._place_after_tail(w)
            w.refresh_event()

    def _following(self) -> bool:
        """Is the feed following live (anchored to the bottom)?"""
        feed = self.query_one("#feed", VerticalScroll)
        return not getattr(feed, "_anchor_released", False)

    def _trim(self, feed: VerticalScroll) -> None:
        """Cap the DOM at MAX_EVENTS. While the reader is scrolled back the trim
        is deferred (dropping events above them would shift the view) up to
        TRIM_SLACK extra events — a hard bound so a busy feed can't grow
        without limit."""
        children = list(feed.children)
        cap = MAX_EVENTS if self._following() else MAX_EVENTS + TRIM_SLACK
        if len(children) <= cap:
            return
        for old in children[: len(children) - MAX_EVENTS]:
            if isinstance(old, EventWidget) and old.event.tool_id:
                self.bash_widgets.pop(old.event.tool_id, None)
            if old is self.selected:
                self._select(None)
            old.remove()

    # -- the animation engine -----------------------------------------------------

    def _tick_animation(self) -> None:
        # NB: not named `_animate` — that would shadow textual's BoundAnimator
        if not self.anim_queue:
            return
        remaining = sum(w.total_chars - w.shown_chars for w in self.anim_queue)
        speed = max(self.speed, remaining / STALENESS_BOUND)
        if speed >= SNAP_SPEED:
            for w in self.anim_queue:       # burst: land everything instantly
                w.shown_chars = w.total_chars
                w.refresh_event()
            self.anim_queue.clear()
        else:
            budget = speed / FPS
            while budget > 0 and self.anim_queue:
                head = self.anim_queue[0]
                need = head.total_chars - head.shown_chars
                step = min(need, budget)
                head.shown_chars += step
                budget -= step
                head.refresh_event()
                if head.shown_chars >= head.total_chars:
                    self.anim_queue.pop(0)

    # -- header + status bar ------------------------------------------------------

    def _tick_second(self) -> None:
        self._refresh_vitals()
        self._refresh_status()

    def _strip(self, sid: str, now: datetime) -> str:
        """8 cells, one minute each, oldest first: events per minute as pure
        block heights — optional garnish, dropped on narrow screens."""
        q = self._activity.get(sid)
        if not q:
            return STRIP_BLOCKS[0] * STRIP_CELLS
        while q and (now - q[0]) > timedelta(minutes=STRIP_CELLS):
            q.popleft()
        cells = []
        for i in range(STRIP_CELLS - 1, -1, -1):
            lo, hi = now - timedelta(minutes=i + 1), now - timedelta(minutes=i)
            n = sum(1 for w in q if lo < w <= hi)
            cells.append(STRIP_BLOCKS[min(len(STRIP_BLOCKS) - 1, n)])
        return "".join(cells)

    def _refresh_vitals(self) -> None:
        t, v = self.t, self.stream.vitals()
        now = datetime.now(timezone.utc)
        width = max(40, self.size.width - 2)
        wide = width >= NARROW - 2
        rows: list[Text] = []
        self._vitals_rows = []

        top = Text(no_wrap=True)
        top.append("standup", style=t.style("muted"))
        top.append(" · ", style=t.style("faint"))
        top.append(v.repo, style=t.style("primary", bold=True))
        top.append("   ⑂ ", style=t.style("faint"))
        top.append(v.branch, style=t.style("primary"))
        top.append("   ✎ ", style=t.style("faint"))
        top.append(str(v.dirty),
                   style=t.style("primary", bold=True) if v.dirty else t.style("muted"))
        top.append(" dirty", style=t.style("muted"))
        clock = f"watch {_elapsed(time.monotonic() - self._t0, wide)}"
        pad = width - top.cell_len - len(clock)
        top.append(" " * max(2, pad))
        top.append(clock, style=t.style("faint"))
        rows.append(top)

        if not v.live:
            quiet = Text(no_wrap=True)
            quiet.append("no live session", style=t.style("primary", bold=True))
            if v.last:
                quiet.append(" · last log append ", style=t.style("muted"))
                quiet.append(_ago(v.last.last_append, now), style=t.style("primary"))
                quiet.append("  (", style=t.style("faint"))
                quiet.append(v.last.handle, style=t.style("address"))
                if v.last.title:
                    quiet.append("  " + _one_line(v.last.title, 40),
                                 style=t.style("muted"))
                quiet.append(")", style=t.style("faint"))
            rows.append(quiet)

        shown = v.live[:3]
        if len(v.live) > 3:
            # the 3 most recent, kept in stable-number order
            shown = sorted(sorted(v.live, key=lambda l: l.last_append,
                                  reverse=True)[:3], key=lambda l: l.num)
        for ls in shown:
            self._vitals_rows.append(ls.session_id)
            row = Text(no_wrap=True)
            row.append(f"[{ls.num}]", style=t.session(ls.num, bold=True))
            row.append("▸" if self.filter_sid == ls.session_id else " ",
                       style=t.style("primary", bold=True))
            row.append(" ")
            row.append(ls.handle, style=t.style("address"))
            row.append("  ")
            row.append(_one_line(ls.title, 50), style=t.style("primary", bold=True))
            ago = _ago(ls.last_append, now)
            recency = t.style("live") if (now - ls.last_append).total_seconds() < FRESH \
                else t.style("muted")
            tail_w = (STRIP_CELLS + 2 if wide else 0) + len(ago)
            if ls.objective and wide:
                # the Brief is garnish beside the strip and recency: it gets
                # whatever room is left, never the other way around
                avail = width - row.cell_len - tail_w - 5
                if avail >= 12:
                    row.append("  ~" + _one_line(ls.objective, min(60, avail)),
                               style=t.style("claim"))
            row.append(" " * max(2, width - row.cell_len - tail_w))
            if wide:
                row.append(self._strip(ls.session_id, now), style=t.style("faint"))
                row.append("  ")
            row.append(ago, style=recency)
            rows.append(row)
        if len(v.live) > 3:
            rows.append(Text(f" … +{len(v.live) - 3} more", no_wrap=True,
                             style=t.style("faint")))

        out = Text("\n", no_wrap=True).join(rows)
        self.query_one("#vitals", VitalsWidget).update(out)
        self.query_one("#rule", Static).update(
            Text("─" * width, style=t.style("faint"), no_wrap=True))

    def _hints(self, wide: bool) -> list[tuple[str, str]]:
        if not wide:
            return [("?", "keys")]
        if not self._following():
            return [("G", "live"), ("⏎", "expand"), ("?", "keys")]
        if self.filter_sid:
            return [("Esc", "clear"), ("Tab", "next"), ("s", "show"), ("?", "keys")]
        if self.stat_mode:
            return [("d", "bodies"), ("⏎", "expand"), ("1-9", "filter"), ("?", "keys")]
        if self.selected is not None and self.selected.expanded:
            return [("⏎", "collapse"), ("s", "show"), ("G", "live"), ("?", "keys")]
        if self.anim_queue:
            return [("⏎", "expand"), ("d", "stat"), ("+ −", "speed"), ("?", "keys")]
        return [("⏎", "expand"), ("d", "stat"), ("[ ]", "chapter"), ("?", "keys")]

    def _activity_state(self, live: list[LiveSessionInfo], now: datetime,
                        budget: int) -> Text | None:
        """Every acting Live Session's Activity State, in lane order.

        A settled session contributes *nothing* — the footer only grows when
        work is actually happening, which is why every acting session can be
        named rather than one being picked over the others.

        The spinner turns only while the log is being appended; past FRESH
        seconds it freezes to a static glyph in muted colour. Motion therefore
        maps to arriving data, never to a state word, so a session that was
        killed mid-turn stops pretending to work instead of spinning forever
        (CONTEXT.md → Activity State; ADR 0011).
        """
        t = self.t
        acting = [ls for ls in live if ls.activity]
        if not acting or budget < 12:
            return None
        frame = SPIN[int(time.monotonic() * SPIN_FPS) % len(SPIN)]
        fresh_of = {ls.session_id: (now - ls.last_append).total_seconds() < FRESH
                    for ls in acting}
        out = Text(no_wrap=True)
        for ls in acting[:ACTS_SHOWN]:
            if out.cell_len:
                out.append("  ·  ", style=t.style("faint"))
            fresh = fresh_of[ls.session_id]
            out.append(f"[{ls.num}]", style=t.session(ls.num, bold=True))
            out.append(" ")
            out.append(frame if fresh else SPIN_STALL,
                       style=t.style("live" if fresh else "muted"))
            out.append(" ")
            out.append(ls.activity.verb, style=t.style("primary", bold=True))
            out.append(" " + _dur((now - ls.activity.since).total_seconds()),
                       style=t.style("muted"))
        if len(acting) > ACTS_SHOWN:
            out.append(f"  +{len(acting) - ACTS_SHOWN}", style=t.style("faint"))
        if out.cell_len <= budget:
            return out
        # no room for the verbs: the count alone still answers the one question
        short = Text(no_wrap=True)
        short.append(frame if any(fresh_of.values()) else SPIN_STALL,
                     style=t.style("live" if any(fresh_of.values()) else "muted"))
        short.append(f" {len(acting)} acting", style=t.style("primary", bold=True))
        return short if short.cell_len <= budget else None

    def _refresh_status(self) -> None:
        t = self.t
        feed = self.query_one("#feed", VerticalScroll)
        following = self._following()
        feed.styles.scrollbar_size_vertical = 0 if following else 1
        if following:
            self._new_below = 0
        width = max(40, self.size.width - 2)
        wide = width >= 90
        now = datetime.now(timezone.utc)
        bar = Text(no_wrap=True)

        if t.paints_backgrounds:
            live_chip = Style(color=t.hex("surface"), bgcolor=t.hex("live"), bold=True)
            back_chip = Style(color=t.hex("surface"), bgcolor=t.hex("claim"), bold=True)
        else:
            live_chip = back_chip = Style(reverse=True, bold=True)
        hints = Text()
        for i, (key, label) in enumerate(self._hints(wide)):
            if i:
                hints.append(" · ", style=t.style("faint"))
            hints.append(key, style=t.style("primary", bold=True))
            hints.append(f" {label}", style=t.style("muted"))

        # the modes, built before the Activity State so it can be given a real
        # width budget rather than pushed off the end by them
        tail = Text(no_wrap=True)
        if self.filter_sid:
            num = self.stream.session_num(self.filter_sid)
            tail.append("  ·  ", style=t.style("faint"))
            tail.append("filter ", style=t.style("muted"))
            tail.append(f"[{num}] ", style=t.session(num, bold=True))
            tail.append(self.filter_sid[:8], style=t.style("address"))
        if self.stat_mode:
            tail.append("  ·  ", style=t.style("faint"))
            tail.append("stat", style=t.style("primary", bold=True))
            tail.append(" — headers only", style=t.style("muted"))
        if self.anim_queue or self.speed != BASE_SPEED:
            tail.append("  ·  ", style=t.style("faint"))
            tail.append(f"{int(self.speed)} c/s", style=t.style("muted"))

        v = self.stream.vitals()
        quiet = False
        if following:
            bar.append(" ● LIVE ", style=live_chip)
            bar.append("  ")
            if not v.live:
                quiet = True
                bar.append("git only", style=t.style("primary"))
                bar.append(" · poll 2s · last change ", style=t.style("muted"))
                bar.append(_ago(self._last_event_at, now)
                           if self._last_event_at else "—", style=t.style("primary"))
        else:
            bar.append(" ▲ SCROLLED ", style=back_chip)
            bar.append("  ")
            behind = max(0, int(feed.max_scroll_y - feed.scroll_y))
            bar.append(f"−{behind} rows ", style=t.style("primary"))
            bar.append("· ", style=t.style("faint"))
            bar.append(f"{self._new_below} new below",
                       style=t.style("primary", bold=True))

        # Activity State sits as far left as the band allows — it is the one
        # thing here you look for without reading. It shows while scrolled back
        # too: that is precisely when you have stopped watching the feed and
        # still need to know whether the agent is done.
        act = None if quiet else self._activity_state(
            v.live, now, width - bar.cell_len - tail.cell_len - hints.cell_len - 4)
        if act is not None:
            if not following:
                bar.append("  ·  ", style=t.style("faint"))
            bar.append_text(act)
        elif following and v.live and self._last_event_at is not None:
            # nothing is acting: the settled sessions say nothing at all, so the
            # band falls back to the plain recency it showed before
            s = max(0, int((now - self._last_event_at).total_seconds()))
            bar.append(f"{s}s since last event", style=t.style("muted"))
        bar.append_text(tail)

        pad = width - bar.cell_len - hints.cell_len - 1
        bar.append(" " * max(2, pad))
        bar.append_text(hints)
        bar.append(" ")
        self.query_one("#status", Static).update(bar)

    # -- controls -----------------------------------------------------------------

    def action_go_live(self) -> None:
        # one key back to now: snap animation debt, drop the selection,
        # re-anchor at the bottom, and trim any scrollback overflow
        for w in self.anim_queue:
            w.shown_chars = w.total_chars
            w.refresh_event()
        self.anim_queue.clear()
        self._select(None)
        feed = self.query_one("#feed", VerticalScroll)
        feed.scroll_end(animate=False)   # also re-engages the anchor
        self._trim(feed)
        self._refresh_status()

    def action_go_top(self) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        feed.release_anchor()
        feed.scroll_home(animate=False)
        self._refresh_status()

    def action_filter_all(self) -> None:
        self.filter_sid = None
        self._apply_filter()

    def action_filter_n(self, n: int) -> None:
        v = self.stream.vitals()
        for ls in v.live:
            if ls.num == n:
                self.filter_sid = ls.session_id
                self._apply_filter()
                return

    def action_cycle_filter(self) -> None:
        v = self.stream.vitals()
        sids = [ls.session_id for ls in v.live]
        if not sids:
            return
        if self.filter_sid not in sids:
            self.filter_sid = sids[0]
        else:
            i = sids.index(self.filter_sid) + 1
            self.filter_sid = None if i >= len(sids) else sids[i]
        self._apply_filter()

    def _apply_filter(self) -> None:
        for w in self.query(EventWidget):
            w.display = self._visible_to(w.event)
        self._relayout()
        self._refresh_vitals()
        self._refresh_status()

    def click_vitals_row(self, y: int) -> None:
        """Clicking a session row in the header toggles its filter."""
        i = y - 1                       # row 0 is the vitals line
        if 0 <= i < len(self._vitals_rows):
            sid = self._vitals_rows[i]
            self.filter_sid = None if self.filter_sid == sid else sid
            self._apply_filter()

    def action_toggle_stat(self) -> None:
        self.stat_mode = not self.stat_mode
        for w in self.query(EventWidget):
            w.refresh_event()
        self._refresh_status()

    def action_toggle_keymap(self) -> None:
        """The full key map as a temporary overlay — a toggle, not a mode."""
        panel = self.query_one("#keymap", Static)
        if panel.display:
            panel.display = False
            return
        t = self.t
        rows = [
            ("↑ ↓ · j k · wheel", "select events / scroll (leaves live-follow)"),
            ("enter · click", "expand ⇄ collapse (commits: header → files → diffs)"),
            ("[ ]", "jump to previous / next chapter"),
            ("G · End", "jump to live, resume following"),
            ("g · Home", "jump to the top of scrollback"),
            ("1–9 · Tab · Esc", "filter to session (binds the id) · cycle · clear"),
            ("d", "toggle stat mode (headers only)"),
            ("s", "open the session's Transcript in less"),
            ("+ −", "typing speed (capped by the ≤2.5s honesty rule)"),
            ("?", "toggle this key map"),
            ("q", "quit; prints a parting snapshot"),
        ]
        out = Text(no_wrap=True)
        for i, (k, does) in enumerate(rows):
            if i:
                out.append("\n")
            out.append(k.ljust(18), style=t.style("primary", bold=True))
            out.append(does, style=t.style("muted"))
        panel.update(out)
        panel.display = True

    def action_toggle_expand(self) -> None:
        w = self.selected or self._last_expandable()
        if w is None:
            return
        if w.event.kind == "commit" and w.event.files:
            w.expand_level = (w.expand_level + 1) % 3
        else:
            w.expand_level = 0 if w.expand_level else 1
        w.shown_chars = w.total_chars   # expanding also finishes any typing
        if w in self.anim_queue:
            self.anim_queue.remove(w)
        w.refresh_event()
        self._refresh_status()

    def _last_expandable(self) -> EventWidget | None:
        for w in reversed(list(self.query(EventWidget))):
            if not w.display:
                continue
            if w.hidden_lines or w.event.kind in ("prompt", "bash") or w.event.files:
                return w
        return None

    def action_faster(self) -> None:
        self.speed = min(SPEED_MAX, self.speed * 1.5)
        self._refresh_status()

    def action_slower(self) -> None:
        self.speed = max(SPEED_MIN, self.speed / 1.5)
        self._refresh_status()

    # -- scrollback (reading releases the live follow; G re-engages it) -------------

    def _visible_events(self) -> list[EventWidget]:
        return [w for w in self.query(EventWidget) if w.display]

    def _select(self, w: EventWidget | None) -> None:
        if self.selected is not None:
            self.selected.remove_class("selected")
            self.selected.refresh_event()
        self.selected = w
        if w is not None:
            # reading intent: stop following so the feed holds still under you
            self.query_one("#feed", VerticalScroll).release_anchor()
            w.add_class("selected")
            w.refresh_event()
            w.scroll_visible(animate=False)
            self._refresh_status()

    def action_select_prev(self) -> None:
        events_ = self._visible_events()
        if not events_:
            return
        if self.selected is None or self.selected not in events_:
            self._select(events_[-1])
            return
        i = events_.index(self.selected)
        if i > 0:
            self._select(events_[i - 1])

    def action_select_next(self) -> None:
        events_ = self._visible_events()
        if not events_ or self.selected is None or self.selected not in events_:
            return
        i = events_.index(self.selected)
        if i < len(events_) - 1:
            self._select(events_[i + 1])
        else:
            self.action_go_live()

    def action_chapter_prev(self) -> None:
        self._jump_chapter(-1)

    def action_chapter_next(self) -> None:
        self._jump_chapter(+1)

    def _jump_chapter(self, step: int) -> None:
        """[ and ]: chapters are the skeleton — jump between prompt rules."""
        events_ = self._visible_events()
        chapters = [w for w in events_ if w.event.kind == "prompt"]
        if not chapters:
            return
        if self.selected in events_:
            i = events_.index(self.selected)
        else:
            i = len(events_)
        if step < 0:
            prior = [c for c in chapters if events_.index(c) < i]
            if prior:
                self._select(prior[-1])
        else:
            later = [c for c in chapters if events_.index(c) > i]
            if later:
                self._select(later[0])

    def action_page_up(self) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        feed.release_anchor()
        feed.scroll_page_up(animate=False)
        self._refresh_status()

    def action_page_down(self) -> None:
        self.query_one("#feed", VerticalScroll).scroll_page_down(animate=False)
        self._refresh_status()

    def click_select(self, w: EventWidget) -> None:
        """A click selects the clicked event and toggles its expansion in one
        gesture — click to expand, click again to collapse. Clicks outside any
        event do nothing."""
        if self.selected is not w:
            self._select(w)
        self.action_toggle_expand()

    # -- hand off to `standup show` --------------------------------------------------

    def action_open_show(self) -> None:
        sid = None
        if self.selected is not None and self.selected.event.session_id:
            sid = self.selected.event.session_id
        sid = sid or self.filter_sid
        if sid is None:
            v = self.stream.vitals()
            if v.live:
                sid = v.live[0].session_id
        if sid is None or sid not in self.stream.tailers:
            return
        log_path = Path(self.stream.tailers[sid].session.log_path)
        from . import show as show_mod
        try:
            text = show_mod.render_transcript(log_path)
        except Exception as e:  # never let a bad log kill the watch
            self.notify(f"show failed: {e}", severity="error")
            return
        with self.suspend():
            subprocess.run(["less", "-R"], input=text.encode(), check=False)


def _css(t: Theme) -> str:
    """Palette-resolved stylesheet. Backgrounds exist only on the raised bands
    and the selection; at 16 colors / NO_COLOR the selection degrades to
    reverse video and the bands to plain rows (the chips carry the state)."""
    if t.paints_backgrounds:
        surface = f"background: {t.css_color('surface')};"
        raised = f"background: {t.css_color('raised')};"
        selection = f"background: {t.css_color('selection')};"
        screen_color = f"color: {t.css_color('primary')};"
        scrollbar = (f"scrollbar-background: {t.css_color('surface')};"
                     f"scrollbar-color: {t.css_color('faint')};")
    else:
        surface = raised = screen_color = scrollbar = ""
        selection = "text-style: reverse;"
    # nothing reflows — rows only truncate, so the grid never breaks
    nowrap = "text-wrap: nowrap; text-overflow: clip;"
    return f"""
    Screen {{ layout: vertical; {surface} {screen_color} }}
    #vitals {{ height: auto; padding: 0 1; {raised} {nowrap} }}
    #rule {{ height: 1; padding: 0 1; {surface} {nowrap} }}
    #feed {{
        height: 1fr; padding: 0 1; {surface}
        align-vertical: bottom;
        {scrollbar}
    }}
    #keymap {{ height: auto; padding: 0 1; {raised} {nowrap} }}
    #status {{ dock: bottom; height: 1; {raised} {nowrap} }}
    #boundary {{ height: auto; margin-top: 1; {nowrap} }}
    EventWidget {{ height: auto; {nowrap} }}
    EventWidget.selected {{ {selection} }}
    EventWidget.chapter {{ margin-top: 1; }}
    """


def run_watch(stream: WatchStream, light: bool = False) -> str:
    """Run the app; returns the parting snapshot to print on plain stdout."""
    theme_ = Theme(light=light)
    WatchApp.CSS = _css(theme_)
    WatchApp(stream, theme_).run()
    return stream.parting_snapshot()
