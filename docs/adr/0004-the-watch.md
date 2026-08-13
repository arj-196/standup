# 0004 — The Watch

Date: 2026-08-06

`standup watch <repo>` is Standup's only live view. Every other view is a
snapshot rendered to stdout; the Watch runs in a terminal you leave and return
to while an agent works, so it is the one surface that must answer *is something
happening now* rather than *what is the state*.

Twelve decisions, recorded together because they are one design — each later
one reaches into an earlier one's geometry.

## The event source

**The Watch tails the session JSONL as its primary stream; git is the
ground-truth layer.** Git only says "the tree differs", anonymously and after
the fact. The JSONL is appended live and carries the actual tool call
(`Edit`/`Write`, with exact text), the Session identity, and the intent. Git
confirms tree state and is alone able to reveal live Unattributed Changes — the
same claims-vs-truth split Attribution uses everywhere else.

*Rejected: git-only* — anonymous, poll-latency bound, cannot narrate what the
agent is doing.

## The worktree lane

**A Repo Entry's worktrees are watched natively — including the ones that do
not exist yet when the Watch starts.** Claude Code runs isolated agents in
worktrees it creates *mid-run* (`.claude/worktrees/…`), and both of the Watch's
witnesses are blind to them by default: a linked worktree is its own working
tree, so the main checkout's `git status` never reports its files, and the
agent's transcript is a **subagent transcript** — written one level below the
top-level Session logs (`<proj>/<parent-session-id>/subagents/agent-<id>.jsonl`,
every line `isSidechain`), where the `*/*.jsonl` glob cannot find it. Four
rules close the gap:

- **the checkout list is re-asked, not frozen.** On the discovery cadence
  (`DISCOVERY_INTERVAL`) the stream re-runs `git worktree list` against the
  main checkout; a new worktree is adopted (one `worktree` feed event, then
  polled like any checkout), a vanished one is let go — worktrees are
  auto-cleaned, so removal is normal life, not an error. A failed `worktree
  list` reads as *no answer*, never as mass removal. Adoption seeds silently
  (dirt predating it is old news, same as launch); the blind window is at most
  one discovery interval, and the agent's own claims cover it.
- **a subagent transcript is its own lane.** Discovery globs
  `*/*/subagents/*.jsonl` alongside the top-level logs. The lane is addressed
  by the agent id (the `agent-` file prefix dropped, so the handle reads like
  any Session Handle), titled by the spawn `description` from the sibling
  `.meta.json` — the one place the parent's intent for the agent is written
  down — and subject to the same Live-window recency claim as every Session.
- **the sidechain skip is scoped to parent logs.** Activity tracking skips
  `isSidechain` lines because a parent's sidechains are not the parent's work;
  a subagent tailer's whole log is one sidechain, so there the flag carries no
  ambiguity and the lane gets an Activity State.
- **roots match deepest-first.** A `.claude/worktrees/…` worktree nests under
  the main checkout, so prefix-matching in list order would file its edits
  under main as `.claude/worktrees/…/x.py`. The root list is ordered
  longest-first and shared by reference with every tailer — mutated in place on
  adopt/remove, so lanes opened before a worktree existed still resolve
  against it.

*Rejected: subagent transcripts in the Scan Universe* — widening the global
scan would make every Agent call an inbox entry and force decisions this
change has no business taking, like what a lane means in views where a
subagent is not addressable (`standup session <handle>` cannot open one).
Readers opt in one by one: the Watch at discovery time, and the cost view,
which folds a subagent's usage into the parent Session's line — the cost-side
decision this section once declined, since taken (and the under-count this
section once carried as an accepted cost, since closed) by
ADR 0002 § subagent usage.

*Rejected: folding subagent events into the parent's lane* — several agents run
at once, and one lane interleaving N workers cannot be filtered or read; the
parent's lane keeps the `Agent` Call, the worker gets its own number.

## The stream/UI boundary

**Textual** is Standup's second dependency. The Watch needs alt-screen,
keystrokes, scrollback, expand/collapse and timed animation; hand-rolling
ANSI/termios means owning a mini-framework forever, and `rich` alone is
rendering without input handling.

**Containment rule: the event stream is plain Python with no textual imports.**
JSONL tail and git observation resolve to typed Feed Events; textual is imported
only by the watch UI module, so the inbox/cost/session path never grows a TUI
dependency.

Unplanned payoff: the diff row renderer sits on the Textual-free side, so the
Watch and the Attributed Diff call the same code and the rules below cannot
drift between the two surfaces.

**Everything between git's bytes and the row is shared, in the same direction.**
Three things were duplicated across the boundary and are now single:

- **one unified-diff parser** (`unidiff`). The Watch had its own state machine
  for commit diffs, because it wants less than the Attributed Diff does — two
  flat blocks, not located hunks. Wanting less is a reason to *project*, not to
  re-parse: `_commit_files` runs `unidiff.parse` over a `-U0` diff and flattens
  it (`added_lines` / `removed_lines`), dropping hunk boundaries and line
  numbers on the way through.
- **one diff-header path reading**. Two readers disagreed about git's C-quoting
  of non-ASCII paths — one stripped the quotes but not the `a/` under them, the
  other stripped neither — and `diffview` joins that path onto the checkout to
  attribute the change, so debris means a path matching nothing on disk.
- **one file-summary line** (`diffrows.file_summary`): `path · change · +N −M`,
  printed by the Watch's expanded commit and the `--stat` view about the same
  file from the same parser.

The cost of having had two is measurable, not hypothetical. `---` and `+++` are
file headers *before* the first hunk and ordinary rows inside one; the Watch's
reader took them for headers everywhere, so a deleted `-- comment` vanished and
an added `++ bumped` rewrote the file's path from its own content. Replayed over
this repository's history, 50 of 51 commits parsed identically and one did not:
an ADR deletion whose body held markdown rules (`----`) and a `---output-format`
line, all silently dropped. The same bug sat on the dirty-file path, where
`_line_diff` formatted a `difflib` diff only to strain `---`/`+++` back out of
the text — there a change consisting *only* of such lines produced no Feed Event
at all. It reads the matcher's opcodes now and formats nothing, so that reader
is gone rather than fixed.

*Rejected: keeping the Watch's parser and fixing the dashes rule in both* — the
rule is subtle enough to have been got wrong once, and two copies of a subtle
rule is the thing that produced the bug.
*Rejected: giving `CommitFile` the parser's full hunk shape* — the Watch renders
a change as it lands, with no line numbers on screen to carry; the projection is
the boundary that keeps the Watch's model as small as what it draws.

## Discovery is an entry point, not the constructor

**`WatchStream.discover(u, repo_arg)` asks the environment; `WatchStream(...)` is
handed the answers.** Discovery owns every question that only the machine can
answer — the Derived Cache and the log scan behind `u.sessions()`, the Project
Handle (or path) resolution behind `_resolve_target`, and where the logs live —
and then constructs the stream from name, checkouts, Sessions and log directory.
Nothing else in the module imports the Universe for its own use.

The reason is testability of the *decisions*, not of the plumbing. Every choice
the stream makes — which Sessions get a lane, how a hunk becomes a Feed Event,
when a tool verb yields to `thinking`, whether a dirty path was already
explained — used to be reachable only through a Scan Universe over a real
`~/.claude`, so the Watch's own rules were pinned by the two witnesses' tests
and by nothing that could see the stream object itself.

Deliberately *not* moved: the git watcher's seeding. It runs in the constructor,
against the checkouts it was handed, so building a stream still shells out to
`git` — a scratch repo is the whole of that environment. The ground-truth half of
the Watch *is* git; faking it would test the fake (`tests/support/repos.py`).

**The UI reads published types only.** Two facts the UI used to take from the
stream's insides now travel on them:

- **the log path**, on `LiveSessionInfo` (and `session_info(sid)` for a Session
  that is tailed but no longer live) — the `s` key opens a Transcript, and used
  to read `stream.tailers[sid].session.log_path`;
- **the widened Live window**, on `Vitals.widened_window` — the fact, not the
  window. The UI compared `stream.live_window` against `LIVE_THRESHOLD`, so the
  default window was a constant imported across the boundary and the decision
  "is this worth stating" was made twice.

*Rejected: a fake Universe for tests* — it pins the Universe's shape, which is
another module's contract, and leaves the stream's own constructor untested.
*Rejected: keeping `live_window` on the published surface and letting consumers
compare* — same rule in two places, and the second copy is the one that goes
stale when the default moves.

## Motion never outlives the data

**A moving glyph asserts liveness, so it may only be driven by arriving bytes —
never by a state word.** Both animated things obey this from opposite
directions.

- The **typing animation** is presentation-only under a hard staleness bound:
  display lags the log by at most a few seconds, and typing speed compresses —
  down to instant — to honour it.
- The **activity spinner** animates only while the log is still being appended
  (the `FRESH` window the header uses for recency), then freezes to a static
  muted glyph. A session killed mid-turn leaves a log tail indistinguishable
  from one still working; without the freeze the worst outcome is reachable —
  watching a spinner turn for ten minutes on a dead session.

A frozen spinner does **not** label the session dead — that stays unknowable. It
stops it looking alive, which is the honest half.

## The Activity State

The feed cannot answer *is it still working, or waiting on me?* — nothing appears
when a turn ends. Finding out meant switching to the Claude Code terminal.

**Activity State is derived from the tail of a Session's own log, mid-turn
only.** A settled session has no Activity State and the Watch says nothing:
absence is the answer.

| From the log | State |
|---|---|
| a `tool_use` **block** on the line | the tool's verb — `reading` / `writing` / `running`, or `acting` unmapped |
| any other `stop_reason` | none: turn over |
| `interruptedMessageId` / `interruptedByShutdown` | none: turn cut short |
| a `tool_result`, or a user prompt, with no assistant line yet | `thinking` — **inferred from silence** |
| a line naming no tool | unchanged: the model is still composing |

Four commitments:

**`thinking` is an inference and is documented as one**, here and in the manual.
It is not `~`-marked: the tilde marks claims about *what happened or was
intended* (a Brief objective, an attribution guess), and tilde-ing one verb of
five would imply the other four are certain in a stronger sense than holds.
Honesty is carried structurally instead.

**No stall threshold, ever.** A non-terminal state shows its own age and nothing
else. `thinking 14m` speaks for itself; re-labelling it `stalled` would be
Standup inferring a process died — the claim **Live Session** exists to refuse.
The reader knows whether they asked for something that takes fourteen minutes.

**A tool verb has a display floor** of `ACT_FLOOR` (1s) before `thinking` may
replace it. Without it `reading` and `writing` were unobservable: across eight of
this repo's sessions they held the state for 7 and 8 seconds *in total* against
6740 for `thinking`, because a local `Read` returns in ~25ms — below the poll
interval. The band read `thinking` in nearly every frame, which is *true* and
useless: the question it exists to answer was answered "composing" while the turn
was in fact fourteen file reads.

That floor is a **word** outliving its tool, which the motion rule bans for
*motion* — so the boundary is stated, not inferred. Motion asserts liveness and
may never be synthesised; a verb asserts what the last thing was, and one second
of staleness buys a legible answer. Three yields keep it load-bearing:
- a settled or interrupted turn clears the held verb, so the bar still blanks
  the instant the agent hands control back (verified: replaying those logs at
  the UI poll rate leaves the blank-frame count identical);
- the next tool verb overwrites immediately — the floor delays staler silence,
  never fresher news;
- the displayed age is the verb's real age, never the floor's, so a held
  `reading` reads `0s` rather than an invented figure.

**Not a Feed Event.** A state is not something that happened; one row per
transition would have added ~248 rows to a real 124-tool-call turn in this
repo's logs — three-quarters of the feed becoming state chatter. It lives in the
status bar only.

`isSidechain` lines are skipped: a subagent's reads are not the session's, and
several subagents have no single answer. Every acting session is named, in lane
order, because a settled one costs zero cells — the display grows only when work
is genuinely parallel, which is when seeing all of it matters. It degrades to a
bare count on a narrow terminal rather than truncating a verb.

Accepted error: a session killed in the gap after a `tool_result` reads as
`thinking` until it ages out of the Live Session window. The frozen spinner makes
this visible; nothing in the log can make it precise.

## Calls

The feed narrated two tool families — `Bash`, and the edit tools as file events
— and dropped every other tool on the floor. A session whose work is MCP
requests therefore produced **no rows at all**: measured on three of this
repo's own sessions, `976d3db8` showed 6 rows for 29 tool calls and `b41c3ff5`
showed 8 for 43, and in both cases the Notion work the session existed to do
was the part that vanished. The status bar said `acting`, correctly and
uselessly — it names a verb, not a target, and it is gone the moment the call
returns.

**Every tool call that changes no file is a Call: one Feed Event kind, `Bash`
included.** A `bash` event was already "a tool call, its argument, and a ✓/✗",
which is exactly the row the missing tools needed, so the kind was generalised
rather than duplicated — one pending-call join, one result back-patch, one
expand rule, one glyph.

**Silence is a denylist, not an allowlist.** `Read`, `Grep`, `Glob`,
`NotebookRead`, `BashOutput`, `KillShell` produce no row; everything else does,
*including a tool that ships next month*. This is the call `ACT_VERBS` already
makes when an unmapped tool falls to `acting` — the failure mode is a new tool
being visible, never invisible, and an allowlist is a table that goes stale by
default. A local read is the one thing genuinely worth dropping: it changes
nothing, and `reading` in the status bar already answers for it.

**The header carries as much of the input as the row holds; the body carries it
entire; neither ever shows the result.** The Watch renders what was *asked* — an
expanded `Bash` event has always shown the command and never its output. A
`notion-fetch` result is 50KB of page markdown, and putting it in the feed makes
the Watch a Transcript. The ban covers the expanded body too, so a failed Call
shows the request that failed and not the error: accepted, and the reason
`session --tools` exists as the retrospective surface.

**The body is built from the input, never from the header's digest.** A digest
cannot be expanded back into what it summarised, and for a year the digest was
the only copy the feed held: `FeedEvent.args` was a string, so
`{page_id, command, content_updates}` reached the UI as the 14-character
`update_content` and `enter` had nothing to open. The event carries the input
dict; the header reads a digest of it, the body reads the input. Median 173
bytes across this machine's logs, p99 4KB, max 29KB — a full `BACKFILL_CAP` of
Calls is ~200KB, so there is no size argument for keeping only the summary.

**The body is one row per leaf, by path** (`content_updates[0].new_str`), with a
value's own newlines becoming rows. Not `json.dumps(indent=2)`: the value being
read here is 5KB of markdown, which pretty-printing leaves as a single escaped
string. Structure stays recoverable from the paths, so the reshape is
presentation, like the diff row shape. No cap and no second expand level — an
expanded file event already shows every line, and 97% of inputs are under 2KB,
so a level for the rest is a rule that almost never fires.

**A Call advertises its body** — `▸ N lines`, flipping to `▾`, exactly as a file
event shows `▸ N lines` and a commit `▸ N files`. It is absent when the header
already carried the whole input, which makes the absence load-bearing: a Call
used to advertise nothing either way, so "nothing to open" and "expanding is
broken" looked identical, and the row that showed least was the one that refused
to open. Marker and verdict are budgeted *before* the argument and the argument
clips against what remains: they are the row's two facts — did it work, is there
more — while the argument is the one part with somewhere else to be read.

**One renderer, shared with the Transcript** (`toolcalls`), which was already
rendering tool one-liners and rendering them badly: raw
`mcp__5ac0edc4-…__notion-fetch`, and *no argument at all* for that call, because
its input key `id` was absent from a preferred-key tuple. Same payoff as
`diffrows` sharing the row shape with the Attributed Diff. Two rules live there:

- **A UUID server segment is dropped.** claude.ai connectors log as
  `mcp__<server>__<tool>` where `<server>` is a bare UUID with no local mapping
  to a name — not in `~/.claude.json`, not in the JSONL (`mcp_instructions_delta`
  carries the same UUID). Printing it names nothing and costs 36 columns of a
  header that clips. A server the log *can* name is kept and joined with `·`,
  because two servers may expose the same `computer`.
- **The preferred key decides what *leads* the argument, never what it
  contains.** The tuple survives from the Transcript because `Read /path` beats
  `Read {"file_path": "/path"}`; the remaining keys follow it as compact JSON,
  and a tool absent from the tuple falls to JSON for all of them. Ranking a key
  used to mean discarding its siblings — see *Tried and retracted*.

**Calls do not fold.** Ten `notion-fetch` calls are ten rows. The Change Run
exists because `MultiEdit` hunks and git-poll windows are *artifactual*
multiplicity — one act of work chopped up — whereas ten fetches are ten acts
with ten arguments, and a folded header could only describe its body by showing
one of them or none. Bash has never folded either.

**One kind, one glyph.** `⏺` marks every Call; the text says which. Bash keeps
`$` and shell lexing as its own argument sigil. A second glyph for MCP would
split one kind into two marks, which is what the unification was for.

Accepted costs, stated rather than engineered around:

- **Most Calls now carry a disclosure marker.** 2648 of 2957 Calls replayed from
  this machine's logs (90%) hold more than their header shows, so the right edge
  is rarely empty. That is the measurement, not a regression: file events carry
  `▸ N lines` at the same rate, and the 10% without one are exactly the
  single-argument calls where the header is the whole story.
- **A long argument now clips sooner.** The digest carries every key, so it
  reaches the width more often, and the header gives ground before the verdict
  or the marker do. The argument is the part with a body to be read in full.
- **The feed is taller.** Those three sessions go 6→29, 26→75, 8→43 rows. A
  Call landing between two edits of one file *closes* that file's Change Run
  under strict adjacency — correct by that rule (the reason two rows didn't
  merge is on screen), but it is a real change to how a mixed session reads.
- **`BACKFILL_CAP` (400) now buys fewer chapters**, since a chapter holds more
  events. Oldest events are dropped first, so the cap degrades toward the
  recent, which is the right direction.
- **`-q` still drops Calls**, keeping its documented promise of files and
  commits only — so `-q` on an MCP-only session shows nothing. That is the
  flag working, not failing.
- **Plumbing tools get rows** (`ToolSearch`, `TodoWrite`). The denylist is
  deliberately minimal; each entry added to it is a table entry, and the whole
  point was not to keep a table.

*Rejected: a row per tool call including local reads* — ~124 extra rows on a
real turn in this repo, and every one of them cuts a Change Run in half.
*Rejected: folding Calls into runs* — costs the "header describes its own body"
rule for multiplicity that is genuine.
*Rejected: fixing only the status bar* (`acting notion-fetch 3s`) — it answers
what is happening now and nothing about five minutes ago, and the Watch is used
as a monitor of work already done.

## The Change Run

One Feed Event per witnessed change, one row per event, meant a file actually
being worked on arrived as a stack of near-identical rows. Two mechanisms
produced the stack; only one was about the agent.

**The git watcher's rows were a sampling artifact.** It diffed each dirty file
against *the previous poll's snapshot*, so one burst of writing became one delta
per poll window and a run reading `+8 +4 +1 +1 +1` described the poll rate. It
now keeps a **reference snapshot** per dirty file and emits the *cumulative*
delta: each event restates the path, and a line added then removed cancels
rather than counting twice. The reference is HEAD for a file that dirties during
the run, and the *launch* snapshot for pre-existing dirt — old news, and
replaying it as one giant event would bury the live narrative.

**A Session's rows were real, and still too many.** Each is one `Edit`;
`MultiEdit` emits one per hunk. Five hunks on one file inside ten seconds is one
act of work to the person watching.

**Consecutive file events for the same path and same witness render as one
Change Run** — one header, one evolving body. The fold is *presentation*, like
the typing animation: a Feed Event is still one tool call and nothing is
discarded.

Four bounds:

**Strict adjacency.** A run grows only at the feed's tail; any intervening event
closes it. So a row above the reader never changes shape — the sticky-scroll
idiom (the view holds still, the data never does) — and when two rows *don't*
merge the reason is on screen.

**The header always describes its own body.** A restating run states the net
delta; an accumulating one states the sum of its hunks. Net delta is unreachable
for a Session's claim (the log carries hunks, never file content), and borrowing
it from the git watcher would produce a header disagreeing with the body and
silently revising itself two seconds after being read. A count is trustworthy
because you can verify it against what it shows, so that beats uniformity. A
claimed run carries `×N` counting **tool calls** (one `MultiEdit` is `×1`),
because the merge is what makes the counts large. A restating run carries no
mark: N would be a fact about the poll interval.

**The window sits where the news is.** A collapsed body shows `HEAD_LINES` of
added text. An accumulating run is chronological, so past one contribution it
shows its **tail**, earlier lines counted above. A lone event and a restating run
show their **head** — a single hunk reads top-down, and a cumulative body is a
file-ordered snapshot with no newest end. A run therefore stays bounded in height
however much it absorbs, which is the actual noise cap. Appends type in from
where the last contribution stopped; restatements land instantly, since
re-typing on-screen rows every two seconds is flicker, not animation.

**A run closes after `RUN_WINDOW`.** Adjacency alone would let one block absorb a
three-minute burst, and a feed that stops producing rows while the agent works
hardest reads as dead. The cap is `FRESH`, so sustained work yields a row every
30 seconds, and a run's displayed timestamp (its first event's, never revised)
can never be staler than that.

Stated rather than engineered around: an empty cumulative diff emits nothing, so
a run whose file is reverted to its reference keeps its last figures. A Change
Run states the delta *as of its last update* — as does every other feed entry.
The Watch is a feed of what happened, not a dashboard of current state.

## The removed-row field

The two-channel rule — *the gutter says what changed, the text says what it is* —
protects the syntax palette: monokai's colours mean *token kinds*, so
change-semantics stayed out of the code text.

Its cost shows up in monitor use. Additions are expected (an agent mostly
writes), so the thing most needing to be *caught* is a deletion — announced by a
single `−` in a 2-column gutter. A bolder `−` is still a glyph you must look at,
and dimming removed code breaks the equal-strength commitment while making the
code you most need to read the hardest to read.

**Removed rows carry a faint red background wash — a third channel that restates
the gutter.** Monokai stays foreground-only at full strength and the `−` remains
the carrier. The wash is redundant by construction, so `NO_COLOR` and 16-colour
terminals lose nothing.

1. **A rectangle, from the sign column to full content width.** Diff lines are
   ragged; a wash ending with the text is a torn shape, and the eye locks onto
   rectangles. Cost: trailing spaces on a mouse-drag copy.
2. **Never reaches the gap gutter or session lane.** The lane's per-session
   identity hue is its own channel; tinting under it would muddy the one mark
   saying *which agent did this*. The blank columns between become a deliberate
   isolating margin.
3. **Deletions only.** Added lines are sliced by character count for the type-on
   animation: a full-width wash behind an untyped line telegraphs its existence,
   and a wash growing with the characters is bound 1's torn shape, crawling.
   Removed lines are emitted whole, which is *why* they can hold a clean
   rectangle. The asymmetry falls out of the animation model.
4. **A tint of `surface`, not a shade of `removed`.** Monokai's bright
   foregrounds need a dark substrate, so the dark value darkens (`#291A1E`) and
   only the light value lightens (`#FCEBEB`, carried for completeness — the
   light variant is unreachable). Calibrated by eye: quiet enough to sit under
   monokai, bright enough to find.

Every `−` row is washed — collapsed preview, expanded body, expanded commit
diffs — so the rule has no exceptions to memorise. `… ▸ N more lines` is **not**
washed: it carries no `−`, is a count rather than removed code, and leaving it
out gives the block a clean bottom edge.

The wash rides the `paints_backgrounds` gate, so truecolor and 256 only. 256 is
the one place the intent can't be honoured: the cube's darkest red is `#5f0000`,
dark in luminance but saturated, so those terminals show a louder wash.
Recorded rather than worked around; the alternative was dropping the feature
there entirely.

The strength is deliberately at the quiet end and the hex is the part expected to
need revisiting. If the tint proves invisible on a low-contrast monitor, change
the value — the geometry is what makes a faint tint legible, so it is not what to
trade away.

## Fold, don't clip

Every row was one terminal row. That bought a predictable row count per event,
gutter and lane at fixed columns, a typing animation growing by whole lines, and
repaint-without-relayout on resize.

It also made a line wider than the terminal **unreadable past the right edge,
with nothing saying so**. Tolerable for a header — a summary Standup composes and
can shorten honestly. Not for a body: a diff body is not a summary of the change,
it *is* the change. A 200-character line showed its first 80 and silently dropped
the rest, and `enter` did not help — expansion adds more lines, never more of a
line.

**Wrap is on by default, for body lines only.** A body line folds; a header line
still clips. **The feed's own text may be shortened; the code it is quoting may
not.**

Folding is done by the widget, not the terminal or Textual's CSS. Every row it
emits is a whole row built through the same prefix, so gutter and lane are
present on continuation rows and the `no_wrap`/`clip` CSS is unchanged — with
wrap off it is still what clips. That keeps the old invariant's *layout*
guarantees while giving up its row-count guarantee.

1. **The `↳` carrier.** A continuation row shows a faint `↳` where the sign would
   be. A fold is one source line, so the sign is stated once; the glyph — not a
   colour — says "still the same line", which is what survives `NO_COLOR`.
   Continuation rows start in the *same* code column, so indentation reads.
2. **The wash spans folds.** The removed-row rectangle is painted per row, so a
   folded removed line stays part of it.
3. **A hard row bound, counted not dropped.** At most `WRAP_ROWS` (40) rows per
   line; past that the tail is `… +N chars`. A minified bundle is one line of
   200 KB, and an unbounded fold would hand one event the entire feed. The bound
   is a *stated* loss, which is the difference from the clipping it replaces.
4. **Word-aware**, folding inside a token only when it must.

`w` toggles it, `--no-wrap` starts without it, and the status bar names the *off*
state because on is the default. Clipping stays useful — a fixed row count per
event lets you read the shape of the last few minutes — so this is a change of
default, not a removal of a mode.

An event's height is no longer predictable from its line count; nothing depended
on that (scrollback works in rows, the DOM cap counts *events*, the animation
counts *characters*). A long-line file event can dominate the screen, which is
the honest rendering of a change to a long-line file.

**Headers keeping their clip is a documented promise.** A future "wrap the header
too" has to argue with this record.

## Selection in the lane

The selected event was marked by a background across the whole widget. That is
the conventional list idiom and wrong for this list: an event here is not a row
you pick from a menu, it is a diff you *read*, so the widest, longest-lived band
in the app landed on the content most needing a neutral substrate. Three costs —
the substrate moved when reading started (monokai is calibrated against a
near-black page); the removed-row field had to yield, stripping the scanning aid
from the very event under review; and it restated a boundary the session lane was
already drawing.

**The selection is drawn into the session lane's bar column — one cell — and
nowhere else.** That cell takes the `selection` background and the bar thickens
`▏` → `▎`, on every row the event occupies: header, body, fold continuations.
There is no widget background at all.

Two geometric bounds, both learned by getting them wrong:

- **The bar grows in place.** `▏` and `▎` are both left-aligned eighth-blocks, so
  thickening moves no pixel of the left edge. The first attempt used `┃`, which
  terminals centre in the cell: selecting slid the lane sideways, and the feed's
  spine jumping is worse than no mark. **A selection may change a column's
  weight, never its position.**
- **The rail has no holes.** One cell wide, present on *every* row including fold
  continuations, which the diff path used to blank. A broken rail reads as
  several blocks — the opposite of what the mark is for.

Consequences: the feed's surface is never repainted under an event, so code is
read on the substrate monokai is calibrated against; the removed-row field yields
to nothing, since selection and field share no cell; and the mark rides the
channel that already means "this block".

One column wide reads as a rail rather than a wash, and the glyph change is the
`NO_COLOR` carrier. At 16 colours the cell falls back to reverse video — one
inverted column is a rail, not a page.

`selection`'s hex is unchanged but its job is not: calibrated as a wash under a
block, now a one-column rail. It is the value most likely to need brightening;
the geometry, not the hue, is what this fixed.

## Mouse gestures

Both rules exist because a gesture aimed at something else was read as a command.
Both guards live on `WatchApp`'s two click seams, not in the widget handlers: the
widgets only forward, so the app methods are the one place every gesture passes,
and a future clickable surface inherits the behaviour by using that seam.

### A refocusing click is not a gesture

Returning to the Watch is usually a mouse act. macOS terminals deliver that click
through, so returning expanded a **random** event — whichever the pointer rested
over — and selecting releases the bottom anchor, so a glance at the live feed
became a scrolled view of an arbitrary diff needing `G` to undo. The same click
on the vitals header toggled a session filter.

Two facts make the fix available: terminals implement focus reporting
(`CSI ?1004h`) and Textual enables it unconditionally; and the focus report
arrives in the *same read burst* as the click that caused it.

**A click arriving with the terminal regaining focus is discarded** — for
`REFOCUS_GRACE` (0.35s) after the blurred → focused transition.

- **A window, not a flag.** "Swallow the next click" arms indefinitely: a
  `⌘-Tab` return would eat an intended click a minute later. 0.35s ties the
  discard to the click that carried the focus change; no human clicks a target
  350ms after deciding to switch windows.
- **Only a real transition arms it** — the stamp is taken only when blurred, so a
  terminal re-reporting focus cannot make the Watch deaf.
- **Degrading is doing nothing.** Apple Terminal never reports focus, so the
  stamp stays zero and every click counts. The guard cannot make a terminal worse
  than it was.

It assumes `FocusIn` precedes the mouse report — what terminals do, not what the
spec promises. If one reverses it the window never fires: the failure mode is the
old behaviour.

This is the Watch's first state about the *terminal window* rather than the
stream, and it is deliberately not shown — the Watch has no modal states, and a
window's focus is not its business to display.

### An open body is text, not a control

A click selected an entry and toggled expansion, with the whole widget as target.
Right for a *closed* entry, wrong for an open one: what you do with an expanded
entry is **read** it, and reading uses the mouse — drag through a prompt to copy,
double-click to take the entry, click a URL. Every one ends in a `Click`, and
every one collapsed the thing being read.

Two mechanics worsen it: Textual synthesises a `Click` whenever the button goes
down and up on the same *widget*, so a **drag** is delivered as a click carrying
the cell it was *released* on; and a double click sends `chain=1` then `chain=2`,
the second being Textual selecting the widget's text.

**A click acts on a closed entry; once open, only its own furniture does — header
row and left rail.** And **a gesture about reading the text is not a click**: a
drag (pressed on one cell, released on another) and any `chain > 1` are discarded
at the same seam.

A closed entry is entirely the feed's own prose, so pressing anywhere means "open
this". An open entry's body is code, command or prompt quoted verbatim; it
belongs to the reader.

The furniture is the part still the Watch talking. The rail is the right handle
because a body can be taller than the screen — then the header is off the top and
a header-only collapse would be unreachable without scrolling back — while the
rail sits beside *every* line in the same columns, so folding is always one click
to the left of wherever you stopped reading. `enter` does the same from the
keyboard.

- Drag-select works inside an open body, and `ctrl+c` copies through the
  terminal's clipboard escape (Apple Terminal excepted). A double click selects
  the whole entry.
- A click on a URL is no longer swallowed; whether it *opens* is the terminal's
  business. The Watch emits no hyperlinks, it just stops competing.
- Clicking the body between rail and right edge does nothing — a deliberate
  silence, since any other answer would be a state change made by a gesture aimed
  at text.
- The rail is a fixed `RAIL_COLS`, the prefix width, true of every row the widget
  draws. Re-sizing the left columns moves the handle only if that constant moves.
- The drag rule also covers the vitals band, where a drag across a session row
  used to toggle a filter on release.
- A double click on a *closed* entry opens it: its first click is real, only the
  second is discarded.
- The rule is stated in terms of the widget's row 0, which is exactly the header
  because headers clip to one row and never fold. A folding body cannot push the
  handle out from under the pointer.

## Tried and retracted

Six shipped rules were reversed. The code still carries their shape, so a reader
diffing against an older spec would otherwise read current behaviour as a bug.

- **"The argument is a preferred key's value" — the key that wins excludes the
  rest.** Shipped with Calls and reversed *(2026-08-09)*. The tuple was written
  for single-argument tools, where a bare value beats JSON; 72% of this machine's
  4615 non-Bash calls are multi-key, and 1108 of them lost their remaining
  arguments to the ranking. The worst shape is `{page_id, command, …}`, where
  `command` outranks `page_id` and the row renders as the bare verb
  `update_content` — a whole screen of Notion writes reading identically, none
  naming its page. Compounding it, the body was gated on the header having
  *visibly clipped*, so those 14-character rows were also the ones `enter`
  refused to open. Both halves are gone: the digest keeps every key, and the body
  is built from the input.

- **"Highlighting is foreground only — no line washes."** The original
  two-channel doctrine, stated in `theme.py`, the UI docstrings and the manual.
  Voided by the removed-row field, which is redundant by construction — which is
  what makes it compatible with the original rule's reasoning.
- **"Nothing reflows — rows only truncate, so the grid never breaks."** Voided by
  folding. The layout guarantees it bought are preserved; only the row-count
  guarantee was given up.
- **The removed-row field yields to the selection.** When selection was a
  whole-widget background, a `Text` bgcolor beat the widget's CSS background, so
  the naive version punched red holes in the selection wash on the very event
  under review. The field was dropped on the selected row, under a general rule:
  when two backgrounds collide, the one carrying unique information wins. Voided
  when selection moved to the lane — the collision cannot happen, and the
  parameter expressing it was deleted (a knob with one caller and one reason
  outlives its reason badly). `removed_bg` keeps its value, now for its own sake
  rather than out of deference.
- **The whole-widget selection band.** Replaced by the one-cell rail. Under
  `NO_COLOR` it degraded to reverse video across the whole widget — the same
  substrate problem, louder.
- **"A user line carrying a `toolUseResult` is not a prompt."** The Watch's own
  prompt rule, one condition stricter than the Transcript's. Retracted when both
  moved onto the one log reader (ADR 0001 § the one log reader): the two rules
  part on a single shape — prose typed *while a call was in flight*, which the
  log records on the same line as that call's result — and dropping it lost a
  real instruction from the feed. The reader's rule (prose makes a prompt)
  stands for both surfaces. Consequence to know: such a line is a full prompt,
  so it is a chapter break, and the backfill's replay starts there.

One Activity State reading was also retracted after meeting real logs *(2026-08-04)*:
`stop_reason` is the *message's*, not the line's. Claude Code flushes a message's
`text` and `thinking` blocks as their own JSONL lines, each carrying that
message's `stop_reason: tool_use`, so the original table's first row matched lines
announcing no tool and fell through to `acting`. Across eight sessions, 315 of 838
`tool_use`-stop-reason lines named no tool (231 `thinking`, 84 `text`) and every
one was misread. The table now requires a `tool_use` **block** on the line.

## Alternatives considered

- **Git polling as the event source** — anonymous, poll-latency bound, cannot
  narrate intent.
- **Hand-rolled ANSI/termios, or `rich` alone** — a mini-framework owned forever,
  or rendering without input handling.
- **Activity State as a Feed Event** — ~248 rows on a real 124-tool-call turn.
- **A Call row for local reads too** — ~124 extra rows on that same turn, each
  one closing a Change Run.
- **A `tool` kind beside `bash`** — two renderers, two pending-call dicts and
  two result joins for one row shape.
- **An allowlist of tools that earn a Call** — a table that goes stale by
  default, which is the defect the Transcript's preferred-key tuple already
  demonstrated in production.
- **A stall threshold** — Standup inferring that a process died.
- **Binding a tool verb strictly to its execution window** — made `reading` and
  `writing` unobservable (15 seconds of visibility across eight sessions).
- **Coalescing Change Runs in the stream** — needs a revision protocol so the
  stream can say "revise what I gave you", and still needs the same UI code.
- **A time window merging Change Runs across intervening events** — would rewrite
  a widget scrolled off above the reader, or reorder the feed.
- **A bolder `−`, or dimming removed code** — still a glyph you must look at; or
  it breaks the equal-strength commitment while making the code you most need to
  read the hardest to read.
- **Dropping the removed-row field on 256-colour terminals** — recorded as a
  known louder rendering instead.
- **Wrapping headers too** — headers are Standup's own prose and can be shortened
  honestly; only quoted code may not be truncated.
- **"Swallow the next click" as a flag** — arms indefinitely.
