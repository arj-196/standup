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

standup <repo> diff      # the attributed diff: what actually changed, by session
standup diff             # same, for the repo you're standing in
standup <repo> diff @abc1234   # one commit's diff, attributed
standup <repo> diff 45e5247    # only the hunks that session accounts for
standup <repo> diff --stat     # per-file counts and tiers, no bodies
standup <repo> diff -U6        # 6 lines of context instead of 3
standup <repo> diff --no-wrap  # clip long body lines instead of folding them

standup cost             # notional cost by project (this calendar month)
standup cost <repo>      # drill-down: that project's sessions, priced and ranked
standup cost --since all # widen the window (3d, 2w, ISO date, or 'all')
standup cost --json      # structured cost output
standup session          # the newest session in the repo you're standing in
standup session <handle> # read a session's transcript (prompts + responses)
standup <repo> session   # the newest session in a named project
standup <repo> session <handle>    # that session, checked to be one of the repo's
standup session --in <repo>         # the verb-first spelling of both
standup session <handle> --thinking   # include hidden thinking
standup session <handle> --raw        # untouched session JSONL
standup session <handle> --no-pager   # print instead of opening the pager

standup watch            # live feed of this repo while an agent works in it
standup watch <repo>     # watch a named repo (or a path) instead
standup watch --quiet    # files and commits only
standup watch --no-wrap  # start with long body lines clipped, not folded

standup completion zsh   # print the zsh completion script (see Typing less)
standup install          # set up the Session Brief Stop hook (once, machine-wide)
standup uninstall        # remove it
```

### Two spellings, one meaning

A repo can lead instead of follow, so `standup tt diff` and `standup diff tt`
are the same command. The position says which axis you're on: **verb-first is a
lens over every project** (`standup cost` prices them all), **a repo followed by
a view is one project at higher magnification** — `standup` → `standup tt` →
`standup tt diff`. It works for `diff`, `cost`, `watch` and `session`
(`standup tt session` is tt's newest), and the rewrite happens before dispatch,
so both spellings are literally the same code (ADR 0015).

Where a view's own argument already implies a repo, the repo you named becomes a
*check* rather than a conflict: `standup st session 040291bc` reads that session
and tells you if it isn't one of `st`'s. Same rule as `@<hash>` — naming a repo
means the answer has to come from it.

`audit` is the exception — verb-first only, always with an explicit handle. It's
the one view that spends money, so it never runs on a session you didn't name.

## Typing less

Every subcommand has a one-letter alias — `c` cost, `w` watch, `s` session,
`a` audit, `d` diff — so the common views are two keystrokes past the binary
name.
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
standup pm c             # the same thing, repo-first
standup w st             # watch standup
standup st d             # standup's attributed diff
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

A file the agent is actually working on arrives as several edits — a
`MultiEdit`'s hunks, four `Edit` calls in ten seconds, or one delta per git
poll — and a row for each buried the work under its own headers. Consecutive
changes to the same file therefore fold into one **Change Run**: one header,
one body that evolves as the work lands, carrying `×N` for the tool calls it
folded (a `MultiEdit` is `×1`). Nothing is discarded — `enter` still expands
every hunk — and the fold is bounded so it can't mislead: it only ever grows
at the bottom of the feed, any other event closes it, and it closes on its own
after 30 seconds so sustained work still produces rows. A claimed run sums the
hunks it shows; a git-witnessed one states the true net delta, measured against
a reference snapshot, so a line added and then removed cancels instead of being
counted twice. Either way the header describes the body printed beneath it
([ADR 0017](docs/adr/0017-a-file-being-worked-on-is-one-evolving-entry.md)).

Its status bar carries the **Activity State** of every session that is
currently mid-turn — `[1] ⠹ thinking 4s · [2] ⠹ running 1m` — so you can tell
whether an agent is still going without switching to its terminal. A session
that has handed control back shows *nothing*: there is no "finished", because
the absence is the answer, and the bar stays quiet when the work is quiet. The
verb is read from the log's own `stop_reason` and pending tool call, except
`thinking`, which is inferred from silence and documented as such; a tool verb
holds the bar for a second even after its tool returns, because a `Read` that
takes 25ms is otherwise a verb nobody can read; and the spinner freezes when
nothing has been appended for 30 seconds, so motion never outlives the data
([ADR 0011](docs/adr/0011-activity-state-inferred-motion-never-outlives-data.md)).

A body line wider than your terminal **folds** rather than running off the
right edge: it continues onto as many rows as it needs, each continuation marked
`↳` in the gutter and starting in the same code column, so a 300-character line
can be read whole. Nothing about the change is off-screen. This applies to diff
bodies, an expanded command, and an expanded prompt — never to headers, which
stay one row per event. `w` (or starting with `--no-wrap`) turns folding off and
clips instead, which gives every event a fixed row count when you want the shape
of the last few minutes rather than the content.

It's interactive; [docs/watch-manual.md](docs/watch-manual.md) is the full guide
to the screen and the keys.

`standup session` opens in your pager (`$PAGER`, or `less -R`) when writing to a
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
  on a session's title line, and it's an address — `standup session <handle>`. A
  **commit hash** renders `@6a4eeef` in grey, because it's only a reference and
  addresses nothing on its own — `standup <repo> diff @6a4eeef` is the one place
  it resolves, inside a repo you named. The `@` keeps the two apart when piped
  or under `NO_COLOR`.
- Attribution: `[exact]` = commit hash captured in the session log,
  `~"title"` = likely (the session edited those files), `unattributed` =
  no session explains it (hand-made or squashed). Unattributed dirt is
  always shown — the inbox must not hide dirt.
- With active work present, the drill-down ends with one dim line naming the
  view after it — `standup st diff · read the changes`.

Only repos some Claude session has ever visited are scanned (ADR 0001) —
but within those repos, *all* dirt is shown, Claude-made or not.

## The attributed diff

`standup <repo> diff` is the drill-down one magnification deeper: where that
listed three changed filenames, this shows what changed in them, grouped under
the session that wrote it. It's a `git diff` of your uncommitted work — staged
and unstaged both, so `git add` never blanks the view — with the watch's
typography over it: syntax highlighting by file type, a `+`/`−` gutter, a faint
red field behind removed lines, and long lines folding with `↳` rather than
running off the edge.

Line numbers sit in one column, not two. A context or added row is numbered on
the **new** side; a removed row on the **old** side. The sign already says which
side you're reading, so a second column would restate it at the cost of width.
Hunks of the same file are separated by a `⋮`.

The part `git diff` can't do is the attribution, and it's per **hunk**, not per
file. That matters: the drill-down puts a file under its *latest* session, so
without this a 200-line rewrite by one session would render under another
session's header because that one changed a single line later. So each hunk is
matched against the actual text of every candidate session's edits, and a hunk is
marked **only when it disagrees with the header above it** — silence means "yes,
this one is theirs":

| mark | meaning |
| --- | --- |
| *(nothing)* | this hunk is the group's session, as the header says |
| `~ 3b0a693b "title"` | a different session wrote this hunk |
| `~ shared` | two or more sessions' work, and it can't be split — both are named |
| `~ unaccounted` | no session's recorded edits account for this hunk |
| `~ unattributed` | no session ever touched this path |

**`unaccounted` is not `unattributed`.** Unattributed is reliable — no session
ever edited that path. Unaccounted sits *inside* a file a session did touch, and
it has two causes that can't be told apart: you edited it by hand, or the agent
wrote it and a later edit moved the text so it no longer matches its own log
verbatim. The matcher only ever recognises text exactly — no fuzzy scoring — so
it reports the gap rather than guessing, and the gap is a decent signal for
"worth a second look". Iterating on the same lines produces these routinely
([ADR 0016](docs/adr/0016-a-hunk-is-attributed-verbatim-or-not-at-all.md)).

Three ways to narrow it:

```sh
standup st diff --stat       # per-file counts + each file's tier digest
standup st diff 3b0a693b     # only the hunks that session accounts for
standup st diff @6a4eeef     # one commit instead of the working tree
```

A commit's header additionally carries `exact` when a session's own `git commit`
output logged that hash — the one attribution in standup that's a fact rather
than a claim. Its hunks are still matched individually, since a commit can bundle
two sessions' work, but only against edits made *before* the commit: a session
that writes the same lines a day later can't have authored it.

Scope is uncommitted work only; committed change is reached by naming its hash.
There is deliberately no flag that dumps every unpushed commit's diff at once.

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
`standup session <handle>` to read the actual prompts and responses, each
assistant turn annotated with its cost.

These dollar figures are **Notional Cost** — API-equivalent *load*, a
comparison weight, **not money paid**. On a subscription the real money is the
account-level credit overflow, which Anthropic does not attribute to any
session; Standup deliberately reports no real-spend figure (there is no
trustworthy local source — see ADR 0005). For your actual bill, use
claude.ai → Settings → Usage. Cost spans all sessions (not just those with
pending git work) and, like the inbox, is stateless.
