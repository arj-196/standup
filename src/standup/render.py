"""Render the Triage Inbox: session-major at every altitude.

Layout rules (CONTEXT.md, "Resume"):
- the Session is the display unit; the overview never lists individual files;
- a Rollup renders as a two-line stanza — title line first so titles align
  for at-a-glance scanning, metadata indented below;
- no emitted line may exceed the terminal width: content grows vertically,
  never wraps.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

from . import join
from .cost import ProjectCost, SessionCost
from .models import Attribution, Commit, RepoEntry, Rollup

AREAS_SHOWN = 3
ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class Style:
    def __init__(self, enabled: bool):
        c = lambda code: (lambda s: f"\033[{code}m{s}\033[0m") if enabled else (lambda s: s)
        self.bold = c("1")
        self.dim = c("2")
        self.red = c("31")
        self.green = c("32")
        self.yellow = c("33")
        self.cyan = c("36")


def _style() -> Style:
    enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return Style(enabled)


def _term_width() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _clamp(line: str, width: int) -> str:
    """Guarantee the line fits; a clamped line loses styling rather than wrap."""
    if len(ANSI_RE.sub("", line)) <= width:
        return line
    plain = ANSI_RE.sub("", line)
    return plain[: max(0, width - 1)].rstrip() + "…"


def humanize(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return ""
    local = dt.astimezone()
    nloc = now.astimezone()
    days = (nloc.date() - local.date()).days
    if days <= 0:
        return local.strftime("%H:%M")
    if days == 1:
        return f"yesterday {local.strftime('%H:%M')}"
    if days < 7:
        return local.strftime("%a %H:%M")
    return local.strftime("%b %d")


def _shorten_home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def _window_label(raw: str) -> str:
    if re.fullmatch(r"\d+[dhw]", raw):
        return f"last {raw}"
    return f"since {raw}"


def _area(path: str) -> str:
    """Touched Area of one path: its dirname capped at two segments."""
    segs = path.rstrip("/").split("/")
    if len(segs) == 1:
        return segs[0]
    if len(segs) == 2:
        return segs[0]
    return "/".join(segs[:2])


def _areas(paths: list[str]) -> str:
    counts = Counter(_area(p) for p in paths)
    ordered = [a for a, _ in counts.most_common()]
    label = ", ".join(ordered[:AREAS_SHOWN])
    if len(ordered) > AREAS_SHOWN:
        label += f" +{len(ordered) - AREAS_SHOWN} more"
    return label


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _attr_label(attrs: list[Attribution], st: Style) -> str:
    if not attrs:
        return st.dim("unattributed")
    parts = []
    for a in attrs[:2]:
        parts.append(f'"{a.title}" {st.green("[exact]")}' if a.tier == "exact" else f'~"{a.title}"')
    label = " / ".join(parts)
    if len(attrs) > 2:
        label += st.dim(f" +{len(attrs) - 2} more")
    return label


def _commit_line(c: Commit, st: Style, indent: str) -> str:
    return f"{indent}{st.dim(c.short)} {c.subject}  {_attr_label(c.attributions, st)}"


def _dominant_sessions(commits: list[Commit], st: Style) -> str:
    """One-line label for a compressed commit list: top session title + count of others."""
    attrs = [a for c in commits for a in c.attributions[:1]]
    if not attrs:
        return st.dim("unattributed")
    dominant = Counter(a.title for a in attrs).most_common(1)[0][0]
    extra = len({a.session_id for a in attrs}) - 1
    label = f'~"{dominant}"'
    if extra > 0:
        label += st.dim(f" +{_plural(extra, 'session')}")
    return label


def _brief_line(brief: "Brief | None", st: Style, width: int, indent: str) -> str | None:
    """The Session Brief's objective, rendered as a marked *claim* line (ADR
    0006): a `~` glyph in the honesty family used for `likely` attribution, plus
    a `(stale)` / status hedge. Never impersonates a derived fact; augments,
    never replaces, the title line above it.
    """
    if brief is None or not brief.objective:
        return None
    tags = []
    if brief.status and brief.status != "done":
        tags.append(brief.status)
    if brief.stale:
        tags.append("stale")
    suffix = st.dim("  (" + ", ".join(tags) + ")") if tags else ""
    return _clamp(f"{indent}{st.dim('~')} {brief.objective}{suffix}", width)


def _rollup_stanza(r: Rollup, now: datetime, st: Style, width: int,
                   briefs: dict | None = None) -> list[str]:
    if r.session_id:
        title_line = f'  {st.dim(r.handle)}  ~ "{r.title}"'
    else:
        title_line = f"    {st.dim('unattributed')}"
    lines = [_clamp(title_line, width)]
    bl = _brief_line((briefs or {}).get(r.session_id) if r.session_id else None, st, width, "      ")
    if bl:
        lines.append(bl)
    meta = f"{_plural(len(r.files), 'file')} · {_areas([pf.path for _, pf in r.files])}"
    if r.last_activity:
        meta += f" · {humanize(r.last_activity, now)}"
    lines.append(_clamp(f"      {st.dim(meta)}", width))
    return lines


def _pending_total(e: RepoEntry) -> int:
    return sum(len(co.pending) for co in e.checkouts)


def _unpushed_total(e: RepoEntry) -> int:
    return sum(len(co.unpushed) for co in e.checkouts)


def _by_recency(entries: list[RepoEntry]) -> list[RepoEntry]:
    return sorted(entries, key=lambda e: e.latest_activity or _EPOCH, reverse=True)


def render_overview(entries: list[RepoEntry], since: datetime, now: datetime,
                    show_all: bool = False, window: str = "7d",
                    briefs: dict | None = None) -> str:
    st = _style()
    width = _term_width()
    out: list[str] = [st.bold(f"standup · {now.astimezone().strftime('%a %b %d')}"), ""]

    active = [e for e in entries if _pending_total(e)]
    unpushed_only = [e for e in entries if not _pending_total(e) and _unpushed_total(e)]

    if active:
        out.append(st.bold(st.yellow("ACTIVE WORK")))
        for e in _by_recency(active):
            rolls = join.rollups(e)
            n_sessions = sum(1 for r in rolls if r.session_id)
            head = f"{st.yellow('●')} {st.bold(e.name)} · {_plural(_pending_total(e), 'file')} uncommitted"
            head += f" · {_plural(n_sessions, 'session')}" if n_sessions else " · unattributed"
            if _unpushed_total(e):
                head += f" · {_plural(_unpushed_total(e), 'commit')} unpushed"
            out.append(_clamp(head, width))
            dirty_branches = list(dict.fromkeys(co.branch for co in e.checkouts if co.pending))
            out.append(_clamp(f"  {st.dim(_shorten_home(e.main_path) + ' · ' + ', '.join(dirty_branches))}", width))
            for r in rolls:
                out.extend(_rollup_stanza(r, now, st, width, briefs))
            out.append("")
    else:
        out.append(st.green("No active work — nothing uncommitted."))
        out.append("")

    if unpushed_only:
        out.append(st.bold("UNPUSHED ONLY"))
        for e in _by_recency(unpushed_only):
            commits = [c for co in e.checkouts for c in co.unpushed]
            branches = list(dict.fromkeys(co.branch for co in e.checkouts if co.unpushed))
            line = (f"○ {st.bold(e.name)} · {_plural(len(commits), 'commit')} unpushed"
                    f" on {', '.join(branches)} · {_dominant_sessions(commits, st)}")
            out.append(_clamp(line, width))
        out.append("")

    if show_all:
        out.append(st.bold(st.green(f"PUSHED · {_window_label(window)}")))
        pushed = [e for e in entries if e.done]
        if pushed:
            for e in _by_recency(pushed):
                line = (f"{st.green('✓')} {st.bold(e.name)} · {_plural(len(e.done), 'commit')} pushed"
                        f" · {_dominant_sessions(e.done, st)}")
                out.append(_clamp(line, width))
        else:
            out.append(st.dim("  nothing pushed in the window"))
        out.append("")

    return "\n".join(out)


def render_detail(entry: RepoEntry, now: datetime,
                  show_all: bool = False, window: str = "7d",
                  briefs: dict | None = None) -> str:
    st = _style()
    width = _term_width()
    out = [st.bold(entry.name) + "  " + st.dim(_shorten_home(entry.main_path)), ""]
    multi = len(entry.checkouts) > 1

    rolls = join.rollups(entry)
    for r in rolls:
        if r.session_id:
            head = f'{st.dim(r.handle)}  ~ "{r.title}"'
            if r.last_activity:
                head += st.dim(f" · {humanize(r.last_activity, now)}")
        else:
            head = st.dim("unattributed")
        out.append(_clamp(head, width))
        bl = _brief_line((briefs or {}).get(r.session_id) if r.session_id else None, st, width, "  ")
        if bl:
            out.append(bl)
        for branch, pf in r.files:
            line = f"  {pf.code.strip() or '??':>2} "
            if multi:
                line += f"{st.cyan('[' + branch + ']')} "
            line += pf.path
            others = [a for a in pf.attributions if a.session_id != r.session_id]
            if others:
                also = f'also ~"{others[0].title}"'
                if len(others) > 1:
                    also += f" +{len(others) - 1}"
                line += f"   {st.dim(also)}"
            out.append(_clamp(line, width))
        out.append("")

    for co in entry.checkouts:
        if not co.unpushed:
            continue
        out.append(st.bold(f"unpushed · {_plural(len(co.unpushed), 'commit')} on {co.branch}"))
        for c in co.unpushed:
            out.append(_clamp(_commit_line(c, st, "  "), width))
        out.append("")

    if not rolls and not any(co.unpushed for co in entry.checkouts):
        out.append(st.dim("clean, nothing unpushed"))
        out.append("")

    if show_all:
        out.append(st.bold(st.green(f"pushed · {_window_label(window)}")))
        if entry.done:
            for c in entry.done:
                out.append(_clamp(_commit_line(c, st, "  "), width))
        else:
            out.append(st.dim("  nothing pushed in the window"))
        out.append("")

    return "\n".join(out)


# ── Cost views (Notional Cost; see CONTEXT.md / ADR 0005) ──────────────────
_FAMILIES = ("opus", "fable", "mythos", "sonnet", "haiku")


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _abbr_model(m: str) -> str:
    bare = (m or "?").replace("claude-", "")
    return next((f for f in _FAMILIES if f in bare), bare)


def _tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}k"
    return str(n)


def _by_family(by_model: dict[str, float]) -> dict[str, float]:
    """Merge model ids sharing a family label (e.g. opus-4-8 + opus-5 → opus)
    so splits never print the same label twice."""
    fam: dict[str, float] = {}
    for m, c in by_model.items():
        k = _abbr_model(m)
        fam[k] = fam.get(k, 0.0) + c
    return fam


def _model_split(by_model: dict[str, float]) -> str:
    total = sum(by_model.values()) or 1
    parts = sorted(_by_family(by_model).items(), key=lambda kv: -kv[1])
    return " · ".join(f"{f} {c / total * 100:.0f}%" for f, c in parts[:3])


def _cost_disclaimer(st: Style) -> str:
    return st.dim("notional API-equivalent load — not money paid (real spend: claude.ai)")


def render_cost_overview(projects: list[ProjectCost], window: str, now: datetime) -> str:
    st = _style()
    width = _term_width()
    out = [st.bold(f"COST · {window}"), _cost_disclaimer(st), ""]
    if not projects:
        out.append(st.dim("no priced sessions in the window"))
        return "\n".join(out)

    total = sum(p.cost for p in projects)
    w = max(len(_money(p.cost)) for p in projects)
    for p in projects:
        line = (f"  {_money(p.cost):>{w}}  {st.bold(p.name)}"
                f"   {_plural(len(p.sessions), 'session')}"
                f"   {st.dim(_model_split(p.by_model))}")
        out.append(_clamp(line, width))

    merged: dict[str, float] = {}
    for p in projects:
        for m, c in p.by_model.items():
            merged[m] = merged.get(m, 0.0) + c
    tail = "  ·  " + " · ".join(f"{f} {_money(c)}"
                                for f, c in sorted(_by_family(merged).items(), key=lambda kv: -kv[1]))
    out += ["  " + "─" * w, f"  {_money(total):>{w}}  {st.bold('total')}{st.dim(tail)}"]

    overhead = sum(p.brief_overhead for p in projects)
    n_briefs = sum(p.brief_count for p in projects)
    if n_briefs:
        out.append(st.dim(f"  {_money(overhead):>{w}}  brief overhead"
                          f"  ({_plural(n_briefs, 'brief')}) — cost of keeping Session Briefs current"))
    audit_overhead = sum(p.audit_overhead for p in projects)
    n_audits = sum(p.audit_count for p in projects)
    if n_audits:
        out.append(st.dim(f"  {_money(audit_overhead):>{w}}  audit overhead"
                          f"  ({_plural(n_audits, 'audit')}) — cost of the Expert Panel runs"))
    return "\n".join(out)


def render_cost_detail(project: ProjectCost, window: str, now: datetime) -> str:
    st = _style()
    width = _term_width()
    head = st.bold(project.name) + st.dim(f" — {_money(project.cost)} notional · {window}")
    if project.brief_count:
        head += st.dim(f"  · +{_money(project.brief_overhead)} brief overhead")
    if project.audit_count:
        head += st.dim(f"  · +{_money(project.audit_overhead)} audit overhead")
    out = [head, _cost_disclaimer(st), ""]
    w = max((len(_money(s.cost)) for s in project.sessions), default=5)
    for s in project.sessions:
        why = f"  {st.yellow(s.why)}" if s.why else ""
        loop_tag = ""
        if s.loops:  # above-floor Loops (ADR 0007) — a measured fact, not a saving
            n = f"{len(s.loops)} loops" if len(s.loops) > 1 else "loop"
            loop_tag = f"  {st.yellow(f'⟳ {n} {_money(s.loop_cost)}')}"
        head = (f"  {_money(s.cost):>{w}}  {st.dim(s.handle)}  \"{s.title}\""
                f"  {st.dim(_abbr_model(s.dominant_model or '?'))}{why}{loop_tag}")
        out.append(_clamp(head, width))
        t = s.tokens
        meta = (f"in {_tok(t['input'])} · out {_tok(t['output'])} · "
                f"cache-w {_tok(t['cache_write'])} · cache-r {_tok(t['cache_read'])}")
        if s.session.last_activity:
            meta += f" · {humanize(s.session.last_activity, now)}"
        indent = " " * (w + 4)
        out.append(_clamp(indent + st.dim(meta), width))
        for l in s.loops:
            evid = f"⟳ {l.iterations}× {l.label} — {_money(l.cost)} loop cost"
            if l.unpriced_turns:
                evid += f" (+{l.unpriced_turns} unpriced turns)"
            out.append(_clamp(indent + st.dim(evid), width))
        out.append(indent + st.dim(f"standup show {s.handle}"))
        out.append("")
    return "\n".join(out)


def render_cost_footer(session_costs: list["SessionCost"], window: str) -> str:
    """One dim notional-load line for the `-a` retrospective (never real money)."""
    st = _style()
    total = sum(s.cost for s in session_costs)
    merged: dict[str, float] = {}
    for s in session_costs:
        for m, c in s.by_model.items():
            merged[m] = merged.get(m, 0.0) + c
    split = " · ".join(f"{f} {_money(c)}"
                       for f, c in sorted(_by_family(merged).items(), key=lambda kv: -kv[1]))
    tail = f"  ({split})" if split else ""
    return st.dim(f"notional load · {_window_label(window)}: {_money(total)}{tail} — not real money")
