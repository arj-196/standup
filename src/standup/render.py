"""Render the Triage Inbox: session-major at every altitude.

Layout rules (CONTEXT.md, "Resume"):
- the Session is the display unit; the overview never lists individual files;
- a Rollup renders as a two-line stanza — title line first so titles align
  for at-a-glance scanning, metadata indented below;
- no emitted line may exceed the terminal width: content grows vertically,
  never wraps.

Colour, width, clamping, the short-hex marks and the `~`-claim renderer are
`termout`'s, shared with the Transcript and the `audit` view — this module is
the Triage Inbox's layout and nothing else.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from . import handles, join
from .cost import ProjectCost, SessionCost
from .models import Attribution, Brief, Commit, RepoEntry, Rollup
from .termout import (Style, claim_hedges, claim_line, clamp, commit_ref,
                      agent_tag, session_ref, style, term_width, visible_len)

AREAS_SHOWN = 3
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


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
    return f"{indent}{commit_ref(c.short, st)} {c.subject}  {_attr_label(c.attributions, st)}"


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


def _brief_line(brief: Brief | None, st: Style, width: int, indent: str) -> str | None:
    """The Session Brief's objective, rendered as a marked *claim* line
    (ADR 0003 § the shared model), through the one claim renderer the Transcript
    and the `audit` view also use (`termout.claim_line`). Never impersonates a
    derived fact; augments, never replaces, the title line above it.
    """
    if brief is None or not brief.objective:
        return None
    return claim_line(brief.objective, st, width, indent=indent,
                      hedges=claim_hedges(brief))


def _rollup_stanza(r: Rollup, now: datetime, st: Style, width: int,
                   briefs: dict | None = None) -> list[str]:
    if r.session_id:
        title_line = f'  {session_ref(r.handle, st)}  ~ "{r.title}"{agent_tag(r.agent, st)}'
    else:
        title_line = f"    {st.dim('unattributed')}"
    lines = [clamp(title_line, width)]
    bl = _brief_line((briefs or {}).get(r.session_id) if r.session_id else None, st, width, "      ")
    if bl:
        lines.append(bl)
    meta = f"{_plural(len(r.files), 'file')} · {_areas([pf.path for _, pf in r.files])}"
    if r.last_activity:
        meta += f" · {humanize(r.last_activity, now)}"
    lines.append(clamp(f"      {st.dim(meta)}", width))
    return lines


def _pending_total(e: RepoEntry) -> int:
    return sum(len(co.pending) for co in e.checkouts)


def _unpushed_total(e: RepoEntry) -> int:
    return sum(len(co.unpushed) for co in e.checkouts)


def _terminal_verb(e: RepoEntry) -> str:
    """How this repo's Done work reached its terminal state. The tier is
    neutral ("done"); the line stays precise (ADR 0006)."""
    return "pushed" if e.has_remote else "committed · no remote"


def _by_recency(entries: list[RepoEntry]) -> list[RepoEntry]:
    return sorted(entries, key=lambda e: e.latest_activity or _EPOCH, reverse=True)


def _namer(items, name_of, path_of, st: Style):
    """A `item -> styled name` function with the item's Project Handle
    underlined inside it (CONTEXT.md → Project Handle). Costs no width, which
    is what the never-wrap rule makes scarce. Computed once per view: a handle
    is only unique relative to the whole set."""
    targets = {id(i): handles.Target(name_of(i), path_of(i)) for i in items}
    universe = list(targets.values())
    enabled = st.bold("x") != "x"   # styling off (piped, NO_COLOR) → plain names
    return lambda i: handles.marked(targets[id(i)], universe, enabled)


def render_overview(entries: list[RepoEntry], since: datetime, now: datetime,
                    show_all: bool = False, window: str = "7d",
                    briefs: dict | None = None) -> str:
    st = style()
    width = term_width()
    out: list[str] = [st.bold(f"standup · {now.astimezone().strftime('%a %b %d')}"), ""]
    named = _namer(entries, lambda e: e.name, lambda e: e.main_path, st)

    active = [e for e in entries if _pending_total(e)]
    unpushed_only = [e for e in entries if not _pending_total(e) and _unpushed_total(e)]

    if active:
        out.append(st.bold(st.yellow("ACTIVE WORK")))
        for e in _by_recency(active):
            rolls = join.rollups(e)
            n_sessions = sum(1 for r in rolls if r.session_id)
            head = f"{st.yellow('●')} {st.bold(named(e))} · {_plural(_pending_total(e), 'file')} uncommitted"
            head += f" · {_plural(n_sessions, 'session')}" if n_sessions else " · unattributed"
            if _unpushed_total(e):
                head += f" · {_plural(_unpushed_total(e), 'commit')} unpushed"
            out.append(clamp(head, width))
            dirty_branches = list(dict.fromkeys(co.branch for co in e.checkouts if co.pending))
            out.append(clamp(f"  {st.dim(handles.shorten_home(e.main_path) + ' · ' + ', '.join(dirty_branches))}", width))
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
            line = (f"○ {st.bold(named(e))} · {_plural(len(commits), 'commit')} unpushed"
                    f" on {', '.join(branches)} · {_dominant_sessions(commits, st)}")
            out.append(clamp(line, width))
        out.append("")

    if show_all:
        out.append(st.bold(st.green(f"DONE · {_window_label(window)}")))
        done = [e for e in entries if e.done]
        if done:
            for e in _by_recency(done):
                line = (f"{st.green('✓')} {st.bold(named(e))} · {_plural(len(e.done), 'commit')} "
                        f"{_terminal_verb(e)} · {_dominant_sessions(e.done, st)}")
                out.append(clamp(line, width))
        else:
            out.append(st.dim("  nothing done in the window"))
        out.append("")

    return "\n".join(out)


def render_detail(entry: RepoEntry, now: datetime,
                  show_all: bool = False, window: str = "7d",
                  briefs: dict | None = None, handle: str | None = None) -> str:
    st = style()
    width = term_width()
    # a Remoteless Repo states it here, unconditionally: the drill-down is the
    # one view you asked for by name, and the inbox stays silent (ADR 0006)
    head = st.bold(entry.name) + "  " + st.dim(handles.shorten_home(entry.main_path))
    if not entry.has_remote:
        head += st.dim(" · no remote")
    out = [clamp(head, width), ""]
    multi = len(entry.checkouts) > 1

    rolls = join.rollups(entry)
    for r in rolls:
        if r.session_id:
            head = f'{session_ref(r.handle, st)}  ~ "{r.title}"{agent_tag(r.agent, st)}'
            if r.last_activity:
                head += st.dim(f" · {humanize(r.last_activity, now)}")
        else:
            head = st.dim("unattributed")
        out.append(clamp(head, width))
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
            out.append(clamp(line, width))
        out.append("")

    for co in entry.checkouts:
        if not co.unpushed:
            continue
        out.append(st.bold(f"unpushed · {_plural(len(co.unpushed), 'commit')} on {co.branch}"))
        for c in co.unpushed:
            out.append(clamp(_commit_line(c, st, "  "), width))
        out.append("")

    if not rolls and not any(co.unpushed for co in entry.checkouts):
        # "nothing unpushed" is vacuous for a Remoteless Repo — it has no
        # Unpushed tier to be empty; the header already carried `no remote`
        out.append(st.dim("clean, nothing unpushed" if entry.has_remote else "clean"))
        out.append("")

    if show_all:
        out.append(st.bold(st.green(f"done · {_window_label(window)}")))
        if entry.done:
            for c in entry.done:
                out.append(clamp(_commit_line(c, st, "  "), width))
        else:
            out.append(st.dim("  nothing done in the window"))
        out.append("")

    # The next magnification, named where it is wanted. Standup's discoverability
    # idiom is that a view prints the address of the command after it — the reason
    # every rollup title line carries its Session Handle. A drill-down that lists
    # three changed filenames and no way to read them is the dead end this closes.
    # Printed only when there is Active Work: a hint pointing at an empty view is
    # noise, and the handle is the argument you already typed.
    if any(co.pending for co in entry.checkouts):
        out.append(st.dim(f"standup {handle or entry.name} diff  ·  read the changes"))

    return "\n".join(out)


# ── Cost views (Notional Cost; see CONTEXT.md / ADR 0002) ──────────────────
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


def _cost_disclaimer(st: Style, width: int) -> str:
    """Shortened rather than truncated when narrow: clamping this line would eat
    the words that make it a disclaimer, leaving a figure that reads as money."""
    full = "notional API-equivalent load — not money paid (real spend: claude.ai)"
    short = "notional load — not money paid"
    return st.dim(clamp(full if len(full) <= width else short, width))


def render_cost_overview(projects: list[ProjectCost], window: str, now: datetime,
                         order: str = "cost") -> str:
    st = style()
    width = term_width()
    # a re-ordered list must say so, or the money column reads as mis-sorted
    head = f"COST · {window}" + (" · by recency" if order == "recent" else "")
    out = [clamp(st.bold(head), width), _cost_disclaimer(st, width), ""]
    if not projects:
        out.append(clamp(st.dim("no priced sessions in the window"), width))
        return "\n".join(out)

    total = sum(p.cost for p in projects)
    w = max(len(_money(p.cost)) for p in projects)
    named = _namer(projects, lambda p: p.name, lambda p: p.path, st)
    for p in projects:
        line = (f"  {_money(p.cost):>{w}}  {st.bold(named(p))}"
                f"   {_plural(len(p.sessions), 'session')}"
                f"   {st.dim(_model_split(p.by_model))}")
        # the sort key is shown when it is what ranked the row
        if order == "recent" and p.last_turn:
            line += f"   {st.dim(humanize(p.last_turn, now))}"
        out.append(clamp(line, width))

    merged: dict[str, float] = {}
    for p in projects:
        for m, c in p.by_model.items():
            merged[m] = merged.get(m, 0.0) + c
    tail = "  ·  " + " · ".join(f"{f} {_money(c)}"
                                for f, c in sorted(_by_family(merged).items(), key=lambda kv: -kv[1]))
    out += [clamp("  " + "─" * w, width),
            clamp(f"  {_money(total):>{w}}  {st.bold('total')}{st.dim(tail)}", width)]

    overhead = sum(p.brief_overhead for p in projects)
    n_briefs = sum(p.brief_count for p in projects)
    if n_briefs:
        out.append(clamp(st.dim(f"  {_money(overhead):>{w}}  brief overhead"
                                f"  ({_plural(n_briefs, 'brief')}) — cost of keeping Session Briefs current"),
                         width))
    audit_overhead = sum(p.audit_overhead for p in projects)
    n_audits = sum(p.audit_count for p in projects)
    if n_audits:
        out.append(clamp(st.dim(f"  {_money(audit_overhead):>{w}}  audit overhead"
                                f"  ({_plural(n_audits, 'audit')}) — cost of the Expert Panel runs"),
                         width))
    return "\n".join(out)


def render_cost_detail(project: ProjectCost, window: str, now: datetime,
                       order: str = "cost") -> str:
    st = style()
    width = term_width()
    # each session line already shows its own recency, so the header mark is
    # the only extra ink a re-ordered drill-down needs
    order_tag = " · by recency" if order == "recent" else ""
    head = st.bold(project.name) + st.dim(
        f" — {_money(project.cost)} notional · {window}{order_tag}")
    # the overheads are appended segments, so the header grows vertically rather
    # than off the edge when they do not fit beside the total
    extra = []
    if project.brief_count:
        extra.append(f"+{_money(project.brief_overhead)} brief overhead")
    if project.audit_count:
        extra.append(f"+{_money(project.audit_overhead)} audit overhead")
    one_line = head + "".join(st.dim(f"  · {e}") for e in extra)
    if visible_len(one_line) <= width:
        out = [one_line]
    else:
        out = [clamp(head, width)] + [clamp("  " + st.dim(e), width) for e in extra]
    out += [_cost_disclaimer(st, width), ""]
    w = max((len(_money(s.cost)) for s in project.sessions), default=5)
    for s in project.sessions:
        why = f"  {st.yellow(s.why)}" if s.why else ""
        loop_tag = ""
        # above-floor Loops (ADR 0003 § the Audit) — a measured fact, not a saving
        if s.loops:
            n = f"{len(s.loops)} loops" if len(s.loops) > 1 else "loop"
            loop_tag = f"  {st.yellow(f'⟳ {n} {_money(s.loop_cost)}')}"
        head = (f"  {_money(s.cost):>{w}}  {session_ref(s.handle, st)}  \"{s.title}\""
                f"{agent_tag(s.session.agent, st)}"
                f"  {st.dim(_abbr_model(s.dominant_model or '?'))}{why}{loop_tag}")
        out.append(clamp(head, width))
        indent = " " * (w + 4)
        # the Session Brief's objective, in the stanza position the Triage Inbox
        # and `standup session` both use: under the title it augments, above the
        # arithmetic. Briefless sessions render exactly as before.
        bl = _brief_line(s.session.brief, st, width, indent)
        if bl:
            out.append(bl)
        t = s.tokens
        meta = (f"in {_tok(t['input'])} · out {_tok(t['output'])} · "
                f"cache-w {_tok(t['cache_write'])} · cache-r {_tok(t['cache_read'])}")
        # subagent transcripts folded into the figures above are marked, never
        # silent (ADR 0002 § subagent usage)
        if s.subagents:
            meta += f" · incl {_plural(s.subagents, 'subagent')}"
        # the newest turn these figures counted, not the log's mtime: a row
        # priced over a window states the recency of the work it priced
        if s.last_turn:
            meta += f" · {humanize(s.last_turn, now)}"
        out.append(clamp(indent + st.dim(meta), width))
        for l in s.loops:
            evid = f"⟳ {l.iterations}× {l.label} — {_money(l.cost)} loop cost"
            if l.unpriced_turns:
                evid += f" (+{l.unpriced_turns} unpriced turns)"
            out.append(clamp(indent + st.dim(evid), width))
        out.append(clamp(indent + st.dim(f"standup session {s.handle}"), width))
        out.append("")
    return "\n".join(out)


def render_cost_footer(session_costs: list["SessionCost"], window: str) -> str:
    """One dim notional-load line for the `-a` retrospective (never real money)."""
    st = style()
    total = sum(s.cost for s in session_costs)
    merged: dict[str, float] = {}
    for s in session_costs:
        for m, c in s.by_model.items():
            merged[m] = merged.get(m, 0.0) + c
    split = " · ".join(f"{f} {_money(c)}"
                       for f, c in sorted(_by_family(merged).items(), key=lambda kv: -kv[1]))
    head = f"notional load · {_window_label(window)}: {_money(total)}"
    width = term_width()
    # the per-family split is a detail, the "not real money" is the point: drop
    # the split before clamping, so narrow terminals never lose the caveat
    for tail in (f"  ({split})" if split else "", ""):
        line = f"{head}{tail} — not real money"
        if len(line) <= width:
            return st.dim(line)
    return st.dim(clamp(line, width))
