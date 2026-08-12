"""Scratch git repos: with and without a remote, with a worktree.

`gitstate` shells out to real `git` and asks it real questions — worktree list,
porcelain status, `@{upstream}..HEAD`, `git remote`. Faking that surface would
be faking the thing under test, so these helpers build actual repositories in a
tmp dir. `git` is the one external binary the suite needs; nothing here touches
the network.

The three shapes that matter to the domain:

* **no remote** — a Remoteless Repo, where committed is already terminal
  (ADR 0006), so its commits are Done and it has no Unpushed tier;
* **with a remote** — a bare repo beside the checkout, standing in for `origin`,
  so a commit is Unpushed until `push()`;
* **with a worktree** — two checkouts sharing one `git-common-dir`, which is the
  identity `gitstate` folds a Repo Entry on.

Every repo carries its own `user.email`/`user.name` in local config: `gitstate`
filters Done commits by the repo's configured email, and the suite's conftest
has already cut global and system config out of the picture.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

USER_NAME = "Standup Tests"
USER_EMAIL = "tests@standup.invalid"


@dataclass
class ScratchRepo:
    """One checkout. A worktree is a `ScratchRepo` too — same operations, a
    different path into the same repository."""

    path: Path
    remote_path: Path | None = None

    def git(self, *args: str) -> str:
        res = subprocess.run(["git", "-C", str(self.path), *args],
                             capture_output=True, text=True, check=True)
        return res.stdout.strip()

    def write(self, rel: str, text: str = "placeholder\n") -> Path:
        """Write a file in the checkout, creating parents. Leaves it untracked
        or modified — i.e. as a pending change — until `commit()`."""
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit(self, message: str = "A commit", *, files: list[str] | None = None) -> str:
        """Stage and commit; return the **short** sha — the one a Session's
        `git commit` stdout announces, and so the join key a fixture Session
        uses to claim this commit."""
        self.git("add", *(files or ["-A"]))
        self.git("commit", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def add_worktree(self, path: Path | str, *, branch: str = "feature") -> "ScratchRepo":
        """Add a worktree on a new branch, and return it as its own checkout."""
        self.git("worktree", "add", "-b", branch, str(path))
        return ScratchRepo(path=Path(path), remote_path=self.remote_path)

    def push(self, *, branch: str | None = None) -> None:
        if self.remote_path is None:
            raise AssertionError("this scratch repo has no remote — make_repo(remote=True)")
        self.git("push", "-u", "origin", branch or self.branch)

    @property
    def branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD")

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    @property
    def short_head(self) -> str:
        return self.git("rev-parse", "--short", "HEAD")


def make_repo(path: Path | str, *, remote: bool = False,
              initial_commit: bool = True) -> ScratchRepo:
    """Create a repo at `path`, on `main`.

    `remote=True` puts a bare repo beside it (`<name>.git`) as `origin` and — if
    there is an initial commit — pushes it, so the checkout starts level with
    its upstream and the *next* commit is the Unpushed one. Without a remote the
    result is a Remoteless Repo (ADR 0006).
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    repo = ScratchRepo(path=path)
    repo.git("init", "-b", "main")
    repo.git("config", "user.name", USER_NAME)
    repo.git("config", "user.email", USER_EMAIL)
    repo.git("config", "commit.gpgsign", "false")

    if remote:
        bare = path.parent / f"{path.name}.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                       capture_output=True, text=True, check=True)
        repo.remote_path = bare
        repo.git("remote", "add", "origin", str(bare))

    if initial_commit:
        repo.write("README.md", "# scratch\n")
        repo.commit("Initial commit")
        if remote:
            repo.push()
    return repo
