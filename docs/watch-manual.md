# `standup watch` — user manual

The **Watch** is Standup's only live view. Every other view is a snapshot: run
it, read it, it's over. The Watch renders *time passing* — one repo, narrated
as an agent works in it.

This manual walks a new user from "what am I looking at" to every key on the
keyboard. Domain words in **bold** are defined in [CONTEXT.md](../CONTEXT.md);
the design decision behind the view is
[ADR 0008](adr/0008-watch-jsonl-primary-textual-behind-boundary.md).

---

## 1. What it does

The Watch interleaves everything happening in **one Repo Entry** (a checkout
plus its worktrees) into a single chronological feed:

- **file edits**, typed out character by character as they land
- **Bash one-liners**, with their exit status
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
`standup show`'s job. (The `thinking` **Activity State** is not an exception: it
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

Files and commits only, no Bash/prompt/session noise:

```bash
standup watch --quiet
```

Long body lines fold by default; start with them clipping at the right edge
instead — the `w` toggle (§4), off from the first row:

```bash
standup watch --no-wrap
```

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
status bar. Backgrounds live only on the two bands and the current selection;
everything else is foreground on the terminal surface.

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
  files, and how long this watch has been open — refreshed once a second
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

A **Live Session** is a *recency claim* — a log appended in the last 30
minutes. It is never a statement that a process is running, which is why the
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

Then a kind mark and the content:

| Mark | Kind | What it means |
|---|---|---|
| `✎` | file | a file was written, edited, or deleted (session-claimed) |
| `~` amber | file | the same, but *git is the only witness* — tagged `~unattributed` |
| `⏺` | bash | a shell command, `✓` or `✗` appended when it returns |
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
8-hex is a Session Handle**: an address you can hand to `standup show
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
added text typing itself out with a `▌` cursor. Long blocks animate their
first 12 lines and collapse the rest to `… ▸ N more lines`. A line wider than
the feed folds onto further rows rather than running off the edge; `w` turns
that off (§4).

The body separates two kinds of information into three channels — three
channels, never fewer. The left gutter carries *what changed*: a green `+` on
added lines, a red `−` on removed ones. The code text carries *what it is*:
both added and removed lines are syntax-highlighted (monokai, chosen per file
extension, foreground only), at identical full strength — the text never says
which of the two it is. And the surface carries *what changed* a second time:
removed rows sit on a faint red field, a block you can find without reading
(ADR 0012). Colors appear live as the text types; a half-typed line is
half-colored.

The field is a rectangle, running from the `−` column to the right edge of the
feed, so a run of removed lines reads as one shape however ragged the code is.
It never covers the gap gutter or the session lane — the lane's identity hue
stays on clean surface. It appears on every removed row, in a collapsed body,
an expanded one, and an expanded commit's diffs alike — and across every row a
wrapped line folds into, so the shape survives `w` — but not on the
`… ▸ N more lines` row, which is a count rather than removed code. Two places
it deliberately isn't there: on the **selected** event, where the selection
band owns the background and the `−` carries the row alone; and on 16-color and
`NO_COLOR` terminals, which never paint backgrounds at all. Nothing is lost in
either case — the field only ever repeats what the `−` already said. Added
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
two you can hand to `standup show`. A commit collapses to its header row
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

The number is how long it has been in that state, and it is never re-labelled:
`thinking 14m` stays `thinking 14m` rather than becoming "stalled", because
Standup would then be claiming a process died, which it cannot know (the same
reason a **Live Session** shows `28m ago` instead of "running"). Fourteen
minutes of thinking is suspicious and you are the one who knows whether you
asked for something that takes it. This is also why the verb is worth having
over a bare "busy": `thinking 4m` and `running 4m` are not the same news — one
means something has probably gone wrong, the other is a test suite behaving.

`reading` and `acting` have no Feed Event of their own — `Read` and `Grep`
produce no feed rows — so for those tools the status bar is the *only* place
they appear.

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
| mouse click | select the clicked event and expand / collapse it |

**Any scrollback gesture stops the follow** — `↑`, `PageUp`, the mouse wheel,
and clicking an event all hold the view still, so the feed doesn't yank
itself out from under you while you're reading. The stream never stops: new
events keep appearing below, and the status chip flips to `▲ SCROLLED`
so you know the bottom is moving on without you.

The first `↑` selects the newest event and highlights it — selection is the
one background wash in the feed, and the syntax colors read through it
unchanged. Selection is what `enter` and `s` act on. A mouse click selects
the clicked event directly — no walking — and expands it in the same gesture,
exactly as if you'd pressed `enter` on it; clicking it again collapses it.
Clicks in empty feed space or the status bar do nothing (a click on a header
session row toggles its filter — see below). Walk `↓` past the last event and
the Watch takes it as "I'm done reading" and goes live.

`[` and `]` move by chapters instead of events: prompts are the skeleton of
the narrative, and an hour of work is a handful of `[` presses, not hundreds
of `↑`s.

The feed keeps the last 500 events. Scroll far enough back and the oldest ones
are simply gone — the Watch is a live view, not an archive. The **Transcript**
(`s`, or `standup show`) is where the full history lives. While you're reading
scrollback the trim is deferred (up to 200 extra events) so dropping old
events can't shift the view under you; going back to live drops the excess.

### See more of one thing

| Key | Does |
|---|---|
| `enter` | expand / collapse the selected event |
| `d` | stat mode: headers only |
| `w` | wrap (on by default): long body lines fold onto further rows ⇄ clip |

`enter` on a file event drops the 12-line/4-line collapse and shows the whole
added and removed text. On a **commit** it cycles through the three levels —
header, file list, every file's full diff, and around again. On a prompt it
shows your full message (when the rule had to truncate it); on a Bash event
it shows the untruncated command. Expanding also finishes any in-progress
typing immediately — if you want to *read* it, you've stopped wanting to
watch it appear.

With nothing selected, `enter` expands the newest expandable event, which is
usually the one still typing. So `enter` alone is "show me all of that", no
selection needed. Clicking an event is select-plus-`enter` in one gesture —
click to expand, click again to collapse.

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

Folding applies to diff bodies, an expanded Bash command, and an expanded
prompt — **never** to header lines, which stay one row per event however long
the path. That split is deliberate: a header is Standup's own prose about an
event, and it says when it shortened something (`▸ N lines`, `… ▸ N more
files`); a body is the agent's code, quoted, where clipping would be a lie
about the content
([ADR 0013](adr/0013-a-body-line-folds-a-header-line-clips.md)).

`w` turns folding off, over the whole feed, expanded and collapsed bodies
alike: long lines then clip at the right edge and every event has a fixed row
count, which is what you want when you're reading the shape of the last few
minutes rather than the code. That's the non-default state, so the status bar
says `no wrap — long lines clip` while you're in it, and `w` again puts it
back. Start a watch already clipping with `standup watch --no-wrap`.

### Focus on one session

| Key | Does |
|---|---|
| `1`–`9` | filter to that numbered Live Session |
| `Tab` | cycle to the next session, then back to all |
| `Esc` or `0` | clear the filter |
| click a header session row | toggle its filter |

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
same rendering as `standup show <handle>`. Search it with `/`, quit `less` with
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
click it — one click selects *and* expands.

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
30 minutes", nothing more. Standup never inspects processes, so it shows you
the recency and lets you judge.

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
Bash command six minutes ago and nothing has come back". That's not a claim the
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

## 7. Troubleshooting

| Symptom | Why |
|---|---|
| `needs an interactive terminal` | stdout isn't a tty; the Watch has no piped mode |
| `no scanned repo matches 'x'` | the name isn't in the **Scan Universe** — the message lists what is; pass a path instead |
| `is not inside a git repo` | the path resolves to no checkout |
| `no Claude Code logs found at …` | `~/.claude/projects` is missing |
| `no live session · last log append …` | nothing live; git-only narration is working as designed |
| `s` does nothing | the selected event has no session (git-only), or its log isn't tailed |
| everything is `~unattributed` | no session log covers this repo — expected outside the Scan Universe |
| a commit shows no file list | it's a merge (no combined diff by default) or its diff is over ~400 KB — no diff was read, so none is shown |
| a body line runs off the right edge | wrap has been turned off (`no wrap` in the status bar, or `--no-wrap`) — press `w` |
| a folded line ends `… +N chars` | one line hit the 40-row fold bound; the count is the rest of it. `s` reads it whole in the Transcript |
| a header clips even though lines fold | wrap is for bodies only; headers stay one row per event. `enter` shows a prompt or command in full |
| one event fills the screen | it's a long-line file (minified, generated) folding to fit — `w` clips it back, or `d` for headers only |
| typing looks instant | the staleness bound is compressing it; the log is ahead of the display |
| no verb in the status bar | nothing is acting — every Live Session has handed its turn back. There is no "finished" to show |
| the spinner has stopped | nothing appended for 30s; the verb and its age are the last thing the log said (a long `running` is normal, a long `thinking` is not) |
| `thinking` for far too long | either a long reasoning pass, or the session was killed in the gap after a tool returned — the log can't tell them apart. The frozen spinner is the tell |
| `[2] ⠹ acting 3s` | a tool with no mapped verb (an MCP tool, or one newer than the table) |

## 8. One-page key reference

```
q            quit (prints a parting snapshot)
G, End       go live (drop selection, scroll to now)
g, Home      jump to the top of scrollback
↑ ↓, k j     select prev / next event  PageUp   scroll up a page
             (both hold the view)      PageDown scroll down a page
[ ]          previous / next chapter
click        select + expand the clicked event; click again collapses
enter        expand / collapse         d        stat mode (headers only)
             (commits: header → files → diffs)
                                       w        wrap ⇄ clip long body lines
                                                (on: folds are marked ↳)
1-9          filter to that session    Tab      next session
Esc, 0       all sessions              s        open the Transcript in less
+ =          type faster               -        type slower
?            toggle this key map as an overlay in the Watch itself
```
