"""Hunk-level attribution: which Session authored *this* change (ADR 0016).

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

The fragment index is derived deterministically from the session logs, so it
belongs in the Derived Cache. The *match* never does: it runs against the live
working tree, which ADR 0003 keeps out of the cache entirely.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

INDEX_VERSION = 2   # bumped: fragments now carry their timestamp

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Verdicts (CONTEXT.md -> Attribution Tier).
LIKELY, SHARED, UNACCOUNTED, UNATTRIBUTED = (
    "likely", "shared", "unaccounted", "unattributed")

# A cached index larger than this is dropped rather than stored: the cache is a
# pure accelerator, and a session that wrote a hundred large files should not be
# allowed to grow the DB without bound. Skipping the write costs a reparse and
# changes no output.
MAX_CACHED_BYTES = 8 * 1024 * 1024


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


def _ts(obj: dict) -> float | None:
    raw = obj.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (ValueError, TypeError):
        return None


def _fragments_from_obj(obj: dict) -> list[Fragment]:
    message = obj.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    out: list[Fragment] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = item.get("name")
        if name not in EDIT_TOOLS:
            continue
        inp = item.get("input") or {}
        fp = inp.get("file_path") or inp.get("notebook_path")
        if not fp or not os.path.isabs(fp):
            continue
        real, when = os.path.realpath(fp), _ts(obj)
        if name == "Write":
            out.append(Fragment(real, norm(inp.get("content") or ""), (), when))
        elif name == "MultiEdit":
            for e in inp.get("edits") or []:
                if isinstance(e, dict):
                    out.append(Fragment(real, norm(e.get("new_string") or ""),
                                        norm(e.get("old_string") or ""), when))
        else:   # Edit / NotebookEdit
            out.append(Fragment(
                real,
                norm(inp.get("new_string") or inp.get("new_source") or ""),
                norm(inp.get("old_string") or ""), when))
    return out


def _scan(log_path: Path) -> list[Fragment]:
    out: list[Fragment] = []
    try:
        with open(log_path, errors="replace") as f:
            for line in f:
                if '"tool_use"' not in line:
                    continue
                if not any(f'"{t}"' in line for t in EDIT_TOOLS):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "assistant":
                    out.extend(_fragments_from_obj(obj))
    except OSError:
        return []
    return out


def _to_cache(frags: list[Fragment]) -> list:
    return [[f.path, list(f.new), list(f.old), f.when] for f in frags]


def _from_cache(data) -> list[Fragment]:
    out = []
    for row in data or []:
        try:
            path, new, old, when = row
            out.append(Fragment(path, tuple(new), tuple(old), when))
        except (ValueError, TypeError):
            continue
    return out


def for_session(log_path: Path, cache=None) -> dict[str, list[Fragment]]:
    """A session's edit fragments, grouped by realpath. Served from the Derived
    Cache when the log is unchanged; a cache miss or any cache failure costs a
    reparse and nothing else."""
    sid = log_path.stem
    st = None
    if cache is not None:
        try:
            st = log_path.stat()
        except OSError:
            st = None
        if st is not None:
            hit = cache.get_fragments(sid, st.st_size, st.st_mtime_ns)
            if hit is not None:
                return _group(_from_cache(hit))

    frags = _scan(log_path)
    if cache is not None and st is not None:
        cache.put_fragments(sid, st.st_size, st.st_mtime_ns, _to_cache(frags))
    return _group(frags)


def _group(frags: list[Fragment]) -> dict[str, list[Fragment]]:
    out: dict[str, list[Fragment]] = {}
    for f in frags:
        out.setdefault(f.path, []).append(f)
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
