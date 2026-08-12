"""The Watch's ground truth: what git tells it, and the events that come out
(ADR 0004 § the event source).

The claim stream is one half of the Watch; this is the other. Every question in
here is asked of a real scratch repo through `gitstate`'s Checkout interface —
dirty paths, the branch, HEAD, a commit's files, the unpushed count — so a
change in how the question is *phrased* to git has to leave these events alone.
"""

from __future__ import annotations

from standup import universe
from standup.watchstream import PUSH_POLL_EVERY, WatchStream


def _watch(repo, projects_dir) -> WatchStream:
    """A WatchStream over the Scan Universe, which the Watch reads once at
    launch and does not hold open (the Derived Cache is closed before the feed
    runs)."""
    with universe.open_universe(projects_dir) as u:
        return WatchStream(u, str(repo.path))


def _git_poll(ws) -> list:
    """One ground-truth pass, with no tailed Session narrating anything."""
    return ws.git.poll(set(), lambda sha: None)


def test_a_dirty_file_is_narrated_as_its_own_delta(scratch_repo, projects_dir):
    repo = scratch_repo("tt")
    ws = _watch(repo, projects_dir)

    repo.write("alpha.py", "print('one')\n")
    (ev,) = [e for e in _git_poll(ws) if e.kind == "file"]

    assert (ev.path, ev.change, ev.added, ev.removed) == \
        ("alpha.py", "create", "print('one')", "")

    repo.write("alpha.py", "print('one')\nprint('two')\n")
    (ev,) = [e for e in _git_poll(ws) if e.kind == "file"]

    # the whole delta since the reference snapshot, not since the last poll —
    # and still a creation, because the reference is an empty untracked file
    assert (ev.change, ev.added, ev.removed) == \
        ("create", "print('one')\nprint('two')", "")


def test_dirt_that_predates_the_watch_is_old_news(scratch_repo, projects_dir):
    """Adoption seeds silently — only what happens after the Watch opens counts."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")

    ws = _watch(repo, projects_dir)

    assert ws.git.dirty_count() == 1
    assert [e for e in _git_poll(ws) if e.kind == "file"] == []


def test_a_commit_is_an_event_carrying_its_own_files(scratch_repo, projects_dir):
    """Committed change reads exactly like uncommitted change: the same
    added/removed blocks, under a commit header carrying the subject and the
    short sha the drill-down prints."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    ws = _watch(repo, projects_dir)

    repo.write("alpha.py", "print('one')\nprint('two')\n")
    repo.write("beta.py", "print('three')\n")
    _git_poll(ws)                       # the dirty pass, before the commit
    short = repo.commit("Teach alpha to count")

    (ev,) = [e for e in _git_poll(ws) if e.kind == "commit"]

    assert (ev.sha, ev.message, ev.session_id) == (short, "Teach alpha to count", None)
    assert [(f.path, f.change, f.added, f.removed) for f in ev.files] == [
        ("alpha.py", "modify", "print('two')", ""),
        ("beta.py", "create", "print('three')", ""),
    ]


def test_a_commit_is_attributed_to_the_session_that_claimed_its_sha(
        scratch_repo, projects_dir):
    repo = scratch_repo("tt")
    ws = _watch(repo, projects_dir)
    repo.write("alpha.py", "print('one')\n")
    short = repo.commit("Add alpha")

    (ev,) = [e for e in ws.git.poll(set(), lambda sha: "sid-1"
                                    if sha.startswith(short) else None)
             if e.kind == "commit"]

    assert ev.session_id == "sid-1"


def test_a_branch_switch_is_an_event(scratch_repo, projects_dir):
    repo = scratch_repo("tt")
    ws = _watch(repo, projects_dir)

    repo.git("checkout", "-b", "feature")

    assert [e.message for e in _git_poll(ws) if e.kind == "branch"] == \
        ["main → feature"]
    assert ws.git.main_branch() == "feature"


def test_a_push_is_counted_off_the_unpushed_count(scratch_repo, projects_dir):
    """The push check runs on its own slower cadence, and names the branch
    rather than a remote: the count comes from `--not --remotes`, which does not
    say which remote took the commits."""
    repo = scratch_repo("tt", remote=True)
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    ws = _watch(repo, projects_dir)
    assert ws.git.unpushed[str(repo.path)] == 1

    repo.push()
    for _ in range(PUSH_POLL_EVERY):
        events = _git_poll(ws)

    (ev,) = [e for e in events if e.kind == "push"]
    assert ev.message == "main  1 commit"
    assert ws.git.unpushed[str(repo.path)] == 0


def test_a_remoteless_repo_never_reports_a_push(scratch_repo, projects_dir):
    """`standup watch` is deliberately unchanged by ADR 0006 § Consequences:
    the count is internal, and with no remote `--not --remotes` excludes
    nothing and so never falls. Add a remote and push mid-watch and the event
    still fires, correctly — which is why the Watch was not special-cased."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    ws = _watch(repo, projects_dir)

    for _ in range(PUSH_POLL_EVERY * 2):
        assert [e for e in _git_poll(ws) if e.kind == "push"] == []
