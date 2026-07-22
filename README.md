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

standup cost             # notional cost by project (this calendar month)
standup cost <repo>      # drill-down: that project's sessions, priced and ranked
standup cost --since all # widen the window (3d, 2w, ISO date, or 'all')
standup cost --json      # structured cost output
standup show <handle>    # read a session's transcript (prompts + responses)
standup show <handle> --thinking   # include hidden thinking
standup show <handle> --raw        # untouched session JSONL
standup show <handle> --no-pager   # print instead of opening the pager
```

`standup show` opens in your pager (`$PAGER`, or `less -R`) when writing to a
terminal — scroll and `/`-search from the top of the conversation. It prints
plainly when piped or with `--no-pager`.

Standup is stateless: the same command at the same moment always prints the
same inbox (ADR 0002). It keeps a derived cache at `~/.standup` to avoid
re-parsing unchanged session logs — a pure accelerator that never changes
output; `rm -rf ~/.standup` is always safe (ADR 0003).

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

## Cost

`standup cost` prices your session logs against the published API rate card to
show where consumption concentrates — ranked by project, then by session, with
a token-bucket breakdown and a one-word "why" tag (`cache-heavy`, `out-heavy`,
`fable`) so the expensive shape is visible. Drill into a session with
`standup show <handle>` to read the actual prompts and responses, each
assistant turn annotated with its cost.

These dollar figures are **Notional Cost** — API-equivalent *load*, a
comparison weight, **not money paid**. On a subscription the real money is the
account-level credit overflow, which Anthropic does not attribute to any
session; Standup deliberately reports no real-spend figure (there is no
trustworthy local source — see ADR 0005). For your actual bill, use
claude.ai → Settings → Usage. Cost spans all sessions (not just those with
pending git work) and, like the inbox, is stateless.
