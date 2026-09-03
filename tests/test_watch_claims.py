"""The Watch's claim stream: what a tailed Session log says it did
(ADR 0004 § the event source).

The other half of the Watch is git's ground truth (`test_watch_git.py`). This
half is read out of the JSONL — and read through the one log reader
(ADR 0001 § the one log reader), so every shape pinned here is a shape the
Transcript and the Attributed Diff read the same way. The Watch's own reading
of a `tool_use` block is gone; what is left is a projection, and these tests
are what says the projection kept every event it used to emit.
"""

from __future__ import annotations

from standup import transcript, universe
from standup.watchstream import WatchStream

from tests.support.sessions import SessionLog


def _events(repo, projects_dir) -> list:
    """The backfill of every Live Session in `repo` — the claim stream with no
    git poll in it."""
    with universe.open_universe(projects_dir) as u:
        return WatchStream.discover(u, str(repo.path)).start()


def test_every_recorded_edit_becomes_a_file_event(scratch_repo, projects_dir):
    """One event per hunk, carrying the text the Session put in and the text it
    took out: an Edit's two sides, a Write's content, a MultiEdit's hunks under
    one tool id, and a NotebookEdit's `new_source` read through the same
    reading. On the backfill pass a Write reads `modify` — the tree has long
    moved on, so the replay does not ask git whether the path was new."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("teach alpha to count")
     .edit(f"{repo.path}/alpha.py", old_string="one\n", new_string="two\n")
     .edit(f"{repo.path}/beta.py", tool="Write", content="print('two')\n")
     .edit(f"{repo.path}/gamma.py", tool="MultiEdit",
           edits=[{"old_string": "a", "new_string": "A"},
                  {"old_string": "b", "new_string": "B"}])
     .edit(f"{repo.path}/nb.ipynb", tool="NotebookEdit", new_source="cell = 1")
     .save(projects_dir))

    files = [e for e in _events(repo, projects_dir) if e.kind == "file"]

    assert [(e.path, e.change, e.added, e.removed) for e in files] == [
        ("alpha.py", "modify", "two\n", "one\n"),
        ("beta.py", "modify", "print('two')\n", ""),
        ("gamma.py", "modify", "A", "a"),
        ("gamma.py", "modify", "B", "b"),
        ("nb.ipynb", "modify", "cell = 1", ""),
    ]
    # the MultiEdit's hunks were one action by the agent, and only the shared
    # tool id remembers that once they are separate events (ADR 0004 § the Change Run)
    gamma = [e.tool_id for e in files if e.path == "gamma.py"]
    assert len(set(gamma)) == 1


def test_a_call_the_log_recorded_no_text_for_narrates_nothing(
        scratch_repo, projects_dir):
    """A MultiEdit whose hunks the reader could not make out still attributes
    its file (ADR 0001 § the one log reader) — but there is no change to show,
    so the feed says nothing rather than drawing an empty one. The path is
    still explained, so the git watcher does not report it as an Unattributed
    Change either."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("rewrite alpha")
     .edit(f"{repo.path}/alpha.py", tool="MultiEdit")
     .save(projects_dir))

    with universe.open_universe(projects_dir) as u:
        ws = WatchStream.discover(u, str(repo.path))
    events = ws.start()

    assert [e for e in events if e.kind == "file"] == []
    assert {p for t in ws.tailers.values() for p in t.edited_paths} == \
        {str((repo.path / "alpha.py").resolve())}


def test_a_call_is_every_tool_that_touches_no_file_and_is_not_silent(
        scratch_repo, projects_dir):
    """Bash carries its shell `command`; another tool carries an `args` digest;
    a local read narrates nothing (ADR 0004 § Calls)."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("look around")
     .call("Read", file_path=f"{repo.path}/alpha.py")
     .call("Bash", command="pytest -q")
     .call("WebFetch", url="https://example.invalid/doc")
     .save(projects_dir))

    calls = [e for e in _events(repo, projects_dir) if e.kind == "call"]

    assert [(e.tool, e.command, e.args) for e in calls] == [
        ("Bash", "pytest -q", ""),
        ("WebFetch", None, "https://example.invalid/doc"),
    ]


def test_a_typed_line_beside_a_tool_result_is_still_a_prompt(
        scratch_repo, projects_dir):
    """The one shape the Watch's prompt reading and the Transcript's parted on,
    settled on the reader's (ADR 0001 § the one log reader): the prose was
    typed, so a Watch that dropped the line lost a real prompt — and the two
    surfaces now answer by construction, not by review.

    It is a prompt in full, so it is a chapter break like any other: the launch
    replay starts *there*, and the call it interrupted stays in the chapter
    above (ADR 0004 § the event source)."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("run the suite")
     .call("Bash", command="pytest")
     .tool_result("2 failed", prose="stop — run the other suite")
     .save(projects_dir))

    events = _events(repo, projects_dir)

    assert [e.message for e in events if e.kind == "prompt"] == \
        ["stop — run the other suite"]
    assert [e.kind for e in events] == ["prompt"]   # the new chapter, alone


def test_the_watch_and_the_transcript_read_prompts_the_same_way(
        scratch_repo, projects_dir):
    """Two surfaces show you what you typed, and one reading answers for both
    (ADR 0001 § the one log reader) — including the shapes that are *not*
    prompts: an injected `isMeta` body nobody typed, and a slash command, which
    reads back as the line you entered rather than the body behind it."""
    repo = scratch_repo("tt")
    log = (SessionLog(cwd=str(repo.path))
           .prompt("run the suite")
           .call("Bash", command="pytest")
           .meta("# Test-Driven Development\nthe injected skill body")
           .tool_result("2 failed", prose="stop — run /tdd instead")
           .save(projects_dir))

    feed = [e.message for e in _events(repo, projects_dir) if e.kind == "prompt"]
    text = transcript.render_transcript(log)

    assert feed == ["stop — run /tdd instead"]
    assert "stop — run /tdd instead" in text
    assert "the injected skill body" not in text


def test_an_interrupt_opens_no_chapter(scratch_repo, projects_dir):
    """`[Request interrupted by user]` is the one non-prompt that reads as
    prose, so the feed used to open a chapter titled with it — a rule crediting
    you with a line you never typed, and a chapter break where the turn merely
    stopped. It is not a prompt in the one reading, so it is no chapter here
    (ADR 0001 § the one log reader)."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("run the suite")
     .call("Bash", command="sleep 600")
     .interrupt()
     .save(projects_dir))

    events = _events(repo, projects_dir)

    assert [e.message for e in events if e.kind == "prompt"] == ["run the suite"]


def test_a_bare_tool_result_is_not_a_prompt(scratch_repo, projects_dir):
    """Injected material is not a prompt: a result with no prose beside it is
    the Session's own machinery, and the feed says nothing about it."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("run the suite")
     .call("Bash", command="pytest")
     .tool_result("all green")
     .save(projects_dir))

    events = _events(repo, projects_dir)

    assert [e.message for e in events if e.kind == "prompt"] == ["run the suite"]
    assert [e.ok for e in events if e.kind == "call_result"] == [True]
