# 0004 — The Watch

Date: 2026-08-06

`standup watch <repo>` is Standup's only live view. Every other view is a
snapshot rendered to stdout; the Watch runs in a terminal you leave and return
to while an agent works, so it is the one surface that must answer *is something
happening now* rather than *what is the state*.

Eight decisions, recorded together because they are one design — each later one
reaches into an earlier one's geometry.

## The event source

**The Watch tails the session JSONL as its primary stream; git is the
ground-truth layer.** Git only says "the tree differs", anonymously and after
the fact. The JSONL is appended live and carries the actual tool call
(`Edit`/`Write`, with exact text), the Session identity, and the intent. Git
confirms tree state and is alone able to reveal live Unattributed Changes — the
same claims-vs-truth split Attribution uses everywhere else.

*Rejected: git-only* — anonymous, poll-latency bound, cannot narrate what the
agent is doing.

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

Four shipped rules were reversed. The code still carries their shape, so a reader
diffing against an older spec would otherwise read current behaviour as a bug.

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
