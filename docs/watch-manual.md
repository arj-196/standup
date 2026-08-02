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

It reads two sources, exactly as the **Triage Inbox** does: the Claude Code
session log claims *who and what*, and git confirms *ground truth*. Assistant
prose and thinking never appear here — reading the conversation is
`standup show`'s job.

The Watch is read-only and stateless. It never writes to your repo, never
touches the session, and quitting it loses nothing.

## 2. Starting it

```bash
standup watch
```

That watches the repo you're standing in. You can also name one:

```bash
standup watch standup
```

The argument is a repo name (as it appears in the inbox) or a path to a git
checkout; it defaults to `.`. Naming a repo resolves it against the **Scan
Universe** — the repos some Claude session has visited — and the error lists
the known names when nothing matches. A path works even for a repo no session
has ever touched: git alone can narrate.

Files and commits only, no Bash/prompt/session noise:

```bash
standup watch --quiet
```

The Watch needs an interactive terminal. Piped or redirected, it refuses
rather than printing something half-alive.

**Quit with `q`.** The alt-screen closes and a parting snapshot is printed on
plain stdout, so it stays in your scrollback:

```
watched standup for 12m — 2 sessions, 7 files touched, 1 commit
now: 3 dirty on main
```

## 3. Reading the screen

Three zones, top to bottom:

```
standup  ⑂ main  ✎ 3 dirty                        ← vitals header
▶[1] a1b2c3d4  Add watch manual  ~document the TUI keys   4s ago
 [2] 9f8e7d6c  Fix rate card rounding                    2m ago
─────────────────────────────────────────────────
14:22:07  a1b2c3d4  ── you: document the watch keys       ← the feed
14:22:31  a1b2c3d4  ✎ docs/watch-manual.md  create  +48
                    + # `standup watch` — user manual
                    + ▌
14:22:44  ········  ⚑ commit 3712aaa Watch: content-diff…   2 files  +31 −4
─────────────────────────────────────────────────
● live   speed 160c/s   ↑↓ scrollback · enter expand · …   ← status bar
```

### The vitals header

- the **repo name**, `⑂` its branch, `✎` its count of dirty files — refreshed
  once a second
- then one line per **Live Session**, numbered `[1]`…`[9]`: its **Session
  Handle** (the 8-character session id), the derived title, its **Session
  Brief** objective if one exists (marked `~`, because a Brief is a claim, not
  a fact), and how long ago its log was last appended
- `▶` marks the session you've filtered to
- `watching — no live session` when nothing is live: the repo still narrates
  from git

A **Live Session** is a *recency claim* — a log appended in the last 30
minutes. It is never a statement that a process is running, which is why the
header shows `4s ago` instead of asserting "running". Sessions are ordered
newest-append-first, so `[1]` can change which session it points at while you
watch (see `1`–`9` below for why that doesn't break your filter).

### The feed

One line per **Feed Event**: `HH:MM:SS`, the **Session Handle**, a kind mark,
then the content.

| Mark | Kind | What it means |
|---|---|---|
| `✎` green | file | a file was written, edited, or deleted |
| `⏺` dim | bash | a shell command, `✓` or `✗` appended when it returns |
| `──` cyan | prompt | **you** typed something — a chapter break |
| `⚑` yellow | commit | a new commit reached HEAD, with its diff |
| `⇧` yellow | push | commits left for the remote |
| `⑂` magenta | branch | branch switch, `old → new` |
| `~` red | unattributed | changed files git can't diff (binary, huge, unreadable) |
| `●` cyan | session | a brand-new session log appeared in this repo |

Two markings carry the honesty rules:

- **`········` instead of a handle**, and a red `~unattributed` tag on file and
  commit events, mean *git is the only witness* — no session claims this
  change. That's not an error; it's hand-editing, another tool, or a squash.
  The Watch shows it live rather than hiding it.
- **dimmed events** are backfill: history replayed at launch, not things
  happening now. See §6.

A file event's header reads `path  change  +added −removed`, where `change` is
`create`, `modify`, or `delete`, and the counts are lines.

Below the header, a file event shows its body: up to 4 removed lines, then the
added text typing itself out with a `▌` cursor. Long blocks animate their
first 12 lines and collapse the rest to `… +N more lines (enter expands)`.

The body separates two kinds of information into two channels. The left
gutter carries *what changed*: a green `+` on added lines, a red `-` on
removed ones. The code text carries *what it is*: both added and removed
lines are syntax-highlighted (monokai, chosen per file extension), at full
strength — only the gutter distinguishes them. Colors appear live as the text
types. The monokai palette is deliberately not matched to the rest of the
TUI: diff bodies are meant to pop. Files with no recognizable extension fall
back to plain text; the gutter still tells the story. There are no line
numbers in the gutter — the stream can't know them for session edits, and a
number that's sometimes missing or wrong would be a lie.

A commit event carries the commit's own diff, so committing a change doesn't
make it unreadable. Its header reads `commit <sha> <subject>  N files +added
−removed`; collapsed, its body lists the files it touched, one line each —
`path  change  +added −removed`, the same shape a file event's header uses:

```
14:22:44  a1b2c3d4  ⚑ commit 3712aaa Watch: content-diff git-only…  3 files  +371
                      CLAUDE.md              modify  +23
                      README.md              modify  +10
                      docs/watch-manual.md   create  +338
                      (enter expands the diff)
```

`enter` replaces that list with every file's full added and removed text, in
the same `+`/`-` gutter and the same monokai highlighting a live edit gets — the
only difference is that git, not a session, supplied it. Past six files the
collapsed list truncates to `… +N more files (enter expands)` — but the header's
`N files` is always the real count, and expanding always shows every file.

Two kinds of commit carry **no** diff, and show only the header line — no file
count, no `+`/`−` totals:

- a **merge commit** — `git show` prints no combined diff by default, and the
  Watch would rather show nothing than invent a reading of one
- a commit whose diff exceeds ~400 KB, for the same reason very large dirty
  files aren't content-diffed

Neither case is an error, and neither is dressed up as one: the counts and the
file list simply aren't there, because no diff was read.

### The status bar

`● live` or `▲ scrolled back (G live)`, the active filter, whether stat mode
is on, the current typing speed, and the key hints. When something surprises
you, read this line first — it always says which mode you're in.

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

`G` (capital G, or the `End` key) is the one key back to now: it drops your
selection, finishes any in-progress typing instantly, and scrolls to the
bottom, where the feed follows again.

### Look back

| Key | Does |
|---|---|
| `↑` | select the previous event |
| `↓` | select the next event |
| `PageUp` | scroll up a page |
| `PageDown` | scroll down a page |
| mouse click | select the clicked event and expand / collapse it |

**Any scrollback gesture stops the follow** — `↑`, `PageUp`, the mouse wheel,
and clicking an event all hold the view still, so the feed doesn't yank
itself out from under you while you're reading. The stream never stops: new
events keep appearing below, and the status bar flips to `▲ scrolled back`
so you know the bottom is moving on without you.

The first `↑` selects the newest event and highlights it. Selection is what
`enter` and `s` act on. A mouse click selects the clicked event directly — no
walking — and expands it in the same gesture, exactly as if you'd pressed
`enter` on it; clicking it again collapses it. Clicks anywhere else (empty
feed space, the vitals header, the status bar) do nothing. Walk `↓` past the
last event and the Watch takes it as "I'm done reading" and goes live.

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

`enter` on a file event drops the 12-line/4-line collapse and shows the whole
added and removed text. On a **commit** it replaces the file list with every
file's full diff. On a prompt it shows your full message; on a Bash event it
shows the untruncated command. Expanding also finishes any in-progress typing
immediately — if you want to *read* it, you've stopped wanting to watch it
appear.

With nothing selected, `enter` expands the newest expandable event, which is
usually the one still typing. So `enter` alone is "show me all of that", no
selection needed. Clicking an event is select-plus-`enter` in one gesture —
click to expand, click again to collapse.

`d` is the opposite move: it strips every body from the feed and leaves one
line per event. Use it when you want the shape of the last few minutes — which
files, which commands, in what order — rather than the content.

### Focus on one session

| Key | Does |
|---|---|
| `1`–`9` | filter to that numbered Live Session |
| `Tab` | cycle to the next session, then back to all |
| `Esc` or `0` | clear the filter |

Two sessions working in one repo produce one interleaved feed, which is the
point — and occasionally the problem. The numbers match the vitals header, and
`▶` moves to the session you picked.

Filtering binds the **session id**, not the row number. If the session you're
watching goes quiet and slips to `[2]`, your filter follows the session, not
the slot.

Git-only events — unattributed changes, branch switches, pushes, commits no
session claims — stay visible under **every** filter. Ground truth is never
filtered away.

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

### Leave

| Key | Does |
|---|---|
| `q` | quit, and print the parting snapshot |

## 5. Five things to try

**1. Watch a live session narrate itself.** Start a Claude Code session in a
repo, ask it to do something with files, and in another terminal run `standup
watch` there. Every Edit appears as it lands. Press `d` and watch the same
activity as a one-line-per-event log; press `d` again to get the text back.

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
your editor and save. It arrives within a couple of seconds with `········`
where a handle would be and a red `~unattributed`, and its actual added and
removed lines — the Watch content-diffs dirty files itself, so a repo with no
Claude session at all still narrates.

**5. Commit while watching.** `git commit` in the repo. A `⚑` event appears with
the commit's file list and its `+`/`−` totals; `enter` opens the whole diff. If
the commit's hash was captured in a session's log it carries that session's
handle; otherwise `········`. Push, and `⇧` follows within ten seconds.

## 6. Behaviors that surprise people

**The first screenful is dimmed.** At launch the Watch backfills **every** Live
Session from its *own* current user prompt — prompts are chapter breaks, so you
get each session's chapter in progress, not the whole book. The chapters are
interleaved chronologically into one feed, exactly as live events are. Backfill
is dimmed and never animated, precisely so you can tell replay from now.

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

**Scrollback holds the view, never the stream.** There is no pause and no
queue: while you read, new events keep landing below your viewport in real
time. `▲ scrolled back` in the status bar means the bottom is moving on
without you; `G` (or scrolling back down) rejoins it.

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
| `watching — no live session` | nothing live; git-only narration is working as designed |
| `s` does nothing | the selected event has no session (git-only), or its log isn't tailed |
| everything is `~unattributed` | no session log covers this repo — expected outside the Scan Universe |
| a commit shows no file list | it's a merge (no combined diff by default) or its diff is over ~400 KB — no diff was read, so none is shown |
| typing looks instant | the staleness bound is compressing it; the log is ahead of the display |

## 8. One-page key reference

```
q            quit (prints a parting snapshot)
G, End       go live (drop selection, scroll to now)
↑ ↓          select prev / next event  PageUp   scroll up a page
             (both hold the view)      PageDown scroll down a page
click        select + expand the clicked event; click again collapses
enter        expand / collapse         d        stat mode (headers only)
             (on a commit: its diff)
1-9          filter to that session    Tab      next session
Esc, 0       all sessions              s        open the Transcript in less
+ =          type faster               -        type slower
```
