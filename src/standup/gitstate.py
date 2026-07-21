"""Git state per repo: discovery, status, unpushed and pushed commits.

Repo identity is `git rev-parse --git-common-dir` (worktrees roll up under
their main checkout; independent clones stay separate).
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from .models import Checkout, Commit, PendingFile, RepoEntry

SEP = "\x1f"
LOG_FORMAT = f"%H{SEP}%h{SEP}%s{SEP}%cI{SEP}%ae"
UNPUSHED_CAP = 30


def git(path: str, *args: str) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _parse_commits(out: str | None) -> list[Commit]:
    commits = []
    for line in (out or "").splitlines():
        parts = line.split(SEP)
        if len(parts) != 5:
            continue
        sha, short, subject, ciso, email = parts
        try:
            when = datetime.fromisoformat(ciso)
        except ValueError:
            continue
        commits.append(Commit(sha=sha, short=short, subject=subject, when=when, author_email=email))
    return commits


def _worktrees(toplevel: str) -> list[str]:
    """All worktree paths of the repo containing `toplevel`; main first."""
    out = git(toplevel, "worktree", "list", "--porcelain")
    paths = []
    for line in (out or "").splitlines():
        if line.startswith("worktree "):
            paths.append(line[len("worktree "):])
    return paths or [toplevel]


def _branch(path: str) -> str:
    out = git(path, "symbolic-ref", "--short", "-q", "HEAD")
    if out and out.strip():
        return out.strip()
    sha = git(path, "rev-parse", "--short", "HEAD")
    return f"detached@{sha.strip()}" if sha else "?"


def _pending(path: str) -> list[tuple[str, str]]:
    out = git(path, "status", "--porcelain")
    items = []
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        code, p = line[:2], line[3:]
        if " -> " in p:  # rename: keep the new path
            p = p.split(" -> ", 1)[1]
        items.append((code, p.strip('"')))
    return items


def _unpushed(path: str) -> list[Commit]:
    out = git(path, "log", f"--format={LOG_FORMAT}", "@{upstream}..HEAD")
    if out is None:
        # no upstream: anything not reachable from any remote ref
        out = git(path, "log", f"--format={LOG_FORMAT}", f"-n{UNPUSHED_CAP}",
                  "HEAD", "--not", "--remotes")
    return _parse_commits(out)[:UNPUSHED_CAP]


def _has_remote(path: str) -> bool:
    out = git(path, "remote")
    return bool(out and out.strip())


def _done(path: str, since: datetime, user_email: str | None) -> list[Commit]:
    """Commits pushed (reachable from a remote ref) since the checkpoint."""
    if not _has_remote(path):
        return []
    out = git(path, "log", f"--format={LOG_FORMAT}",
              f"--since={since.isoformat()}", "--remotes")
    commits = _parse_commits(out)
    if user_email:
        commits = [c for c in commits if c.author_email == user_email]
    return commits[:UNPUSHED_CAP]


def commit_files(toplevel: str, sha: str) -> list[str]:
    out = git(toplevel, "show", "--name-only", "--format=", sha)
    return [l for l in (out or "").splitlines() if l]


def discover_repos(cwds: list[str], since: datetime) -> list[RepoEntry]:
    entries: dict[str, RepoEntry] = {}
    seen_toplevels: set[str] = set()

    for cwd in dict.fromkeys(cwds):  # unique, order-preserving
        if not os.path.isdir(cwd):
            continue
        toplevel = git(cwd, "rev-parse", "--show-toplevel")
        common = git(cwd, "rev-parse", "--git-common-dir")
        if not toplevel or not common:
            continue
        toplevel = toplevel.strip()
        common = common.strip()
        if not os.path.isabs(common):
            common = os.path.join(toplevel, common)
        key = os.path.realpath(common)
        if key in entries:
            continue

        worktrees = [w for w in _worktrees(toplevel) if os.path.isdir(w)]
        main = worktrees[0]
        entry = RepoEntry(name=os.path.basename(main), main_path=main)
        user_email = (git(main, "config", "user.email") or "").strip() or None

        for wt in worktrees:
            real = os.path.realpath(wt)
            if real in seen_toplevels:
                continue
            seen_toplevels.add(real)
            checkout = Checkout(path=wt, branch=_branch(wt), is_main=(wt == main))
            checkout.pending = [PendingFile(code=c, path=p) for c, p in _pending(wt)]
            checkout.unpushed = _unpushed(wt)
            entry.checkouts.append(checkout)

        entry.done = _done(main, since, user_email)
        # drop done commits that are still sitting in an unpushed list (belt & braces)
        unpushed_shas = {c.sha for co in entry.checkouts for c in co.unpushed}
        entry.done = [c for c in entry.done if c.sha not in unpushed_shas]
        entries[key] = entry

    return list(entries.values())
