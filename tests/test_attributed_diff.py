"""The Attributed Diff over a scratch repo (ADR 0007 § Decision).

`standup <repo> diff` is the one view that reads git's *diff structure* —
hunks, context, line numbers — rather than a status line. Everything it renders
comes back from `git` through `gitstate`, so these tests are also the guard
that the Checkout interface asks git the same questions the raw argv did:
uncommitted work is read as `diff HEAD` (staged change included), and a commit
is read as its own `show`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from standup import cache as cache_mod
from standup import claude_logs, diffview, gitstate, join
from standup.fragments import LIKELY, UNACCOUNTED, Matcher

from tests.support.sessions import SessionLog

SID = "1a2b3c4d-0000-4000-8000-000000000001"
HANDLE = SID[:8]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed(repo, projects_dir) -> None:
    """A committed `alpha.py`, an uncommitted second line, and a Session whose
    log records the edit that produced it."""
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("alpha.py", "print('one')\nprint('two')\n")
    (SessionLog(session_id=SID, cwd=str(repo.path))
     .prompt("teach alpha to count")
     .ai_title("Teach alpha to count")
     .edit(str(repo.path / "alpha.py"),
           old_string="print('one')\n",
           new_string="print('one')\nprint('two')\n")
     ).save(projects_dir)


def _scan(projects_dir, cache):
    """The inbox's own pipeline, as `standup <repo> diff` runs it."""
    sessions = claude_logs.scan_sessions(projects_dir, cache)
    entries = gitstate.discover_repos([s.cwd for s in sessions if s.cwd],
                                      _now() - timedelta(days=7))
    join.attribute(entries, sessions, cache)
    return entries[0], {s.session_id: s for s in sessions}


def test_uncommitted_work_is_grouped_and_attributed(scratch_repo, projects_dir,
                                                    null_cache):
    """One Session's uncommitted hunk, under that Session's header: the group
    comes from the drill-down's Rollups, the verdict from the log's own edit
    fragments (ADR 0007 § Decision)."""
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)
    entry, by_id = _scan(projects_dir, null_cache)

    groups = diffview.build_active(entry, by_id, Matcher(null_cache))

    (g,) = groups
    assert (g.session_id, g.handle) == (SID, HANDLE)
    (fb,) = g.files
    assert (fb.path, fb.change, fb.code) == ("alpha.py", "modify", " M")
    assert (fb.added, fb.removed) == (1, 0)
    (hb,) = fb.blocks
    assert hb.hunk.added == ["print('two')"]
    assert (hb.verdict.tier, hb.verdict.session_ids) == (LIKELY, (SID,))


def test_a_call_whose_hunks_are_unreadable_still_names_a_file_a_session_touched(
        scratch_repo, projects_dir, null_cache):
    """A MultiEdit whose `edits[]` the reader cannot make out still says the
    Session touched the file (ADR 0001 § the one log reader), and hunk
    attribution reads the same blocks: the honest verdict is `unaccounted` —
    a change in a file a Session *did* touch, matched by nothing — not
    `unattributed`, which claims no Session ever touched the path."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("alpha.py", "print('one')\nprint('two')\n")
    (SessionLog(session_id=SID, cwd=str(repo.path))
     .prompt("teach alpha to count")
     .edit(str(repo.path / "alpha.py"), tool="MultiEdit")
     ).save(projects_dir)
    entry, by_id = _scan(projects_dir, null_cache)

    groups = diffview.build_active(entry, by_id, Matcher(null_cache))

    (hb,) = groups[0].files[0].blocks
    assert hb.verdict.tier == UNACCOUNTED
    assert hb.verdict.partial is False


def test_attribution_reads_the_same_hunks_warm_and_cold(scratch_repo, projects_dir):
    """The Derived Cache is a pure accelerator (ADR 0001 § the Derived Cache):
    the fragments a hunk is matched against are a projection of the cached
    reading, so a warm run must produce the same verdicts as a cold one."""
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)

    def _verdicts():
        cache = cache_mod.open_cache()
        entry, by_id = _scan(projects_dir, cache)
        groups = diffview.build_active(entry, by_id, Matcher(cache))
        out = [(b.verdict.tier, b.verdict.session_ids)
               for g in groups for fb in g.files for b in fb.blocks]
        cache.flush()
        return out

    cold = _verdicts()
    assert cold == [(LIKELY, (SID,))]
    assert _verdicts() == cold


def test_staged_work_still_shows_up(scratch_repo, projects_dir, null_cache):
    """`diff HEAD`, never `diff`: staging your work must not blank the view."""
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)
    repo.git("add", "alpha.py")

    entry, by_id = _scan(projects_dir, null_cache)
    groups = diffview.build_active(entry, by_id, Matcher(null_cache))

    (fb,) = groups[0].files
    assert (fb.path, fb.code, fb.added) == ("alpha.py", "M ", 1)
    assert fb.blocks[0].hunk.added == ["print('two')"]


def test_an_untracked_file_diffs_against_empty(scratch_repo, projects_dir,
                                               null_cache):
    """git cannot diff a file it does not know about, so the view synthesises
    the diff from its content rather than reporting "no diff available"."""
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)
    repo.write("beta.py", "print('three')\n")
    entry, by_id = _scan(projects_dir, null_cache)

    groups = diffview.build_active(entry, by_id, Matcher(null_cache))

    files = {fb.path: fb for g in groups for fb in g.files}
    assert files["beta.py"].change == "create"
    assert files["beta.py"].blocks[0].hunk.added == ["print('three')"]


def test_a_commit_is_addressed_by_hash_and_read_as_its_own_diff(
        scratch_repo, projects_dir, null_cache):
    """`standup tt diff @abc1234`: the header carries the commit's own subject,
    author and time, and the body is the commit's files (ADR 0005 § two
    grammars)."""
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)
    short = repo.commit("Teach alpha to count")
    entry, by_id = _scan(projects_dir, null_cache)

    checkout, sha = diffview.resolve_commit(entry, f"@{short}")
    cd = diffview.build_commit(entry, checkout, sha, by_id, Matcher(null_cache))

    assert checkout == str(repo.path)
    assert sha == repo.head
    assert (cd.sha, cd.short, cd.subject) == (repo.head, short,
                                              "Teach alpha to count")
    assert cd.author == "Standup Tests"
    assert cd.when is not None and cd.when.tzinfo is not None
    (fb,) = cd.files
    assert (fb.path, fb.change, fb.added, fb.removed) == ("alpha.py", "modify", 1, 0)
    assert fb.blocks[0].hunk.added == ["print('two')"]


def test_a_hash_that_names_no_commit_here_is_an_error(scratch_repo,
                                                      projects_dir, null_cache):
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)
    entry, _ = _scan(projects_dir, null_cache)

    for ref in ("@deadbee", "@main"):
        try:
            diffview.resolve_commit(entry, ref)
        except diffview.DiffError:
            continue
        raise AssertionError(f"{ref} should not resolve")


def test_the_rendered_view_says_what_it_read(scratch_repo, projects_dir,
                                             null_cache, monkeypatch):
    """The whole text, plainly: the repo header (`· no remote`, carried
    unconditionally per ADR 0006 § Decision), the
    Session header, the file line and the hunk body with its line numbers."""
    monkeypatch.setenv("NO_COLOR", "1")
    repo = scratch_repo("tt")
    _seed(repo, projects_dir)
    entry, by_id = _scan(projects_dir, null_cache)
    groups = diffview.build_active(entry, by_id, Matcher(null_cache))

    text = diffview.render(entry, groups, _now(),
                           titles={SID: "Teach alpha to count"}, width=80)

    lines = [ln.rstrip() for ln in text.splitlines()]
    assert lines[0].startswith("tt  ")
    assert lines[0].endswith("· no remote")
    assert lines[2].startswith(f'{HANDLE}  ~ "Teach alpha to count"')
    assert "  alpha.py  modify  +1" in lines[3]
    assert [ln for ln in lines if "print('two')" in ln] == \
        ["      2 │ + print('two')"]
