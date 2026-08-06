# `standup watch` — user manual

The **Watch** is Standup's only live view. Every other view is a snapshot: run
it, read it, it's over. The Watch renders *time passing* — one repo, narrated
as an agent works in it.

This manual walks a new user from "what am I looking at" to every key on the
keyboard. Domain words in **bold** are defined in [CONTEXT.md](../CONTEXT.md);
the design decision behind the view is
[ADR 0004 § the stream/UI boundary](adr/0004-the-watch.md).

---

## 1. What it does

The Watch interleaves everything happening in **one Repo Entry** (a checkout
plus its worktrees) into a single chronological feed:

- **file edits**, typed out character by character as they land — consecutive
  edits to one file fold into a single entry that evolves, so a file being
  worked on reads as one act of work rather than a row per tool call
- **Calls** — every tool call that changes no file: a shell command, an MCP
  request, a web fetch, a subagent spawn. Each shows the tool's name, one line
  of its argument, and `✓`/`✗` when the result comes back. Local reads
  (`Read`, `Grep`, `Glob`) are the exception and stay silent
- **your prompts**, as the chapter breaks of the narrative
- **commits** — each carrying its own diff — **pushes, branch switches**
- **Unattributed Changes** — dirt that no session claims — the moment they appear

Above that feed it also answers the one question the feed can't: **is the agent
still working?** Each session that's mid-turn shows its **Activity State** in
the status bar — `thinking`, `reading`, `writing`, `running` — and a session
that has handed control back shows nothing at all. See §3.

It reads two sources, exactly as the **Triage Inbox** does: the Claude Code
session log claims *who and what*, and git confirms *ground truth*. Assistant
prose and thinking never appear here — reading the conversation is
`standup session`'s job. (The `thinking` **Activity State** is not an exception: it
reports *that* the model is composing, never a word of what it is composing.)

The Watch is read-only and stateless. It never writes to your repo, never
touches the session, and quitting it loses nothing.

## 2. Starting it

```bash
standup watch
```

That watches the repo you're standing in. You can also name one — `w` is the
alias for `watch`, and `st` is standup's **Project Handle**:

```bash
standup w st
```

The argument is a **Project Handle**, a full repo name, or a path to a git
checkout; it defaults to `.`. A handle is the underlined letters of a project's
name in the inbox — the acronym for a multi-word name (`pm` for
ProjectManagement), the shortest unique prefix otherwise (`st` for standup) —
and it resolves against the **Scan Universe**, the repos some Claude session
has visited. A fragment that fits two projects is an error listing both, never
a silent pick; nothing matching at all lists the known names.

A bare word is always a handle, never a directory, so a folder in your cwd can
never shadow a project. To force the path reading, write it as a path: `.`,
`./robin`, `../other`, `~/code/thing`. A path works even for a repo no session
has ever touched: git alone can narrate.

Files and commits only, no Call/prompt/session noise — `-q`, `--quiet`. Note
what that costs on a session whose work is all MCP: with no file changes there
is nothing left to show, and the feed stays empty. That's the flag doing its
job, not the Watch failing.

```bash
standup watch -q
```

Long body lines fold by default; start with them clipping at the right edge
instead — `-W`, `--no-wrap`, the `w` toggle (§4) off from the first row. The
capital says it turns something off; every negative flag in this CLI is spelled
that way ([ADR 0005 § short option letters](adr/0005-addressing-on-the-command-line.md)):

```bash
standup watch -W
```

Pick up sessions that already went quiet — the **Live window** is 30 minutes by
default, and `-s`, `--since` widens it:

```bash
standup watch -s 2h
```

The window is a duration (`45m`, `2h`, `3d`, `1w`), never a date: it is re-read
against the clock on every poll, so `since 9am` would mean a longer span every
minute you watched. It decides two things at once — which sessions are tailed
(each one backfills its current chapter, §3) and which ones the vitals band
still calls live. A widened window is stated in the header, `live ≤2h` beside
the watch clock, because `live` then means something other than the default.
Use it when you sit down after the agent did the work: at the default, a
session last appended 40 minutes ago isn't there to filter to.

The palette assumes a dark terminal, and there is no light variant — a
`--light` flag existed and was withdrawn. The chrome converted cleanly; the
code bodies did not, because the syntax palette (monokai) has no light-page
counterpart, so on a light surface the diff text faded to near-invisible. A
Watch you can't read the diffs in is worse than one that assumes the wrong
background, so the flag is gone until a light syntax theme goes with it.

The Watch needs an interactive terminal. Piped or redirected, it refuses
rather than printing something half-alive.

**Quit with `q`.** The alt-screen closes and a parting snapshot is printed on
plain stdout, so it stays in your scrollback:

```
watched standup for 12m — 2 sessions, 7 files touched, 1 commit
now: 3 dirty on main
```

## 3. Reading the screen

Three zones, top to bottom — a raised header band, the feed, and a raised
status bar. Backgrounds live only on the two bands, the selected event's lane
bar (one column) and the field behind removed code; everything else is
foreground on the terminal surface.

```
 standup · standup-cli   ⑂ main   ✎ 3 dirty            watch 00:12:02   ← vitals band
 [1]  a1b2c3d4  Add watch manual  ~document the TUI keys  ▁▂▃▅▆▃▁▂  31s ago
 [2]▸ 9f8e7d6c  Fix rate card rounding  ~round half-cents down  ▃▁▁▂▅▆▂█  1s ago
 ─────────────────────────────────────────────────────────────────────
       1▏ ── you: document the watch keys ─ a1b2c3d4 ────── 14:22 ──   ← the feed
        ▏ ✎  docs/watch-manual.md  create  +48
        ▏     + # `standup watch` — user manual
        ▏     + ▌
  +2m  ·· ⚑  commit @3712aaa  Watch: content-diff…  2 files +31 −4   ▸ 2 files
 ─────────────────────────────────────────────────────────────────────
  ● LIVE   [2] ⠹ writing 3s            ⏎ expand · d stat · [ ] chapter · ? keys
```

### The vitals band

- first row: the **repo name** (bold), `⑂` its branch, `✎` its count of dirty
  files, and how long this watch has been open — refreshed once a second; a
  Live window widened with `--since` is stated here too (`live ≤2h`)
- then one row per **Live Session** (at most three; more collapse to
  `… +N more`): its number `[1]`…, its **Session Handle** (cyan — the
  address), the derived title (bold), its **Session Brief** objective if one
  exists (italic, marked `~`, because a Brief is a claim, not a fact), an
  8-cell activity strip (events per minute over the last 8 minutes), and how
  long ago its log was last appended — in live-green when under 30 seconds
- `▸` after the number marks the session you've filtered to; clicking a
  session row toggles its filter
- when nothing is live the band states the absence plainly —
  `no live session · last log append 42m ago  (a1b2c3d4  Add watch manual)` —
  and the feed narrates from git alone

On narrow terminals (under ~100 columns) the activity strips and Brief
objectives drop; title and recency survive.

A **Live Session** is a *recency claim* — a log appended within the Live
window, the last 30 minutes unless `--since` widened it (§2). It is never a
statement that a process is running, which is why the
header shows `4s ago` instead of asserting "running". Session numbers are
**stable**: `[1]` is assigned when a session is first seen and never re-sorted,
so the number in the header, the digit in the feed's lane, and your filter all
keep pointing at the same session.

### The feed

Each **Feed Event** renders behind two narrow left columns:

- **the gap gutter** (columns 1–6): how long the feed was quiet before this
  event. Gaps under 5 seconds stay blank; second-gaps (`+40s`) are faint,
  minute-gaps (`+2m`, `+11m`) slightly brighter. There are no per-row
  timestamps — absolute time appears where you'd quote it: on chapter rules
  and the backfill boundary.
- **the session lane** (columns 7–8): the session's number at the start of a
  run of its events, then a `▏` bar tinted with its hue (blue, pink, teal for
  sessions 1–3; sessions past three get the digit only) for as long as the
  run continues. Git-only events — nothing claims them — get `··` instead.
  The digit, not the hue, is the carrier: under `NO_COLOR` the lane still
  reads.

Together those nine columns are the event's **left rail**, repeated on every row
it occupies — header, body, folded continuation. It is the block's spine, it is
where selection is marked (§4), and on an expanded event it is also the handle
that folds it again, beside whatever line you're on.

Then a kind mark and the content:

| Mark | Kind | What it means |
|---|---|---|
| `✎` | file | a file was written, edited, or deleted (session-claimed) |
| `~` amber | file | the same, but *git is the only witness* — tagged `~unattributed` |
| `⏺` | call | a tool call that changed no file — `$ cmd` for Bash, otherwise the tool's name and its argument. `✓` or `✗` appended when it returns |
| `──` violet | prompt | **you** typed something — a full-width chapter rule |
| `⚑` gold | commit | a new commit reached HEAD, `@<sha>` and its diff |
| `⇧` gold | push | commits left for a remote |
| `⑂` violet | branch | branch switch, `old → new` — it restructures the narrative |
| `~` amber | unattributed | changed files git can't even diff (binary, huge, unreadable) |
| `●` cyan | session | a brand-new session log appeared in this repo |

Every color is a role, and no distinction lives in color alone — each role
also carries a glyph or attribute that survives `NO_COLOR`: claims are `~` +
italic, commits are `@`-sigiled, milestones are `⚑`/`⇧`, the lane is a digit,
chapters are full-width rules, and dim/bold are attributes, not colors.

| Role | Worn by | `NO_COLOR` carrier |
|---|---|---|
| primary | titles, prompt text, subjects, branch names | — |
| muted | verbs, labels, counts' nouns, directory segments | dim |
| faint | gap gutter seconds, rules, separators, `▸`/`▾` | dim |
| address (cyan) | **Session Handles**, everywhere they appear | bare 8-hex shape |
| reference (grey) | commit hashes | `@` sigil |
| claim (amber, italic) | Briefs, `~unattributed` | `~` prefix + italic |
| chapter (violet) | prompt rules, branch switches | full-width rule / `⑂` |
| added (green) | `+` gutter, `+N` counts, `✓`, `● LIVE`, fresh recency | sign glyphs |
| removed (red) | `−` gutter, `−N` counts, `✗` | sign glyphs |
| removed-field | the faint wash behind removed code | `−` gutter (it only repeats it) |
| file | `✎` marks, bold basenames | bold weight |
| git-truth (gold) | `⚑` commits, `⇧` pushes — facts outshine claims | marks |
| session hues (blue/pink/teal) | lane digit + bar, header number — aid only | lane digit |
| selection | the band under the selected event's lane bar, col 8 | thick `▎` bar |

The exact values (dark, light, 256- and 16-color fallbacks) live in
`src/standup/theme.py`, one `Role` per row of this table.

Three markings carry the honesty rules:

- a `··` lane and an italic amber `~unattributed` tag mean *git is the only
  witness* — no session claims this change. That's not an error (and it is
  deliberately not red); it's hand-editing, another tool, or a squash. The
  Watch shows it live rather than hiding it.
- **position, not fading, marks backfill**: everything above the
  `┈┈ backfill · N events replayed from log ┈┈ … ┈ live below ┈┈` rule is
  history replayed at launch, everything below it is happening now. Replayed
  events are rendered at full strength — a replayed diff is as legible as a
  live one. See §6.
- claims are italic and `~`-prefixed, always and everywhere; facts are
  upright and unmarked.

One more marking keeps two lookalikes apart, and it ranks them. A **cyan
8-hex is a Session Handle**: an address you can hand to `standup session
<handle>`. A **git commit hash is always written `@<sha>`, in grey** — here
and everywhere else Standup prints one — because it is only a reference,
addresses nothing, and should recede behind the handle beside it. `standup
show` says as much if you hand it one. The colour is what makes the rank
instant on a terminal; the `@` is what survives piping and `NO_COLOR`.

Your prompts render as **chapter rules** — a full-width violet rule carrying
the prompt text (bold), the session's handle, and the time of day:

```
      1▏ ── you: document the watch keys ─ a1b2c3d4 ───────────── 14:20 ──
```

The prompt is what the agent was told; everything until the next rule is the
agent answering. `[` and `]` jump between chapters.

A file event's header reads `path  change  +added −removed`, where `change`
is `create`, `modify`, or `delete`, and the counts are lines. The path itself
is ranked: directory muted, basename bold, extension in the address family.
When a collapsed body has more lines than it shows, the count sits at the
right edge as `▸ N lines`; expanded, it flips to `▾`.

Below the header, a file event shows its body: up to 4 removed lines, then the
added text typing itself out with a `▌` cursor. Long blocks animate 12 lines
and collapse the rest to `… ▸ N more lines`. A line wider than
the feed folds onto further rows rather than running off the edge; `w` turns
that off (§4).

#### One file being worked on is one entry

A file the agent is actually working on doesn't arrive as one change. It
arrives as a `MultiEdit`'s hunks, or four `Edit` calls in ten seconds, or —
when git is the only witness — one delta per two-second poll. Rendering a row
for each buried the work under its own headers, so consecutive file events for
the **same file and the same witness** fold into one **Change Run**: one
header, one body, evolving as the work lands.

```
  +8s 1▏ ✎  src/standup/watchstream.py  modify  +6  ×5
       ▏      + # Display floor for a tool verb (ADR 0004 § the Activity State).
       ▏      + ACT_FLOOR = timedelta(seconds=1.0)
       ▏      +         self.act_tool: str | None = None
       ▏      +             self.act_tool = None
       ▏      +         self.act_tool, self.act_tool_since = self.act_verb, ts
```

`×5` counts the **tool calls** folded in — one `MultiEdit` reads `×1` however
many hunks it emitted, because that was one action. It appears only on
session-claimed runs; a `~unattributed` run never carries it, since there the
number would only tell you how many times the 2-second poll happened to catch
the file. Nothing is discarded by folding: `enter` expands the run to every
hunk it holds, and a **Feed Event** is still one tool call underneath.

What **closes** a run:

| Cause | Why |
|---|---|
| any other event between two same-file events — a Call, another file, a prompt chapter | a run grows only at the bottom of the feed, so a row you've already scrolled past never changes shape. When two rows *don't* merge, the reason is on screen |
| ~30 seconds since the run opened | sustained work on one file still produces rows — a feed that goes still while the agent works hardest reads as dead — and it bounds how stale the run's time can be |
| a different file, or a different session | — |
| the different *witness* — a session claim never folds into a git observation | they are different kinds of statement (see the honesty rules above) |
| the backfill boundary | nothing live grows a replayed run across the rule |

The gap gutter and the run's position are its **first** event's, and are never
revised — that is what keeps the rows above you still. The ~30s cap is what
makes that honest.

The two witnesses fold differently, and in both cases **the header describes
the body printed beneath it** — you can count the rows and get the number:

- a **session-claimed** run (`✎`) *accumulates*: the log carries hunks, so the
  body grows and the counts are the sum of the hunks shown. New text types in
  from where the last one stopped rather than restarting.
- a **git-witnessed** run (`~`) *restates*: the watcher diffs the whole file
  against a reference snapshot, so each update replaces the body and the counts
  are the true net delta — a line added and then removed inside the run cancels
  instead of being counted twice. Restatements land instantly; re-typing rows
  already on screen every two seconds would be a flicker, not an animation.

Because a run accumulates chronologically, once it has folded anything its
collapsed body shows its **tail** — the newest hunks — with the earlier lines
counted above as `… ▸ N earlier lines`. Otherwise the code you were watching
for would be the code hidden behind the count. A single event, and a
git-witnessed run (whose body is a file-ordered snapshot with no newest end),
show their **head** and count what's below as usual. Either way a run stays a
bounded height however much it absorbs.

One thing a `~` run does **not** do is keep itself current. It states its
delta *as of its last update*: revert a file all the way back and the run
keeps its last figures rather than ticking down to zero. That's deliberate —
every entry in the feed is a statement about what happened, not a live reading
of the tree, and the ~30s cap closes the run shortly anyway. See §6.

The body separates two kinds of information into three channels — three
channels, never fewer. The left gutter carries *what changed*: a green `+` on
added lines, a red `−` on removed ones. The code text carries *what it is*:
both added and removed lines are syntax-highlighted (monokai, chosen per file
extension, foreground only), at identical full strength — the text never says
which of the two it is. And the surface carries *what changed* a second time:
removed rows sit on a faint red field, a block you can find without reading
(ADR 0004 § the removed-row field). Colors appear live as the text types; a half-typed line is
half-colored.

The field is a rectangle, running from the `−` column to the right edge of the
feed, so a run of removed lines reads as one shape however ragged the code is.
It never covers the gap gutter or the session lane — the lane's identity hue
stays on clean surface. It appears on every removed row, in a collapsed body,
an expanded one, and an expanded commit's diffs alike — and across every row a
wrapped line folds into, so the shape survives `w` — but not on the
`… ▸ N more lines` row, which is a count rather than removed code. It is there
on the selected event too: selection marks the lane and never the body
([ADR 0004 § selection in the lane](adr/0004-the-watch.md)), so a diff you
are inspecting reads on exactly the surface a diff you are not does. The one
place the field isn't there is 16-color and `NO_COLOR` terminals, which never
paint backgrounds at all — and nothing is lost, because the field only ever
repeats what the `−` already said. Added
lines get no field of their own: they type themselves out a character at a
time, and a rectangle behind a line that hasn't arrived yet would give the
ending away.

The monokai palette is deliberately its own system, licensed next to the
chrome: UI colors are desaturated, so code bodies are
the saturated register and pop on purpose. Files with no recognizable
extension fall back to plain text; the gutter still tells the story. There
are no line numbers in the gutter — the stream can't know them for session
edits, and a number that's sometimes missing or wrong would be a lie.

A commit event carries the commit's own diff, so committing a change doesn't
make it unreadable. Its header reads `⚑ commit @<sha>  <subject>  N files
+added −removed  ▸ N files` — the `@` and the grey mark the hash as a *commit*
hash, never a Session Handle, which is the same shape and the only one of the
two you can hand to `standup session`. To read the commit's diff outside the
Watch, name it in the repo: `standup <repo> diff @<sha>`. A commit collapses to its header row
alone; `enter` steps it through two shallow levels instead of one deep dump:

```
 +30s 1▏ ⚑  commit @3712aaa  Watch: content-diff git-only…  3 files +371   ▾ 3 files
       ▏       CLAUDE.md              modify  +23
       ▏       README.md              modify  +10
       ▏       docs/watch-manual.md   create  +338
```

The first `enter` opens the file list — one line each, `path  change
+added −removed`, the same shape a file event's header uses. The second
replaces it with every file's full added and removed text, in the same
`+`/`−` gutter and the same monokai highlighting a live edit gets — the only
difference is that git, not a session, supplied it. A third collapses it
again. Past six files the list truncates to `… ▸ N more files` — but the
header's `N files` is always the real count, and the diff level always shows
every file.

Two kinds of commit carry **no** diff, and show only the header line — no file
count, no `+`/`−` totals:

- a **merge commit** — `git show` prints no combined diff by default, and the
  Watch would rather show nothing than invent a reading of one
- a commit whose diff exceeds ~400 KB, for the same reason very large dirty
  files aren't content-diffed

Neither case is an error, and neither is dressed up as one: the counts and the
file list simply aren't there, because no diff was read.

### The status bar

Three segments, fixed positions. Left, a reverse-video **state chip**: green
`● LIVE` while the feed follows the bottom (or `git only · poll 2s · last
change Nm ago` when nothing is live), amber `▲ SCROLLED` while you read
scrollback — with how far back (`−38 rows`) and how much has landed beneath you
(`6 new below`, ticking). The chip inverts so it reads from three feet, with or
without color.

Next to the chip, the **Activity State** of every session that is currently
working:

```
 ● LIVE   [1] ⠹ thinking 4s  ·  [2] ⠹ running 1m          ⏎ expand · d stat · ? keys
```

This is the segment that answers "do I need to go back to the terminal yet".
It is deliberately one-sided: a session that has **finished its turn shows
nothing at all**. There is no `finished`, no `done`, no `waiting on you`, and no
recency in its place — the absence *is* the answer, so with nothing acting this
segment is simply **empty**. The bar is quiet when the work is quiet, which is
what makes anything appearing there worth a glance.

| Verb | Means |
|---|---|
| `thinking` | the model is composing — no tool call is in flight |
| `reading` | `Read`, `Grep`, `Glob`, `WebFetch`, `WebSearch` |
| `writing` | `Write`, `Edit`, `MultiEdit`, `NotebookEdit` |
| `running` | `Bash` and its shell companions |
| `acting` | any other tool, including MCP tools — an unmapped tool is still true |

**A tool verb lingers for a second.** A local `Read` returns in about 25
milliseconds — far too fast to read, and faster than the Watch's own 250ms
poll — so bound strictly to its tool's runtime, `reading` and `writing` would
never appear at all and the bar would say `thinking` in almost every frame. A
tool verb therefore holds the bar for at least a second before `thinking` may
replace it. It yields to everything that matters: the bar goes blank the *instant*
the turn is handed back, and the next tool verb replaces it immediately, so the
lingering only ever displaces silence. The age is the verb's real age throughout,
which is why a held one reads `reading 0s` (ADR 0004 § the Activity State).

The number is how long it has been in that state, and it is never re-labelled:
`thinking 14m` stays `thinking 14m` rather than becoming "stalled", because
Standup would then be claiming a process died, which it cannot know (the same
reason a **Live Session** shows `28m ago` instead of "running"). Fourteen
minutes of thinking is suspicious and you are the one who knows whether you
asked for something that takes it. This is also why the verb is worth having
over a bare "busy": `thinking 4m` and `running 4m` are not the same news — one
means something has probably gone wrong, the other is a test suite behaving.

`reading` has no Feed Event of its own — `Read`, `Grep` and `Glob` produce no
feed rows — so for those tools the status bar is the *only* place they appear.
`acting` does have one: the MCP request or web fetch behind it lands as a
**Call**, so the bar tells you it is in flight and the feed tells you, after the
fact, what it was.

The Activity State shows while you're scrolled back too. That's precisely when
you've stopped watching the feed and most need to know whether the agent is
still going.

Then the modes, when they're non-default: the active filter
(`filter [2] 9f8e7d6c`), `stat — headers only`, `no wrap — long lines clip`
(wrap is on unless you turned it off), and the typing speed while text is
animating or you've changed it.

The right edge shows at most four key hints, contextual to what you're doing;
`?` toggles the full key map as a temporary overlay. When something surprises
you, read this line first — it always says which mode you're in.

A one-column scrollbar appears at the right edge of the feed only while
you're scrolled back; while following, there is nothing to scroll to.

## 4. The keys

Nothing is modal and nothing needs confirmation; every key is a toggle or a
jump. Grouped by what you're trying to do:

### Follow, or hold still

There is no pause. The feed **follows** the bottom while you're at the bottom;
the moment you scroll up, select, or click an event, it stops following and
the view holds still — events keep landing beneath you, nothing queues,
nothing is deferred. Scroll back down to the bottom and it follows again by
itself.

| Key | Does |
|---|---|
| `End` or `G` | go live |
| `Home` or `g` | jump to the top of scrollback |

`G` (capital G, or the `End` key) is the one key back to now: it drops your
selection, finishes any in-progress typing instantly, and scrolls to the
bottom, where the feed follows again. `g` is its opposite — the oldest event
still in the feed.

### Look back

| Key | Does |
|---|---|
| `↑` or `k` | select the previous event |
| `↓` or `j` | select the next event |
| `[` | jump to the previous chapter (prompt rule) |
| `]` | jump to the next chapter |
| `PageUp` | scroll up a page |
| `PageDown` | scroll down a page |
| mouse click | select the clicked event and expand it — on an open event, its header row or its left rail collapses it |
| drag / double click | select text; never expands or collapses |

**Any scrollback gesture stops the follow** — `↑`, `PageUp`, the mouse wheel,
and clicking an event all hold the view still, so the feed doesn't yank
itself out from under you while you're reading. The stream never stops: new
events keep appearing below, and the status chip flips to `▲ SCROLLED`
so you know the bottom is moving on without you.

The first `↑` selects the newest event and marks it **in the session lane**:
the bar column lights up and the lane's thin `▏` thickens to `▎`, down every
row of the event — header, diff body, folded continuations, in one unbroken
rail. Both bars are drawn from the left edge of the same column, so the lane
grows in place rather than shifting sideways. Nothing right of the lane
changes, so an expanded diff is read on the terminal's own surface whether it
is selected or not
([ADR 0004 § selection in the lane](adr/0004-the-watch.md)); on a git-only
event, which has no lane hue, the band alone carries it. Selection is what
`enter` and `s` act on. A mouse click selects
the clicked event directly — no walking — and expands it in the same gesture,
exactly as if you'd pressed `enter` on it.
Clicks in empty feed space or the status bar do nothing (a click on a header
session row toggles its filter — see below). Walk `↓` past the last event and
the Watch takes it as "I'm done reading" and goes live.

**An open body is text, not a control.** While an event is collapsed the whole
of it is one button: header and preview rows alike, a click opens it. Once it is
open, only the entry's own furniture still toggles — its **header row** and its
**left rail**, the gap gutter and session lane in columns 1–9. Everything right
of the rail is yours: a click there does nothing, so you can drag through a diff
or a prompt to select it, double-click to take the whole entry, and click a link
without the thing you're reading folding shut under you.

That rail is the answer to a long body. A diff taller than the screen pushes its
header off the top, but the lane runs unbroken down **every** row of the block —
so to fold it, click the rail beside whatever line you stopped on, two columns
to the left of the code. `enter` does the same from the keyboard: it acts on the
selected event wherever the view has scrolled to.

Reading gestures are never toggles, on any surface: a **drag** (pressed on one
cell, released on another) and a **double or triple click** are selections, so
neither expands an event nor flips a session filter. `ctrl+c` copies the
selection, through the terminal's own clipboard escape — supported by iTerm2,
Ghostty, WezTerm and kitty, not by Apple Terminal. Whether a click on a URL
*opens* it is likewise the terminal's business (`⌘-click` in iTerm2, Ghostty,
kitty): the Watch prints no hyperlinks, it just stops competing for the click
([ADR 0004 § mouse gestures](adr/0004-the-watch.md)).

**The click that brings the terminal forward is not a click on the feed.** When
you come back from another window by clicking the terminal, that click lands
wherever the pointer happened to be resting — you meant to focus the window,
not to expand whatever event is under the cursor. The Watch ignores it: it
takes the terminal's focus report and discards the click that arrives with it,
along with any other click in the next 0.35s. Focus the window with `⌘-Tab`
instead and nothing is discarded, because no click needs discarding. This needs
a terminal that reports focus (iTerm2, Ghostty, WezTerm, kitty, Alacritty);
Apple Terminal doesn't, so there the refocusing click still counts
([ADR 0004 § mouse gestures](adr/0004-the-watch.md)).

`[` and `]` move by chapters instead of events: prompts are the skeleton of
the narrative, and an hour of work is a handful of `[` presses, not hundreds
of `↑`s.

The feed keeps the last 500 events. Scroll far enough back and the oldest ones
are simply gone — the Watch is a live view, not an archive. The **Transcript**
(`s`, or `standup session`) is where the full history lives. For the *code* the
sessions changed rather than what they said, the **Attributed Diff**
(`standup <repo> diff`) reads the working tree and attributes it per hunk. While you're reading
scrollback the trim is deferred (up to 200 extra events) so dropping old
events can't shift the view under you; going back to live drops the excess.

### See more of one thing

| Key | Does |
|---|---|
| `enter` | expand / collapse the selected event |
| `d` | stat mode: headers only |
| `w` | wrap (on by default): long body lines fold onto further rows ⇄ clip |

`enter` on a file event drops the 12-line/4-line collapse and shows the whole
added and removed text — on a **Change Run**, every hunk it folded, not just
the window. On a **commit** it cycles through the three levels —
header, file list, every file's full diff, and around again. On a prompt it
shows your full message (when the rule had to truncate it); on a **Call** it
shows the untruncated command or argument — never the result, which the Watch
never reads. Expanding also finishes any in-progress
typing immediately — if you want to *read* it, you've stopped wanting to
watch it appear.

With nothing selected, `enter` expands the newest expandable event, which is
usually the one still typing. So `enter` alone is "show me all of that", no
selection needed. Clicking a collapsed event is select-plus-`enter` in one
gesture; once it is open, `enter` — or a click on its **header row** or its
**left rail**, which is beside every line of the body — collapses it again. The
body itself is yours to select from and no longer a button
([ADR 0004 § mouse gestures](adr/0004-the-watch.md)).

`d` is the opposite move: it strips every body from the feed and leaves one
line per event. Use it when you want the shape of the last few minutes — which
files, which commands, in what order — rather than the content.

`w` answers the other way a line can be out of reach: not collapsed, but
**wider than your terminal**. By default it **folds** — a long body line
continues onto as many rows as it needs, so the whole line is there:

```
       ▏      +     result = some_function(argument_one, argument_two,
       ▏      ↳ argument_three) + another_call(x)  # and the rest of the line
```

Three things make a fold read as one line rather than several. The `↳` sits
where the `+` or `−` would be — the sign states a change once, and a fold is
the same source line. Continuation rows start in the **same code column**, so
indentation still lines up. And the removed-field wash spans them, so a run of
removed lines is still one rectangle.

The fold is word-aware; it breaks inside a token only when the token itself is
longer than the row, so a long path or string continues rather than vanishing.
One line may occupy at most 40 rows — past that the tail is *counted*
(`… +N chars`), never silently dropped, so a minified file can't fill the feed.

Folding applies to diff bodies, an expanded Call, and an expanded
prompt — **never** to header lines, which stay one row per event however long
the path. That split is deliberate: a header is Standup's own prose about an
event, and it says when it shortened something (`▸ N lines`, `… ▸ N more
files`); a body is the agent's code, quoted, where clipping would be a lie
about the content
([ADR 0004 § fold, don't clip](adr/0004-the-watch.md)).

`w` turns folding off, over the whole feed, expanded and collapsed bodies
alike: long lines then clip at the right edge and every event has a fixed row
count, which is what you want when you're reading the shape of the last few
minutes rather than the code. That's the non-default state, so the status bar
says `no wrap — long lines clip` while you're in it, and `w` again puts it
back. Start a watch already clipping with `standup watch -W`.

### Focus on one session

| Key | Does |
|---|---|
| `1`–`9` | filter to that numbered Live Session |
| `Tab` | cycle to the next session, then back to all |
| `Esc` or `0` | clear the filter |
| click a header session row | toggle its filter |

A click that only brings the terminal forward doesn't toggle a filter either,
and neither does a drag or a double click across the band — the same guards
cover the header band and the feed.

Two sessions working in one repo produce one interleaved feed, which is the
point — and occasionally the problem. The numbers match the vitals header —
they are **stable**, assigned first-seen and never re-sorted, so `[2]` in the
header, the `2▏` lane in the feed, and the `2` key all mean the same session
for the whole watch. `▸` after the header number marks the one you picked,
and the status bar states the filter in its own segment.

Repo facts are never filtered away: commits, pushes, branch switches, and
Unattributed Changes stay visible under **every** filter — even a commit
another session claims. The filter hides other sessions' work, not the
ground truth.

### Read the conversation

| Key | Does |
|---|---|
| `s` | open that session's **Transcript** in `less` |

`s` suspends the Watch and hands the session's transcript to `less -R` — the
same rendering as `standup session <handle>`. Search it with `/`, quit `less` with
`q`, and the Watch resumes exactly where it was (still scrolled back, if you
were reading).

It picks the session from your selection first, then your filter, then the
newest Live Session. So `↑` to an interesting edit and `s` reads *that*
session. On an unattributed event there's no session to open and `s` does
nothing.

### Change the typing speed

| Key | Does |
|---|---|
| `+` or `=` | 1.5× faster (up to 2000 chars/sec) |
| `-` | 1.5× slower (down to 40 chars/sec) |

The default is 160 chars/sec — leisurely, readable. This is a **floor, not a
ceiling**: the display may never lag the log by more than a couple of seconds,
so when a burst of edits lands the animation compresses to honor that bound,
and past a certain point blocks stop typing and just flash into place. Setting
40 c/s does not make the Watch show you the past slowly. Delight never
outranks truth.

### Remember the keys

| Key | Does |
|---|---|
| `?` | toggle the full key map as an overlay above the status bar |

A toggle, not a mode: the feed keeps flowing behind it, and `?` again (or
just carrying on) puts it away.

### Leave

| Key | Does |
|---|---|
| `q` | quit, and print the parting snapshot |

## 5. Five things to try

**1. Watch a live session narrate itself.** Start a Claude Code session in a
repo, ask it to do something with files, and in another terminal run `standup
watch` there. Every Edit appears as it lands. Press `d` and watch the same
activity as a one-line-per-event log; press `d` again to get the text back.
Keep an eye on the status bar while you're there: the verb next to the chip
walks `thinking` → `reading` → `writing` and then, when the agent hands the turn
back, disappears. That disappearance is your cue to switch terminals — you don't
have to go and check.

**2. Read something that scrolled past.** When an interesting edit flies by:
`↑` (the view holds still), `↑` again until it's highlighted, `enter` to see
the whole diff, `s` to read what the agent was told. Then `G` — one key, back
to live. Or do it all with the mouse: wheel up to it (the view holds still),
click it — one click selects *and* expands — read the diff, drag through the
line you want to keep, and click its left rail to fold it back up.

**3. Untangle two sessions.** With two sessions in one repo, note their numbers
in the header, press `2` to watch only the second, `Tab` to swap, `Esc` for
both again. The dirt count and any commits keep updating throughout — those
are git's, not any session's.

**4. See an Unattributed Change appear.** With the Watch open, edit a file in
your editor and save. It arrives within a couple of seconds wearing the `··`
lane and an italic amber `~unattributed`, with its actual added and removed
lines — the Watch content-diffs dirty files itself, so a repo with no Claude
session at all still narrates.

**5. Commit while watching.** `git commit` in the repo. A `⚑` event appears
with the commit's `+`/`−` totals; `enter` opens its file list, `enter` again
the whole diff. If the commit's hash was captured in a session's log it rides
that session's lane; otherwise `··`. Push, and `⇧` follows within ten
seconds.

## 6. Behaviors that surprise people

**The first screenful is history.** At launch the Watch backfills **every** Live
Session from its *own* current user prompt — prompts are chapter breaks, so you
get each session's chapter in progress, not the whole book. The chapters are
interleaved chronologically into one feed, exactly as live events are. Backfill
is rendered at full strength — replayed code is code you have to read, so it
gets the same clarity as live code — and never animated. The
`┈┈ backfill · N events replayed from log ┈┈ … ┈ live below ┈┈` rule carries
the launch time and separates the two: what you can tell apart is *where* an
event sits, not how bright it is.

Backfilling every session is what makes `1`–`9` and `Tab` useful on arrival:
filter to `[2]` and you see what that session did, even if it finished its work,
committed, and went quiet before you opened the Watch. Once a session's changes
are committed the tree is clean, so its **edits** are the only place that work is
still legible — which is why they're replayed rather than skipped.

If the sessions' chapters add up to more than 400 events, the **oldest** are
dropped and the newest 400 are replayed.

**A session that says `28m ago` is still listed.** Live means "appended within
the Live window" — 30 minutes by default — nothing more. Standup never inspects
processes, so it shows you the recency and lets you judge.

**A session you know worked on this repo isn't there.** Its last append is
older than the Live window, so it was never picked up: nothing backfills it and
no lane exists to filter to. `standup watch --since 2h` widens the window (§2)
and it comes back with its chapter. The header then reads `live ≤2h`, so the
screen never claims a stricter recency than it applied.

**Nothing is said when a session finishes.** There's no `finished` in the status
bar and no row in the feed — its **Activity State** simply stops being shown.
That's the design: the bar is quiet when the work is quiet, so anything there
means work is still going.

**`thinking` is read from silence.** It's the one state the log never states.
Claude Code writes a line when a tool is called and a line when it returns, but
nothing about the pause between them — so the pause is what `thinking` is. The
practical consequence: a session killed outright in that gap (window closed,
`kill -9`) leaves a tail that looks exactly like a session still composing, and
it will read `thinking` until it ages past the 30-minute Live window.

**A frozen spinner means nothing is arriving.** The `⠹` beside the verb turns
only while the log is still being appended. After 30 quiet seconds it stops and
changes to a static, dimmed `⠿` — so `[1] ⠿ running 6m` reads "it announced a
shell command six minutes ago and nothing has come back". That's not a claim the
session died; it's the refusal to keep implying it's alive. Motion in the Watch
always maps to arriving data, never to a word on screen.

**Scrollback holds the view, never the stream.** There is no pause and no
queue: while you read, new events keep landing below your viewport in real
time. The `▲ SCROLLED` chip means the bottom is moving on without you —
with how far back you are and how much has landed since; `G` (or scrolling
back down) rejoins it.

**New sessions join by themselves.** The Watch rescans for new session logs
every ten seconds; a session started after you opened the Watch shows up as a
`●` event and starts narrating.

**Git lags the log slightly.** The session log is tailed continuously; git is
polled every two seconds, and pushes every ten. An edit typically appears
*before* the dirt count moves.

**A file changed twice in a row still narrates.** Git's status codes can't see
the second change (already ` M`, still ` M`), so the Watch snapshots dirty
files and diffs them itself.

**Editing one file for a while produces one row, not one per edit.** Those
edits fold into a single **Change Run** that grows in place. During a long
burst you'll get a fresh row about every 30 seconds rather than one per tool
call or per git poll — the Activity State in the status bar is what tells you
work is still going in between. `×N` on the header says how many tool calls
went into it.

**A run's `+N` doesn't tick back down when you revert.** A git-witnessed run
states its net delta *as of its last update*. Undo the change and the row keeps
its last figures rather than falling to zero — the feed records what happened,
it isn't a live reading of the tree. The run closes ~30 seconds after it opened
in any case, and the vitals band's dirt count is the figure that *is* current.

**Startup dirt isn't replayed as a giant event.** A file already dirty when you
launched is measured from what the Watch first saw, not from HEAD — so a repo
with existing uncommitted work narrates the changes from here on rather than
opening with one enormous block of old news.

## 7. Troubleshooting

| Symptom | Why |
|---|---|
| `needs an interactive terminal` | stdout isn't a tty; the Watch has no piped mode |
| `no scanned repo matches 'x'` | the name isn't in the **Scan Universe** — the message lists what is; pass a path instead |
| `is not inside a git repo` | the path resolves to no checkout |
| `no Claude Code logs found at …` | `~/.claude/projects` is missing |
| `no live session · last log append …` | nothing appended inside the Live window; git-only narration is working as designed — `--since 2h` widens the window if the work you want is older |
| `s` does nothing | the selected event has no session (git-only), or its log isn't tailed |
| everything is `~unattributed` | no session log covers this repo — expected outside the Scan Universe |
| a commit shows no file list | it's a merge (no combined diff by default) or its diff is over ~400 KB — no diff was read, so none is shown |
| a body line runs off the right edge | wrap has been turned off (`no wrap` in the status bar, or `--no-wrap`) — press `w` |
| `… ▸ N earlier lines` above a body | a **Change Run** showing its tail: the newest hunks are visible and the run's earlier lines are counted above. `enter` shows all of them |
| a `+N` that looks too big for one edit | it's a **Change Run** — `×N` on the header says how many tool calls it folded, and the counts are their sum |
| a file's row doesn't update while it's being edited | the run already closed (something else happened, or ~30s passed); the next edit opens a fresh row below |
| a folded line ends `… +N chars` | one line hit the 40-row fold bound; the count is the rest of it. `s` reads it whole in the Transcript |
| a header clips even though lines fold | wrap is for bodies only; headers stay one row per event. `enter` shows a prompt or command in full |
| one event fills the screen | it's a long-line file (minified, generated) folding to fit — `w` clips it back, or `d` for headers only |
| typing looks instant | the staleness bound is compressing it; the log is ahead of the display |
| no verb in the status bar | nothing is acting — every Live Session has handed its turn back. There is no "finished" to show |
| the spinner has stopped | nothing appended for 30s; the verb and its age are the last thing the log said (a long `running` is normal, a long `thinking` is not) |
| `thinking` for far too long | either a long reasoning pass, or the session was killed in the gap after a tool returned — the log can't tell them apart. The frozen spinner is the tell |
| `reading 0s` / `writing 0s` | the tool has already returned and the verb is inside its one-second floor. The age is honest — the tool really did take under a second |
| almost always `thinking` | expected, and not the bug it was: the model composing genuinely is most of a turn's wall-clock. `running` shows for `Bash`, and `reading`/`writing` for their floor; if you see *nothing else, ever*, the floor is one constant (`ACT_FLOOR`) in `watchstream.py` |
| `[2] ⠹ acting 3s` | a tool with no mapped verb (an MCP tool, or one newer than the table) — and only that: a log line announcing no tool at all leaves the previous verb standing instead of falling to `acting`. The call itself lands in the feed as a **Call**, so the bar says it is in flight and the feed says what it was |
| the feed is empty while the agent is clearly working | the only silent tools are the local reads (`Read`, `Grep`, `Glob`, `NotebookRead`, `BashOutput`, `KillShell`) — everything else is a **Call**. An empty feed with `reading` in the bar is the agent reading; an empty feed under `-q` is the flag, which drops Calls |

## 8. One-page key reference

```
q            quit (prints a parting snapshot)
G, End       go live (drop selection, scroll to now)
g, Home      jump to the top of scrollback
↑ ↓, k j     select prev / next event  PageUp   scroll up a page
             (both hold the view)      PageDown scroll down a page
[ ]          previous / next chapter
click        select + expand the clicked event; on an open event its
             header row or its left rail collapses it, and the body is
             text (drag / double click to select, ctrl+c to copy)
             (the click that refocuses the terminal is ignored)
enter        expand / collapse         d        stat mode (headers only)
             (commits: header → files → diffs)
                                       w        wrap ⇄ clip long body lines
                                                (on: folds are marked ↳)
1-9          filter to that session    Tab      next session
Esc, 0       all sessions              s        open the Transcript in less
+ =          type faster               -        type slower
?            toggle this key map as an overlay in the Watch itself
```
