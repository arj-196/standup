"""The Scan Universe module: identity, the scan pipeline, and handle resolution
(ADR 0001 § one module owns the scan).

The seams are the ones the views are allowed to know about — `owner_of`,
`open_universe`, and the `Universe` methods — never the git calls or the cache
rows underneath them.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from standup import cache as cache_mod
from standup import handles, universe


def _real(p) -> str:
    return os.path.realpath(str(p))


def _yesterday() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


# --- Repo Entry identity (CONTEXT.md -> Repo Entry) -------------------------

def test_a_worktree_and_its_main_checkout_are_one_repo_entry(scratch_repo, tmp_path):
    """Identity is git-common-dir, so a worktree rolls up under its main
    checkout — and answers with the main checkout's path, never its own."""
    repo = scratch_repo("tt")
    wt = repo.add_worktree(tmp_path / "wt")

    here = universe.owner_of(str(repo.path))
    inside = universe.owner_of(str(wt.path))

    assert inside.key == here.key
    assert _real(inside.path) == _real(repo.path)
    assert inside.name == "tt"


def test_two_clones_are_two_repo_entries(scratch_repo):
    one = scratch_repo("one")
    two = scratch_repo("two")
    assert universe.owner_of(str(one.path)).key != universe.owner_of(str(two.path)).key


def test_a_directory_outside_git_owns_itself(tmp_path):
    """A cwd with no repo is still a `cost` grouping key — it just isn't a Repo
    Entry, and says so rather than being silently dropped."""
    plain = tmp_path / "notarepo"
    plain.mkdir()

    owner = universe.owner_of(str(plain))

    assert owner.is_repo is False
    assert owner.key == _real(plain)
    assert owner.name == "notarepo"


def test_a_vanished_directory_still_answers(tmp_path):
    owner = universe.owner_of(str(tmp_path / "gone"))
    assert owner.is_repo is False
    assert owner.key == _real(tmp_path / "gone")


def test_no_cwd_has_no_owner():
    assert universe.owner_of(None) is None
    assert universe.owner_of("") is None


# --- the scan pipeline ------------------------------------------------------

def test_the_pipeline_discovers_and_attributes_in_one_call(
        scratch_repo, projects_dir, session_log):
    """open cache -> scan sessions -> discover repos -> attribute, behind one
    method: a view asks for Repo Entries and gets them attributed."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    session_log(cwd=str(repo.path)).save(projects_dir)

    with universe.open_universe(projects_dir) as u:
        (entry,) = u.entries(_yesterday())

    assert entry.name == "tt"
    (pending,) = entry.checkouts[0].pending
    assert pending.path == "alpha.py"
    assert [a.tier for a in pending.attributions] == ["likely"]


def test_the_pipeline_is_walked_once_per_command(scratch_repo, projects_dir, session_log):
    """Two views' worth of questions, one scan: `entries` and `sessions` are the
    same pipeline, not two of them."""
    repo = scratch_repo("tt")
    session_log(cwd=str(repo.path)).save(projects_dir)

    since = _yesterday()
    with universe.open_universe(projects_dir) as u:
        first = u.entries(since)
        assert u.entries(since) is first
        assert u.sessions() is u.sessions()


def test_missing_logs_are_one_named_failure(tmp_path):
    with pytest.raises(universe.UniverseError) as e:
        with universe.open_universe(tmp_path / "nowhere"):
            pass
    assert "no Claude Code logs found" in str(e.value)


def test_the_cache_is_flushed_even_when_the_view_raises(projects_dir, session_log):
    """The Derived Cache is a pure accelerator, so a view that blew up must
    still leave it warm — a flush skipped on the error path costs the next run
    a full reparse and nothing says so (ADR 0001 § the Derived Cache)."""
    log = session_log(cwd="/tmp/tt").save(projects_dir)

    with pytest.raises(RuntimeError):
        with universe.open_universe(projects_dir) as u:
            u.sessions()
            raise RuntimeError("the view blew up")

    st = log.stat()
    warm = cache_mod.open_cache()
    assert warm.get_session(log.stem, st.st_size, st.st_mtime_ns) is not None


# --- resolution, without a git checkout where the domain allows it ----------

def test_a_project_handle_resolves_without_touching_git(projects_dir, session_log):
    """`handles` knows no git: resolution is name matching over Targets, so a
    project whose directory does not exist is still addressable."""
    session_log(cwd="/tmp/ProjectManagement",
                session_id="11111111-0000-4000-8000-000000000001").save(projects_dir)
    session_log(cwd="/tmp/standup",
                session_id="22222222-0000-4000-8000-000000000002").save(projects_dir)

    with universe.open_universe(projects_dir) as u:
        assert u.resolve_repo("pm").name == "ProjectManagement"
        assert u.resolve_repo("st").name == "standup"
        with pytest.raises(handles.HandleError):
            u.resolve_repo("nope")


def test_a_session_handle_resolves_without_touching_git(projects_dir, session_log):
    session_log(cwd="/tmp/tt",
                session_id="3b0a693b-0000-4000-8000-000000000001").save(projects_dir)

    with universe.open_universe(projects_dir) as u:
        assert u.resolve_session("3b0a").stem.startswith("3b0a693b")


def test_a_path_resolves_to_the_repo_entry_that_owns_it(
        scratch_repo, tmp_path, projects_dir, session_log):
    repo = scratch_repo("tt")
    wt = repo.add_worktree(tmp_path / "wt")
    session_log(cwd=str(repo.path)).save(projects_dir)

    with universe.open_universe(projects_dir) as u:
        assert u.resolve_repo(str(wt.path)).name == "tt"


def test_the_newest_session_of_a_repo_entry_includes_its_worktrees(
        scratch_repo, tmp_path, projects_dir, session_log):
    """A worktree's Session is the Repo Entry's Session — worktrees roll up
    (CONTEXT.md -> Repo Entry)."""
    repo = scratch_repo("tt")
    wt = repo.add_worktree(tmp_path / "wt")
    older = session_log(cwd=str(repo.path),
                        session_id="11111111-0000-4000-8000-000000000001")
    older.start = datetime.now(timezone.utc) - timedelta(hours=5)
    older.__post_init__()
    older.prompt("older").save(projects_dir)
    session_log(cwd=str(wt.path),
                session_id="22222222-0000-4000-8000-000000000002").save(projects_dir)

    with universe.open_universe(projects_dir) as u:
        session, where = u.newest_session_in("tt")

    assert session.session_id.startswith("22222222")
    assert where == "tt"


def test_standing_outside_the_scan_universe_is_an_error(projects_dir, monkeypatch, tmp_path):
    """No fallback to "newest session anywhere": a transcript from an unrelated
    repo is exactly the silent misdirection every view is built to avoid."""
    plain = tmp_path / "notarepo"
    plain.mkdir()
    monkeypatch.chdir(plain)

    with universe.open_universe(projects_dir) as u:
        with pytest.raises(universe.UniverseError) as e:
            u.newest_session_in(None)
    assert "not inside a git repo" in str(e.value)
