"""The Watch UI: a Textual app over the watchstream Feed Events (ADR 0008).

This is the only module that imports textual. The typing animation is
presentation-only under a hard staleness bound: the display may lag the log by
at most STALENESS_BOUND seconds — typing speed compresses (down to instant
landing) to honor it. Delight never outranks truth (CONTEXT.md → Watch).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Span, Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from .watchstream import FeedEvent, WatchStream

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
SYNTAX_THEME = "monokai"   # diff bodies pop on purpose — not matched to the TUI

_KIND_MARK = {
    "file": ("✎", "green"),
    "bash": ("⏺", "dim"),
    "prompt": ("──", "cyan"),
    "commit": ("⚑", "yellow"),
    "push": ("⇧", "yellow"),
    "branch": ("⑂", "magenta"),
    "unattributed": ("~", "red"),
    "session": ("●", "cyan"),
}


def _hms(when: datetime) -> str:
    return when.astimezone().strftime("%H:%M:%S")


def _ago(when: datetime, now: datetime) -> str:
    s = max(0, int((now - when).total_seconds()))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m ago"


def _one_line(s: str, limit: int = 120) -> str:
    s = " ".join(s.split())
    return s[: limit - 1] + "…" if len(s) > limit else s


def _no_bg(style: Style | str) -> Style | str:
    # token styles carry the theme's page background; the feed supplies its
    # own, and the selection highlight must show through
    if not isinstance(style, Style) or style.bgcolor is None:
        return style
    return Style(color=style.color, bold=style.bold,
                 italic=style.italic, underline=style.underline)


def _styled_lines(code: str, path: str | None) -> list[Text]:
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
        t.spans = [Span(s.start, s.end, _no_bg(s.style)) for s in t.spans]
    return list(lines)


class EventWidget(Static):
    """One Feed Event. File events own a typed-animation body; everything else
    renders as a single header line (prompts expand to their full text)."""

    def __init__(self, event: FeedEvent) -> None:
        super().__init__()
        self.event = event
        self.expanded = False
        self.bash_ok: bool | None = None
        # animation state: how many chars of the (collapsed) body are visible
        self.head_text, self.hidden_lines = self._split_body()
        if event.kind == "file":
            self.added_lines = _styled_lines(event.added, event.path) if event.added else []
            self.removed_lines = _styled_lines(event.removed, event.path) if event.removed else []
        else:
            self.added_lines, self.removed_lines = [], []
        self.head_lines = self.added_lines[:HEAD_LINES]
        self.total_chars = len(self.head_text)
        self.shown_chars = self.total_chars  # instant by default; app may reset to 0
        # commit diffs are highlighted lazily: a commit can carry many files, and
        # lexing them all at mount time would stall the feed for a body nobody
        # has asked to see yet
        self._commit_lines: list[tuple[str, str, list[Text], list[Text]]] | None = None
        if event.backfill:
            self.add_class("backfill")

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
            self._commit_lines = [
                (f.path, f.change,
                 _styled_lines(f.added, f.path) if f.added else [],
                 _styled_lines(f.removed, f.path) if f.removed else [])
                for f in self.event.files
            ]
        return self._commit_lines

    # -- rendering --------------------------------------------------------------

    def _header(self) -> Text:
        e = self.event
        mark, color = _KIND_MARK.get(e.kind, ("·", "white"))
        t = Text()
        t.append(_hms(e.when) + " ", style="dim")
        # The handle outranks everything else in its column: it is the one
        # token here you can hand to `standup show`. Its absence is the
        # opposite — a claim gap — so the placeholder stays grey.
        if e.handle:
            t.append(e.handle + " ", style="cyan")
        else:
            t.append("········ ", style="dim")
        t.append(mark + " ", style=color)
        if e.kind == "file":
            plus = len(e.added.split("\n")) if e.added else 0
            minus = len(e.removed.split("\n")) if e.removed else 0
            t.append(e.path or "?", style="bold")
            t.append(f"  {e.change}", style="dim")
            t.append(f"  +{plus}", style="green")
            if minus:
                t.append(f" −{minus}", style="red")
            if not e.session_id:   # git is the only witness — mark the claim gap
                t.append("  ~unattributed", style="red dim")
        elif e.kind == "bash":
            t.append("$ " + _one_line(e.command or "", 100))
            if self.bash_ok is True:
                t.append("  ✓", style="green")
            elif self.bash_ok is False:
                t.append("  ✗", style="red")
        elif e.kind == "prompt":
            t.append("you: ", style="cyan bold")
            t.append(_one_line(e.message, 100), style="cyan")
        elif e.kind == "commit":
            t.append(f"commit @{e.sha} ", style="dim")
            t.append(_one_line(e.message, 90))
            if e.files:   # only when a diff was actually read (see _commit_files)
                plus = sum(len(f.added.split("\n")) for f in e.files if f.added)
                minus = sum(len(f.removed.split("\n")) for f in e.files if f.removed)
                t.append(f"  {len(e.files)} file{'s' if len(e.files) != 1 else ''}",
                         style="dim")
                t.append(f"  +{plus}", style="green")
                if minus:
                    t.append(f" −{minus}", style="red")
            if not e.session_id:
                t.append("  ~unattributed", style="red dim")
        elif e.kind == "unattributed":
            t.append("unattributed change: ", style="red")
            t.append(_one_line(e.message, 90))
        else:  # push | branch | session
            t.append(e.kind + " ", style=color)
            t.append(_one_line(e.message, 90))
        return t

    def _commit_body(self, out: Text) -> Text:
        """A commit's diff: its file list collapsed, every file's added and
        removed text expanded. Same gutter and same highlighting as a live file
        event — the only difference is that git, not a session, supplied it."""
        blocks = self.event.files
        if not self.expanded:
            for f in blocks[:COMMIT_FILE_LINES]:
                plus = len(f.added.split("\n")) if f.added else 0
                minus = len(f.removed.split("\n")) if f.removed else 0
                out.append("\n  ")
                out.append(f.path, style="bold")
                out.append(f"  {f.change}", style="dim")
                out.append(f"  +{plus}", style="green")
                if minus:
                    out.append(f" −{minus}", style="red")
            if len(blocks) > COMMIT_FILE_LINES:
                out.append("\n")
                out.append(f"  … +{len(blocks) - COMMIT_FILE_LINES} more files"
                           f" (enter expands)", style="dim")
            else:
                out.append("\n")
                out.append("  (enter expands the diff)", style="dim")
            return out
        for path, change, added, removed in self.commit_lines():
            out.append("\n  ")
            out.append(path, style="bold")
            out.append(f"  {change}", style="dim")
            for ln in removed:
                out.append("\n")
                out.append("- ", style="red")
                out.append_text(ln)
            for ln in added:
                out.append("\n")
                out.append("+ ", style="green")
                out.append_text(ln)
        return out

    def render_event(self, stat_mode: bool) -> Text:
        e = self.event
        out = self._header()
        if stat_mode or e.kind not in ("file", "prompt", "bash", "commit"):
            return out
        if e.kind == "commit":
            return self._commit_body(out) if e.files else out
        if e.kind == "prompt":
            if self.expanded and len(" ".join(e.message.split())) > 100:
                out.append("\n")
                out.append(e.message, style="cyan")
            return out
        if e.kind == "bash":
            if self.expanded and e.command and len(_one_line(e.command)) < len(e.command):
                out.append("\n")
                out.append(e.command, style="dim")
            return out

        # file event body: the gutter says what changed, the colors say what it is
        if self.expanded:
            for ln in self.removed_lines:
                out.append("\n")
                out.append("- ", style="red")
                out.append_text(ln)
            for ln in self.added_lines:
                out.append("\n")
                out.append("+ ", style="green")
                out.append_text(ln)
            return out

        if self.removed_lines:
            for ln in self.removed_lines[:REMOVED_LINES]:
                out.append("\n")
                out.append("- ", style="red")
                out.append_text(ln)
            if len(self.removed_lines) > REMOVED_LINES:
                out.append("\n")
                out.append(f"  … −{len(self.removed_lines) - REMOVED_LINES} more lines", style="red dim")
        shown = int(self.shown_chars)
        animating = self.shown_chars < self.total_chars
        for ln in self.head_lines:
            if shown <= 0:
                break
            out.append("\n")
            out.append("+ ", style="green")
            out.append_text(ln if shown >= len(ln.plain) else ln.divide([shown])[0])
            shown -= len(ln.plain) + 1   # +1 spends the newline
        if animating:
            out.append("▌", style="green blink")
        elif self.hidden_lines:
            out.append("\n")
            out.append(f"  … +{self.hidden_lines} more lines (enter expands)", style="dim")
        return out

    def refresh_event(self) -> None:
        app = self.app
        self.update(self.render_event(getattr(app, "stat_mode", False)))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        app = self.app
        if isinstance(app, WatchApp):
            app.click_select(self)


class WatchApp(App):
    """`standup watch` — see CONTEXT.md (Watch, Feed Event, Live Session)."""

    CSS = """
    Screen { layout: vertical; }
    #vitals { height: auto; padding: 0 1; background: $surface; }
    #feed { height: 1fr; padding: 0 1; }
    #status { dock: bottom; height: 1; padding: 0 1; background: $surface; color: $text-muted; }
    EventWidget { height: auto; }
    EventWidget.selected { background: $primary 20%; }
    EventWidget.backfill { opacity: 0.55; }
    """

    BINDINGS = [
        Binding("q", "quit", "quit", priority=True),
        Binding("escape,0", "filter_all", "all sessions", show=False, priority=True),
        Binding("tab", "cycle_filter", "next session", show=False, priority=True),
        Binding("enter", "toggle_expand", "expand", show=False, priority=True),
        Binding("d", "toggle_stat", "detail", priority=True),
        Binding("s", "open_show", "show transcript", priority=True),
        Binding("plus,equals_sign", "faster", "faster", show=False, priority=True),
        Binding("minus", "slower", "slower", show=False, priority=True),
        Binding("up", "select_prev", "scrollback", show=False, priority=True),
        Binding("down", "select_next", show=False, priority=True),
        Binding("pageup", "page_up", show=False, priority=True),
        Binding("pagedown", "page_down", show=False, priority=True),
        Binding("end,G", "go_live", "live", show=False, priority=True),
    ] + [Binding(str(i), f"filter_n({i})", show=False, priority=True) for i in range(1, 10)]

    def __init__(self, stream: WatchStream) -> None:
        super().__init__()
        self.stream = stream
        self.stat_mode = False
        self.speed = BASE_SPEED
        self.filter_sid: str | None = None
        self.anim_queue: list[EventWidget] = []
        self.bash_widgets: dict[str, EventWidget] = {}
        self.selected: EventWidget | None = None
        self._backfill_events = stream.start()

    # -- layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="vitals")
        yield VerticalScroll(id="feed")
        yield Static(id="status")

    def on_mount(self) -> None:
        for ev in self._backfill_events:
            self._add_event(ev, animate=False)
        self._refresh_vitals()
        self._refresh_status()
        self.set_interval(POLL_INTERVAL, self._poll)
        self.set_interval(1 / FPS, self._tick_animation)
        self.set_interval(1.0, self._refresh_vitals)
        # the feed follows the bottom while you're there; any scroll up releases
        # it, and returning to the bottom (or G) re-engages it — nothing pauses
        self.query_one("#feed", VerticalScroll).anchor()

    # -- the feed ---------------------------------------------------------------

    def _poll(self) -> None:
        for ev in self.stream.poll():
            self._add_event(ev, animate=True)
        self._refresh_status()

    def _add_event(self, ev: FeedEvent, animate: bool) -> None:
        if ev.kind == "bash_result":
            w = self.bash_widgets.pop(ev.tool_id or "", None)
            if w is not None:
                w.bash_ok = ev.ok
                w.refresh_event()
            return
        feed = self.query_one("#feed", VerticalScroll)
        w = EventWidget(ev)
        if ev.kind == "bash" and ev.tool_id:
            self.bash_widgets[ev.tool_id] = w
        if self.filter_sid and ev.session_id and ev.session_id != self.filter_sid:
            w.display = False
        if animate and ev.kind == "file" and w.total_chars > 0 and not ev.backfill:
            w.shown_chars = 0
            self.anim_queue.append(w)
        feed.mount(w)
        w.refresh_event()
        self._trim(feed)

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

    def _refresh_vitals(self) -> None:
        v = self.stream.vitals()
        now = datetime.now(timezone.utc)
        t = Text()
        t.append(v.repo, style="bold")
        t.append(f"  ⑂ {v.branch}", style="magenta")
        t.append(f"  ✎ {v.dirty} dirty", style="yellow" if v.dirty else "dim")
        if not v.live:
            t.append("   watching — no live session", style="dim")
        for i, ls in enumerate(v.live[:9], start=1):
            t.append("\n")
            marker = "▶" if self.filter_sid == ls.session_id else " "
            t.append(f"{marker}[{i}] ", style="dim")
            t.append(ls.handle + "  ", style="cyan")
            t.append(_one_line(ls.title, 50), style="bold")
            if ls.objective:
                t.append("  ~" + _one_line(ls.objective, 60), style="italic dim")
            t.append("  " + _ago(ls.last_append, now), style="dim")
        self.query_one("#vitals", Static).update(t)

    def _refresh_status(self) -> None:
        parts = []
        if self._following():
            parts.append("● live")
        else:
            parts.append("▲ scrolled back (G live)")
        if self.filter_sid:
            parts.append(f"filter: {self.filter_sid[:8]} (esc clears)")
        if self.stat_mode:
            parts.append("stat mode (d toggles)")
        parts.append(f"speed {int(self.speed)}c/s")
        parts.append("↑↓ scrollback · enter expand · d detail · s show · q quit")
        self.query_one("#status", Static).update("  ".join(parts))

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

    def action_filter_all(self) -> None:
        self.filter_sid = None
        self._apply_filter()

    def action_filter_n(self, n: int) -> None:
        v = self.stream.vitals()
        if 1 <= n <= len(v.live):
            self.filter_sid = v.live[n - 1].session_id
            self._apply_filter()

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
            w.display = (self.filter_sid is None or w.event.session_id is None
                         or w.event.session_id == self.filter_sid)
        self._refresh_vitals()
        self._refresh_status()

    def action_toggle_stat(self) -> None:
        self.stat_mode = not self.stat_mode
        for w in self.query(EventWidget):
            w.refresh_event()
        self._refresh_status()

    def action_toggle_expand(self) -> None:
        w = self.selected or self._last_expandable()
        if w is None:
            return
        w.expanded = not w.expanded
        w.shown_chars = w.total_chars   # expanding also finishes any typing
        if w in self.anim_queue:
            self.anim_queue.remove(w)
        w.refresh_event()

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
        self.selected = w
        if w is not None:
            # reading intent: stop following so the feed holds still under you
            self.query_one("#feed", VerticalScroll).release_anchor()
            w.add_class("selected")
            w.scroll_visible(animate=False)
            self._refresh_status()

    def action_select_prev(self) -> None:
        events = self._visible_events()
        if not events:
            return
        if self.selected is None or self.selected not in events:
            self._select(events[-1])
            return
        i = events.index(self.selected)
        if i > 0:
            self._select(events[i - 1])

    def action_select_next(self) -> None:
        events = self._visible_events()
        if not events or self.selected is None or self.selected not in events:
            return
        i = events.index(self.selected)
        if i < len(events) - 1:
            self._select(events[i + 1])
        else:
            self.action_go_live()

    def action_page_up(self) -> None:
        self.query_one("#feed", VerticalScroll).scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.query_one("#feed", VerticalScroll).scroll_page_down(animate=False)

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


def run_watch(stream: WatchStream) -> str:
    """Run the app; returns the parting snapshot to print on plain stdout."""
    WatchApp(stream).run()
    return stream.parting_snapshot()
