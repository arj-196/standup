"""The Watch sees worktrees natively (ADR 0004 § the worktree lane).

Claude Code runs agents in worktrees it creates mid-run under
`.claude/worktrees/`, and writes each agent's transcript one level below the
top-level Session logs. Both halves of the Watch have to notice: the claim
stream (a subagent transcript becomes its own lane) and the ground truth (a
checkout that appears mid-watch gets polled, one that vanishes is let go).
"""

from __future__ import annotations

from standup import claude_logs, universe
from standup.watchstream import WatchStream

from tests.support.sessions import SessionLog, fixture_session

AGENT_ID = "ab12cd34ef567890a"


def _watch(repo, projects_dir) -> WatchStream:
    """A WatchStream over the Scan Universe, which the Watch reads once at
    launch and does not hold open (the Derived Cache is closed before the feed
    runs)."""
    with universe.open_universe(projects_dir) as u:
        return WatchStream(u, str(repo.path))


def _worktree_agent(repo, projects_dir, *, agent_id=AGENT_ID,
                    description="Implement issue #1 in worktree",
                    mid_turn=False):
    """A parent Session in the main checkout, plus a subagent transcript whose
    cwd is a `.claude/worktrees/…` worktree of the same repo. Returns the
    worktree checkout."""
    wt = repo.add_worktree(repo.path / ".claude" / "worktrees" / f"agent-{agent_id}",
                           branch=f"wt-{agent_id[:4]}")
    parent = fixture_session(cwd=str(repo.path))
    parent.save(projects_dir)
    sub = (SessionLog(session_id=agent_id, cwd=str(wt.path), sidechain=True)
           .prompt("You are implementing issue #1 in an isolated git worktree.")
           .edit(f"{wt.path}/alpha.py", mid_turn=mid_turn))
    sub.save_subagent(projects_dir, parent, description=description)
    return wt


# --- the claim stream: subagent transcripts become lanes -----------------


def test_a_worktree_agents_transcript_is_a_lane(scratch_repo, projects_dir):
    """A subagent transcript already on disk at launch is tailed like any Live
    Session: addressed by the agent id, titled by the spawn description from
    its `.meta.json` (ADR 0004 § the worktree lane)."""
    repo = scratch_repo("tt")
    _worktree_agent(repo, projects_dir)

    ws = _watch(repo, projects_dir)

    assert AGENT_ID in ws.tailers
    assert ws.tailers[AGENT_ID].subagent is True
    assert ws.tailers[AGENT_ID].session.title == "Implement issue #1 in worktree"


def test_a_worktree_edit_resolves_against_the_worktree_root(
        scratch_repo, projects_dir):
    """Deepest root wins: an edit inside a worktree nested under the main
    checkout reads `alpha.py`, never `.claude/worktrees/…/alpha.py` relative to
    main (ADR 0004 § the worktree lane)."""
    repo = scratch_repo("tt")
    _worktree_agent(repo, projects_dir)

    ws = _watch(repo, projects_dir)
    events = ws.start()

    claimed = [e for e in events if e.kind == "file" and e.session_id == AGENT_ID]
    assert [e.path for e in claimed] == ["alpha.py"]


def test_a_subagent_transcript_appearing_mid_watch_gets_a_lane(
        scratch_repo, projects_dir):
    """The whole point: the Watch is already open when the agent (and its
    worktree) is spawned. One discovery pass adopts the checkout first, then
    finds the transcript — so the lane's paths resolve against the worktree
    from its first event (ADR 0004 § the worktree lane)."""
    repo = scratch_repo("tt")
    ws = _watch(repo, projects_dir)

    _worktree_agent(repo, projects_dir)
    ws._last_discovery = float("-inf")
    events = ws.poll()

    assert any(e.kind == "worktree" and e.message.endswith(f"appeared — wt-{AGENT_ID[:4]}")
               for e in events)
    assert any(e.kind == "session" and e.session_id == AGENT_ID for e in events)
    assert [e.path for e in events
            if e.kind == "file" and e.session_id == AGENT_ID] == ["alpha.py"]


def test_sidechain_lines_drive_a_subagent_lanes_activity(
        scratch_repo, projects_dir):
    """A subagent transcript is all `isSidechain` lines. In its own lane they
    are the session's activity; in a *parent* log the sidechain skip stands
    (ADR 0004 § the worktree lane)."""
    repo = scratch_repo("tt")
    _worktree_agent(repo, projects_dir, mid_turn=True)
    # a parent-log line marked sidechain: some other agent's work, not this lane's
    inline = (SessionLog(session_id="9" * 36, cwd=str(repo.path), sidechain=True)
              .prompt("inline sidechain")
              .edit(f"{repo.path}/beta.py", mid_turn=True))
    inline.save(projects_dir)

    ws = _watch(repo, projects_dir)
    ws.start()

    assert ws.tailers[AGENT_ID].act_verb == "writing"
    assert ws.tailers["9" * 36].act_verb is None


def test_the_inbox_scanner_still_ignores_subagent_transcripts(
        scratch_repo, projects_dir, null_cache):
    """Subagent lanes are a Watch-only discovery: the Scan Universe (and with
    it the Triage Inbox and `cost`) keeps reading top-level Session logs alone
    (ADR 0004 § the worktree lane)."""
    repo = scratch_repo("tt")
    _worktree_agent(repo, projects_dir)

    sessions = claude_logs.scan_sessions(projects_dir, null_cache)

    assert [s.session_id for s in sessions] == [fixture_session().session_id]


# --- the ground truth: checkouts come and go mid-watch --------------------


def test_a_worktree_created_mid_watch_is_adopted_and_polled(
        scratch_repo, projects_dir):
    """The checkout list is re-asked on the discovery cadence: a worktree that
    appears mid-watch joins the watched set (one `worktree` event), and dirt in
    it is narrated by the git watcher from then on (ADR 0004 § the worktree
    lane)."""
    repo = scratch_repo("tt")
    ws = _watch(repo, projects_dir)
    assert len(ws.checkouts) == 1

    wt = repo.add_worktree(repo.path / ".claude" / "worktrees" / "agent-x",
                           branch="wt-x")
    ws._last_discovery = float("-inf")
    events = ws.poll()

    assert [e.message for e in events if e.kind == "worktree"] == \
        ["agent-x appeared — wt-x"]
    assert len(ws.checkouts) == 2

    wt.write("gamma.py", "x = 1\n")
    ws._last_git = float("-inf")
    events = ws.poll()

    (ev,) = [e for e in events if e.kind == "file"]
    assert (ev.path, ev.change, ev.session_id) == ("gamma.py", "create", None)
    assert ev.added == "x = 1"


def test_a_removed_worktree_is_let_go(scratch_repo, projects_dir):
    repo = scratch_repo("tt")
    wt = repo.add_worktree(repo.path / ".claude" / "worktrees" / "agent-x",
                           branch="wt-x")
    ws = _watch(repo, projects_dir)
    assert len(ws.checkouts) == 2

    repo.git("worktree", "remove", "--force", str(wt.path))
    ws._last_discovery = float("-inf")
    events = ws.poll()

    assert [e.message for e in events if e.kind == "worktree"] == ["agent-x removed"]
    assert len(ws.checkouts) == 1
    assert ws.git.dirty_count() == 0
