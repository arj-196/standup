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
standup -a               # also show work done within the recent window (7d)
standup <repo>           # drill-down: one repo's rollups expanded into files/commits
standup --since 3d       # override the recent window (yesterday, 12h, 2w, ISO date)
standup --json           # collect-layer output for scripts/TUI

standup cost             # notional cost by project (this calendar month)
standup cost <repo>      # drill-down: that project's sessions, priced and ranked
standup cost --since all # widen the window (3d, 2w, ISO date, or 'all')
standup cost --json      # structured cost output
standup show             # the newest session in the repo you're standing in
standup show <handle>    # read a session's transcript (prompts + responses)
standup show <handle> --thinking   # include hidden thinking
standup show <handle> --raw        # untouched session JSONL
standup show <handle> --no-pager   # print instead of opening the pager

standup watch            # live feed of this repo while an agent works in it
standup watch <repo>     # watch a named repo (or a path) instead
standup watch --quiet    # files and commits only

standup completion zsh   # print the zsh completion script (see Typing less)
standup install          # set up the Session Brief Stop hook (once, machine-wide)
standup uninstall        # remove it
```

## Typing less

Every subcommand has a one-letter alias — `c` cost, `w` watch, `s` show,
`a` audit — so the common views are two keystrokes past the binary name.
`install` and `uninstall` are deliberately unaliased: a machine-wide mutation
should cost you the whole word.

Every project has a **Project Handle**: a short, derived address shown as the
**underlined letters of its name** wherever the name appears in the inbox and
the cost overview. It is the acronym for a multi-word name and the shortest
unique prefix otherwise, so `pm` is ProjectManagement, `cd` is
ClientDeployment, `st` is standup. Handles are not registered anywhere — they
fall out of the names currently in the **Scan Universe**, so one can grow a
letter when a colliding project appears (ADR 0009). Nothing is ever silently
resolved: a fragment that fits two projects errors and lists both.

```sh
standup c pm             # ProjectManagement's cost drill-down
standup w st             # watch standup
standup s                # read the newest session here
```

A `<repo>` argument also takes a full name or a path — `.`, `../other`,
`~/code/thing`. A **bare word is always a handle**, never a directory, so a
folder sitting in your cwd can never shadow a project; write `./name` when you
mean the path. `<handle>` for a session is any unambiguous prefix of its id,
not necessarily the 8 characters the inbox prints — `standup s 3b0a` is enough.

Shell completion covers subcommands, flags, Project Handles, and Session
Handles (with their titles, so the menu is readable):

```sh
standup completion zsh > /opt/homebrew/share/zsh/site-functions/_standup
```

Then restart your shell. If you alias the binary (`alias s=standup`), point
completion at the alias too with `compdef s=standup` in your `.zshrc`.

`standup watch` is the one live view — an interleaved feed of every Live
Session's edits (typed out as they land, syntax-highlighted by file type,
with a `+`/`−` gutter carrying the diff and a faint red field behind removed
code, so a deletion is findable without reading), Bash one-liners, your
prompts as full-width chapter rules, commits/pushes, and Unattributed
Changes as they appear. Each session gets a lane (a numbered, hue-tinted bar
down the left),
the clock is a gap gutter (quiet rows are quiet; a 40-second think is a
visible `+40s`), and every color is a role with a glyph or attribute that
survives `NO_COLOR`. A commit carries its own diff, in the same gutter and
highlighting as a live edit, so committing a change doesn't make it
unreadable; and on launch every Live Session replays its current chapter at
full strength behind a boundary rule, so filtering to a session that has
already committed and gone quiet still shows its work, as legibly as if you
had watched it happen.

Its status bar carries the **Activity State** of every session that is
currently mid-turn — `[1] ⠹ thinking 4s · [2] ⠹ running 1m` — so you can tell
whether an agent is still going without switching to its terminal. A session
that has handed control back shows *nothing*: there is no "finished", because
the absence is the answer, and the bar stays quiet when the work is quiet. The
verb is read from the log's own `stop_reason` and pending tool call, except
`thinking`, which is inferred from silence and documented as such; the spinner
freezes when nothing has been appended for 30 seconds, so motion never outlives
the data ([ADR 0011](docs/adr/0011-activity-state-inferred-motion-never-outlives-data.md)).

It's interactive; [docs/watch-manual.md](docs/watch-manual.md) is the full guide
to the screen and the keys.

`standup show` opens in your pager (`$PAGER`, or `less -R`) when writing to a
terminal — scroll and `/`-search from the top of the conversation. It prints
plainly when piped or with `--no-pager`.

Standup is stateless: the same command at the same moment always prints the
same inbox (ADR 0002). It keeps a derived cache at `~/.standup/cache` to avoid
re-parsing unchanged session logs — a pure accelerator that never changes
output; `rm -rf ~/.standup/cache` is always safe (ADR 0003). The `~/.standup`
root also holds durable, non-recomputable data (Session Briefs), so delete the
`cache/` subdirectory, not the root.

## Reading the output

The session is the display unit; individual files appear only in the
drill-down.

- **ACTIVE WORK** — repos with uncommitted changes, ageless. Each repo lists
  its Session Rollups: session title, then file count · touched areas ·
  recency. Footprints may overlap (a file claimed by two sessions counts
  under both — standup never fakes a single winner); the repo header carries
  the true git totals.
- **UNPUSHED ONLY** — repos whose only pending work is committed-but-local,
  compressed to one line each. Also ageless. A repo with no remote configured
  never appears here: with nowhere to push, committing *is* the terminal state,
  so its commits are Done rather than Needs-Decision (ADR 0010).
- **DONE** (`-a` only) — work that reached its terminal state within the recent
  window; a retrospective, not a decision queue. Each line names how it got
  there: `N commits pushed`, or `N commits committed · no remote` for a repo
  that has none. The drill-down states `· no remote` in its header
  unconditionally, so a local-only repo says so even when it is clean.
- Two short hexes, coloured by rank: a **session handle** is the cyan 8-char id
  on a session's title line, and it's an address — `standup show <handle>`. A
  **commit hash** renders `@6a4eeef` in grey, because it's only a reference and
  addresses nothing. The `@` keeps them apart when piped or under `NO_COLOR`.
- Attribution: `[exact]` = commit hash captured in the session log,
  `~"title"` = likely (the session edited those files), `unattributed` =
  no session explains it (hand-made or squashed). Unattributed dirt is
  always shown — the inbox must not hide dirt.

Only repos some Claude session has ever visited are scanned (ADR 0001) —
but within those repos, *all* dirt is shown, Claude-made or not.

## Session Briefs

A session's title rarely says what the session was *for*. Run `standup install`
once and a Claude Code Stop hook will, in the background, summarise each coding
session's **objective** with Haiku and drop it at
`~/.standup/briefs/<sessionId>.brief.md`. Standup then shows that objective as a
marked line under the session's title (hedged `(stale)` when the session moved on
after the summary was written). It's a *claim*, never derived truth — it augments
the title, never replaces it, and a session with no Brief just renders as before.

Generation is entirely out-of-band: the hook returns immediately and a detached
`claude -p` does the work, so your live session pays nothing. It uses your
existing Claude Code login — no API key. It only summarises sessions that touched
code, and debounces to ~once per session. The token cost of generating Briefs is
tracked as **brief overhead** in `standup cost`, attributed to the repo it
summarised, so the price of the feature is never hidden (ADR 0006).

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
