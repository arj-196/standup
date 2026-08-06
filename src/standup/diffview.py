"""The Attributed Diff: `standup <repo> diff` (ADR 0005 § two grammars, ADR 0007).

The drill-down tells you *three files changed*. This tells you *what changed*,
and who changed it — the last magnification of the same session-major model:

    standup            the Triage Inbox
    standup tt         the drill-down: rollups expanded into files
    standup tt diff    the Attributed Diff: files expanded into hunks

Two things it is deliberately not. It is not `watch`: it renders a snapshot, so
it is paged static text rather than a live view, and it takes git's diff
*structure* (hunks, context, line numbers) where the Watch takes an added block
and a removed block. And it is not `git diff`: every hunk carries the Session
that authored it, which is the whole point of the view (ADR 0007).

Scope is **Active Work** — uncommitted change, read as `git diff HEAD` so
staged and unstaged both appear (staging your work must not blank the view) and
untracked files diff against empty. Committed change is reached by naming a
commit: `standup tt diff @abc1234`. The `@` is required, and it is the sigil the
drill-down already prints, so a commit is paste-ready — and a bare hex stays
what it is everywhere else in Standup, a Session Handle (CONTEXT.md).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.text import Text

from . import diffrows, gitstate, join, unidiff
from .fragments import LIKELY, SHARED, UNACCOUNTED, UNATTRIBUTED, Matcher, Verdict
from .models import RepoEntry
from .render import humanize
from .theme import Theme

MAX_DIFF_BYTES = 2_000_000    # a diff past this is summarised, never rendered
CONTEXT_DEFAULT = 3
NUM_COL = 5                   # width of the line-number gutter
MAX_WIDTH = 160               # code stops getting wider than this is readable
COMMIT_SLACK = 30 * 60        # seconds of slack on a commit's attribution cutoff


class DiffError(Exception):
    pass


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


@dataclass
class HunkBlock:
    hunk: unidiff.Hunk
    verdict: Verdict


@dataclass
class FileBlock:
    branch: str                       # worktree branch, for a multi-checkout repo
    path: str                         # repo-relative
    change: str
    code: str                         # porcelain status code, "" for a commit
    blocks: list[HunkBlock] = field(default_factory=list)
    binary: bool = False
    note: str = ""                    # why there is no body, when there isn't

    @property
    def added(self) -> int:
        return sum(len(b.hunk.added) for b in self.blocks)

    @property
    def removed(self) -> int:
        return sum(len(b.hunk.removed) for b in self.blocks)


@dataclass
class Group:
    """One session's files (session_id None = unattributed), mirroring the
    drill-down's Session Rollups exactly — same grouping, same order."""
    session_id: str | None
    title: str
    last_activity: datetime | None = None
    files: list[FileBlock] = field(default_factory=list)

    @property
    def handle(self) -> str | None:
        return self.session_id[:8] if self.session_id else None


def _file_diff(checkout: str, rel: str, code: str, context: int) -> unidiff.FileDiff | None:
    """One dirty file as a located diff, or None when git can't produce one.

    `git diff HEAD` rather than `git diff`: the inbox counts staged change as
    Active Work, so a view that showed only unstaged change would go blank the
    moment you `git add`. Untracked files are invisible to `git diff` entirely
    and are synthesised from their content instead.
    """
    if code.startswith("?"):
        abs_path = os.path.join(checkout, rel)
        if rel.endswith("/") or os.path.isdir(abs_path):
            # git reports a wholly-untracked directory as one entry; there is no
            # file to diff, and saying "no diff available" would read as a
            # failure rather than as what it is
            return None
        try:
            if os.path.getsize(abs_path) > MAX_DIFF_BYTES:
                return None
            with open(abs_path, errors="replace") as f:
                text = f.read()
        except OSError:
            return None
        if "\x00" in text:
            fd = unidiff.FileDiff(path=rel, change=unidiff.CREATE, binary=True)
            return fd
        return unidiff.untracked_filediff(rel, text)

    out = gitstate.git(checkout, "diff", f"-U{context}", "--no-color", "--no-ext-diff",
                       "--find-renames", "HEAD", "--", rel)
    if out is None:
        return None
    if len(out) > MAX_DIFF_BYTES:
        return None
    parsed = unidiff.parse(out)
    return parsed[0] if parsed else None


def _attribute_file(matcher: Matcher, checkout: str, fd: unidiff.FileDiff,
                    candidates: list[str], before: float | None = None) -> list[HunkBlock]:
    abs_path = os.path.join(checkout, fd.path)
    # a brand-new file is one hunk with no old side: attribute it as a creation,
    # not as a run match over its whole content (see Matcher.attribute)
    whole = fd.change == unidiff.CREATE and len(fd.hunks) == 1
    return [HunkBlock(h, matcher.attribute(abs_path, h, candidates,
                                           whole_file=whole, before=before))
            for h in fd.hunks]


def build_active(entry: RepoEntry, sessions_by_id: dict, matcher: Matcher,
                 context: int = CONTEXT_DEFAULT,
                 only_session: str | None = None) -> list[Group]:
    """The repo's Active Work as attributed groups.

    Grouping is `join.rollups()` verbatim — a file sits under its *latest*
    session, unattributed files trail last. That model is fine for a filename
    and dangerous for a body of code, which is exactly why the hunks inside
    carry their own verdicts: where a hunk disagrees with the header it sits
    under, the view says so (ADR 0007).
    """
    checkout_of = {co.branch: co.path for co in entry.checkouts}
    groups: list[Group] = []

    for r in join.rollups(entry):
        g = Group(session_id=r.session_id, title=r.title,
                  last_activity=r.last_activity)
        for branch, pf in r.files:
            checkout = checkout_of.get(branch, entry.main_path)
            candidates = [a.session_id for a in pf.attributions]
            for sid in candidates:
                s = sessions_by_id.get(sid)
                if s is not None:
                    matcher.load(sid, s.log_path)

            fd = _file_diff(checkout, pf.path, pf.code, context)
            if fd is None:
                note = ("untracked directory — nothing here is tracked yet"
                        if pf.path.endswith("/")
                        else "no diff available (too large, or git could not read it)")
                g.files.append(FileBlock(branch, pf.path, _change_of(pf.code),
                                         pf.code, note=note))
                continue
            if fd.binary:
                g.files.append(FileBlock(branch, pf.path, fd.change, pf.code,
                                         binary=True, note="binary"))
                continue
            blocks = _attribute_file(matcher, checkout, fd, candidates)
            if only_session:
                blocks = [b for b in blocks if only_session in b.verdict.session_ids]
                if not blocks:
                    continue
            g.files.append(FileBlock(branch, pf.path, fd.change, pf.code,
                                     blocks=blocks))
        if g.files:
            groups.append(g)
    return groups


def _change_of(code: str) -> str:
    if code.startswith("?"):
        return unidiff.CREATE
    if "D" in code:
        return unidiff.DELETE
    if "A" in code:
        return unidiff.CREATE
    if "R" in code:
        return unidiff.RENAME
    return unidiff.MODIFY


@dataclass
class CommitDiff:
    sha: str
    short: str
    subject: str
    when: datetime | None
    author: str
    exact: list[str] = field(default_factory=list)   # session ids that logged this sha
    files: list[FileBlock] = field(default_factory=list)
    truncated: bool = False


def resolve_commit(entry: RepoEntry, ref: str) -> tuple[str, str]:
    """`@abc1234` -> (checkout, full sha), inside this Repo Entry only.

    A commit hash addresses nothing globally in Standup (CONTEXT.md); ADR 0005
    § two grammars narrows that to "nothing *outside* a named Repo Entry". So
    the search stops at this repo's checkouts and errors rather than widening —
    the repo was named, and answering about a different one is the misdirection
    every other view is built to avoid. Hash prefixes only: `@main` and
    `@HEAD~2` are revision expressions the glossary has no word for.
    """
    sha = ref[1:] if ref.startswith("@") else ref
    if not sha or not all(c in "0123456789abcdefABCDEF" for c in sha):
        raise DiffError(
            f"standup diff: {ref!r} is not a commit hash\n"
            "  a commit is named as @<hash> (the grey @abc1234 the drill-down prints);\n"
            "  branch names and revision expressions (@main, @HEAD~2) are not accepted")
    for co in entry.checkouts:
        full = gitstate.git(co.path, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        if full and full.strip():
            return co.path, full.strip()
    raise DiffError(f"standup diff: no commit @{sha} in {entry.name}")


def build_commit(entry: RepoEntry, checkout: str, sha: str, sessions_by_id: dict,
                 matcher: Matcher, context: int = CONTEXT_DEFAULT,
                 only_session: str | None = None) -> CommitDiff:
    """One commit as an attributed diff.

    The commit *header* can carry the one attribution in Standup that is a fact
    rather than a claim: `exact`, when a session's own `git commit` stdout
    recorded this hash. The hunks are still matched individually, because a
    commit can bundle more than one session's work — and where it does, the
    header's single name would be a half-truth.
    """
    fmt = "%H%x1f%h%x1f%s%x1f%cI%x1f%an"
    meta = gitstate.git(checkout, "show", "-s", f"--format={fmt}", sha) or ""
    parts = meta.strip().split("\x1f")
    full, short, subject, iso, author = (parts + [""] * 5)[:5]
    when = None
    try:
        when = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        when = None

    exact = [sid for sid, s in sessions_by_id.items()
             if any(sha.startswith(h) or full.startswith(h)
                    for h in s.commit_hashes)]
    # evidence recorded after the commit cannot be in it (ADR 0007). The slack
    # absorbs clock skew and an `--amend` that lands just after the edits.
    before = (when.timestamp() + COMMIT_SLACK) if when else None
    cd = CommitDiff(sha=full or sha, short=short or sha[:8], subject=subject,
                    when=when, author=author, exact=exact)

    out = gitstate.git(checkout, "show", f"-U{context}", "--no-color", "--no-ext-diff",
                       "--find-renames", "--format=", sha)
    if out is None:
        cd.truncated = True
        return cd
    if len(out) > MAX_DIFF_BYTES:
        cd.truncated = True
        return cd

    branch = next((co.branch for co in entry.checkouts if co.path == checkout), "")
    files = unidiff.parse(out)
    # One reverse index for the whole commit, not a scan per file: a commit can
    # touch dozens of paths and the Scan Universe holds hundreds of sessions
    # with thousands of edited files between them.
    touched: dict[str, list[str]] = {}
    wanted = {os.path.realpath(os.path.join(checkout, fd.path)) for fd in files}
    for sid, s in sessions_by_id.items():
        for p in s.edited_files:
            real = os.path.realpath(p)
            if real in wanted:
                touched.setdefault(real, []).append(sid)

    for fd in files:
        abs_path = os.path.join(checkout, fd.path)
        candidates = touched.get(os.path.realpath(abs_path), [])
        for sid in candidates:
            matcher.load(sid, sessions_by_id[sid].log_path)
        if fd.binary:
            cd.files.append(FileBlock(branch, fd.path, fd.change, "",
                                      binary=True, note="binary"))
            continue
        blocks = _attribute_file(matcher, checkout, fd, candidates, before=before)
        if only_session:
            blocks = [b for b in blocks if only_session in b.verdict.session_ids]
            if not blocks:
                continue
        cd.files.append(FileBlock(branch, fd.path, fd.change, "", blocks=blocks))
    return cd


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _handle_text(t: Theme, sid: str | None) -> Text:
    return Text(sid[:8] if sid else "········", style=t.style("address"))


def _verdict_note(t: Theme, v: Verdict, group_sid: str | None,
                  titles: dict, grouped: bool = True) -> Text | None:
    """The one-line mark above a hunk — printed only when the hunk disagrees
    with the header it sits under.

    Silence therefore means "as the group header says", which is what keeps the
    marks meaningful: a note on every hunk of an unremarkable file would be
    read as decoration and skipped.

    `grouped=False` is the commit view, which has no session header for a hunk
    to agree with — so there silence would mean nothing, and every verdict is
    stated.
    """
    if v.tier == LIKELY:
        if v.session_ids and v.session_ids[0] == group_sid:
            return None
        out = Text("~ ", style=t.style("claim", bold=True))
        out.append_text(_handle_text(t, v.session_ids[0] if v.session_ids else None))
        title = titles.get(v.session_ids[0]) if v.session_ids else None
        if title:
            out.append(f'  ~"{title}"', style=t.style("claim"))
        return out
    if v.tier == SHARED:
        out = Text("~ shared", style=t.style("claim", bold=True))
        out.append("  no single session accounts for this hunk · ",
                   style=t.style("claim"))
        for i, sid in enumerate(v.session_ids):
            if i:
                out.append(" ", style=t.style("muted"))
            out.append_text(_handle_text(t, sid))
        return out
    if v.tier == UNACCOUNTED:
        out = Text("~ unaccounted", style=t.style("claim", bold=True))
        if v.partial:
            out.append("  part of this hunk is not accounted for by ",
                       style=t.style("claim"))
            for i, sid in enumerate(v.session_ids):
                if i:
                    out.append(" ", style=t.style("muted"))
                out.append_text(_handle_text(t, sid))
        else:
            out.append("  no session's edits account for this hunk",
                       style=t.style("claim"))
        return out
    if v.tier == UNATTRIBUTED and (group_sid is not None or not grouped):
        # silent only inside the `unattributed` group, whose header already said it
        out = Text("~ unattributed", style=t.style("claim", bold=True))
        out.append("  no session ever touched this path", style=t.style("claim"))
        return out
    return None


def _file_line(t: Theme, fb: FileBlock, multi: bool, pad: int = 0) -> Text:
    line = Text("  ")
    if multi and fb.branch:
        line.append(f"[{fb.branch}] ", style=t.style("address"))
    line.append_text(diffrows.path_text(t, fb.path))
    if pad:
        line.append(" " * max(1, pad - len(fb.path)))
    else:
        line.append("  ")
    line.append(f"{fb.change}  ", style=t.style("muted"))
    if fb.binary or fb.note:
        line.append(fb.note or "no diff", style=t.style("faint"))
        return line
    line.append_text(diffrows.counts(t, fb.added, fb.removed))
    return line


def _tier_summary(t: Theme, fb: FileBlock, group_sid: str | None) -> Text | None:
    """The `--stat` line's attribution digest.

    `--stat` prints no hunks, so a hunk-level verdict has nowhere to live; the
    file line carries the shape of what the bodies would have said instead —
    never a single name that the hunks would contradict.
    """
    tiers = [b.verdict.tier for b in fb.blocks]
    if not tiers:
        return None
    n_shared = tiers.count(SHARED)
    n_unacc = tiers.count(UNACCOUNTED)
    others = {b.verdict.session_ids[0] for b in fb.blocks
              if b.verdict.tier == LIKELY and b.verdict.session_ids
              and b.verdict.session_ids[0] != group_sid}
    bits: list[str] = []
    if n_shared:
        bits.append(f"{n_shared} shared")
    if n_unacc:
        bits.append(f"{n_unacc} unaccounted")
    if not bits and not others:
        return None
    out = Text("~ ", style=t.style("claim", bold=True))
    if bits:
        out.append(" · ".join(bits) + (" · " if others else ""),
                   style=t.style("claim"))
    for i, sid in enumerate(sorted(others)):
        if i:
            out.append(" ", style=t.style("muted"))
        out.append_text(_handle_text(t, sid))
    return out


def _number_gutter(t: Theme, row: unidiff.Row) -> Text:
    """Cols 1–10: the line number, then the rule.

    One column, not two: a context or added row is numbered on the new side, a
    removed row on the old side, and the sign column already says which side
    you are reading — a second column would restate it at the cost of width the
    fold rule makes scarce.
    """
    style = t.style("faint") if row.sign == " " else t.style("muted")
    g = Text("  ")
    g.append(str(row.number).rjust(NUM_COL), style=style)
    g.append(" │ ", style=t.style("faint"))
    return g


def _hunk_rows(t: Theme, fb: FileBlock, hb: HunkBlock, width: int,
               group_sid: str | None, titles: dict, wrap: bool,
               first: bool, grouped: bool = True) -> list[Text]:
    out: list[Text] = []
    if not first:
        # hunks are discontiguous regions of one file; without a gap the line
        # numbers jump mid-column and read as a single run of code
        gap = Text(" " * (2 + NUM_COL))
        gap.append(" ⋮ ", style=t.style("faint"))
        out.append(gap)
    note = _verdict_note(t, hb.verdict, group_sid, titles, grouped)
    if note is not None:
        # above the hunk it describes, never below: a mark that trails a body is
        # read as belonging to it
        lead = Text(" " * (2 + NUM_COL))
        lead.append(" │ ", style=t.style("faint"))
        lead.append_text(note)
        out.append(lead)
    no_color = t.depth == "none"
    for row in hb.hunk.rows:
        code = diffrows.styled_lines(row.text, fb.path, no_color)[0]
        out.extend(diffrows.sign_rows(t, row.sign.strip() or None, code,
                                      gutter=_number_gutter(t, row),
                                      width=width, wrap=wrap))
    return out


def _group_header(t: Theme, g: Group, now: datetime, titles: dict,
                  briefs: dict | None) -> list[Text]:
    out: list[Text] = []
    if g.session_id:
        head = _handle_text(t, g.session_id)
        head.append(f'  ~ "{g.title}"', style=t.style("claim"))
        if g.last_activity:
            head.append(f" · {humanize(g.last_activity, now)}", style=t.style("muted"))
        out.append(head)
        brief = (briefs or {}).get(g.session_id)
        if brief is not None and brief.objective:
            bl = Text("  ~ ", style=t.style("claim", bold=True))
            bl.append(" ".join(brief.objective.split()), style=t.style("claim"))
            if brief.stale:
                bl.append("  (stale)", style=t.style("faint"))
            out.append(bl)
    else:
        head = Text("unattributed", style=t.style("claim", bold=True))
        head.append("  no session explains these changes", style=t.style("claim"))
        out.append(head)
    return out


def _to_ansi(lines: list[Text], width: int, depth: str) -> str:
    system = {"truecolor": "truecolor", "256": "256",
              "16": "standard", "none": None}[depth]
    buf = io.StringIO()
    con = Console(file=buf, width=width, color_system=system,
                  force_terminal=system is not None, no_color=system is None,
                  highlight=False, soft_wrap=True, legacy_windows=False)
    for line in lines:
        con.print(line)
    return buf.getvalue().rstrip("\n")


def render(entry: RepoEntry, groups: list[Group], now: datetime, *,
           titles: dict, briefs: dict | None = None, width: int = 100,
           stat: bool = False, wrap: bool = True,
           only_session: str | None = None,
           commit: CommitDiff | None = None) -> str:
    t = Theme()
    width = min(width, MAX_WIDTH)
    multi = len(entry.checkouts) > 1
    lines: list[Text] = []

    head = Text(entry.name, style=t.style("primary", bold=True))
    head.append("  " + _short_home(entry.main_path), style=t.style("muted"))
    if not entry.has_remote:
        head.append(" · no remote", style=t.style("faint"))
    lines += [head, Text()]

    if only_session:
        note = Text("filtered to session ", style=t.style("muted"))
        note.append_text(_handle_text(t, only_session))
        note.append("  — hunks this session accounts for, in this repo only",
                    style=t.style("muted"))
        lines += [note, Text()]

    if commit is not None:
        lines += _commit_header(t, commit, now)
        files = commit.files
        if commit.truncated:
            lines += [Text("  diff not read — too large or unreadable",
                           style=t.style("faint")), Text()]
        lines += _files_section(t, files, None, titles, width, multi, stat, wrap,
                                grouped=False)
        return _to_ansi(lines, width, t.depth)

    if not groups:
        lines.append(Text("clean — no Active Work to diff", style=t.style("faint")))
        return _to_ansi(lines, width, t.depth)

    for g in groups:
        lines += _group_header(t, g, now, titles, briefs)
        lines += _files_section(t, g.files, g.session_id, titles, width, multi,
                                stat, wrap)
    return _to_ansi(lines, width, t.depth)


def _commit_header(t: Theme, cd: CommitDiff, now: datetime) -> list[Text]:
    head = Text("commit ", style=t.style("git"))
    head.append(f"@{cd.short}", style=t.style("reference"))
    head.append("  ")
    head.append(cd.subject, style=t.style("primary", bold=True))
    out = [head]
    meta = Text("  ")
    if cd.when:
        meta.append(humanize(cd.when, now), style=t.style("muted"))
    if cd.author:
        meta.append(f" · {cd.author}", style=t.style("muted"))
    out.append(meta)
    if cd.exact:
        # the one attribution in Standup that is a fact: the session's own
        # `git commit` stdout recorded this hash (CONTEXT.md -> Attribution Tier)
        line = Text("  exact", style=t.style("added", bold=True))
        line.append("  logged by ", style=t.style("muted"))
        for i, sid in enumerate(cd.exact):
            if i:
                line.append(" ", style=t.style("muted"))
            line.append_text(_handle_text(t, sid))
        out.append(line)
    out.append(Text())
    return out


def _files_section(t: Theme, files: list[FileBlock], group_sid: str | None,
                   titles: dict, width: int, multi: bool, stat: bool,
                   wrap: bool, grouped: bool = True) -> list[Text]:
    lines: list[Text] = []
    if stat:
        pad = max((len(f.path) for f in files), default=0) + 2
        for fb in files:
            line = _file_line(t, fb, multi, pad)
            summary = _tier_summary(t, fb, group_sid)
            if summary is not None:
                line.append("   ")
                line.append_text(summary)
            lines.append(line)
        lines.append(Text())
        return lines

    for fb in files:
        lines.append(_file_line(t, fb, multi))
        for i, hb in enumerate(fb.blocks):
            lines += _hunk_rows(t, fb, hb, width, group_sid, titles, wrap,
                                first=(i == 0), grouped=grouped)
        lines.append(Text())
    return lines


def _short_home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path
