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
from . import claude_logs, cost, gitstate, handles, join, loops, rates, render, transcript

# the Recent Window (ADR 0001 § the Recent Window); --since overrides
RECENT_WINDOW_DAYS = 7
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

# Subcommand aliases: a fixed table, so each letter is owned forever and a
# future subcommand can never quietly steal one (ADR 0005 § the reserved-letter
# rule). `install` and `uninstall` are deliberately unaliased — a mistyped
# letter should not be able to rip out the machine-wide Stop hook.
ALIASES = {"c": "cost", "w": "watch", "s": "session", "a": "audit", "d": "diff"}

SUBCOMMANDS = {"cost", "watch", "session", "audit", "diff", "completion",
               "install", "uninstall", "_brief", "_complete"}

# Short option letters, owned across the whole CLI (ADR 0005 § short option
# letters). One letter, one meaning, in every parser that has the flag at all —
# `-s` is `--since` in `standup`, `cost` and `watch`, and can therefore never be
# `--stat` in `diff`:
#
#   -a --all       -s --since     -j --json      -q --quiet     -i --in
#   -t --thinking  -r --raw       -n --stat      -l --recent    -U --context
#   -P --no-pager  -W --no-wrap
#
# Letterless by the same table: `--refresh` and `--projects-dir`, plus
# `session --tools` — `-t` is `--thinking` and `-T` is reserved for a negation,
# so it spends the whole word rather than bending either rule.
#
# Three rules keep the table honest, and each one costs something:
#   * an uppercase *boolean* is the negation of its lowercase, which is why
#     `-p` and `-w` stay unclaimed — a future affirmative `--pager`/`--wrap`
#     must be able to have the obvious letter. `-U` takes a value, so it is not
#     in that class and not an exception to it (it is git's spelling anyway).
#   * dashed and undashed are separate namespaces: `-a` is `--all` while a bare
#     `a` is `audit`. The dash is the separator, so the ALIASES table above and
#     this one never contend.
#   * a flag that spends money gets no letter. `audit --refresh` re-runs the
#     Expert Panel against your subscription, so it costs the whole word — the
#     same reason `install`/`uninstall` are unaliased. `--projects-dir` has
#     none either: it is a hidden entry point, and hidden is a decision.

# The views reachable object-first — `standup <repo> <view>`
# (ADR 0005 § two grammars).
#
# Two axes, and the distinction is the whole rule: a *verb-first* subcommand is a
# different lens over the whole Scan Universe (`standup cost` prices every
# project), while a *second positional* is a deeper magnification of one repo's
# inbox (`standup` -> `standup tt` -> `standup tt diff`). Both spellings reach
# the same code: the object-first form is rewritten to the verb-first one before
# dispatch, so there is one implementation and no second dispatcher.
#
# `audit` is deliberately absent. Every view here is free and read-only; `audit`
# spawns an Expert Panel against your subscription, and a paid action must name
# its target explicitly rather than be reachable by a two-word shorthand.
OBJECT_FIRST = {"cost", "watch", "session", "diff"}


def _resolve_repo(arg: str, targets: list[handles.Target], prog: str) -> handles.Target:
    """A CLI repo argument -> one project. A path (`.`, `../x`, `~/y`) resolves
    through git; anything else is a Project Handle."""
    if handles.looks_like_path(arg):
        return handles.resolve_target_path(arg, targets, prog)
    return handles.resolve(arg, targets, prog)


def _normalize(argv: list[str]) -> list[str]:
    """`standup <repo> <view> <rest…>` -> `standup <view> <repo> <rest…>`.

    A pre-dispatch rewrite (ADR 0005 § two grammars), which is why grammar B
    costs one function rather than a parallel command tree. Nothing here
    resolves a repo: it finds the one place a view name can sit — directly after
    a non-option token — which nothing could occupy before, since
    `standup <repo>` accepted flags and nothing else.

    A *positional scan*, deliberately: `standup -s 2h st watch` has to work now
    that options have one-letter forms people actually type in front
    (ADR 0005 § short option letters), and the pair is found by shape rather
    than by knowing that `-s` swallows the token after it. That ignorance is the
    point — the alternative is a table of every value-taking flag in six
    parsers, kept in sync forever, and a silent misparse the day someone
    forgets. The cost is paid only by input that was already an error:
    `standup -s 3d diff` names no repo, so `3d` is read as one, `-s` is left
    stranded without its value, and `diff` refuses the flag it never had.

    Leading options are handed to the view's own parser, because that is the
    parser that runs — `standup -j st cost` works, and `standup -a st diff`
    fails on `-a`, which `diff` has never had.

    `session` is the one view whose positional is not a repo (it takes a Session
    Handle), so the repo is rewritten onto its `--in` flag instead: `standup tt
    session` means "the newest session in tt", the same generalisation of "here"
    that bare `standup session` already makes for the current directory.
    """
    if len(argv) < 2:
        return argv
    lead = next((i for i, a in enumerate(argv) if not a.startswith("-")), None)
    if lead is None:
        return argv                        # options only: the inbox's own flags
    if ALIASES.get(argv[lead], argv[lead]) in SUBCOMMANDS:
        return argv                        # already verb-first
    for i in range(lead + 1, len(argv)):
        view = ALIASES.get(argv[i], argv[i])
        if (view not in OBJECT_FIRST and view != "audit") or argv[i - 1].startswith("-"):
            continue
        repo = argv[i - 1]
        rest = [a for j, a in enumerate(argv) if j not in (i - 1, i)]
        if view == "audit":
            raise SystemExit(
                f"standup: `{repo} audit` is not accepted — audit is the one paid view\n"
                "  it runs an Expert Panel against your Claude subscription, so it takes an\n"
                "  explicit Session Handle: standup audit <handle>")
        if view == "session":
            return [argv[i], "--in", repo, *rest]
        return [argv[i], repo, *rest]
    return argv


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


def parse_window(raw: str) -> timedelta:
    """A rolling window as a duration: `45m`, `2h`, `3d`, `1w`.

    Deliberately narrower than `parse_since`, which resolves a *point* in time.
    The Watch's Live window is re-read against the clock on every poll, so an
    absolute date would keep widening as the run goes on — "since 9am" would
    mean a different span every minute you watched.
    """
    m = re.fullmatch(r"(\d+)([mhdw])", raw.strip())
    if not m:
        raise SystemExit(
            f"standup: cannot parse --since {raw!r} (a window is a duration: 45m, 2h, 3d, 1w)")
    n, unit = int(m.group(1)), m.group(2)
    if n == 0:
        raise SystemExit("standup: --since must be a window longer than zero")
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n),
            "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


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


def _cost_json(projects, window_start, label, now, order) -> str:
    payload = {
        "window": label,
        "window_start": window_start.isoformat() if window_start != _EPOCH else None,
        "generated_at": now.isoformat(),
        # how the project and session lists below are ranked: "cost" or "recent"
        "order": order,
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
                        # the Session Brief as a claim, hedges attached: a
                        # consumer that reads the objective reads its status and
                        # staleness with it, never the bare assertion.
                        "brief": {
                            "objective": s.session.brief.objective,
                            "status": s.session.brief.status,
                            "stale": s.session.brief.stale,
                        } if s.session.brief else None,
                        "cost": round(s.cost, 4),
                        "by_model": {m: round(c, 4) for m, c in s.by_model.items()},
                        "tokens": s.tokens,
                        "turns": s.turns,
                        # transcripts folded into cost/tokens/turns above
                        # (ADR 0002 § subagent usage)
                        "subagents": s.subagents,
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
                                            "A session's figure includes the subagents it spawned — their "
                                            "transcripts carry usage the parent log never echoes — marked "
                                            "'incl N subagents' in the drill-down. "
                                            "The per-session drill-down carries each Session's Brief "
                                            "objective when one exists (~-marked as a claim, hedged when "
                                            "stale), and flags Loops — repeated "
                                            "tool-call grinds — with each Loop's share of the session's "
                                            "cost (a measured carve-out, not a projected saving).")
    p.add_argument("repo", nargs="?",
                   help="Project Handle (the underlined letters of a name in the "
                        "overview), full name, or a path, for a per-session drill-down")
    p.add_argument("-s", "--since", help="window override (3d, 2w, ISO date, or 'all'); default: this calendar month")
    # `-l` as in *latest first*: the honest letter `-r` is `--raw` globally,
    # and `-R` is reserved as its negation (ADR 0005 § short option letters)
    p.add_argument("-l", "--recent", action="store_true",
                   help="order by last activity (newest first) instead of cost — "
                        "sessions in the drill-down, projects in the overview")
    p.add_argument("-j", "--json", action="store_true", help="structured output")
    p.add_argument("-P", "--no-pager", action="store_true", help="print instead of opening a pager")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"), help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    now = datetime.now(timezone.utc)
    window_start, label = _cost_window(args.since, now)
    order = "recent" if args.recent else "cost"
    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1

    session_costs = cost.scan_session_costs(projects_dir, window_start)
    projects = cost.group_by_project(session_costs, order)
    cost.attach_briefs(projects)
    cost.attach_audit_overhead(projects)
    cache = cache_mod.open_cache()
    cost.attach_loops(session_costs, cache)
    cache.flush()

    if args.json:
        print(_cost_json(projects, window_start, label, now, order))
        return 0

    if args.repo:
        by_target = {handles.Target(p_.name, p_.path): p_ for p_ in projects}
        try:
            hit = _resolve_repo(args.repo, list(by_target), "standup cost")
        except handles.HandleError as e:
            print(str(e), file=sys.stderr)
            return 1
        text = render.render_cost_detail(by_target[hit], label, now, order)
    else:
        text = render.render_cost_overview(projects, label, now, order)
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
        description="Audit one session for automatable cost "
                    "(ADR 0003 § the Audit): prints its "
                    "free deterministic Loops, then a fixed Expert Panel "
                    "(4 parallel Sonnet Experts + an Opus concluder, billed to "
                    "your Claude subscription) judges LLM-as-CPU turns, prompt "
                    "structure, and recurrence across same-repo siblings, ending "
                    "in a paste-ready Handoff Prompt. Standup never writes the "
                    "script itself. The Audit is stored durably and re-rendered "
                    "on later runs; its cost is recorded as Audit Overhead.")
    p.add_argument("handle",
                   help="any unambiguous session id prefix (from `standup cost <repo>`)")
    p.add_argument("--refresh", action="store_true",
                   help="regenerate even if a stored Audit exists (no short form: "
                        "it spends your subscription, so it costs the whole word)")
    p.add_argument("-P", "--no-pager", action="store_true", help="print instead of paging")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"),
                   help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1
    try:
        log_path = transcript.resolve_handle(projects_dir, args.handle)
    except transcript.HandleError as e:
        print(str(e), file=sys.stderr)
        return 1
    sid = log_path.stem

    st = render._style()
    width = render._term_width()
    cache = cache_mod.open_cache()

    # the free layer first — always, generation or not (ADR 0003 § the Audit)
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


_ZSH_COMPLETION = r"""#compdef standup
# zsh completion for standup — print with `standup completion zsh`.
# Candidates come from `standup _complete`, so this script stays dumb: new
# projects and sessions need no regeneration, only new flags do.

_standup_projects() {
  local -a items
  items=("${(@f)$(standup _complete projects 2>/dev/null)}")
  _describe -t projects 'project' items
}

_standup_sessions() {
  local -a items
  items=("${(@f)$(standup _complete sessions 2>/dev/null)}")
  _describe -t sessions 'session' items
}

_standup_views() {
  # the views a repo can be followed by (ADR 0005 § two grammars). `audit` is
  # absent on purpose: it is the one paid view and always names its own session.
  local -a views
  views=(
    'diff:the Attributed Diff of this repo (d)'
    'd:the Attributed Diff of this repo'
    'cost:Notional Cost of this repo (c)'
    'c:Notional Cost of this repo'
    'watch:live feed of this repo (w)'
    'w:live feed of this repo'
    'session:this repo newest session Transcript (s)'
    's:this repo newest session Transcript'
  )
  _describe -t views 'view' views
}

_standup() {
  local -a subs
  subs=(
    'diff:the Attributed Diff of a repo Active Work (d)'
    'd:the Attributed Diff of a repo Active Work'
    'cost:Notional Cost by project and session (c)'
    'c:Notional Cost by project and session'
    'session:read a session Transcript (s)'
    's:read a session Transcript'
    'audit:Expert Panel audit of one session (a)'
    'a:Expert Panel audit of one session'
    'watch:live feed of a repo while an agent works (w)'
    'w:live feed of a repo while an agent works'
    'completion:print the shell completion script'
    'install:set up the Session Brief Stop hook'
    'uninstall:remove the Session Brief Stop hook'
  )

  local context state state_descr line
  typeset -A opt_args
  _arguments -C \
    '(-a --all)'{-a,--all}'[also show work done within the recent window]' \
    '(-s --since)'{-s,--since}'[override the recent window]:when (3d, 2w, yesterday, ISO date):' \
    '(-j --json)'{-j,--json}'[structured output for scripts/TUI]' \
    '1: :->first' \
    '*:: :->rest'

  case $state in
    first)
      _describe -t commands 'subcommand' subs
      _standup_projects
      ;;
    rest)
      case $words[1] in
        cost|c)
          _arguments \
            '(-s --since)'{-s,--since}'[window override]:when (3d, 2w, all, ISO date):' \
            '(-l --recent)'{-l,--recent}'[order by last activity instead of cost]' \
            '(-j --json)'{-j,--json}'[structured output]' \
            '(-P --no-pager)'{-P,--no-pager}'[print instead of paging]' \
            '1:project:_standup_projects'
          ;;
        session|s)
          _arguments \
            '(-i --in)'{-i,--in}'[newest session in this project instead of here]:project:_standup_projects' \
            '(-t --thinking)'{-t,--thinking}'[include hidden thinking blocks]' \
            '--tools[each tool call'"'"'s whole input, never its result]' \
            '(-r --raw)'{-r,--raw}'[dump the untouched session JSONL]' \
            '(-P --no-pager)'{-P,--no-pager}'[print instead of paging]' \
            '1:session:_standup_sessions'
          ;;
        diff|d)
          _arguments \
            '(-n --stat)'{-n,--stat}'[per-file counts only]' \
            '(-U --context)'{-U,--context}'[lines of context per hunk]:lines:' \
            '(-W --no-wrap)'{-W,--no-wrap}'[clip long body lines instead of folding them]' \
            '(-P --no-pager)'{-P,--no-pager}'[print instead of paging]' \
            '1:project:_standup_projects' \
            '2:session or @commit:_standup_sessions'
          ;;
        audit|a)
          _arguments \
            '--refresh[regenerate even if a stored Audit exists]' \
            '(-P --no-pager)'{-P,--no-pager}'[print instead of paging]' \
            '1:session:_standup_sessions'
          ;;
        watch|w)
          _arguments \
            '(-q --quiet)'{-q,--quiet}'[files and commits only]' \
            '(-s --since)'{-s,--since}'[widen the Live window]:window (45m, 2h, 3d, 1w):' \
            '(-W --no-wrap)'{-W,--no-wrap}'[start with long body lines clipped, not folded]' \
            '1:project:_standup_projects'
          ;;
        completion)
          _values 'shell' zsh
          ;;
        *)
          # a project led: the second word is a view (`standup tt diff`)
          _standup_views
          ;;
      esac
      ;;
  esac
}

_standup "$@"
"""


def _cmd_completion(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="standup completion",
        description="Print a shell completion script on stdout. It completes "
                    "subcommands, flags, Project Handles, and Session Handles — "
                    "the two things worth not typing. See the README for where "
                    "to install it.")
    p.add_argument("shell", choices=["zsh"], help="the shell to generate for (zsh only)")
    p.parse_args(argv)
    print(_ZSH_COMPLETION)
    return 0


def _cmd_complete(argv: list[str]) -> int:
    """Hidden: the candidate source the completion script shells out to.

    Hidden by decision, like `_brief` — it is a machine interface, and listing
    it in the help would invite it to be used as one by hand.
    """
    what = argv[0] if argv else ""
    projects_dir = Path(os.path.expanduser("~/.claude/projects"))
    if not projects_dir.is_dir():
        return 0
    clean = lambda s: (s or "").replace(":", " ").replace("\n", " ")

    if what == "projects":
        # Built from the cached session scan, not the cost scan: a tab press
        # must not pay for pricing every turn of every log (1.4s vs 0.2s).
        cache = cache_mod.open_cache()
        sessions = claude_logs.scan_sessions(projects_dir, cache)
        cache.flush()
        targets = _session_targets(sessions)
        for t in targets:
            h = handles.handle_of(t, targets)
            print(f"{h[0] if h else t.name}:{clean(t.name)}")
        return 0

    if what == "sessions":
        cache = cache_mod.open_cache()
        sessions = claude_logs.scan_sessions(projects_dir, cache)
        cache.flush()
        for s in sorted(sessions, key=lambda s: s.last_activity or _EPOCH, reverse=True):
            print(f"{s.session_id[:8]}:{clean(s.title)}")
        return 0
    return 2


def _session_targets(sessions) -> list[handles.Target]:
    """The Scan Universe as resolvable Project Handles, one per Repo Entry.

    Built from the cached session scan rather than from git discovery: every
    caller here only needs *names and paths* to resolve a handle against, and a
    handle resolution must not pay for a full repo walk."""
    seen: dict[str, handles.Target] = {}
    for cwd in dict.fromkeys(s.cwd for s in sessions if s.cwd):
        res = gitstate._resolve(cwd)
        key = res[1] if res else os.path.realpath(cwd)
        path = res[0] if res else cwd
        seen.setdefault(key, handles.Target(
            os.path.basename(path.rstrip("/")) or path, path))
    return list(seen.values())


def _newest_session_in(projects_dir: Path, repo: str | None):
    """The most recently appended Session of one Repo Entry — what `standup
    session` means with no handle (`repo=None`: the one you are standing in),
    and what `standup <repo> session` means with one.

    Deliberately no fallback to "newest session anywhere": handing back a
    transcript from an unrelated repo is the kind of silent misdirection every
    other view is built to avoid. Standing outside the Scan Universe is an
    error, and says so.
    """
    cache = cache_mod.open_cache()
    sessions = claude_logs.scan_sessions(projects_dir, cache)
    cache.flush()

    if repo is None:
        here = gitstate._resolve(os.getcwd())
        if not here:
            raise transcript.HandleError(
                "standup session: not inside a git repo — name a session handle, "
                "or a repo with `standup <repo> session`")
        _, key = here
        where = render._shorten_home(os.getcwd())
    else:
        try:
            hit = _resolve_repo(repo, _session_targets(sessions), "standup session")
        except handles.HandleError as e:
            raise transcript.HandleError(str(e)) from None
        res = gitstate._resolve(hit.path)
        if not res:
            raise transcript.HandleError(f"standup session: {hit.name} is not a git repo")
        _, key = res
        where = hit.name

    keys = {}   # cwd -> repo key, resolved once per distinct cwd
    mine = []
    for s in sessions:
        if not s.cwd or not s.last_activity:
            continue
        if s.cwd not in keys:
            res = gitstate._resolve(s.cwd)
            keys[s.cwd] = res[1] if res else None
        if keys[s.cwd] == key:
            mine.append(s)
    if not mine:
        raise transcript.HandleError(
            f"standup session: no sessions recorded for {where}")
    return max(mine, key=lambda s: s.last_activity), where


def _check_session_in(projects_dir: Path, log_path: Path, repo: str) -> None:
    """A handle *and* a repo: confirm the handle belongs to that Repo Entry.

    `standup st session <handle>` names a repo it does not strictly need — a
    Session Handle already implies its repo. Rather than reject the pair (which
    made the object-first spelling useless the moment you pasted a handle into
    it) or ignore the repo (which would answer about a different project without
    saying so), the repo becomes a *constraint*: it is the same rule
    ADR 0005 § two grammars applies to `@<hash>`, where naming a repo means the
    answer must come from it.
    """
    cache = cache_mod.open_cache()
    sessions = claude_logs.scan_sessions(projects_dir, cache)
    cache.flush()
    try:
        hit = _resolve_repo(repo, _session_targets(sessions), "standup session")
    except handles.HandleError as e:
        raise transcript.HandleError(str(e)) from None
    here = gitstate._resolve(hit.path)
    if not here:
        raise transcript.HandleError(f"standup session: {hit.name} is not a git repo")
    _, want = here

    sid = log_path.stem
    session = next((s for s in sessions if s.session_id == sid), None)
    found = gitstate._resolve(session.cwd) if (session and session.cwd) else None
    if found and found[1] == want:
        return
    where = (os.path.basename(found[0].rstrip("/")) if found
             else render._shorten_home(session.cwd) if (session and session.cwd)
             else "an unknown directory")
    raise transcript.HandleError(
        f"standup session: {sid[:8]} is not a session of {hit.name} — it ran in {where}\n"
        f"  a Session Handle already names its repo: `standup session {sid[:8]}` reads it")


def _cmd_session(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="standup session",
                                description="Read a session's Transcript (prompts + responses). "
                                            "Leads with the Session Brief when one exists; tool calls "
                                            "collapse to one-liners (--tools prints each one's whole "
                                            "input, never its result), and calls belonging to a detected "
                                            "Loop are gutter-marked ⟳. With no handle: the most recent "
                                            "session in the repo you are standing in — or in the repo "
                                            "you name with --in.")
    p.add_argument("handle", nargs="?",
                   help="any unambiguous session id prefix (from `standup cost <repo>`); "
                        "omit for the newest session in the current repo")
    p.add_argument("-i", "--in", dest="in_repo", metavar="REPO",
                   help="the verb-first spelling of `standup <repo> session`: with no "
                        "handle, the newest session in this project instead of the "
                        "current directory; with a handle, a check that the handle is "
                        "one of that project's sessions")
    p.add_argument("-t", "--thinking", action="store_true", help="include hidden thinking blocks")
    # no short letter: `-t` is `--thinking` globally, and `-T` is reserved for a
    # negation under ADR 0005 § short option letters
    p.add_argument("--tools", action="store_true",
                   help="print each tool call's whole input beneath its one-liner "
                        "(never its result)")
    p.add_argument("-r", "--raw", action="store_true", help="dump the untouched session JSONL")
    p.add_argument("-P", "--no-pager", action="store_true", help="print instead of opening a pager")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"), help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1
    header = ""
    try:
        if args.handle:
            path = transcript.resolve_handle(projects_dir, args.handle)
            if args.in_repo:   # a repo was named too: it constrains the handle
                _check_session_in(projects_dir, path, args.in_repo)
        else:
            session, place = _newest_session_in(projects_dir, args.in_repo)
            path = Path(session.log_path)
            if not args.raw:   # --raw must stay an untouched dump (CONTEXT.md)
                st = render._style()
                # the *resolved* project, never the handle typed: the header has
                # to say which repo actually answered
                where = "here" if args.in_repo is None else f"in {place}"
                # the handle keeps its own colour — nesting it inside the dim
                # would need the reset that ends the dim for the rest of the line
                header = (render._session_ref(session.session_id[:8], st)
                          + st.dim(f'  ~ "{session.title}"'
                                   f"  — newest session {where}; name a handle for another")
                          + "\n\n")
    except transcript.HandleError as e:
        print(str(e), file=sys.stderr)
        return 1
    text = header + transcript.render_transcript(path, show_thinking=args.thinking,
                                                 raw=args.raw, show_tools=args.tools)
    if args.no_pager:
        print(text)
    else:
        _page(text)
    return 0


def _cmd_diff(argv: list[str]) -> int:
    from . import diffview

    p = argparse.ArgumentParser(
        prog="standup diff",
        description="The Attributed Diff: a repo's Active Work as reviewable "
                    "unified diffs, grouped under the session that authored "
                    "each one — the drill-down at one more magnification. Every "
                    "hunk carries its own attribution, so a file two sessions "
                    "touched does not silently credit the later one: a hunk that "
                    "disagrees with the header it sits under is marked (~shared "
                    "when no single session accounts for it, ~unaccounted when "
                    "nobody's edits do). Scope is uncommitted change, staged and "
                    "unstaged both; name a commit with @<hash> to read one that "
                    "already landed.")
    p.add_argument("repo", nargs="?", default=".",
                   help="Project Handle (the underlined letters of a name in the "
                        "inbox), full name, or a path; defaults to the current directory")
    p.add_argument("ref", nargs="?",
                   help="@<hash> for one commit's diff, or a Session Handle to keep "
                        "only the hunks that session accounts for")
    p.add_argument("-n", "--stat", action="store_true",
                   help="per-file counts only, with each file's attribution digest "
                        "instead of hunk bodies")
    p.add_argument("-U", "--context", type=int, default=diffview.CONTEXT_DEFAULT,
                   metavar="N", help="lines of context around each hunk (default %(default)s)")
    p.add_argument("-W", "--no-wrap", action="store_true",
                   help="clip a body line wider than the terminal at the right edge "
                        "instead of folding it onto further rows (marked ↳)")
    p.add_argument("-P", "--no-pager", action="store_true", help="print instead of opening a pager")
    p.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"),
                   help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    repo, ref = args.repo, args.ref
    # `@abc1234` in the first slot is a commit, not a project: the sigil is
    # unambiguous (no Project Handle starts with @), so `standup diff @abc1234`
    # reads the commit in the repo you are standing in. A *bare* hex stays a
    # Project Handle — untouched by this (ADR 0005 § Project Handles).
    if ref is None and repo.startswith("@"):
        repo, ref = ".", repo

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=RECENT_WINDOW_DAYS)
    cache = cache_mod.open_cache()
    sessions = claude_logs.scan_sessions(projects_dir, cache)
    entries = gitstate.discover_repos([s.cwd for s in sessions if s.cwd], since)
    join.attribute(entries, sessions, cache)

    by_target = {handles.Target(e.name, e.main_path): e for e in entries}
    try:
        hit = _resolve_repo(repo, list(by_target), "standup diff")
    except handles.HandleError as e:
        print(str(e), file=sys.stderr)
        cache.flush()
        return 1
    entry = by_target[hit]

    sessions_by_id = {s.session_id: s for s in sessions}
    titles = {s.session_id: s.title for s in sessions}
    briefs = brief_mod.load_for_sessions(sessions)
    matcher = diffview.Matcher(cache)

    only_session = None
    commit_ref = None
    if ref:
        if ref.startswith("@"):
            commit_ref = ref
        else:
            try:
                log = transcript.resolve_handle(projects_dir, ref)
            except transcript.HandleError as e:
                print(str(e), file=sys.stderr)
                cache.flush()
                return 1
            only_session = log.stem

    width = render._term_width()
    try:
        if commit_ref:
            checkout, sha = diffview.resolve_commit(entry, commit_ref)
            cd = diffview.build_commit(entry, checkout, sha, sessions_by_id, matcher,
                                       context=args.context, only_session=only_session)
            text = diffview.render(entry, [], now, titles=titles, briefs=briefs,
                                   width=width, stat=args.stat,
                                   wrap=not args.no_wrap, only_session=only_session,
                                   commit=cd)
        else:
            groups = diffview.build_active(entry, sessions_by_id, matcher,
                                           context=args.context,
                                           only_session=only_session)
            text = diffview.render(entry, groups, now, titles=titles, briefs=briefs,
                                   width=width, stat=args.stat,
                                   wrap=not args.no_wrap, only_session=only_session)
    except diffview.DiffError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        cache.flush()   # buffered fragment index, whichever way this went

    if args.no_pager:
        print(text)
    else:
        _page(text, less_flags="-RF")   # F: a short diff prints straight through
    return 0


def _cmd_watch(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="standup watch",
        description="Watch a repo live while an agent works in it: an interleaved "
                    "feed of every Live Session's activity — file edits (typed out "
                    "as they land; consecutive edits to one file fold into a "
                    "single Change Run that evolves, marked ×N for the tool calls "
                    "it folded), Calls (every tool call that changes no file — a "
                    "Bash one-liner, an MCP request, a web fetch, a subagent — "
                    "with its argument and a ✓/✗ when it returns; local reads "
                    "stay silent), your prompts as chapter "
                    "rules, commits (with their diff), pushes, branch switches, "
                    "and Unattributed Changes the "
                    "moment they appear. The status bar carries the Activity State "
                    "of every session still mid-turn (thinking / reading / writing "
                    "/ running, with how long); a session that has handed control "
                    "back shows nothing, so a quiet bar means nothing is working. "
                    "A Session counts as live if its log was appended within the "
                    "Live window (30 minutes by default; --since widens it, and "
                    "each picked-up Session backfills its current chapter). "
                    "Worktrees are covered natively: one created mid-watch (e.g. "
                    "Claude Code's .claude/worktrees agents) joins the watched "
                    "set within seconds, marked in the feed, and an agent "
                    "working inside one gets its own numbered lane, titled by "
                    "what it was spawned to do. "
                    "Session logs are the claim stream; git is "
                    "the ground truth. Interactive: 1-9/tab filters to one "
                    "session (repo facts always stay), ↑↓/j/k scrolls back (the "
                    "view holds still while the feed keeps flowing; G returns to "
                    "live), [ ] jumps between chapters, enter expands a block or "
                    "steps a commit through its file list and diff, d toggles "
                    "stat mode, w toggles wrap (on by default: a body line "
                    "wider than the terminal folds onto further rows instead of "
                    "clipping), s opens the transcript, ? shows the key map, "
                    "q quits with a parting snapshot.")
    p.add_argument("repo", nargs="?", default=".",
                   help="Project Handle (the underlined letters of a name in the "
                        "inbox), full name, or a path to a git checkout; "
                        "defaults to the current directory")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="files and commits only (no Calls, prompts, or session marks)")
    p.add_argument("-s", "--since", metavar="WINDOW",
                   help="widen the Live window: a Session whose log was appended "
                        "within it is picked up and backfills its current chapter "
                        "(45m, 2h, 3d, 1w; default 30m)")
    p.add_argument("-W", "--no-wrap", action="store_true",
                   help="start with wrap off: a body line wider than the terminal "
                        "clips at the right edge instead of folding onto further "
                        "rows (marked ↳), so every event keeps a fixed row "
                        "count; w toggles it in the Watch")
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

    window = parse_window(args.since) if args.since else None

    from . import watchstream, watchui
    try:
        stream = watchstream.WatchStream(args.repo, projects_dir, quiet=args.quiet,
                                         live_window=window)
    except watchstream.WatchError as e:
        print(str(e), file=sys.stderr)
        return 1
    snapshot = watchui.run_watch(stream, wrap=not args.no_wrap)
    print(snapshot)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # object-first -> verb-first, before anything is dispatched
    # (ADR 0005 § two grammars)
    argv = _normalize(argv)
    sub = ALIASES.get(argv[0], argv[0]) if argv else None
    if sub == "cost":
        return _cmd_cost(argv[1:])
    if sub == "watch":
        return _cmd_watch(argv[1:])
    if sub == "session":
        return _cmd_session(argv[1:])
    if sub == "diff":
        return _cmd_diff(argv[1:])
    if sub == "audit":
        return _cmd_audit(argv[1:])
    if sub == "completion":
        return _cmd_completion(argv[1:])
    # hidden: the Stop hook's entry point (ADR 0003 § the Session Brief)
    if sub == "_brief":
        from . import briefgen
        return briefgen.run_from_hook_stdin()
    if sub == "_complete":  # hidden: the completion script's candidate source
        return _cmd_complete(argv[1:])
    if sub in ("install", "uninstall"):
        if "-h" in argv[1:] or "--help" in argv[1:]:
            print(f"usage: standup {sub}\n")
            if sub == "install":
                print("Install the Session Brief Stop hook into ~/.claude/settings.json so\n"
                      "every Claude Code session gets an out-of-band objective summary.\n"
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
            "  diff, d [repo]     the Attributed Diff: this repo's Active Work as\n"
            "                     unified diffs, grouped under the session that\n"
            "                     authored each hunk. @<hash> reads one commit\n"
            "  cost, c [repo]     Notional Cost by project/session (not real money);\n"
            "                     the drill-down flags Loops (repeated tool-call grinds)\n"
            "  session, s [hdl]   read a session's Transcript (prompts + responses);\n"
            "                     Loop calls are gutter-marked ⟳. No handle: the newest\n"
            "                     session in the repo you're standing in\n"
            "  audit, a <handle>  Expert Panel audit of one session: scriptable Loops,\n"
            "                     LLM-as-CPU turns, recurrence, and a Handoff Prompt\n"
            "                     (on-demand; billed to your Claude subscription)\n"
            "  watch, w [repo]    live feed of a repo while an agent works: edits\n"
            "                     typed out as they land, commits, prompts, and\n"
            "                     unattributed changes, plus each session's live\n"
            "                     Activity State (interactive; q quits)\n"
            "  completion zsh     print the zsh completion script (see the README)\n"
            "  install            set up the Session Brief Stop hook (machine-wide)\n"
            "  uninstall          remove the Session Brief Stop hook\n"
            "\n"
            "a [repo] is a Project Handle — the underlined letters of a project's name\n"
            "in the inbox (`pm` for ProjectManagement, `st` for standup) — or a path\n"
            "(`.`, ../other). An ambiguous handle errors and lists the candidates.\n"
            "\n"
            "two spellings, one meaning: a repo can lead instead of follow, so\n"
            "`standup tt diff` == `standup diff tt`, and likewise for cost, watch and\n"
            "session (`standup tt session` = tt's newest). Verb-first is a lens over\n"
            "every project (`standup cost` prices them all); repo-first is one repo at\n"
            "higher magnification. `audit` is verb-first only — it is the one view that\n"
            "spends, so it always names its session.\n"
            "\n"
            "run `standup <subcommand> -h` for a subcommand's options."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo", nargs="?",
                        help="Project Handle (the underlined letters of a name in the "
                             "inbox), full name, or a path, for a drill-down")
    parser.add_argument("-a", "--all", action="store_true",
                        help="also show work done — pushed, or committed in a "
                             "repo with no remote — within the recent window "
                             "(default %dd)" % RECENT_WINDOW_DAYS)
    parser.add_argument("-s", "--since", help="override the recent window (yesterday, 3d, 12h, 2w, ISO date)")
    parser.add_argument("-j", "--json", action="store_true", help="structured output for scripts/TUI")
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

    # Session Briefs (ADR 0003 § the Session Brief): read-only join, then drop
    # briefs for dead logs.
    briefs = brief_mod.load_for_sessions(sessions)
    live_ids = {s.session_id for s in sessions}
    brief_mod.prune_orphans(live_ids)
    # Audits mirror the Brief lifecycle (ADR 0003 § the Audit)
    audit_mod.prune_orphans(live_ids)

    if args.json:
        print(_to_json(entries, sessions, since, now))
        return 0

    if args.repo:
        by_target = {handles.Target(e.name, e.main_path): e for e in entries}
        try:
            hit = _resolve_repo(args.repo, list(by_target), "standup")
        except handles.HandleError as e:
            print(str(e), file=sys.stderr)
            return 1
        # the handle the footer hint should echo: what currently resolves to
        # this project, which is not necessarily what the user typed
        shown = handles.handle_of(hit, list(by_target))
        print(render.render_detail(by_target[hit], now, show_all=args.all,
                                   window=window, briefs=briefs,
                                   handle=shown[0] if shown else hit.name))
        return 0

    print(render.render_overview(entries, since, now, show_all=args.all, window=window, briefs=briefs))
    if args.all:  # optional notional-load footer, retrospective only (CONTEXT.md)
        sc = cost.scan_session_costs(projects_dir, since)
        print(render.render_cost_footer(sc, window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
