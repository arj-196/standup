"""What the Watch draws for a fixture feed, frozen.

The row model moved out of the Textual widget (ADR 0004 § the stream/UI
boundary): admission, folding, the collapsed window, the animation's character
budget, a Call's body rows and the disclosure rule all live in `watchrow` now,
and the widget only draws what they decide. The contract of that move is that
*nothing on screen shifts* — so these goldens were captured from the feed
before it and fail here if a row's shape, count, marker or window moves.

They are the rendering half; `test_watch_rows.py` pins the same rules as policy,
with no textual import at all.

Rows are compared `rstrip`ed. What trails a row is geometry the two diff
surfaces share and `diffrows` owns — the disclosure marker's own space, and the
removed-row wash rectangle that runs to full content width (ADR 0004 § the
removed-row field). The rectangle is asserted directly, once, rather than
being smuggled into every golden as invisible whitespace.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from standup.theme import Theme
from standup.watchstream import FeedEvent
from standup.watchui import EventWidget

T0 = datetime(2026, 8, 13, 9, 15, tzinfo=timezone.utc)
SID = "1234abcd-0000-0000-0000-000000000000"


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _claimed(seconds: float, added: str, removed: str = "",
             tool_id: str = "t1") -> FeedEvent:
    """A Session's claim: one Edit hunk on `src/alpha.py`."""
    return FeedEvent(kind="file", when=_at(seconds), session_id=SID,
                     path="src/alpha.py", change="modify", added=added,
                     removed=removed, tool_id=tool_id)


def _observed(seconds: float, added: str, removed: str = "") -> FeedEvent:
    """Git's observation: the whole delta of a dirty path, restating."""
    return FeedEvent(kind="file", when=_at(seconds), path="notes/todo.md",
                     change="modify", added=added, removed=removed,
                     restates=True)


CALL = FeedEvent(kind="call", when=_at(9), session_id=SID,
                 tool="notion-update-page",
                 args='a-page-id  {"command": "update_content", '
                      '"content_updates": [{"new_str": "hello\\nworld"}]}',
                 tool_input={"page_id": "a-page-id", "command": "update_content",
                             "content_updates": [{"new_str": "hello\nworld"}],
                             "n": 3},
                 tool_id="c2")

BASH = FeedEvent(kind="call", when=_at(10), session_id=SID, tool="Bash",
                 command="cd src\nuv run pytest -x",
                 tool_input={"command": "cd src\nuv run pytest -x",
                             "description": "run the suite"}, tool_id="c1")

LONG = FeedEvent(kind="file", when=_at(11), session_id=SID, path="src/wide.py",
                 change="modify", added="x = " + "y" * 90, tool_id="t4")

BLOCK = "\n".join(f"line {i} of the block" for i in range(14))


def _rows(events: list[FeedEvent], *, expand: int = 0, width: int = 100,
          wrap: bool = True) -> str:
    """Render one feed entry — the first event plus whatever its Change Run
    absorbs — as the rows it occupies."""
    w = EventWidget(events[0], Theme(depth="truecolor"), 1)
    w.wrap = wrap
    w.gap_seconds = 12.0                  # a visible gap, so the gutter renders
    for ev in events[1:]:
        assert w.absorbs(ev), f"the fixture meant this to fold: {ev}"
        w.absorb(ev, animate=False)
    w.expand_level = expand
    return "".join(r.rstrip() + "\n"
                   for r in w.render_event(False, width).plain.split("\n"))


# --- the Change Run, both witnesses ---------------------------------------

CLAIMED_RUN = """\
 +12s 1▏ ✎  src/alpha.py  modify  +19 −2  ×2                                             ▸ 21 lines
       ▏      − ONE
       ▏      − TWO
       ▏      … ▸ 7 earlier lines
       ▏      + line 2 of the block
       ▏      + line 3 of the block
       ▏      + line 4 of the block
       ▏      + line 5 of the block
       ▏      + line 6 of the block
       ▏      + line 7 of the block
       ▏      + line 8 of the block
       ▏      + line 9 of the block
       ▏      + line 10 of the block
       ▏      + line 11 of the block
       ▏      + line 12 of the block
       ▏      + line 13 of the block
"""

CLAIMED_RUN_EXPANDED = """\
 +12s 1▏ ✎  src/alpha.py  modify  +5 −2  ×2                                                       ▾
       ▏      − ONE
       ▏      − TWO
       ▏      + one
       ▏      + two
       ▏      + three
       ▏      + four
       ▏      + five
"""

OBSERVED_RUN = """\
 +12s ·· ~  notes/todo.md  modify  +4 −1  ~unattributed
      ·       − c
      ·       + a
      ·       + b
      ·       + d
      ·       + e
"""


def test_a_claimed_run_renders_its_tail_with_the_earlier_lines_counted():
    """Three hunks on one file, folded: `×2` counts the *tool calls* the run
    absorbed, the counts are the sum of the body beneath, and the window sits
    on the tail with the earlier lines counted above it (ADR 0004 § the Change
    Run)."""
    assert _rows([_claimed(1, "one\ntwo\nthree", "ONE\nTWO"),
                  _claimed(3, "four\nfive", tool_id="t2"),
                  _claimed(5, BLOCK, tool_id="t2")]) == CLAIMED_RUN


def test_an_expanded_run_shows_every_line_and_counts_nothing():
    """Expanded, there is no window and so nothing to count — the whole
    accumulated body, removed side first."""
    assert _rows([_claimed(1, "one\ntwo\nthree", "ONE\nTWO"),
                  _claimed(3, "four\nfive", tool_id="t2")],
                 expand=1) == CLAIMED_RUN_EXPANDED


def test_a_git_witnessed_run_restates_and_carries_no_multiplier():
    """Git's observation supersedes: the body is the latest whole delta, the
    head is what shows, and there is no `×N` — N would be a fact about the poll
    interval, not about the agent (ADR 0004 § the Change Run). No Session
    claims it, so the row is `~`-marked and the lane reads `··`."""
    assert _rows([_observed(2, "a\nb", "c"),
                  _observed(4, "a\nb\nd\ne", "c")]) == OBSERVED_RUN


def test_a_removed_row_is_washed_to_full_content_width():
    """The wash is a rectangle from the sign column to full content width (ADR
    0004 § the removed-row field), which is why the goldens above are compared
    `rstrip`ed: a removed row is padded out with spaces to carry it."""
    w = EventWidget(_claimed(1, "one", "ONE"), Theme(depth="truecolor"), 1)

    header, removed, added = w.render_event(False, 100).plain.split("\n")

    assert removed.rstrip().endswith("− ONE") and len(removed) == 100
    # the added side is sliced by character count for the typing animation, so
    # it is emitted ragged and carries no wash
    assert added.rstrip().endswith("+ one") and len(added) == len(added.rstrip())
    assert header.rstrip().endswith("+1 −1")


# --- a Call's disclosure and its body -------------------------------------

CALL_HEADER = """\
 +12s 1▏ ⏺  notion-update-page  a-page-id {"command": "update_content", "content_update…  ▸ 5 lines
"""

CALL_BODY = """\
 +12s 1▏ ⏺  notion-update-page  a-page-id {"command": "update_content", "content_updates": [{"n…  ▾
       ▏        page_id  a-page-id
       ▏        command  update_content
       ▏        content_updates[0].new_str  hello
       ▏        world
       ▏        n  3
"""

BASH_BODY = """\
 +12s 1▏ ⏺  $ cd src uv run pytest -x                                                             ▾
       ▏        cd src
       ▏        uv run pytest -x
       ▏        description  run the suite
"""


def test_a_call_advertises_its_body_and_clips_the_argument_around_the_marker():
    """The marker is budgeted before the argument, so the argument is what
    yields to the width (ADR 0004 § Calls)."""
    assert _rows([CALL]) == CALL_HEADER


def test_an_expanded_call_shows_the_whole_input_by_key_path():
    """Every key, by path, with a value's own line break becoming its own row
    and nothing clipped — and never a result (ADR 0004 § Calls)."""
    assert _rows([CALL], expand=1) == CALL_BODY


def test_an_expanded_bash_call_leads_with_its_command_then_its_other_keys():
    """Bash is the one Call whose argument is a shell command, so its lines
    lead the body shell-lexed; the remaining keys follow as ordinary rows, so
    no tool is carved out of "the body is the input" (ADR 0004 § Calls)."""
    assert _rows([BASH], expand=1) == BASH_BODY


# --- fold, don't clip -----------------------------------------------------

FOLDED = """\
 +12s 1▏ ✎  src/wide.py  modify  +1
       ▏      + x =
       ▏      ↳ yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
       ▏      ↳ yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
       ▏      ↳ yyyyyyyyyy
"""

CLIPPED = """\
 +12s 1▏ ✎  src/wide.py  modify  +1
       ▏      + x = yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
"""


def test_a_long_body_line_folds_by_default_and_clips_with_wrap_off():
    """A body line is the change itself and may not be truncated silently, so
    it folds onto further rows carrying the `↳` and the same left columns; wrap
    off gives back the one-row-per-line grid (ADR 0004 § fold, don't clip)."""
    assert _rows([LONG], width=56) == FOLDED
    assert _rows([LONG], width=56, wrap=False) == CLIPPED
