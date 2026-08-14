"""One entry in the Watch's feed, as policy rather than as a widget.

An `EventRow` is what a Watch entry *is* before anything draws it: one Feed
Event, or one **Change Run** of them (CONTEXT.md → Change Run), holding the
rules that decide what the entry contains — which later event it admits, how the
two witnesses fold, where the collapsed window sits, how many characters the
typing animation still owes, how a **Call**'s input decomposes into body rows,
and whether there is anything to disclose at all.

Plain Python by design, one layer further out than the stream (ADR 0004 § the
stream/UI boundary): no textual, and no rich either. Nothing here measures a
cell, picks a style or knows a terminal width — the numbers it produces are
counts of *lines* and *characters* in the text the log and git supplied. The
widget in `watchui` holds one of these and renders it; the policy is therefore
table-testable against Feed Events alone, which is the whole reason it sits
here.

The division of labour, precisely: this module owns the *content* of an entry
and `watchui` owns its *appearance*. So the collapsed window's bounds are here
and the syntax highlighting of the lines inside it is there; the animation's
character budget is here and the clock driving it is there; a Call's body rows
are here and their columns are there.
"""

from __future__ import annotations

from datetime import timedelta

from . import toolcalls
from .watchstream import FeedEvent

HEAD_LINES = 12            # animated window into a large block; rest collapses
REMOVED_LINES = 4          # removed-text lines shown collapsed
CALL_HEAD_LIMIT = 100      # chars of a Call's argument the header carries; past
                           # this it clips and the body holds the rest
FRESH = 30                 # seconds: recency younger than this reads as live.
                           # It lives here rather than in the UI because
                           # RUN_WINDOW *is* it — see below — and one constant
                           # with two readers beats two that must be kept equal
RUN_WINDOW = timedelta(seconds=FRESH)   # a Change Run stops absorbing this long
                           # after it was born (ADR 0004 § the Change Run), so
                           # sustained work on one file still produces rows and
                           # a run's displayed time can never be staler than
                           # this. Deliberately FRESH: the same threshold the
                           # header already uses to mean "recent" should mean
                           # it here too


class EventRow:
    """One feed entry's content: a Feed Event, plus whatever its Change Run
    absorbed.

    Constructed from the event and then *evolved* by `absorb`; every derived
    figure is recomputed there, so a reader of this object never has to know
    which order things happened in."""

    def __init__(self, event: FeedEvent) -> None:
        self.event = event
        # Change Run state. `calls` holds the *tool call* ids folded in, so one
        # MultiEdit counts once however many hunks it emitted; `contributions`
        # counts the events, which is what decides where the window sits.
        self.calls: set[str] = {event.tool_id} if event.tool_id else set()
        self.contributions = 1
        # the body as raw lines: counting, window bounds and the char budget all
        # work off these, so none of them pays for highlighting
        self.added_raw = event.added.split("\n") if event.kind == "file" and event.added else []
        self.removed_raw = event.removed.split("\n") if event.kind == "file" and event.removed else []
        # A Call's body: the input entire, as `(path, text)` rows. Bash keeps its
        # own reading — the command's lines lead, shell-lexed, and its remaining
        # keys follow as ordinary rows, so no tool is silently carved out of "the
        # body is the input" (ADR 0004 § Calls).
        self.shell_rows = 0
        self.call_rows: list[tuple[str | None, str]] = []
        if event.kind == "call":
            rest = dict(event.tool_input or {})
            if event.command is not None:
                lines = event.command.splitlines() or [""]
                self.shell_rows = len(lines)
                self.call_rows = [(None, ln) for ln in lines]
                rest.pop("command", None)
            self.call_rows += toolcalls.input_rows(rest)
        # the collapsed window into the body, and the animation's char budget
        self.win_start = self.win_end = 0
        self.lines_above = self.lines_below = 0
        self.total_chars = self.shown_chars = self.frozen_chars = 0
        self._reflow(len(self.added_raw))

    # -- the Change Run ---------------------------------------------------------

    def absorbs(self, ev: FeedEvent) -> bool:
        """Does `ev` continue this row's Change Run (ADR 0004 § the Change Run)?

        Strict adjacency: the caller only ever asks the *tail* row, so a run
        grows at the bottom of the feed and never rewrites a row above the
        reader. Any other event between two same-file events has already closed
        the run by the time this is asked.

        The witness must match — a Session's claim and git's observation are
        different kinds of statement and never merge — as must the backfill side
        of the launch boundary, so a live event cannot grow a replayed block
        across the rule that separates them. RUN_WINDOW closes a run that would
        otherwise absorb a long burst forever, which is what keeps the feed
        producing rows while the agent works and bounds how stale the run's
        displayed timestamp can be."""
        e = self.event
        return (e.kind == "file" and ev.kind == "file"
                and ev.path == e.path
                and ev.session_id == e.session_id
                and ev.restates == e.restates
                and ev.backfill == e.backfill
                and ev.when - e.when <= RUN_WINDOW)

    def absorb(self, ev: FeedEvent, animate: bool) -> None:
        """Fold `ev` into this Change Run.

        Two shapes, one per witness. A Session claims hunks, which *accumulate*:
        the body grows and the new text types in from where the last one stopped.
        The git watcher states the whole delta of the path, which *supersedes*:
        the body is replaced and lands instantly, because re-typing rows already
        on screen every poll is a flicker, not an animation.

        `when`, `change` and the gap gutter stay as the run was born with them —
        rows must not rewrite themselves under a reader — and RUN_WINDOW is what
        bounds the resulting staleness."""
        e = self.event
        if ev.tool_id:
            self.calls.add(ev.tool_id)
        if ev.restates:
            e.added, e.removed = ev.added, ev.removed
            fresh = 0
        else:
            e.added = "\n".join(p for p in (e.added, ev.added) if p)
            e.removed = "\n".join(p for p in (e.removed, ev.removed) if p)
            fresh = len(ev.added.split("\n")) if ev.added else 0
        self.added_raw = e.added.split("\n") if e.added else []
        self.removed_raw = e.removed.split("\n") if e.removed else []
        self.contributions += 1
        self._reflow(fresh)
        if animate and fresh:
            self.shown_chars = self.frozen_chars

    # -- the collapsed window ---------------------------------------------------

    def _reflow(self, fresh_lines: int) -> None:
        """Recompute the collapsed window and the animation's char budget.

        The window sits where the news is (ADR 0004 § the Change Run). An
        accumulating run is chronological, so once it holds more than one
        contribution it shows its
        *tail* — otherwise the newest hunk, the one you are watching for, would
        be the one hidden behind the line count. A lone event and a restating
        witness both show their *head*: a single hunk reads top-down, and a
        cumulative body is a file-ordered snapshot with no newest end at all.

        `fresh_lines` is how many added lines arrived in the contribution being
        reflowed for. Everything ahead of them in the window is already on
        screen, and becomes the frozen head that must not re-type."""
        raw = self.added_raw
        tail = self.contributions > 1 and not self.event.restates
        self.win_start = max(0, len(raw) - HEAD_LINES) if tail else 0
        self.win_end = min(len(raw), self.win_start + HEAD_LINES)
        win = raw[self.win_start:self.win_end]
        self.lines_above = self.win_start
        self.lines_below = len(raw) - self.win_end
        self.total_chars = sum(len(s) + 1 for s in win) - 1 if win else 0
        n_fresh = min(fresh_lines, len(win))
        frozen = sum(len(s) + 1 for s in win[: len(win) - n_fresh])
        self.frozen_chars = min(frozen, self.total_chars)
        self.shown_chars = self.total_chars  # instant by default; the UI may lower it

    @property
    def added_window(self) -> list[str]:
        """The added lines a collapsed body shows, unhighlighted."""
        return self.added_raw[self.win_start:self.win_end]

    @property
    def removed_head(self) -> list[str]:
        """The removed lines a collapsed body shows. Removed text has no window
        that moves: it is the text that *was* there, so the head of it is always
        the readable part."""
        return self.removed_raw[:REMOVED_LINES]

    @property
    def removed_hidden(self) -> int:
        """Removed lines a collapsed body counts rather than shows."""
        return max(0, len(self.removed_raw) - REMOVED_LINES)

    # -- disclosure -------------------------------------------------------------

    @property
    def disclosure_lines(self) -> int | None:
        """`▸ N lines` for a collapsed file body — the whole body's line count —
        or None when the collapsed rendering already showed all of it.

        The count describes the body beneath it (ADR 0004 § the Change Run: the
        header always describes its own body), so it counts both sides of the
        change and compares against what the collapsed window plus the removed
        head actually put on screen."""
        body = len(self.added_raw) + len(self.removed_raw)
        shown = len(self.removed_head) + (self.win_end - self.win_start)
        return body if body > shown else None

    @property
    def call_expandable(self) -> bool:
        """Does this Call hold more than its header row showed? One scalar the
        header carried whole is not more — and saying so is the point: a Call
        used to advertise nothing either way, so a row with nothing to open was
        indistinguishable from one that simply refused to open."""
        rows = self.call_rows
        if len(rows) > 1:
            return True
        return bool(rows) and len(rows[0][1]) > CALL_HEAD_LIMIT

    @property
    def expandable(self) -> bool:
        """Is there a body to open at all?

        What bare `enter` needs in order to pick the newest entry that will
        actually answer it. A file entry qualifies on *hidden added lines*
        alone: `disclosure_lines` is the header's promise about the whole body,
        while this is the question of whether expanding reveals text the
        collapsed rendering withheld.

        A prompt is not answered here — whether its chapter rule truncated the
        text is a question about the rendered width, so it stays with the
        renderer."""
        e = self.event
        if e.kind == "call":
            return self.call_expandable
        if e.kind == "commit":
            return bool(e.files)
        return bool(self.lines_above or self.lines_below)

    # -- the animation's character budget -----------------------------------------

    @property
    def animating(self) -> bool:
        """Is there text in the window still owed to the screen?"""
        return self.shown_chars < self.total_chars

    @property
    def pending_chars(self) -> float:
        return max(0.0, self.total_chars - self.shown_chars)

    def advance(self, chars: float) -> float:
        """Type up to `chars` more of the window; returns what was spent, so a
        frame's budget can be shared across the queue without the caller
        recomputing what was owed."""
        step = min(self.pending_chars, chars)
        self.shown_chars += step
        return step

    def rewind(self) -> None:
        """Type this window from nothing — a freshly arrived live block."""
        self.shown_chars = 0

    def snap(self) -> None:
        """Land the whole window at once. The staleness bound, `G`, and
        expanding all end here: motion may never outlive the data, so finishing
        instantly is always allowed (ADR 0004 § Motion never outlives the
        data)."""
        self.shown_chars = self.total_chars
