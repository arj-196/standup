# standup

Morning triage inbox for Claude Code activity across your repos. Joins
`~/.claude/projects` session logs with git state and answers: which repos
changed, what still needs a decision (uncommitted / unpushed), and which
session did it.

See [CONTEXT.md](CONTEXT.md) for the domain language and
[docs/adr/](docs/adr/) for design decisions.

## Install

```sh
uv tool install --editable .
```

## Use

```sh
standup                  # the triage inbox; advances the checkpoint
standup <repo>           # drill-down: files, commits, sessions for one repo
standup --since 3d       # ad-hoc window (yesterday, 12h, 2w, ISO date); checkpoint untouched
standup --json           # collect-layer output for scripts/TUI; checkpoint untouched
standup --no-checkpoint  # peek without advancing the checkpoint
```

## Reading the output

- **NEEDS DECISION** — ageless: uncommitted files and unpushed commits, however old.
  Worktrees roll up under their main checkout as `└ branch` lines.
- **DONE since checkpoint** — commits pushed since you last ran `standup`.
- Attribution tiers: `[exact]` = commit hash captured in the session log,
  `~"title"` = likely (the session edited those files near that time),
  `unattributed` = no session explains it (hand-made or squashed).

Only repos some Claude session has ever visited are scanned (ADR 0001) —
but within those repos, *all* dirt is shown, Claude-made or not.

The single piece of state is the checkpoint timestamp at
`~/.local/state/standup/checkpoint`.
