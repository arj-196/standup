"""The Watch's row model: what a feed entry contains, decided without a UI.

`watchrow` is the Textual-free layer between the event stream and the widget
(ADR 0004 § the stream/UI boundary): admission into a **Change Run**, the fold
for each of the two witnesses, where the collapsed window sits, what the typing
animation still owes, how a **Call**'s input decomposes into body rows, and
whether there is anything to disclose. These are the rules a reader of the feed
is trusting, so they are pinned here as tables over Feed Events — nothing in
this module imports textual, or rich, or a Theme.

`test_watch_render.py` is the other half: the same rules as pixels, frozen on
fixtures.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from standup.watchrow import (CALL_HEAD_LIMIT, HEAD_LINES, REMOVED_LINES,
                              RUN_WINDOW, EventRow)
from standup.watchstream import CommitFile, FeedEvent

T0 = datetime(2026, 8, 13, 9, 15, tzinfo=timezone.utc)
SID = "1234abcd-0000-0000-0000-000000000000"
OTHER = "5678efab-0000-0000-0000-000000000000"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def claim(seconds: float = 0, added: str = "one", removed: str = "",
          path: str = "src/alpha.py", session_id: str | None = SID,
          tool_id: str | None = "t1", **kw) -> FeedEvent:
    """A Session's claimed edit — one hunk, from one tool call."""
    return FeedEvent(kind="file", when=at(seconds), session_id=session_id,
                     path=path, change="modify", added=added, removed=removed,
                     tool_id=tool_id, **kw)


def observation(seconds: float = 0, added: str = "one", removed: str = "",
                path: str = "src/alpha.py", **kw) -> FeedEvent:
    """Git's observation of a dirty path — the whole delta, restating what it
    last said about that path."""
    return FeedEvent(kind="file", when=at(seconds), path=path, change="modify",
                     added=added, removed=removed, restates=True, **kw)


def call(tool: str = "notion-fetch", tool_input: dict | None = None,
         command: str | None = None, **kw) -> FeedEvent:
    return FeedEvent(kind="call", when=at(0), session_id=SID, tool=tool,
                     command=command, tool_input=tool_input, **kw)


# --- admission: what a Change Run absorbs ---------------------------------

ADMISSION = [
    ("the next hunk of the same file, same session",
     claim(0), claim(2, added="two", tool_id="t2"), True),
    ("another hunk of the same tool call",
     claim(0), claim(0, added="two"), True),
    ("a hunk of a different file",
     claim(0), claim(2, path="src/beta.py"), False),
    ("a hunk claimed by a different session",
     claim(0), claim(2, session_id=OTHER), False),
    ("git's observation of the file a session is claiming",
     claim(0), observation(2), False),
    ("a session's claim about the file git is observing",
     observation(0), claim(2), False),
    ("git's next observation of the same path",
     observation(0), observation(2, added="one\ntwo"), True),
    ("a replayed hunk landing under a live run",
     claim(0), claim(2, backfill=True), False),
    ("a live hunk landing under a replayed run",
     claim(0, backfill=True), claim(2), False),
    ("a hunk arriving exactly on the run window",
     claim(0), claim(RUN_WINDOW.total_seconds()), True),
    ("a hunk arriving a second past the run window",
     claim(0), claim(RUN_WINDOW.total_seconds() + 1), False),
    ("a Call on the same file's tool id",
     claim(0), call(tool_id="t1"), False),
    ("a file event under a Call",
     call(tool_id="t1"), claim(2), False),
    ("a commit of the same path",
     claim(0), FeedEvent(kind="commit", when=at(2), session_id=SID,
                         sha="abc1234", message="landed"), False),
]


@pytest.mark.parametrize("what,first,nxt,absorbed",
                         [(c[0], c[1], c[2], c[3]) for c in ADMISSION],
                         ids=[c[0] for c in ADMISSION])
def test_what_a_change_run_admits(what, first, nxt, absorbed):
    """The four bounds of a Change Run, one row each (ADR 0004 § the Change
    Run): the path and the *witness* must match — a Session's claim and git's
    observation are different kinds of statement — the backfill boundary is not
    crossed, and the run closes RUN_WINDOW after it was born so sustained work
    on one file still produces rows.

    Strict adjacency is the caller's half and is not decidable here: only the
    feed's tail row is ever asked, so any event in between has already closed
    the run (`WatchApp._add_event`)."""
    assert EventRow(first).absorbs(nxt) is absorbed


def test_adjacency_is_asked_of_the_tail_row_only():
    """The fold grows a run at the feed's tail and never rewrites a row above
    the reader, so admission is a question about *this* row and the event just
    in — never a search back through the feed for a matching path."""
    row = EventRow(claim(0))
    row.absorb(claim(2, added="two", tool_id="t2"), animate=False)

    # the run has moved on with its newest event, but its identity — path,
    # witness, and the birth instant the window is measured from — has not
    assert row.absorbs(claim(4, added="three", tool_id="t3"))
    assert not row.absorbs(claim(RUN_WINDOW.total_seconds() + 1))


# --- the fold: one shape per witness --------------------------------------


def test_a_claimed_run_accumulates_its_hunks_and_counts_tool_calls():
    """A Session claims hunks, which accumulate — and the header's `×N` counts
    the *tool calls* folded in, so one MultiEdit spanning three hunks is `×1`
    (ADR 0004 § the Change Run)."""
    row = EventRow(claim(0, added="one", removed="ONE"))

    row.absorb(claim(1, added="two", removed="TWO"), animate=False)
    row.absorb(claim(2, added="three"), animate=False)

    assert row.added_raw == ["one", "two", "three"]
    assert row.removed_raw == ["ONE", "TWO"]
    assert row.calls == {"t1"}          # one MultiEdit, three hunks
    assert row.contributions == 3

    row.absorb(claim(3, added="four", tool_id="t2"), animate=False)
    assert row.calls == {"t1", "t2"}


def test_a_git_witnessed_run_restates_the_whole_delta():
    """Git states the cumulative delta of the path, so a later observation
    *supersedes* the body rather than being added to it — a line added then
    removed cancels instead of counting twice (ADR 0004 § the Change Run)."""
    row = EventRow(observation(0, added="one\ntwo", removed="ONE"))

    row.absorb(observation(2, added="one", removed=""), animate=False)

    assert row.added_raw == ["one"]
    assert row.removed_raw == []
    assert row.contributions == 2
    assert row.calls == set()           # git polls are not tool calls


def test_a_run_keeps_the_time_and_change_it_was_born_with():
    """Rows must not rewrite themselves under a reader: the displayed time is
    the run's *first* event's, never revised, and RUN_WINDOW is what bounds how
    stale that can get (ADR 0004 § the Change Run)."""
    first = claim(0)
    row = EventRow(first)

    row.absorb(claim(5, added="two", tool_id="t2"), animate=False)

    assert row.event.when == at(0)
    assert row.event.change == "modify"


def test_the_counts_describe_the_body_beneath_them():
    """A header count is trustworthy because it can be verified against what it
    shows, so a claimed run's `+N −M` is the sum of the body it accumulated and
    a restating run's is the delta it last stated (ADR 0004 § the Change Run)."""
    claimed = EventRow(claim(0, added="one\ntwo", removed="ONE"))
    claimed.absorb(claim(2, added="three", tool_id="t2"), animate=False)
    observed = EventRow(observation(0, added="one\ntwo"))
    observed.absorb(observation(2, added="one\ntwo\nthree"), animate=False)

    assert (len(claimed.added_raw), len(claimed.removed_raw)) == (3, 1)
    assert (len(observed.added_raw), len(observed.removed_raw)) == (3, 0)


# --- the collapsed window: fold, don't clip -------------------------------


def test_a_lone_event_shows_its_head():
    """One hunk reads top-down, so the window starts at the first line and the
    rest is counted below it (ADR 0004 § the Change Run: the window sits where
    the news is)."""
    row = EventRow(claim(0, added="\n".join(f"l{i}" for i in range(20))))

    assert (row.win_start, row.win_end) == (0, HEAD_LINES)
    assert (row.lines_above, row.lines_below) == (0, 20 - HEAD_LINES)
    assert row.added_window == [f"l{i}" for i in range(HEAD_LINES)]


def test_an_accumulating_run_shows_its_tail_once_it_holds_more_than_one_hunk():
    """A claimed run is chronological, so past one contribution the newest text
    is the news and the earlier lines are counted *above* the window — a
    counter beneath them would claim they came after."""
    row = EventRow(claim(0, added="\n".join(f"l{i}" for i in range(20))))
    row.absorb(claim(2, added="fresh", tool_id="t2"), animate=False)

    assert row.win_end == 21
    assert row.lines_above == 21 - HEAD_LINES
    assert row.lines_below == 0
    assert row.added_window[-1] == "fresh"


def test_a_restating_run_keeps_showing_its_head_however_often_it_restates():
    """A cumulative body is a file-ordered snapshot with no newest end at all,
    so there is no tail to move to."""
    row = EventRow(observation(0, added="\n".join(f"l{i}" for i in range(20))))
    row.absorb(observation(2, added="\n".join(f"l{i}" for i in range(20))),
               animate=False)

    assert (row.win_start, row.lines_above) == (0, 0)
    assert row.lines_below == 20 - HEAD_LINES


def test_the_removed_side_shows_its_head_and_counts_the_rest():
    """Removed text has no window that moves: it is what *was* there."""
    row = EventRow(claim(0, added="", removed="\n".join(f"r{i}" for i in range(9))))

    assert row.removed_head == [f"r{i}" for i in range(REMOVED_LINES)]
    assert row.removed_hidden == 9 - REMOVED_LINES


def test_a_run_stays_bounded_however_much_it_absorbs():
    """The window is the actual noise cap: a run that swallowed a burst is no
    taller than one that swallowed a line."""
    row = EventRow(claim(0, added="one"))
    for i in range(40):
        row.absorb(claim(1, added=f"l{i}", tool_id=f"t{i}"), animate=False)

    assert row.win_end - row.win_start == HEAD_LINES
    assert row.lines_above == 41 - HEAD_LINES


# --- disclosure: is there anything to open? -------------------------------


def test_a_file_row_discloses_the_whole_body_when_the_window_hid_some_of_it():
    """`▸ N lines` counts the body beneath it — both sides of the change — and
    is absent when the collapsed rendering already showed all of it. The window
    is what hides lines (ADR 0004 § the Change Run); that the marker's absence
    means "nothing to open" rather than "expanding is broken" is the rule
    ADR 0004 § Calls states for the Call carrying the same marker."""
    small = EventRow(claim(0, added="one\ntwo", removed="ONE"))
    big = EventRow(claim(0, added="\n".join(f"l{i}" for i in range(20)),
                         removed="\n".join(f"r{i}" for i in range(9))))

    assert small.disclosure_lines is None
    assert big.disclosure_lines == 29        # 20 added + 9 removed
    assert big.expandable and not small.expandable


def test_a_call_that_the_header_carried_whole_discloses_nothing():
    """One scalar the header showed entire is not "more", and saying so is the
    point: a row with nothing to open must not look like one that refuses to
    open (ADR 0004 § Calls)."""
    short = EventRow(call(tool="WebFetch", tool_input={"url": "https://x.test"}))
    long = EventRow(call(tool="WebFetch",
                         tool_input={"url": "https://x.test/" + "a" * CALL_HEAD_LIMIT}))
    multi = EventRow(call(tool_input={"id": "p", "depth": 2}))
    empty = EventRow(call(tool_input={}))

    assert not short.call_expandable and not short.expandable
    assert long.call_expandable and long.expandable
    assert multi.call_expandable
    assert empty.call_rows == [] and not empty.call_expandable


def test_a_commit_discloses_its_files_and_a_bare_git_fact_discloses_nothing():
    """What bare `enter` may land on. A commit carries its own diff, so there is
    always something beneath it; a push or a branch switch is one line of
    Standup's own prose."""
    commit = EventRow(FeedEvent(kind="commit", when=at(0), sha="abc1234",
                                message="landed",
                                files=[CommitFile(path="a.py", change="modify",
                                                  added="one", removed="")]))
    bare = EventRow(FeedEvent(kind="commit", when=at(0), sha="abc1234",
                              message="landed"))
    push = EventRow(FeedEvent(kind="push", when=at(0), message="main  1 commit"))

    assert commit.expandable
    assert not bare.expandable
    assert not push.expandable


# --- a Call's body: the input entire --------------------------------------


def test_a_calls_body_is_every_key_by_path_and_unclipped():
    """The body is built from the input, never from the header's digest — a
    digest cannot be expanded back into what it summarised. Every leaf carries
    its dotted/indexed path, a value's own newlines become their own rows, and
    nothing is dropped or truncated (ADR 0004 § Calls)."""
    row = EventRow(call(tool="notion-update-page",
                        args="update_content",          # the old lossy digest
                        tool_input={"page_id": "p1", "command": "update_content",
                                    "content_updates": [{"new_str": "hello\nworld"}],
                                    "empty": {}}))

    assert row.call_rows == [
        ("page_id", "p1"),
        ("command", "update_content"),
        ("content_updates[0].new_str", "hello"),
        (None, "world"),
        ("empty", "{}"),
    ]
    assert row.shell_rows == 0


def test_a_bash_calls_command_leads_its_body_and_its_other_keys_follow():
    """Bash is the one Call whose argument is a shell command, so its lines lead
    the body (the caller shell-lexes exactly `shell_rows` of them) and its
    remaining keys follow as ordinary rows — no tool is silently carved out of
    "the body is the input" (ADR 0004 § Calls)."""
    row = EventRow(call(tool="Bash", command="cd src\nuv run pytest",
                        tool_input={"command": "cd src\nuv run pytest",
                                    "description": "run the suite"}))

    assert row.shell_rows == 2
    assert row.call_rows == [
        (None, "cd src"),
        (None, "uv run pytest"),
        ("description", "run the suite"),
    ]


def test_a_file_or_git_event_has_no_call_body():
    """Only a Call decomposes; a file event's body is its diff."""
    assert EventRow(claim(0)).call_rows == []
    assert EventRow(observation(0)).call_rows == []


# --- the animation's character budget -------------------------------------


def test_the_budget_counts_the_windows_characters_and_its_newlines():
    """The animation counts *characters*, not rows, which is why folding could
    give up the row-count guarantee without touching it (ADR 0004 § fold, don't
    clip). A newline costs one."""
    row = EventRow(claim(0, added="ab\ncde"))

    assert row.total_chars == len("ab") + 1 + len("cde")
    assert row.frozen_chars == 0          # a fresh block types from nothing
    assert row.shown_chars == row.total_chars   # instant until the UI rewinds it


def test_absorbed_text_types_in_from_where_the_last_contribution_stopped():
    """Rows already on screen must not re-type, so everything ahead of the
    arriving lines is frozen (ADR 0004 § the Change Run)."""
    row = EventRow(claim(0, added="one\ntwo"))
    row.snap()

    row.absorb(claim(2, added="three", tool_id="t2"), animate=True)

    assert row.frozen_chars == len("one\ntwo\n")
    assert row.shown_chars == row.frozen_chars
    assert row.total_chars == len("one\ntwo\nthree")
    assert row.animating


def test_a_restatement_lands_instantly():
    """Re-typing rows already on screen every git poll is a flicker, not an
    animation."""
    row = EventRow(observation(0, added="one"))

    row.absorb(observation(2, added="one\ntwo"), animate=True)

    assert not row.animating
    assert row.shown_chars == row.total_chars


def test_the_budget_is_spent_a_frame_at_a_time_and_can_always_be_snapped():
    """`advance` reports what it spent so one frame's budget can be shared
    across the queue, and `snap` finishes: motion may never outlive the data, so
    landing early is always allowed (ADR 0004 § Motion never outlives the
    data)."""
    row = EventRow(claim(0, added="abcdef"))
    row.rewind()

    assert row.advance(4) == 4
    assert (row.shown_chars, row.pending_chars) == (4, 2)
    assert row.advance(10) == 2            # never overspends the window
    assert not row.animating

    row.rewind()
    row.snap()
    assert row.shown_chars == row.total_chars == 6
    assert row.advance(1) == 0             # nothing left to owe


# --- the containment rule -------------------------------------------------


def test_the_row_model_imports_no_tui():
    """ADR 0004 § the stream/UI boundary, one layer further out than the stream:
    the row model is plain Python, so the policy above is testable — and the
    inbox/cost/session path can never grow a TUI dependency through it. A
    subprocess, because textual may already be imported by a sibling test."""
    probe = ("import standup.watchrow, sys; "
             "print(sorted(m for m in sys.modules if m.split('.')[0] "
             "in ('textual', 'rich')))")

    out = subprocess.run([sys.executable, "-c", probe], check=True,
                         capture_output=True, text=True).stdout

    assert out.strip() == "[]"
