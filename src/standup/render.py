"""Render the Triage Inbox to a terminal."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from .models import Attribution, Commit, RepoEntry, Session

PENDING_SHOWN = 6
COMMITS_SHOWN = 5


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


def _attr_label(attrs: list[Attribution], now: datetime, st: Style, with_time=True) -> str:
    if not attrs:
        return st.dim("unattributed")
    parts = []
    for a in attrs[:2]:
        mark = "[exact]" if a.tier == "exact" else "~"
        when = f" ({humanize(a.when, now)})" if with_time and a.when else ""
        title = f'"{a.title}"'
        parts.append(f"{mark + ' ' if a.tier == 'exact' else '~'}{title}{when}"
                     if a.tier != "exact" else f"{title}{when} {st.green('[exact]')}")
    label = " / ".join(parts)
    if len(attrs) > 2:
        label += st.dim(f" +{len(attrs) - 2} more")
    return label


def _commit_line(c: Commit, now: datetime, st: Style, indent: str) -> str:
    attr = _attr_label(c.attributions, now, st, with_time=False)
    return f"{indent}{st.dim(c.short)} {c.subject}  {attr}"


def render_overview(entries: list[RepoEntry], sessions: list[Session],
                    since: datetime, now: datetime) -> str:
    st = _style()
    out: list[str] = []
    header = f"standup · {now.astimezone().strftime('%a %b %d')} · since {humanize(since, now)}"
    out.append(st.bold(header))
    out.append("")

    needs = [e for e in entries if e.needs_decision]
    done = [e for e in entries if not e.needs_decision and e.done]
    needs.sort(key=lambda e: e.name.lower())

    if needs:
        out.append(st.bold(st.yellow("NEEDS DECISION")))
        for e in needs:
            out.append(f"{st.yellow('●')} {st.bold(e.name)}"
                       f"{' ' * max(1, 40 - len(e.name))}{st.dim(_shorten_home(e.main_path))}")
            for co in e.checkouts:
                label = "" if co.is_main else f"└ {st.cyan(co.branch)}  "
                if co.pending:
                    where = f"on {co.branch}" if co.is_main else ""
                    out.append(f"  {label}{len(co.pending)} file"
                               f"{'s' if len(co.pending) != 1 else ''} uncommitted {where}".rstrip())
                    for pf in co.pending[:PENDING_SHOWN]:
                        mark = "~ " if pf.attributions else "  "
                        attr = _attr_label(pf.attributions, now, st)
                        out.append(f"    {mark}{pf.code.strip() or '??':>2} {pf.path:<34} {attr}")
                    if len(co.pending) > PENDING_SHOWN:
                        out.append(st.dim(f"      +{len(co.pending) - PENDING_SHOWN} more files"))
                if co.unpushed:
                    label2 = "" if co.is_main else (f"  └ {st.cyan(co.branch)}  " if not co.pending else "     ")
                    prefix = "  " if co.is_main else label2
                    out.append(f"{prefix}{len(co.unpushed)} commit"
                               f"{'s' if len(co.unpushed) != 1 else ''} unpushed"
                               f"{' on ' + co.branch if co.is_main else ''}")
                    for c in co.unpushed[:COMMITS_SHOWN]:
                        out.append(_commit_line(c, now, st, "      "))
                    if len(co.unpushed) > COMMITS_SHOWN:
                        extra = len(co.unpushed) - COMMITS_SHOWN
                        out.append(st.dim(f"      +{extra} more commit{'s' if extra != 1 else ''}"))
            out.append("")
    else:
        out.append(st.green("Nothing needs a decision. Inbox zero."))
        out.append("")

    done_lines: list[str] = []
    for e in sorted(entries, key=lambda e: e.name.lower()):
        if not e.done:
            continue
        first = e.done[0]
        attr = _attr_label(first.attributions, now, st, with_time=False)
        done_lines.append(f"{st.green('✓')} {e.name:<16} "
                          f"{len(e.done)} commit{'s' if len(e.done) != 1 else ''} pushed  {attr}")
    if done_lines:
        out.append(st.bold(st.green("DONE since checkpoint")))
        out.extend(done_lines)
        out.append("")

    return "\n".join(out)


def render_detail(entry: RepoEntry, repo_sessions: list[Session], now: datetime) -> str:
    st = _style()
    out = [st.bold(f"{entry.name}  {st.dim(_shorten_home(entry.main_path))}"), ""]

    for co in entry.checkouts:
        head = co.path if co.is_main else f"worktree {_shorten_home(co.path)}"
        out.append(st.bold(f"[{co.branch}] {st.dim(head) if not co.is_main else ''}").rstrip())
        if not co.pending and not co.unpushed:
            out.append(st.dim("  clean, nothing unpushed"))
        for pf in co.pending:
            attr = _attr_label(pf.attributions, now, st)
            out.append(f"  {pf.code.strip() or '??':>2} {pf.path:<40} {attr}")
        for c in co.unpushed:
            out.append(_commit_line(c, now, st, "  "))
            if c.attributions and c.attributions[0].when:
                pass
        out.append("")

    if entry.done:
        out.append(st.bold(st.green("Pushed since checkpoint")))
        for c in entry.done:
            out.append(_commit_line(c, now, st, "  "))
        out.append("")

    if repo_sessions:
        out.append(st.bold("Sessions"))
        for s in repo_sessions[:12]:
            n_edits = len(s.edited_files)
            edits = f"{n_edits} edit{'s' if n_edits != 1 else ''}" if n_edits else ""
            out.append(f"  {st.dim(s.session_id[:8])} {s.title:<44} "
                       f"{humanize(s.last_activity, now):<16} {st.dim(edits)}")
        out.append("")

    return "\n".join(out)
