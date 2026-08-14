"""The Watch stream's own seam: a stream built from resolved values, and the
two answers it publishes — `poll()` and `vitals()`
(ADR 0004 § discovery is an entry point, not the constructor).

The other Watch test files each pin one witness: the claim stream
(`test_watch_claims.py`), git's ground truth (`test_watch_git.py`), the
worktree lane (`test_watch_worktrees.py`). This one pins the *stream object* —
that it can be built without a Scan Universe, that one `poll()` folds both
witnesses into one chronological answer, and that `vitals()` carries everything
the UI needs without reaching into the stream's own state.

Everything here runs against temp JSONL under a temp `projects_dir` plus a
scratch git repo: no `~/.claude`, no Derived Cache, no `claude` binary.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from standup import cache as cache_mod
from standup import claude_logs, universe
from standup.watchstream import LIVE_THRESHOLD, WatchStream

from tests.support.sessions import SessionLog


def _sessions(projects_dir: Path):
    """Every Session in the temp log tree, read the way the Watch's caller
    reads them — through the one log reader, over no cache at all."""
    return claude_logs.scan_sessions(projects_dir, cache_mod.NullCache())


def _stream(repo, projects_dir: Path, **kwargs) -> WatchStream:
    """A WatchStream from resolved values: this repo's checkout, these Sessions,
    this log directory. The plain constructor is the whole fixture surface —
    nothing here resolves a Project Handle or opens the Derived Cache."""
    return WatchStream(name=repo.path.name, checkouts=[str(repo.path)],
                       sessions=_sessions(projects_dir),
                       projects_dir=projects_dir, **kwargs)


# --- discovery is the entry point, not the constructor --------------------


def test_a_stream_is_built_from_resolved_checkouts_and_sessions(
        scratch_repo, projects_dir):
    """No Universe: the constructor takes the checkouts and the Sessions
    somebody else resolved, and tails the ones that ran in this Repo Entry
    inside the Live window (ADR 0004 § discovery is an entry point, not the
    constructor)."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("teach alpha to count")
     .edit(f"{repo.path}/alpha.py", old_string="one\n", new_string="two\n")
     .save(projects_dir))
    # a Session of another repo entirely: resolved, handed over, and not tailed
    (SessionLog(session_id="9" * 36, cwd="/tmp/elsewhere")
     .prompt("somewhere else").save(projects_dir))

    ws = _stream(repo, projects_dir)

    assert list(ws.tailers) == [SessionLog().session_id]
    assert [e.path for e in ws.start() if e.kind == "file"] == ["alpha.py"]


def test_discovery_resolves_the_same_stream_through_the_universe(
        scratch_repo, projects_dir):
    """`discover()` is the launch path: a Project Handle (here a checkout path)
    and the Scan Universe in, the same stream out — so the two entry points
    cannot drift (ADR 0004 § discovery is an entry point, not the
    constructor)."""
    repo = scratch_repo("tt")
    SessionLog(cwd=str(repo.path)).prompt("teach alpha to count").save(projects_dir)

    with universe.open_universe(projects_dir) as u:
        ws = WatchStream.discover(u, str(repo.path))

    assert ws.name == "tt"
    assert ws.checkouts == [str(repo.path)]
    assert list(ws.tailers) == [SessionLog().session_id]
    assert ws.projects_dir == projects_dir


def test_a_settled_session_is_out_of_the_window_until_it_is_widened(
        scratch_repo, projects_dir):
    """One window decides both which Sessions are picked up and which ones the
    header calls live, and `--since` widens it — the Watch's one recency claim
    (CONTEXT.md → Live Session)."""
    repo = scratch_repo("tt")
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    log = SessionLog(cwd=str(repo.path), start=old).prompt("long done").save(projects_dir)
    # a Session's recency is its log's mtime — when the log last grew
    # (ADR 0001 § the one log reader), so ageing the fixture means ageing the file
    os.utime(log, (old.timestamp(), old.timestamp()))

    assert _stream(repo, projects_dir).tailers == {}
    widened = _stream(repo, projects_dir, live_window=timedelta(hours=4))
    assert list(widened.tailers) == [SessionLog().session_id]
    assert widened.live_window == timedelta(hours=4)
    assert _stream(repo, projects_dir).live_window == LIVE_THRESHOLD


# --- poll(): both witnesses, one answer -----------------------------------


def test_one_poll_folds_the_claim_stream_and_the_ground_truth(
        scratch_repo, projects_dir):
    """A poll reads what the tailed logs appended *and* what git can see, and
    hands back both — the claims-vs-truth split the whole Watch is built on
    (ADR 0004 § the event source). A path a Session claimed is narrated once, by
    the Session; the unclaimed one is the git watcher's, and unattributed."""
    repo = scratch_repo("tt")
    log = SessionLog(cwd=str(repo.path)).prompt("teach alpha to count")
    log.save(projects_dir)
    ws = _stream(repo, projects_dir)
    ws.start()

    # the Session claims one file, a human dirties another, and both land in the
    # same window — the tail is appended to the *same* log the Watch is tailing
    repo.write("alpha.py", "print('one')\n")
    repo.write("beta.py", "print('two')\n")
    log.edit(f"{repo.path}/alpha.py", tool="Write", content="print('one')\n")
    log.save(projects_dir)
    ws._last_git = float("-inf")
    events = ws.poll()

    claimed = [e for e in events if e.kind == "file" and e.session_id]
    witnessed = [e for e in events if e.kind == "file" and e.session_id is None]
    assert [(e.path, e.added) for e in claimed] == [("alpha.py", "print('one')\n")]
    assert [(e.path, e.change, e.added) for e in witnessed] == \
        [("beta.py", "create", "print('two')")]
    # the git watcher restates a path's whole delta; a Session's claim adds one
    # hunk to it (ADR 0004 § the Change Run)
    assert [e.restates for e in claimed] == [False]
    assert [e.restates for e in witnessed] == [True]


def test_a_session_log_appearing_mid_watch_gets_a_lane(scratch_repo, projects_dir):
    """Discovery runs on its own cadence while the Watch is open: a log that did
    not exist at launch is announced once, then tailed from the top — it is all
    fresh."""
    repo = scratch_repo("tt")
    ws = _stream(repo, projects_dir)

    (SessionLog(session_id="7" * 36, cwd=str(repo.path))
     .prompt("a session that started after the watch")
     .edit(f"{repo.path}/alpha.py", old_string="one\n", new_string="two\n")
     .save(projects_dir))
    ws._last_discovery = float("-inf")
    events = ws.poll()

    assert [(e.kind, e.session_id) for e in events if e.kind == "session"] == \
        [("session", "7" * 36)]
    assert [e.path for e in events if e.kind == "file"] == ["alpha.py"]
    assert ws.session_num("7" * 36) == 1


def test_quiet_drops_everything_that_is_not_a_change(scratch_repo, projects_dir):
    """`-q`: files, commits, pushes and Unattributed Changes only — no Calls, no
    prompts, no session marks."""
    repo = scratch_repo("tt")
    (SessionLog(cwd=str(repo.path))
     .prompt("teach alpha to count")
     .call("Bash", command="pytest -q")
     .edit(f"{repo.path}/alpha.py", old_string="one\n", new_string="two\n")
     .save(projects_dir))

    kinds = {e.kind for e in _stream(repo, projects_dir, quiet=True).start()}

    assert kinds == {"file"}


# --- vitals: everything the header needs, published -----------------------


def test_vitals_state_the_repo_its_branch_and_its_dirt(scratch_repo, projects_dir):
    repo = scratch_repo("tt")
    SessionLog(cwd=str(repo.path)).prompt("teach alpha to count").save(projects_dir)
    repo.write("alpha.py", "print('one')\n")

    v = _stream(repo, projects_dir).vitals()

    assert (v.repo, v.path, v.branch, v.dirty) == ("tt", str(repo.path), "main", 1)


def test_a_live_session_row_carries_its_lane_number_handle_and_log(
        scratch_repo, projects_dir):
    """A `LiveSessionInfo` is the whole of what the header (and the `s` key)
    needs about a Session — including the log path, so opening a Transcript
    never means reaching into the stream's tailers
    (ADR 0004 § discovery is an entry point, not the constructor)."""
    repo = scratch_repo("tt")
    log = (SessionLog(cwd=str(repo.path)).ai_title("Teach alpha to count")
           .prompt("teach alpha to count").save(projects_dir))

    v = _stream(repo, projects_dir).vitals()

    (ls,) = v.live
    assert (ls.num, ls.handle, ls.title) == (1, SessionLog().session_id[:8],
                                             "Teach alpha to count")
    assert Path(ls.log_path) == log
    assert v.last is ls or v.last.session_id == ls.session_id


def test_the_last_appended_session_is_named_even_when_nothing_is_live(
        scratch_repo, projects_dir):
    """The quiet header states the absence with a handle, so a widened window is
    what turns that Session into a lane rather than the only way to name it."""
    repo = scratch_repo("tt")
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    log = SessionLog(cwd=str(repo.path)).prompt("long done").save(projects_dir)
    os.utime(log, (old.timestamp(), old.timestamp()))

    v = _stream(repo, projects_dir, live_window=timedelta(hours=4)).vitals()
    assert [ls.session_id for ls in v.live] == [SessionLog().session_id]

    ws = _stream(repo, projects_dir)
    ws._add_tailer(_sessions(projects_dir)[0])   # tailed, but long out of window
    v = ws.vitals()
    assert v.live == []
    assert v.last is not None and v.last.session_id == SessionLog().session_id


def test_a_widened_live_window_is_published_as_a_fact(scratch_repo, projects_dir):
    """The header states a widened window, always — it is why a Session that
    went quiet an hour ago has a lane. The *fact* travels on Vitals, so the UI
    neither imports `LIVE_THRESHOLD` nor re-derives the comparison
    (ADR 0004 § discovery is an entry point, not the constructor)."""
    repo = scratch_repo("tt")

    assert _stream(repo, projects_dir).vitals().widened_window is None
    assert _stream(repo, projects_dir,
                  live_window=timedelta(hours=2)).vitals().widened_window == \
        timedelta(hours=2)


def test_a_tailed_sessions_info_is_published_by_id(scratch_repo, projects_dir):
    """What the `s` key asks: the Session it is about, live or settled, and
    None for one this Watch never tailed."""
    repo = scratch_repo("tt")
    log = SessionLog(cwd=str(repo.path)).prompt("teach alpha").save(projects_dir)

    ws = _stream(repo, projects_dir)

    info = ws.session_info(SessionLog().session_id)
    assert info is not None and Path(info.log_path) == log
    assert ws.session_info("9" * 36) is None
