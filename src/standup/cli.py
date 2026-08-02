from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cache as cache_mod
from . import audit as audit_mod
from . import brief as brief_mod
from . import claude_logs, cost, gitstate, join, loops, rates, render, show

RECENT_WINDOW_DAYS = 7  # the Recent Window (ADR 0002); --since overrides
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _month_start(now: datetime) -> datetime:
    """Start of the current calendar month, local time, as UTC."""
    local = now.astimezone().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def parse_since(raw: str) -> datetime:
    now = datetime.now(timezone.utc)
    if raw == "yesterday":
        local = now.astimezone()
        start = (local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc)
    m = re.fullmatch(r"(\d+)([dhw])", raw)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "w": timedelta(weeks=n)}[unit]
        return now - delta
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.astimezone()
    except ValueError:
        raise SystemExit(f"standup: cannot parse --since {raw!r} (try yesterday, 3d, 12h, 2w, or an ISO date)")


def _to_json(entries, sessions, since, now) -> str:
    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [clean(v) for v in obj]
        if isinstance(obj, set):
            return sorted(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    payload = {
        "since": since.isoformat(),
        "generated_at": now.isoformat(),
        "repos": [clean(asdict(e)) for e in entries],
        "sessions": [
            {
                "session_id": s.session_id,
                "title": s.title,
                "cwd": s.cwd,
                "last_activity": s.last_activity.isoformat() if s.last_activity else None,
                "edited_files": {p: t.isoformat() for p, t in s.edited_files.items()},
                "commit_hashes": {h: t.isoformat() for h, t in s.commit_hashes.items()},
                "branches": sorted(s.branches),
                "brief": ({
                    "objective": s.brief.objective,
                    "status": s.brief.status,
                    "generated": s.brief.generated.isoformat() if s.brief.generated else None,
                    "model": s.brief.model,
                    "stale": s.brief.stale,
                } if s.brief else None),
            }
            for s in sessions
            if s.edited_files or s.commit_hashes
        ],
    }
    return json.dumps(payload, indent=2)


def _cost_window(since: str | None, now: datetime) -> tuple[datetime, str]:
    """(window_start, display label) for the cost view; default = calendar month."""
    if since == "all":
        return _EPOCH, "all time"
    if since:
        start = parse_since(since)
        label = f"last {since}" if re.fullmatch(r"\d+[dhw]", since) else f"since {since}"
        return start, label
    start = _month_start(now)
    return start, now.astimezone().strftime("%B %Y")


def _cost_json(projects, window_start, label, now) -> str:
    payload = {
        "window": label,
        "window_start": window_start.isoformat() if window_start != _EPOCH else None,
        "generated_at": now.isoformat(),
        "disclaimer": "Notional Cost — API-equivalent load, not money paid. Real spend: claude.ai only.",
        "total": round(sum(p.cost for p in projects), 4),
        "projects": [
            {
                "name": p.name,
                "path": p.path,
                "cost": round(p.cost, 4),
                "brief_overhead": round(p.brief_overhead, 4),
                "brief_count": p.brief_count,
                "audit_overhead": round(p.audit_overhead, 4),
                "audit_count": p.audit_count,
                "by_model": {m: round(c, 4) for m, c in p.by_model.items()},
                "sessions": [
                    {
                        "handle": s.handle,
                        "session_id": s.session.session_id,
                        "title": s.title,
                        "cost": round(s.cost, 4),
                        "by_model": {m: round(c, 4) for m, c in s.by_model.items()},
                        "tokens": s.tokens,
                        "turns": s.turns,
                        "why": s.why,
                        "loop_cost": round(s.loop_cost, 4),
                        "loops": [
                            {
                                "label": l.label,
                                "iterations": l.iterations,
                                "turns": l.turns,
                                "cost": round(l.cost, 4),
                                "unpriced_turns": l.unpriced_turns,
                            }
                            for l in s.loops
                        ],
                        "last_activity": s.session.last_activity.isoformat() if s.session.last_activity else None,
                    }
                    for s in p.sessions
                ],
            }
            for p in projects
        ],
    }
    return json.dumps(payload, indent=2)


def _cmd_cost(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="standup cost",
                                description="Notional Cost by project and session (not real money). "
                                            "The per-session drill-down also flags Loops — repeated "
                                            "tool-call grinds — with each Loop's share of the session's "
                                            "cost (a measured carve-out, not a projected saving).")
    p.add_argument("repo", nargs="?", help="project name/path fragment for a per-session drill-down")
    p.add_argument("--since", help="window override (3d, 2w, ISO date, or 'all'); default: this calendar month")
    p.add_argument("--json", action="store_true", help="structured output")
    p.add_argument("--no-pager", action="store_true", help="print instead of opening a pager")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"), help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    now = datetime.now(timezone.utc)
    window_start, label = _cost_window(args.since, now)
    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1

    session_costs = cost.scan_session_costs(projects_dir, window_start)
    projects = cost.group_by_project(session_costs)
    cost.attach_brief_overhead(projects)
    cost.attach_audit_overhead(projects)
    cache = cache_mod.open_cache()
    cost.attach_loops(session_costs, cache)
    cache.flush()

    if args.json:
        print(_cost_json(projects, window_start, label, now))
        return 0

    if args.repo:
        needle = args.repo.rstrip("/").lower()
        matches = [p_ for p_ in projects
                   if needle == p_.name.lower() or needle in p_.path.lower()]
        if not matches:
            print(f"standup cost: no project matches {args.repo!r}", file=sys.stderr)
            print("known projects: " + ", ".join(sorted(p_.name for p_ in projects)), file=sys.stderr)
            return 1
        exact = [p_ for p_ in matches if p_.name.lower() == needle]
        text = render.render_cost_detail(exact[0] if exact else matches[0], label, now)
    else:
        text = render.render_cost_overview(projects, label, now)
    if args.no_pager:
        print(text)
    else:
        _page(text, less_flags="-RF")  # F: short output prints straight through
    return 0


def _page(text: str, less_flags: str = "-R") -> None:
    """Print through a pager when stdout is a terminal (less by default);
    plain print otherwise, or if the pager can't be launched. `less_flags`
    is the fallback when $LESS is unset (-R keeps colors; add F to quit
    immediately when the content fits one screen)."""
    import shutil
    import subprocess

    if not sys.stdout.isatty():
        print(text)
        return
    pager = os.environ.get("PAGER")
    if pager:
        cmd, shell = pager, True
    elif shutil.which("less"):
        cmd, shell = ["less", less_flags], False
    else:
        print(text)
        return
    env = {**os.environ, "LESS": os.environ.get("LESS", less_flags)}
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, shell=shell, env=env, text=True)
        proc.communicate(text)
    except (OSError, BrokenPipeError, KeyboardInterrupt):
        pass  # user quit the pager early, or it couldn't start


def _render_audit(a, st, width: int) -> str:
    tags = [a.generated.date().isoformat() if a.generated else None,
            "may be stale — session continued after this audit" if a.stale else None]
    label = "── ~ audit · " + " · ".join(t for t in tags if t) + " "
    rule = "─" * width
    out = [st.dim(label + rule[len(label):] if len(label) < width else label), ""]
    out.append(a.body)
    items = " · ".join(f"{lbl} ${c:.2f}" for lbl, c in a.overhead_items())
    out += ["", st.dim(f"audit overhead: ${a.overhead_cost:.2f}  ({items})"
                       + (f" · {a.siblings_considered} siblings considered"
                          if a.siblings_considered else ""))]
    return "\n".join(out)


def _cmd_audit(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="standup audit",
        description="Audit one session for automatable cost (ADR 0007): prints its "
                    "free deterministic Loops, then a fixed Expert Panel "
                    "(4 parallel Sonnet Experts + an Opus concluder, billed to "
                    "your Claude subscription) judges LLM-as-CPU turns, prompt "
                    "structure, and recurrence across same-repo siblings, ending "
                    "in a paste-ready Handoff Prompt. Standup never writes the "
                    "script itself. The Audit is stored durably and re-rendered "
                    "on later runs; its cost is recorded as Audit Overhead.")
    p.add_argument("handle", help="8-char session id prefix (from `standup cost <repo>`)")
    p.add_argument("--refresh", action="store_true",
                   help="regenerate even if a stored Audit exists")
    p.add_argument("--no-pager", action="store_true", help="print instead of paging")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"),
                   help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1
    try:
        log_path = show.resolve_handle(projects_dir, args.handle)
    except show.HandleError as e:
        print(str(e), file=sys.stderr)
        return 1
    sid = log_path.stem

    st = render._style()
    width = render._term_width()
    cache = cache_mod.open_cache()

    # the free layer first — always, generation or not (ADR 0007)
    scan = loops.for_session(log_path, cache)
    sig = loops.significant(scan)
    head = [st.bold(f"{sid[:8]}") + st.dim(f"  ${scan.session_cost:,.2f} notional"), ""]
    if sig:
        for l in sig:
            evid = f"⟳ {l.iterations}× {l.label} — ${l.cost:.2f} loop cost"
            if l.unpriced_turns:
                evid += f" (+{l.unpriced_turns} unpriced turns)"
            head.append("  " + evid)
    else:
        head.append(st.dim("  no above-floor Loops detected"))
    head.append("")
    print("\n".join(head))

    existing = None if args.refresh else audit_mod.load_one(sid)
    if existing is None:
        n_experts = 4
        print(st.dim(f"running Expert Panel: {n_experts}× {'sonnet'} experts "
                     f"+ opus concluder (billed to your subscription)…"))

        def progress(res: dict) -> None:
            c = rates.turn_cost(res["model"], res["usage"]) if res.get("usage") else None
            tag = f"  ${c:.2f}" if c is not None else ""
            print(st.dim(f"  ✓ {res['label']}{tag}"))

        from . import auditgen
        try:
            auditgen.generate(log_path, projects_dir, cache, progress)
        except auditgen.AuditError as e:
            print(f"standup audit: {e}", file=sys.stderr)
            cache.flush()
            return 1
        existing = audit_mod.load_one(sid)
        if existing is None:
            print("standup audit: generation produced no readable Audit", file=sys.stderr)
            cache.flush()
            return 1
        print()
    cache.flush()

    try:
        mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        mtime = None
    audit_mod.stamp_staleness(existing, mtime)
    text = _render_audit(existing, st, min(width, 100))
    if args.no_pager or not sys.stdout.isatty():
        print(text)
    else:
        _page(text)
    return 0


def _cmd_show(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="standup show",
                                description="Read a session's transcript (prompts + responses). "
                                            "Leads with the Session Brief when one exists; tool calls "
                                            "collapse to one-liners, and calls belonging to a detected "
                                            "Loop are gutter-marked ⟳.")
    p.add_argument("handle", help="8-char session id prefix (from `standup cost <repo>`)")
    p.add_argument("--thinking", action="store_true", help="include hidden thinking blocks")
    p.add_argument("--raw", action="store_true", help="dump the untouched session JSONL")
    p.add_argument("--no-pager", action="store_true", help="print instead of opening a pager")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"), help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1
    try:
        path = show.resolve_handle(projects_dir, args.handle)
    except show.HandleError as e:
        print(str(e), file=sys.stderr)
        return 1
    text = show.render_transcript(path, show_thinking=args.thinking, raw=args.raw)
    if args.no_pager:
        print(text)
    else:
        _page(text)
    return 0


def _cmd_watch(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="standup watch",
        description="Watch a repo live while an agent works in it: an interleaved "
                    "feed of every Live Session's activity — file edits (typed out "
                    "as they land), Bash one-liners, your prompts as chapter breaks, "
                    "commits (with their diff), pushes, branch switches, and "
                    "Unattributed Changes the "
                    "moment they appear. Session logs are the claim stream; git is "
                    "the ground truth. Interactive: 1-9/tab filters to one "
                    "session, ↑↓ scrolls back (the view holds still while the "
                    "feed keeps flowing; G returns to live), enter expands a "
                    "block or a commit's diff, d "
                    "toggles stat mode, s opens the transcript, q quits with a "
                    "parting snapshot.")
    p.add_argument("repo", nargs="?", default=".",
                   help="repo name (as in the inbox) or a path to a git checkout; "
                        "defaults to the current directory")
    p.add_argument("--quiet", action="store_true",
                   help="files and commits only (no bash, prompts, or session marks)")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"),
                   help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if not sys.stdout.isatty():
        print("standup watch: needs an interactive terminal", file=sys.stderr)
        return 1
    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1

    from . import watchstream, watchui
    try:
        stream = watchstream.WatchStream(args.repo, projects_dir, quiet=args.quiet)
    except watchstream.WatchError as e:
        print(str(e), file=sys.stderr)
        return 1
    snapshot = watchui.run_watch(stream)
    print(snapshot)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "cost":
        return _cmd_cost(argv[1:])
    if argv and argv[0] == "watch":
        return _cmd_watch(argv[1:])
    if argv and argv[0] == "show":
        return _cmd_show(argv[1:])
    if argv and argv[0] == "audit":
        return _cmd_audit(argv[1:])
    if argv and argv[0] == "_brief":  # hidden: the Stop hook's entry point (ADR 0006)
        from . import briefgen
        return briefgen.run_from_hook_stdin()
    if argv and argv[0] in ("install", "uninstall"):
        sub = argv[0]
        if "-h" in argv[1:] or "--help" in argv[1:]:
            print(f"usage: standup {sub}\n")
            if sub == "install":
                print("Install the Session Brief Stop hook into ~/.claude/settings.json so\n"
                      "every Claude Code session gets an out-of-band objective summary (ADR 0006).\n"
                      "Idempotent; runs a headless `claude -p` auth check. Takes no options.")
            else:
                print("Remove the Session Brief Stop hook from ~/.claude/settings.json.\n"
                      "Leaves existing Briefs in ~/.standup/briefs/ in place. Takes no options.")
            return 0
        if argv[1:]:
            print(f"standup {sub}: unexpected argument {argv[1]!r} (takes no options)", file=sys.stderr)
            return 2
        from . import install as install_mod
        return install_mod.install() if sub == "install" else install_mod.uninstall()

    parser = argparse.ArgumentParser(
        prog="standup",
        description="Morning triage inbox for Claude Code activity across your repos.",
        epilog=(
            "subcommands:\n"
            "  cost [repo]        Notional Cost by project/session (not real money);\n"
            "                     the drill-down flags Loops (repeated tool-call grinds)\n"
            "  show <handle>      read a session's transcript (prompts + responses);\n"
            "                     Loop calls are gutter-marked ⟳\n"
            "  audit <handle>     Expert Panel audit of one session: scriptable Loops,\n"
            "                     LLM-as-CPU turns, recurrence, and a Handoff Prompt\n"
            "                     (on-demand; billed to your Claude subscription)\n"
            "  watch [repo]       live feed of a repo while an agent works: edits\n"
            "                     typed out as they land, commits, prompts, and\n"
            "                     unattributed changes (interactive; q quits)\n"
            "  install            set up the Session Brief Stop hook (machine-wide)\n"
            "  uninstall          remove the Session Brief Stop hook\n"
            "\n"
            "run `standup <subcommand> -h` for a subcommand's options."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo", nargs="?", help="repo name or path fragment for a drill-down")
    parser.add_argument("-a", "--all", action="store_true",
                        help="also show work pushed within the recent window (default %dd)" % RECENT_WINDOW_DAYS)
    parser.add_argument("--since", help="override the recent window (yesterday, 3d, 12h, 2w, ISO date)")
    parser.add_argument("--json", action="store_true", help="structured output for scripts/TUI")
    parser.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    since = parse_since(args.since) if args.since else now - timedelta(days=RECENT_WINDOW_DAYS)
    window = args.since or f"{RECENT_WINDOW_DAYS}d"

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1

    cache = cache_mod.open_cache()
    sessions = claude_logs.scan_sessions(projects_dir, cache)
    cwds = [s.cwd for s in sessions if s.cwd]
    entries = gitstate.discover_repos(cwds, since)
    join.attribute(entries, sessions, cache)
    cache.flush()

    # Session Briefs (ADR 0006): read-only join, then drop briefs for dead logs.
    briefs = brief_mod.load_for_sessions(sessions)
    live_ids = {s.session_id for s in sessions}
    brief_mod.prune_orphans(live_ids)
    audit_mod.prune_orphans(live_ids)  # Audits mirror the Brief lifecycle (ADR 0007)

    if args.json:
        print(_to_json(entries, sessions, since, now))
        return 0

    if args.repo:
        needle = args.repo.rstrip("/").lower()
        matches = [e for e in entries
                   if needle == e.name.lower() or needle in e.main_path.lower()]
        if not matches:
            print(f"standup: no scanned repo matches {args.repo!r}", file=sys.stderr)
            print("known repos: " + ", ".join(sorted(e.name for e in entries)), file=sys.stderr)
            return 1
        exact = [e for e in matches if e.name.lower() == needle]
        entry = exact[0] if exact else matches[0]
        print(render.render_detail(entry, now, show_all=args.all, window=window, briefs=briefs))
        return 0

    print(render.render_overview(entries, since, now, show_all=args.all, window=window, briefs=briefs))
    if args.all:  # optional notional-load footer, retrospective only (CONTEXT.md)
        sc = cost.scan_session_costs(projects_dir, since)
        print(render.render_cost_footer(sc, window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
