from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import checkpoint, claude_logs, gitstate, join, render

DEFAULT_LOOKBACK_DAYS = 30


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
            }
            for s in sessions
            if s.edited_files or s.commit_hashes
        ],
    }
    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="standup",
        description="Morning triage inbox for Claude Code activity across your repos.",
    )
    parser.add_argument("repo", nargs="?", help="repo name or path fragment for a drill-down")
    parser.add_argument("--since", help="override the checkpoint (yesterday, 3d, 12h, 2w, ISO date)")
    parser.add_argument("--json", action="store_true", help="structured output; never advances the checkpoint")
    parser.add_argument("--no-checkpoint", action="store_true", help="don't advance the checkpoint")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS,
                        metavar="DAYS", help="how far back to fully parse session logs (default %(default)s)")
    parser.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    since = parse_since(args.since) if args.since else checkpoint.read_or_default()
    horizon = min(since, now - timedelta(days=args.lookback))

    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        print(f"standup: no Claude Code logs found at {projects_dir}", file=sys.stderr)
        return 1

    sessions = claude_logs.scan_sessions(projects_dir, horizon)
    cwds = [s.cwd for s in sessions if s.cwd]
    entries = gitstate.discover_repos(cwds, since)
    join.attribute(entries, sessions)

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
        print(render.render_detail(entry, join.sessions_for_repo(entry, sessions), now))
        return 0

    print(render.render_overview(entries, sessions, since, now))
    if not args.no_checkpoint and not args.since:
        checkpoint.write(now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
