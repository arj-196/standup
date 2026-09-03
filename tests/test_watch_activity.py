"""The Activity State: what the tail of a Session's log says the agent is doing
*now* (ADR 0004 § the Activity State).

Read through the stream's published state, never off a tailer, because the state
as published is the state *plus* the display floor — a reading of the log
against the clock — and the floor is the half no other Watch test could see.

The clock is monkeypatched rather than waited on: `_now` is the one place the
stream asks what time it is, so pinning it is what makes a one-second rule
testable in a millisecond. Everything runs over temp JSONL plus a scratch repo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from standup import cache as cache_mod
from standup import claude_logs, watchstream
from standup.watchstream import ACT_FLOOR, Activity, WatchStream

from tests.support.sessions import SessionLog

T0 = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
STEP = timedelta(milliseconds=200)      # a real log's gap, not a fixture minute
LATER = timedelta(seconds=30)           # long past the display floor


def _log(repo, session_id: str = "1" * 36) -> SessionLog:
    """A Session in `repo`, its lines a fifth of a second apart from T0."""
    return SessionLog(cwd=str(repo.path), session_id=session_id, start=T0,
                      step=STEP)


def _watch(repo, projects_dir, monkeypatch, at: timedelta) -> WatchStream:
    """A started Watch over every log written so far, read `at` after T0.

    Started and read at the same instant: backfill walks the whole file and
    leaves the state at the true tail, so a session mid-turn at launch is
    already in the right state before its first live line arrives.
    """
    monkeypatch.setattr(watchstream, "_now", lambda: T0 + at)
    sessions = claude_logs.scan_sessions(projects_dir, cache_mod.NullCache())
    ws = WatchStream(name=repo.path.name, checkouts=[str(repo.path)],
                     sessions=sessions, projects_dir=Path(projects_dir),
                     live_window=timedelta(days=365))
    ws.start()
    return ws


def _activity(repo, projects_dir, monkeypatch, log: SessionLog, *,
              at: timedelta = LATER) -> Activity | None:
    """One log's published Activity State — None when the turn is over."""
    log.save(projects_dir)
    info = _watch(repo, projects_dir, monkeypatch, at).session_info(log.session_id)
    assert info is not None, "the lane should exist even with no activity"
    return info.activity


# --- the verbs the log states ---------------------------------------------


def test_a_pending_tool_call_is_read_as_one_word(scratch_repo, projects_dir,
                                                 monkeypatch):
    """The verb is a fact off the log: the tool the turn stopped on, mapped to
    one word. A local read narrates nothing the feed can show and still answers
    `reading` here; an unmapped tool falls to `acting`, which is true of
    anything — so a new or MCP tool never needs a table entry to stay honest."""
    repo = scratch_repo("tt")
    logs = {
        "reading": _log(repo, "a" * 36).prompt("go")
        .call("Read", mid_turn=True, file_path=f"{repo.path}/alpha.py"),
        "writing": _log(repo, "b" * 36).prompt("go")
        .edit(f"{repo.path}/alpha.py", tool="Write", content="x = 1\n",
              mid_turn=True),
        "running": _log(repo, "c" * 36).prompt("go")
        .call("Bash", mid_turn=True, command="pytest -q"),
        "acting": _log(repo, "d" * 36).prompt("go")
        .call("mcp__notion__notion-fetch", mid_turn=True, page_id="abc"),
    }
    for log in logs.values():
        log.save(projects_dir)

    ws = _watch(repo, projects_dir, monkeypatch, LATER)

    assert {verb: ws.session_info(log.session_id).activity.verb
            for verb, log in logs.items()} == {v: v for v in logs}


def test_thinking_is_inferred_from_silence(scratch_repo, projects_dir, monkeypatch):
    """The one verb no line ever states. A tool result with no assistant line
    after it leaves the model composing, and the state's age is the age of that
    line — the pause is all there is to read."""
    repo = scratch_repo("tt")
    log = (_log(repo).prompt("write alpha")
           .call("Bash", mid_turn=True, command="pytest -q")
           .tool_result("ok"))

    act = _activity(repo, projects_dir, monkeypatch, log)

    assert act.verb == "thinking"
    assert act.since == T0 + 3 * STEP        # the tool_result line, not `now`


def test_a_prompt_with_no_answer_yet_is_thinking(scratch_repo, projects_dir,
                                                 monkeypatch):
    repo = scratch_repo("tt")
    act = _activity(repo, projects_dir, monkeypatch, _log(repo).prompt("what changed?"))

    assert (act.verb, act.since) == ("thinking", T0 + STEP)


def test_a_settled_turn_has_no_activity_at_all(scratch_repo, projects_dir,
                                               monkeypatch):
    """Absence is the answer: any `stop_reason` other than `tool_use` ends the
    turn, and the Watch then says nothing about the session."""
    repo = scratch_repo("tt")
    log = (_log(repo).prompt("write alpha")
           .call("Bash", mid_turn=True, command="pytest -q")
           .tool_result("ok")
           .turn("Done."))

    assert _activity(repo, projects_dir, monkeypatch, log) is None


def test_a_line_naming_no_tool_leaves_the_state_where_it_was(
        scratch_repo, projects_dir, monkeypatch):
    """`stop_reason` belongs to the whole assistant *message*, whose blocks are
    flushed as separate lines: a preamble text block carries `tool_use` while
    naming no tool. Reading the stop reason alone made every one of those
    `acting` — a verb reserved for a tool absent from the table."""
    repo = scratch_repo("tt")
    log = (_log(repo).prompt("write alpha")
           .call("Bash", mid_turn=True, command="pytest -q")
           .turn("Let me check the failure.", mid_turn=True))

    act = _activity(repo, projects_dir, monkeypatch, log)

    assert act.verb == "running"              # not `acting`
    assert act.since == T0 + 2 * STEP         # and not the later line's age


# --- an interrupt settles the state ---------------------------------------


def test_an_interrupt_settles_the_state(scratch_repo, projects_dir, monkeypatch):
    """Esc, a mid-turn shutdown and a refused tool call all arrive as plain
    user lines, and must be read before the prompt reading: their text is
    `[Request interrupted by user]`, which would otherwise look like a question
    — and the state would read `thinking` for as long as the Watch stays open."""
    repo = scratch_repo("tt")
    esc = (_log(repo, "e" * 36).prompt("write alpha")
           .call("Bash", mid_turn=True, command="sleep 600").interrupt())
    refused = (_log(repo, "f" * 36).prompt("write alpha")
               .call("Bash", mid_turn=True, command="sleep 600")
               .interrupt(text="[Request interrupted by user for tool use]"))

    assert _activity(repo, projects_dir, monkeypatch, esc) is None
    assert _activity(repo, projects_dir, monkeypatch, refused) is None


def test_an_interrupt_settles_the_state_without_its_retired_tag(
        scratch_repo, projects_dir, monkeypatch):
    """The interrupt used to be read off `interruptedMessageId` /
    `interruptedByShutdown` alone — tags almost no real interrupt line carries
    (2 of 79 across a machine's logs), which left interrupted sessions stuck
    reading `thinking` until they aged out of the Live window. Both shapes
    settle: a log that has the tag is not wrong
    (ADR 0004 § the Activity State)."""
    repo = scratch_repo("tt")
    untagged = (_log(repo, "a" * 36).prompt("write alpha")
                .call("Bash", mid_turn=True, command="sleep 600").interrupt())
    tagged = (_log(repo, "b" * 36).prompt("write alpha")
              .call("Bash", mid_turn=True, command="sleep 600")
              .interrupt(tagged=True))
    quit_ = (_log(repo, "c" * 36).prompt("write alpha")
             .call("Bash", mid_turn=True, command="sleep 600")
             .interrupt(shutdown=True, tagged=True))

    assert _activity(repo, projects_dir, monkeypatch, untagged) is None
    assert _activity(repo, projects_dir, monkeypatch, tagged) is None
    assert _activity(repo, projects_dir, monkeypatch, quit_) is None


# --- the display floor ----------------------------------------------------


def test_a_tool_verb_holds_the_band_against_thinking_for_the_floor(
        scratch_repo, projects_dir, monkeypatch):
    """Without the floor `reading` and `writing` were unobservable — a local
    `Read` returns in ~25ms, below the poll interval, so the band read `thinking`
    in nearly every frame. The verb holds for ACT_FLOOR, and the age shown is
    the verb's real age, never the floor's."""
    repo = scratch_repo("tt")
    log = (_log(repo).prompt("write alpha")
           .edit(f"{repo.path}/alpha.py", tool="Write", content="x = 1\n",
                 mid_turn=True)
           .tool_result("ok"))
    tool_line, result_line = T0 + 2 * STEP, T0 + 3 * STEP

    within = _activity(repo, projects_dir, monkeypatch, log,
                       at=2 * STEP + ACT_FLOOR / 2)
    assert (within.verb, within.since) == ("writing", tool_line)

    past = _activity(repo, projects_dir, monkeypatch, log,
                     at=2 * STEP + ACT_FLOOR * 2)
    assert (past.verb, past.since) == ("thinking", result_line)


def test_the_floor_yields_to_a_turn_that_is_over(scratch_repo, projects_dir,
                                                 monkeypatch):
    """The floor delays staler silence, never fresher news: a settled turn
    blanks the band the instant the agent hands control back, even inside the
    floor's window."""
    repo = scratch_repo("tt")
    log = (_log(repo).prompt("write alpha")
           .edit(f"{repo.path}/alpha.py", tool="Write", content="x = 1\n",
                 mid_turn=True)
           .tool_result("ok")
           .turn("Done."))

    assert _activity(repo, projects_dir, monkeypatch, log,
                     at=2 * STEP + ACT_FLOOR / 2) is None


def test_the_next_tool_verb_overwrites_the_held_one(scratch_repo, projects_dir,
                                                    monkeypatch):
    repo = scratch_repo("tt")
    log = (_log(repo).prompt("write alpha")
           .edit(f"{repo.path}/alpha.py", tool="Write", content="x = 1\n",
                 mid_turn=True)
           .tool_result("ok")
           .call("Bash", mid_turn=True, command="pytest -q"))

    act = _activity(repo, projects_dir, monkeypatch, log,
                    at=4 * STEP + ACT_FLOOR / 2)

    assert (act.verb, act.since) == ("running", T0 + 4 * STEP)
