# standup

Morning triage inbox for Claude Code activity across your repos. Joins
`~/.claude/projects` session logs with git state and answers: which repos
have pending work, **which sessions did it**, and how big each footprint is.

See [CONTEXT.md](CONTEXT.md) for the domain language and
[docs/adr/](docs/adr/) for design decisions.

## Install

```sh
uv tool install --editable .
```

## Use

```sh
standup                  # the triage inbox: active work + unpushed
standup -a               # also show work pushed within the recent window (7d)
standup <repo>           # drill-down: one repo's rollups expanded into files/commits
standup --since 3d       # override the recent window (yesterday, 12h, 2w, ISO date)
standup --json           # collect-layer output for scripts/TUI
```

Standup is stateless: the same command at the same moment always prints the
same inbox (ADR 0002).

## Reading the output

The session is the display unit; individual files appear only in the
drill-down.

- **ACTIVE WORK** — repos with uncommitted changes, ageless. Each repo lists
  its Session Rollups: session title, then file count · touched areas ·
  recency. Footprints may overlap (a file claimed by two sessions counts
  under both — standup never fakes a single winner); the repo header carries
  the true git totals.
- **UNPUSHED ONLY** — repos whose only pending work is committed-but-local,
  compressed to one line each. Also ageless.
- **PUSHED** (`-a` only) — commits pushed within the recent window; a
  retrospective, not a decision queue.
- Attribution: `[exact]` = commit hash captured in the session log,
  `~"title"` = likely (the session edited those files), `unattributed` =
  no session explains it (hand-made or squashed). Unattributed dirt is
  always shown — the inbox must not hide dirt.

Only repos some Claude session has ever visited are scanned (ADR 0001) —
but within those repos, *all* dirt is shown, Claude-made or not.
