"""`gitstate`: the Checkout interface, and what a Repo Entry says about a repo.

Two things are pinned here. The **Checkout interface** — the named questions
every other module asks about one checkout ("current branch", "diff of this
path", "this commit's files and subject", "unpushed count") — is the only place
porcelain formats, record separators and flag choices live; no other module
issues git argv. And the **Remoteless Repo** rule (ADR 0006 § Decision), which
is a property of the repository and therefore of every one of its checkouts at
once.

Everything runs against real scratch repos: faking git's answers here would be
faking the thing under test.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import standup
from standup import gitstate

SRC = Path(standup.__file__).parent


def _yesterday() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


# --- the Checkout interface ----------------------------------------------


def test_git_subprocesses_have_exactly_one_calling_module():
    """Every other module asks a named question. A module that assembles git
    argv of its own has to re-decide the porcelain format, the separator and
    the failure convention — and the three of them drifting apart is how two
    dialects of "what git said" start."""
    offenders = {}
    for py in sorted(SRC.glob("*.py")):
        text = py.read_text()
        raw_argv = re.findall(r"""subprocess\.\w+\(\s*\[\s*["']git["']""", text)
        if py.name == "gitstate.py":
            assert len(raw_argv) == 1, "gitstate runs git from one place"
            continue
        hits = raw_argv + re.findall(r"gitstate\._?git\(", text)
        if hits:
            offenders[py.name] = hits
    assert offenders == {}


def test_the_current_branch_is_a_named_question(scratch_repo):
    repo = scratch_repo("tt")

    assert gitstate.branch(str(repo.path)) == "main"

    repo.git("checkout", "-b", "feature")
    assert gitstate.branch(str(repo.path)) == "feature"

    repo.git("checkout", "--detach")
    assert gitstate.branch(str(repo.path)) == f"detached@{repo.short_head}"


def test_head_is_a_named_question(scratch_repo, tmp_path):
    repo = scratch_repo("tt")

    assert gitstate.head_sha(str(repo.path)) == repo.head
    # a directory git cannot answer for is empty, never an exception: the Watch
    # polls on a timer and a transient failure must not read as a change
    assert gitstate.head_sha(str(tmp_path / "nowhere")) == ""


def test_status_answers_with_each_pending_path_and_its_code(scratch_repo):
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("alpha.py", "print('two')\n")
    repo.write("pkg/beta.py", "print('three')\n")
    repo.git("add", "alpha.py")

    assert gitstate.status(str(repo.path)) == {"alpha.py": "M ", "pkg/": "??"}
    # -uall lists files inside an untracked directory individually, which is
    # what the Watch needs to narrate one of them changing
    assert gitstate.status(str(repo.path), untracked_all=True) == {
        "alpha.py": "M ", "pkg/beta.py": "??"}


def test_an_untracked_path_is_distinguishable_from_a_modified_one(scratch_repo):
    """A `Write` names a path the file already exists at by the time the log is
    read, so only git can say whether the tool created it."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("alpha.py", "print('two')\n")
    repo.write("beta.py", "print('three')\n")

    assert gitstate.is_untracked(str(repo.path), "beta.py") is True
    assert gitstate.is_untracked(str(repo.path), "alpha.py") is False


def test_a_file_can_be_read_as_head_left_it(scratch_repo):
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("alpha.py", "print('two')\n")

    assert gitstate.file_at_head(str(repo.path), "alpha.py") == "print('one')\n"
    assert gitstate.file_at_head(str(repo.path), "beta.py") is None


def test_a_paths_diff_is_taken_against_head(scratch_repo):
    """`diff HEAD`, so staged and unstaged change both appear — staging your
    work must not blank the Attributed Diff."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("alpha.py", "print('one')\nprint('two')\n")
    repo.git("add", "alpha.py")

    out = gitstate.path_diff(str(repo.path), "alpha.py", context=0)

    assert out is not None
    assert [ln for ln in out.splitlines() if ln.startswith(("+", "-"))
            and not ln.startswith(("+++", "---"))] == ["+print('two')"]
    assert gitstate.path_diff(str(repo.path), "nope.py", context=0) == ""


def test_a_commit_is_read_through_one_record_separated_format(scratch_repo):
    """One format, one parser: a commit read on its own carries the same fields
    as one read out of a log range."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    short = repo.commit("Add alpha")

    c = gitstate.commit_meta(str(repo.path), short)

    assert c is not None
    assert (c.sha, c.short, c.subject) == (repo.head, short, "Add alpha")
    assert (c.author_email, c.author_name) == ("tests@standup.invalid",
                                               "Standup Tests")
    assert c.when.tzinfo is not None
    assert gitstate.commit_meta(str(repo.path), "deadbee") is None


def test_commits_between_two_heads_are_newest_first(scratch_repo):
    repo = scratch_repo("tt")
    base = repo.head
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")
    repo.write("beta.py", "print('two')\n")
    repo.commit("Add beta")

    commits = gitstate.commits_between(str(repo.path), base, repo.head)

    assert [c.subject for c in commits] == ["Add beta", "Add alpha"]
    assert gitstate.commits_between(str(repo.path), repo.head, repo.head) == []


def test_a_commits_files_are_a_named_question(scratch_repo):
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.write("beta.py", "print('two')\n")
    short = repo.commit("Add two files")

    assert gitstate.commit_files(str(repo.path), short) == ["alpha.py", "beta.py"]

    out = gitstate.commit_diff(str(repo.path), short, context=0)
    assert out is not None and "+print('one')" in out


def test_a_hash_prefix_resolves_to_its_commit_or_to_nothing(scratch_repo):
    """The `@abc1234` the drill-down prints, back to a full sha. A branch name
    is not a commit hash and must not resolve here (ADR 0005 § two grammars)."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    short = repo.commit("Add alpha")

    assert gitstate.resolve_commit_sha(str(repo.path), short) == repo.head
    assert gitstate.resolve_commit_sha(str(repo.path), "deadbee") is None


def test_the_unpushed_count_falls_when_the_work_is_pushed(scratch_repo):
    repo = scratch_repo("tt", remote=True)
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")

    assert gitstate.unpushed_count(str(repo.path)) == 1

    repo.push()
    assert gitstate.unpushed_count(str(repo.path)) == 0


def test_worktrees_are_listed_main_first_and_a_failure_says_so(scratch_repo,
                                                               tmp_path):
    """`None` is git declining to answer, which is not the same as "no
    worktrees" — the Watch must not read a transient failure as removals
    (ADR 0004 § the worktree lane)."""
    repo = scratch_repo("tt")
    wt = repo.add_worktree(tmp_path / "tt-feature", branch="feature")

    assert gitstate.worktrees(str(repo.path)) == [str(repo.path), str(wt.path)]
    assert gitstate.worktrees(str(tmp_path / "nowhere")) is None


# --- the Remoteless Repo rule (ADR 0006 § Decision) -----------------------


def test_a_remoteless_repo_has_no_unpushed_tier(scratch_repo):
    """With nowhere to push, committing is the last action available, so
    committed work is Done rather than Needs-Decision."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")

    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())

    assert entry.has_remote is False
    assert entry.checkouts[0].unpushed == []
    assert [c.subject for c in entry.done] == ["Add alpha", "Initial commit"]


def test_the_rule_is_repo_level_not_branch_level(scratch_repo, tmp_path):
    """A branch with no upstream in a repo that *does* have a remote is
    genuinely pending a push: it keeps the Unpushed tier, and its commits stay
    out of Done. Worktrees share one `git-common-dir`, so the classification
    cannot split across a Repo Entry."""
    repo = scratch_repo("tt", remote=True)
    wt = repo.add_worktree(tmp_path / "tt-feature", branch="feature")
    wt.write("beta.py", "print('two')\n")
    subject = "Work with no upstream"
    wt.commit(subject)

    (entry,) = gitstate.discover_repos([str(repo.path), str(wt.path)],
                                       _yesterday())

    assert entry.has_remote is True
    main, feature = entry.checkouts
    assert (main.branch, feature.branch) == ("main", "feature")
    assert main.unpushed == []
    assert [c.subject for c in feature.unpushed] == [subject]
    assert subject not in [c.subject for c in entry.done]


def test_every_checkout_of_a_remoteless_repo_agrees(scratch_repo, tmp_path):
    """The remote question is asked once at the repo, so a worktree cannot
    disagree with its main checkout — and `--branches` is what makes the
    worktree's commits reachable as Done."""
    repo = scratch_repo("tt")
    wt = repo.add_worktree(tmp_path / "tt-feature", branch="feature")
    wt.write("beta.py", "print('two')\n")
    wt.commit("Work in a worktree")

    (entry,) = gitstate.discover_repos([str(repo.path), str(wt.path)],
                                       _yesterday())

    assert entry.has_remote is False
    assert [co.unpushed for co in entry.checkouts] == [[], []]
    assert [c.subject for c in entry.done] == ["Work in a worktree",
                                               "Initial commit"]


def test_adding_a_remote_flips_committed_work_back_to_unpushed(scratch_repo,
                                                               tmp_path):
    """Not adding a remote is a decision; adding one is too. A push is now
    possible and pending, so the same commit moves back to Needs-Decision."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    subject = "Add alpha"
    repo.commit(subject)

    bare = tmp_path / "origin.git"
    repo.git("init", "--bare", "-b", "main", str(bare))
    repo.git("remote", "add", "origin", str(bare))

    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())

    assert entry.has_remote is True
    assert [c.subject for c in entry.checkouts[0].unpushed] == [subject,
                                                                "Initial commit"]
    assert entry.done == []
