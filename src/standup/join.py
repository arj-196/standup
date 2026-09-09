"""The attribution join: git changes -> Sessions, with honest tiers.

- exact:  commit hash captured in a Session's `git commit` stdout
- likely: file-path overlap with a Session's Edit/Write calls (shown with ~)
File paths are matched by the path itself, not the Session's cwd, so a
Session that edited files outside its own repo attributes correctly.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from . import cache as cache_mod
from . import gitstate
from .models import Attribution, Commit, RepoEntry, Rollup, Session

LIKELY_COMMIT_CAP = 15  # max commits per repo to attribute via file overlap
LIKELY_WINDOW_BEFORE = timedelta(days=7)   # session edit must precede the commit by less than this
LIKELY_WINDOW_AFTER = timedelta(minutes=30)  # small slack for clock skew / amend
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _attr(session: Session, tier: str, when=None) -> Attribution:
    return Attribution(tier=tier, session_id=session.session_id,
                       title=session.title, when=when, agent=session.agent)


def _match_pending(entry: RepoEntry, sessions: list[Session]) -> None:
    for checkout in entry.checkouts:
        for pf in checkout.pending:
            target = os.path.realpath(os.path.join(checkout.path, pf.path))
            is_dir = pf.path.endswith("/")
            for s in sessions:
                best = None
                for fp, ts in s.edited_files.items():
                    rp = os.path.realpath(fp)
                    hit = rp == target or (is_dir and rp.startswith(target + os.sep))
                    if hit and (best is None or ts > best):
                        best = ts
                if best is not None:
                    pf.attributions.append(_attr(s, "likely", best))
            pf.attributions.sort(key=lambda a: a.when or _EPOCH, reverse=True)


def _match_commits_exact(commits: list[Commit], sessions: list[Session]) -> None:
    for c in commits:
        for s in sessions:
            for short, ts in s.commit_hashes.items():
                if c.sha.startswith(short):
                    c.attributions.append(_attr(s, "exact", ts))
                    break
        c.attributions.sort(key=lambda a: a.when or _EPOCH, reverse=True)


def _commit_files(cache, toplevel: str, sha: str) -> list[str]:
    """commit_files(sha), served from / recorded in the Derived Cache (immutable by sha)."""
    def compute() -> list[str]:
        return gitstate.commit_files(toplevel, sha)

    if cache is None:
        return compute()
    return cache.derive(cache_mod.COMMIT_FILES, sha, None, compute=compute,
                        dump=lambda files: files or None)  # never cache an empty result


def _match_commits_likely(entry: RepoEntry, commits: list[Commit],
                          sessions: list[Session], cache=None) -> None:
    budget = LIKELY_COMMIT_CAP
    tops = [os.path.realpath(co.path) for co in entry.checkouts]
    for c in commits:
        if c.attributions or budget <= 0:
            continue
        budget -= 1
        files = set(_commit_files(cache, entry.main_path, c.sha))
        if not files:
            continue
        lo = c.when - LIKELY_WINDOW_BEFORE
        hi = c.when + LIKELY_WINDOW_AFTER
        candidates: list[tuple[float, Session, datetime]] = []
        for s in sessions:
            best: datetime | None = None
            for fp, ts in s.edited_files.items():
                if not (lo <= ts <= hi):
                    continue
                rp = os.path.realpath(fp)
                rel = next((os.path.relpath(rp, t) for t in tops
                            if rp.startswith(t + os.sep)), None)
                if rel not in files:
                    continue
                if best is None or abs((c.when - ts).total_seconds()) < abs((c.when - best).total_seconds()):
                    best = ts
            if best is not None:
                candidates.append((abs((c.when - best).total_seconds()), s, best))
        candidates.sort(key=lambda t: t[0])
        c.attributions.extend(_attr(s, "likely", ts) for _, s, ts in candidates[:3])


def attribute(entries: list[RepoEntry], sessions: list[Session], cache=None) -> None:
    active = [s for s in sessions if s.edited_files or s.commit_hashes]
    for entry in entries:
        _match_pending(entry, active)
        all_commits = [c for co in entry.checkouts for c in co.unpushed] + entry.done
        _match_commits_exact(all_commits, active)
        _match_commits_likely(entry, all_commits, active, cache)


def rollups(entry: RepoEntry) -> list[Rollup]:
    """Invert pending-file attributions into Session Rollups.

    Each file lands in exactly one Rollup — its latest Session (attributions
    are sorted newest-first). Older Sessions that also touched the file survive
    as the `also ~"…"` annotation in the drill-down, not as duplicate Rollups.
    Files no Session explains collect in a trailing unattributed Rollup. Sorted
    by most recent activity, unattributed always last.
    """
    buckets: dict[str, Rollup] = {}
    unattributed = Rollup(session_id=None, title="unattributed")
    for co in entry.checkouts:
        for pf in co.pending:
            if not pf.attributions:
                unattributed.files.append((co.branch, pf))
                continue
            a = pf.attributions[0]  # latest Session wins; one Rollup per file
            r = buckets.get(a.session_id)
            if r is None:
                r = buckets[a.session_id] = Rollup(session_id=a.session_id, title=a.title,
                                                       agent=a.agent)
            r.files.append((co.branch, pf))
            if a.when and (r.last_activity is None or a.when > r.last_activity):
                r.last_activity = a.when
    out = sorted(buckets.values(), key=lambda r: r.last_activity or _EPOCH, reverse=True)
    if unattributed.files:
        out.append(unattributed)
    return out
