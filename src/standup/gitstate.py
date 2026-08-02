"""Git state per repo: discovery, status, unpushed and done commits.

Repo identity is `git rev-parse --git-common-dir` (worktrees roll up under
their main checkout; independent clones stay separate).
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .models import Checkout, Commit, PendingFile, RepoEntry

SEP = "\x1f"
LOG_FORMAT = f"%H{SEP}%h{SEP}%s{SEP}%cI{SEP}%ae"
UNPUSHED_CAP = 30
MAX_GIT_WORKERS = 16


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


def _unpushed(path: str, has_remote: bool) -> list[Commit]:
    """Committed-but-not-pushed work. Empty for a Remoteless Repo: with nowhere
    to push, committed is already terminal, so those commits are Done, not
    Needs-Decision (ADR 0010)."""
    if not has_remote:
        return []
    out = git(path, "log", f"--format={LOG_FORMAT}", "@{upstream}..HEAD")
    if out is None:
        # no upstream: anything not reachable from any remote ref
        out = git(path, "log", f"--format={LOG_FORMAT}", f"-n{UNPUSHED_CAP}",
                  "HEAD", "--not", "--remotes")
    return _parse_commits(out)[:UNPUSHED_CAP]


def _has_remote(path: str) -> bool:
    """Whether the repo has any remote configured. Repo-level, never
    branch-level: a branch with no upstream in a repo that *does* have a remote
    is genuinely pending a push, and stays in the Unpushed tier (ADR 0010).
    Worktrees share `git-common-dir`, so this cannot split across a Repo Entry."""
    out = git(path, "remote")
    return bool(out and out.strip())


def _done(path: str, since: datetime, user_email: str | None,
          has_remote: bool) -> list[Commit]:
    """My commits that reached their terminal state within the Recent Window.

    Terminal is repo-relative (ADR 0010): pushed (reachable from a remote ref)
    for a normal repo, merely committed for a Remoteless Repo — `--branches` is
    the local mirror of `--remotes`, "everywhere this repo's work has landed".
    Do not "fix" this back to an early return: a Remoteless Repo has no Unpushed
    tier, so this is the only place its commits are ever reported."""
    scope = "--remotes" if has_remote else "--branches"
    out = git(path, "log", f"--format={LOG_FORMAT}",
              f"--since={since.isoformat()}", scope)
    commits = _parse_commits(out)
    if user_email:
        commits = [c for c in commits if c.author_email == user_email]
    return commits[:UNPUSHED_CAP]


def commit_files(toplevel: str, sha: str) -> list[str]:
    out = git(toplevel, "show", "--name-only", "--format=", sha)
    return [l for l in (out or "").splitlines() if l]


def _resolve(cwd: str) -> tuple[str, str] | None:
    """Map a cwd to (main-ish toplevel, repo key = realpath of git-common-dir)."""
    if not os.path.isdir(cwd):
        return None
    toplevel = git(cwd, "rev-parse", "--show-toplevel")
    common = git(cwd, "rev-parse", "--git-common-dir")
    if not toplevel or not common:
        return None
    toplevel = toplevel.strip()
    common = common.strip()
    if not os.path.isabs(common):
        common = os.path.join(toplevel, common)
    return toplevel, os.path.realpath(common)


def _build_entry(toplevel: str, since: datetime) -> RepoEntry:
    worktrees = [w for w in _worktrees(toplevel) if os.path.isdir(w)]
    main = worktrees[0]
    user_email = (git(main, "config", "user.email") or "").strip() or None
    # asked once at the repo, not once per worktree: remote config is common
    has_remote = _has_remote(main)
    entry = RepoEntry(name=os.path.basename(main), main_path=main,
                      has_remote=has_remote)

    seen: set[str] = set()
    for wt in worktrees:
        real = os.path.realpath(wt)
        if real in seen:
            continue
        seen.add(real)
        checkout = Checkout(path=wt, branch=_branch(wt), is_main=(wt == main))
        checkout.pending = [PendingFile(code=c, path=p) for c, p in _pending(wt)]
        checkout.unpushed = _unpushed(wt, has_remote)
        entry.checkouts.append(checkout)

    entry.done = _done(main, since, user_email, has_remote)
    # drop done commits that are still sitting in an unpushed list (belt & braces)
    unpushed_shas = {c.sha for co in entry.checkouts for c in co.unpushed}
    entry.done = [c for c in entry.done if c.sha not in unpushed_shas]
    return entry


def discover_repos(cwds: list[str], since: datetime) -> list[RepoEntry]:
    unique_cwds = list(dict.fromkeys(cwds))  # order-preserving

    def _pool(items, fn):
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=min(MAX_GIT_WORKERS, len(items))) as ex:
            return list(ex.map(fn, items))

    # Phase 1: resolve cwds -> repo keys in parallel, then dedup keeping first-seen order.
    resolved = _pool(unique_cwds, _resolve)
    order: list[str] = []
    top_of_key: dict[str, str] = {}
    for res in resolved:
        if not res:
            continue
        toplevel, key = res
        if key not in top_of_key:
            top_of_key[key] = toplevel
            order.append(key)

    # Phase 2: build each repo entry in parallel; reassemble in first-seen order.
    entries = _pool(order, lambda key: _build_entry(top_of_key[key], since))
    return [e for e in entries if e is not None]
