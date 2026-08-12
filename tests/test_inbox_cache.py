"""The Derived Cache is a pure accelerator (ADR 0001 § the Derived Cache).

The invariant it stands or falls on: the Triage Inbox prints the same bytes
whether the cache is warm, cold, or deleted. It is easy to break without
noticing — a cached field that the cold path derives slightly differently
shows up as one changed character in one line of one repo — so it is pinned
here at the seam a user reads, `cli.main`'s output.
"""

from __future__ import annotations

import shutil

import pytest

from standup import cli


@pytest.fixture
def one_repo_inbox(scratch_repo, projects_dir, session_log):
    """A Repo Entry with all three tiers in play: a pushed commit (Done), an
    unpushed one, and uncommitted dirt a Session claims."""
    repo = scratch_repo("tt", remote=True)
    repo.write("alpha.py", "print('one')\n")
    sha = repo.commit("Add alpha")
    repo.push()
    repo.write("beta.py", "print('two')\n")
    repo.commit("Add beta")
    repo.write("alpha.py", "print('one, changed')\n")
    session_log(cwd=str(repo.path), sha=sha).save(projects_dir)
    return repo


def _run(capsys, projects_dir, *argv) -> str:
    assert cli.main(["--projects-dir", str(projects_dir), *argv]) == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("argv", [(), ("-a",), ("tt",)],
                         ids=["overview", "retrospective", "drill-down"])
def test_the_inbox_is_byte_identical_warm_cold_and_deleted(
        one_repo_inbox, projects_dir, fake_home, capsys, argv):
    cache_dir = fake_home / ".standup" / "cache"

    cold = _run(capsys, projects_dir, *argv)
    assert cache_dir.is_dir(), "the first run should have written a cache"
    warm = _run(capsys, projects_dir, *argv)
    shutil.rmtree(cache_dir)
    deleted = _run(capsys, projects_dir, *argv)

    assert cold == warm
    assert cold == deleted
    assert "tt" in cold
