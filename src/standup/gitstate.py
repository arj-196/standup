"""Git state: the Checkout interface, and the Repo Entry built out of it.

Repo identity is `git rev-parse --git-common-dir` (worktrees roll up under
their main checkout; independent clones stay separate).

**One module runs git.** `_git` is the single subprocess call site in Standup,
and every other module — the Attributed Diff, the Watch, the drill-down — asks
a *named question* about a checkout instead of assembling argv: `branch()`,
`status()`, `head_sha()`, `path_diff()`, `commit_meta()`, `unpushed_count()`.
That is the Checkout interface. What it keeps in one place is everything a
caller would otherwise have to re-decide: the porcelain status shape and its
rename arrow, the `\\x1f`-separated commit-log format and its parser, the diff
flags that make output stable (`--no-color --no-ext-diff --find-renames`), and
the failure convention below.

**A question git cannot answer returns nothing, never an exception.** `None`
(or `""`, or `[]`, per the signature) means "git declined" — a missing object,
a directory that is not a repo, a timeout. Callers poll on timers and render
snapshots; a raised error there would turn a transient failure into a crash.
Where "git declined" and "the answer is empty" must be told apart, the return
type says so: `worktrees()` is `None` on failure and a list otherwise, because
the Watch would otherwise read a failed `worktree list` as every worktree
having been removed.
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .models import Checkout, Commit, PendingFile, RepoEntry

SEP = "\x1f"
# One commit, one line, record-separated: the fields every view needs, in the
# only format Standup asks git for. `_parse_commits` is its only reader.
LOG_FORMAT = f"%H{SEP}%h{SEP}%s{SEP}%cI{SEP}%ae{SEP}%an"
LOG_FIELDS = 6
UNPUSHED_CAP = 30
MAX_GIT_WORKERS = 16
# stable diff output: no pager colour, no user's external differ, renames
# detected so a move is not read as a delete plus a create
DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--find-renames")


def _git(path: str, *args: str) -> str | None:
    """Run git in `path` and return stdout, or None if it could not answer.

    The one place Standup shells out. Everything above it is a named question.
    """
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
    """`LOG_FORMAT` records -> Commits, in the order git listed them (newest
    first). A line that is not that format is dropped rather than guessed at."""
    commits = []
    for line in (out or "").splitlines():
        parts = line.split(SEP)
        if len(parts) != LOG_FIELDS:
            continue
        sha, short, subject, ciso, email, name = parts
        try:
            when = datetime.fromisoformat(ciso)
        except ValueError:
            continue
        commits.append(Commit(sha=sha, short=short, subject=subject, when=when,
                              author_email=email, author_name=name))
    return commits


# --------------------------------------------------------------------------
# the Checkout interface: named questions about one checkout
# --------------------------------------------------------------------------


def branch(checkout: str) -> str:
    """The checkout's current branch, or `detached@<short sha>`. `?` when git
    cannot say — the Watch prints this, so it always has to be a string."""
    out = _git(checkout, "symbolic-ref", "--short", "-q", "HEAD")
    if out and out.strip():
        return out.strip()
    sha = _git(checkout, "rev-parse", "--short", "HEAD")
    return f"detached@{sha.strip()}" if sha else "?"


def head_sha(checkout: str) -> str:
    """The full sha at HEAD, `""` when git cannot say."""
    return (_git(checkout, "rev-parse", "HEAD") or "").strip()


def status(checkout: str, *, untracked_all: bool = False) -> dict[str, str]:
    """Pending paths -> their two-letter porcelain code, in git's own order.

    A rename is keyed on its *new* path (git prints `old -> new`), and the
    quoting git applies to unusual names is undone. `untracked_all` asks for
    `-uall`, which lists the files inside an untracked directory individually
    instead of collapsing them to one `dir/` entry.
    """
    args = ["status", "--porcelain"] + (["-uall"] if untracked_all else [])
    out = _git(checkout, *args) or ""
    st: dict[str, str] = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, p = line[:2], line[3:]
        if " -> " in p:      # rename: keep the new path
            p = p.split(" -> ", 1)[1]
        st[p.strip('"')] = code
    return st


def is_untracked(checkout: str, rel: str) -> bool:
    """Whether git has never seen this path — create vs modify for a `Write`,
    which the file itself can no longer answer by the time a log line is read."""
    out = _git(checkout, "status", "--porcelain", "--", rel)
    return (out or "").startswith("??")


def file_at_head(checkout: str, rel: str) -> str | None:
    """The path's content as HEAD has it; None when there is no such blob (a
    brand-new file, or git could not read it)."""
    return _git(checkout, "show", f"HEAD:{rel}")


def path_diff(checkout: str, rel: str, *, context: int) -> str | None:
    """One path's uncommitted diff, as unified text.

    Against `HEAD`, not the index: the inbox counts staged change as Active
    Work, so a view that showed only unstaged change would go blank the moment
    you `git add`.
    """
    return _git(checkout, "diff", f"-U{context}", *DIFF_FLAGS, "HEAD", "--", rel)


def commit_diff(checkout: str, sha: str, *, context: int) -> str | None:
    """One commit's diff, as unified text with no header lines of its own
    (`--format=`). Empty for a merge commit, which git shows no combined diff
    for by default."""
    return _git(checkout, "show", f"-U{context}", *DIFF_FLAGS, "--format=", sha)


def commit_meta(checkout: str, sha: str) -> Commit | None:
    """One commit's sha, subject, time and author. None when this checkout has
    no such commit."""
    commits = _parse_commits(_git(checkout, "show", "-s",
                                  f"--format={LOG_FORMAT}", sha))
    return commits[0] if commits else None


def commits_between(checkout: str, old_sha: str, new_sha: str) -> list[Commit]:
    """The commits `old_sha..new_sha` adds, newest first. Empty when the range
    is empty or git could not walk it — a rebase, an amend or a reset makes the
    old sha unreachable, and the caller decides what to say about that."""
    return _parse_commits(_git(checkout, "log", f"--format={LOG_FORMAT}",
                               f"{old_sha}..{new_sha}"))


def commit_files(toplevel: str, sha: str) -> list[str]:
    """The repo-relative paths one commit touched."""
    out = _git(toplevel, "show", "--name-only", "--format=", sha)
    return [l for l in (out or "").splitlines() if l]


def resolve_commit_sha(checkout: str, prefix: str) -> str | None:
    """A hash prefix -> the full sha of the commit it names in this checkout,
    or None. Commits only: a branch name resolves to nothing here."""
    out = _git(checkout, "rev-parse", "--verify", "--quiet",
               f"{prefix}^{{commit}}")
    return out.strip() if out and out.strip() else None


def unpushed_count(checkout: str) -> int:
    """How many commits are not reachable from any remote ref. Zero when git
    cannot say, and zero for a Remoteless Repo, where nothing is on a remote
    but nothing can be pushed either (ADR 0006 § Decision)."""
    out = _git(checkout, "rev-list", "--count", "HEAD", "--not", "--remotes")
    try:
        return int((out or "").strip())
    except ValueError:
        return 0


def worktrees(toplevel: str) -> list[str] | None:
    """Every checkout of the repo containing `toplevel`, main first — or None
    when git could not answer, which callers must not read as "no worktrees"."""
    out = _git(toplevel, "worktree", "list", "--porcelain")
    if out is None:
        return None
    return [line[len("worktree "):] for line in out.splitlines()
            if line.startswith("worktree ")]


def has_remote(path: str) -> bool:
    """Whether the repo has any remote configured. Repo-level, never
    branch-level: a branch with no upstream in a repo that *does* have a remote
    is genuinely pending a push, and stays in the Unpushed tier (ADR 0006
    § Decision). Worktrees share `git-common-dir`, so this cannot split across
    a Repo Entry."""
    out = _git(path, "remote")
    return bool(out and out.strip())


# --------------------------------------------------------------------------
# the Repo Entry
# --------------------------------------------------------------------------


def _pending(path: str) -> list[PendingFile]:
    return [PendingFile(code=code, path=p)
            for p, code in status(path).items()]


def _unpushed(path: str, remote: bool) -> list[Commit]:
    """Committed-but-not-pushed work. Empty for a Remoteless Repo: with nowhere
    to push, committed is already terminal, so those commits are Done, not
    Needs-Decision (ADR 0006 § Decision)."""
    if not remote:
        return []
    out = _git(path, "log", f"--format={LOG_FORMAT}", "@{upstream}..HEAD")
    if out is None:
        # no upstream: anything not reachable from any remote ref
        out = _git(path, "log", f"--format={LOG_FORMAT}", f"-n{UNPUSHED_CAP}",
                   "HEAD", "--not", "--remotes")
    return _parse_commits(out)[:UNPUSHED_CAP]


def _done(path: str, since: datetime, user_email: str | None,
          remote: bool) -> list[Commit]:
    """My commits that reached their terminal state within the Recent Window.

    Terminal is repo-relative (ADR 0006 § Decision): pushed (reachable from a
    remote ref) for a normal repo, merely committed for a Remoteless Repo —
    `--branches` is the local mirror of `--remotes`, "everywhere this repo's
    work has landed". Do not "fix" this back to an early return: a Remoteless
    Repo has no Unpushed tier, so this is the only place its commits are ever
    reported."""
    scope = "--remotes" if remote else "--branches"
    out = _git(path, "log", f"--format={LOG_FORMAT}",
               f"--since={since.isoformat()}", scope)
    commits = _parse_commits(out)
    if user_email:
        commits = [c for c in commits if c.author_email == user_email]
    return commits[:UNPUSHED_CAP]


def _resolve(cwd: str) -> tuple[str, str] | None:
    """Map a cwd to (main-ish toplevel, repo key = realpath of git-common-dir)."""
    if not os.path.isdir(cwd):
        return None
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    common = _git(cwd, "rev-parse", "--git-common-dir")
    if not toplevel or not common:
        return None
    toplevel = toplevel.strip()
    common = common.strip()
    if not os.path.isabs(common):
        common = os.path.join(toplevel, common)
    return toplevel, os.path.realpath(common)


def _build_entry(toplevel: str, since: datetime) -> RepoEntry:
    listed = worktrees(toplevel) or [toplevel]
    checkouts = [w for w in listed if os.path.isdir(w)]
    main = checkouts[0]
    user_email = (_git(main, "config", "user.email") or "").strip() or None
    # asked once at the repo, not once per worktree: remote config is common
    remote = has_remote(main)
    entry = RepoEntry(name=os.path.basename(main), main_path=main,
                      has_remote=remote)

    seen: set[str] = set()
    for wt in checkouts:
        real = os.path.realpath(wt)
        if real in seen:
            continue
        seen.add(real)
        checkout = Checkout(path=wt, branch=branch(wt), is_main=(wt == main))
        checkout.pending = _pending(wt)
        checkout.unpushed = _unpushed(wt, remote)
        entry.checkouts.append(checkout)

    entry.done = _done(main, since, user_email, remote)
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
