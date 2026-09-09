"""Hunk-level attribution: which Session authored *this* change (ADR 0007).

The inbox attributes at file granularity by **path overlap** — a reliable test
that answers "did this session ever touch this file". A diff needs a sharper
question, because a file two sessions touched renders as one body of code and
the drill-down's model (each file belongs to its *latest* session) would print
200 lines of session A's work under session B's header. At file granularity
that mistake is a filename; at diff granularity it is a misattributed change.

So the diff view matches **content**. A session's log records the actual text of
every edit it made (`Edit.old_string`/`new_string`, `Write.content`,
`MultiEdit.edits[]`, `NotebookEdit.new_source`), and those fragments are
compared against the hunk in front of you.

The matcher is deliberately **verbatim** — a contiguous, exact line block,
trailing whitespace stripped, no similarity scoring. Fuzzy matching is the
mechanism that produces confident wrong attributions, and a confident wrong
attribution is worse than an admitted gap. So the matcher either recognises a
block or says it cannot account for it.

Five verdicts, one of which is new to the vocabulary:

- **likely** — every run in the hunk is accounted for, all by one session. A
  claim, `~`-marked like every other claim.
- **shared** — every run is accounted for, but by two or more sessions. The
  hunk genuinely contains more than one session's work and cannot be split at
  this granularity, so the view declines to split it.
- **unaccounted** — some run is accounted for by nobody, in a file a session
  *did* touch. This is **not** an Unattributed Change and must never be
  rendered as one: it is either a hand edit or a false negative (the agent
  wrote it, then something reformatted it and the fragment no longer matches
  verbatim), and the matcher cannot tell those apart. Naming it separately is
  the honest move — and it happens to flag exactly the hunks worth a look.
- **unattributed** — no session ever touched this path. Reliable, because it
  rests on the path-overlap test rather than on content matching.

One further strictness, not a softening: when the change being attributed is a
**commit**, only fragments recorded at or before the commit's own timestamp count
as evidence. A session that writes the same lines a day later cannot have
authored that commit, and without the bound it is reported as a co-author of one.

The index is a **projection** of the one log reader's edit blocks
(ADR 0001 § the one log reader), not a second reading of the log: `norm()` and
`realpath` are all this module adds. The reading itself is what the Derived
Cache holds, so there is no fragment index to keep in step with it. The *match*
is never cached: it runs against the live working tree, which ADR 0001 § the
Derived Cache keeps out of the cache entirely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import logs

# Verdicts (CONTEXT.md -> Attribution Tier).
LIKELY, SHARED, UNACCOUNTED, UNATTRIBUTED = (
    "likely", "shared", "unaccounted", "unattributed")


def norm(text: str) -> tuple[str, ...]:
    """A fragment or run as comparable lines: trailing whitespace stripped, a
    trailing newline not counted as a line. Leading whitespace is *kept* —
    indentation is content, and two identically-worded lines at different
    depths are different lines."""
    lines = [ln.rstrip() for ln in (text or "").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return tuple(lines)


@dataclass(frozen=True)
class Fragment:
    """One recorded edit: the text a session put in, and the text it took out.
    `new` is empty for a pure deletion, `old` for a Write or a create.

    `when` (epoch seconds, None when the log line carried no timestamp) exists
    for one job: an edit made *after* a commit cannot have produced that
    commit's content. Without it, a session that later happened to write the
    same lines is reported as a co-author of a commit it could not have touched.
    Using it makes attribution strictly *stricter*, never fuzzier.
    """
    path: str            # realpath of the file the edit targeted
    new: tuple[str, ...]
    old: tuple[str, ...]
    when: float | None = None


def for_session(log_path: Path, cache=None) -> dict[str, list[Fragment]]:
    """A session's edit fragments, grouped by realpath.

    The reader's `EditBlock`s, projected: comparable lines (`norm`) under a
    resolved path. Reading through `read_log` means the log is parsed once per
    change however many views ask for it, and that a shape the reader knows
    about cannot be missing here — a MultiEdit whose hunks it could not read
    still contributes its path, so a file the inbox attributes is a file this
    index has heard of.
    """
    parsed = (logs.read_log(log_path, cache) if cache is not None
              else logs.parse_log(log_path))
    real: dict[str, str] = {}         # one realpath call per distinct path
    out: dict[str, list[Fragment]] = {}
    for e in parsed.edits:
        if e.path not in real:
            real[e.path] = os.path.realpath(e.path)
        path = real[e.path]
        out.setdefault(path, []).append(Fragment(
            path, norm(e.new), norm(e.old),
            e.when.timestamp() if e.when else None))
    return out


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """Is `needle` a contiguous block of `haystack`? Exact, never fuzzy."""
    n, h = len(needle), len(haystack)
    if n == 0 or n > h:
        return False
    first = needle[0]
    for i in range(h - n + 1):
        if haystack[i] == first and haystack[i:i + n] == needle:
            return True
    return False


def _eligible(frags: list[Fragment], before: float | None) -> list[Fragment]:
    """Fragments that could have produced the change being attributed.

    `before` is a commit's timestamp (plus slack): an edit recorded after the
    commit cannot be in it. For working-tree change there is no such bound —
    every recorded edit is a candidate — so `before` is None there."""
    if before is None:
        return frags
    return [f for f in frags if f.when is None or f.when <= before]


def _accounts_for(frags: list[Fragment], sign: str, run: tuple[str, ...]) -> bool:
    """Does any of this session's fragments contain `run` verbatim? An added run
    is looked for in what the session wrote, a removed run in what it replaced."""
    for f in frags:
        if _contains(f.new if sign == "+" else f.old, run):
            return True
    return False


@dataclass
class Verdict:
    """One hunk's attribution: a tier plus the sessions it names.

    `partial` marks an `unaccounted` verdict that *did* account for some of the
    hunk — the view says so rather than implying the whole hunk is a mystery."""
    tier: str
    session_ids: tuple[str, ...] = ()
    partial: bool = False


class Matcher:
    """Hunk attribution over a fixed candidate set per file.

    Candidates come from the inbox's own file-level attributions, which is both
    cheaper and stricter than searching every session: a session that edited the
    file necessarily has a `likely` attribution on it, so a session outside that
    set cannot have authored the content either.
    """

    def __init__(self, cache=None):
        self._cache = cache
        self._index: dict[str, dict[str, list[Fragment]]] = {}   # sid -> path -> frags

    def load(self, session_id: str, log_path: str | Path) -> None:
        if session_id in self._index:
            return
        self._index[session_id] = for_session(Path(log_path), self._cache)

    def attribute(self, abs_path: str, hunk, candidates: list[str],
                  whole_file: bool = False, before: float | None = None) -> Verdict:
        """Attribute one hunk of `abs_path` among `candidates` (session ids).

        `whole_file` marks the synthetic single hunk of a brand-new file, where
        run matching is the wrong instrument: the file has no old side, so its
        entire content is one run, and one later edit to any line would make the
        whole file read `unaccounted`. A file that did not exist before was
        *created* by whoever wrote it, so a create is attributed at file level —
        which is both true and stable under further editing.

        `before` bounds the evidence to edits recorded no later than that epoch
        time — a commit's timestamp plus slack. It exists because a session that
        wrote the same lines *after* a commit cannot have authored that commit,
        and without the bound it is reported as a co-author of one. Working-tree
        change has no such bound, so `before` is None there.
        """
        real = os.path.realpath(abs_path)
        frags = {sid: _eligible(self._index.get(sid, {}).get(real) or [], before)
                 for sid in candidates}
        touchers = [sid for sid in candidates if frags[sid]]
        if not touchers:
            # No candidate session recorded an edit to this path at all. The
            # path-overlap verdict is the reliable one, so it stands.
            return Verdict(UNATTRIBUTED)

        if whole_file:
            return Verdict(LIKELY if len(touchers) == 1 else SHARED, tuple(touchers))

        # Whitespace-only runs carry no identity: a blank line matches almost
        # every fragment, so counting them would turn every hunk `shared`.
        runs = [(s, r) for s, r in hunk.runs() if any(ln.strip() for ln in r)]
        if not runs:
            # A whitespace-only hunk. Nothing to match on, so inherit the
            # file-level verdict rather than invent a sharper one.
            return Verdict(LIKELY if len(touchers) == 1 else SHARED, tuple(touchers))

        found: list[str] = []
        unmatched = False
        for sign, run in runs:
            owners = [sid for sid in touchers
                      if _accounts_for(frags[sid], sign, run)]
            if not owners:
                unmatched = True
                continue
            for sid in owners:
                if sid not in found:
                    found.append(sid)

        if unmatched:
            return Verdict(UNACCOUNTED, tuple(found), partial=bool(found))
        if len(found) == 1:
            return Verdict(LIKELY, tuple(found))
        return Verdict(SHARED, tuple(found))
