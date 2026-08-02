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
- **commits, pushes, branch switches**
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
14:22:44  ········  ⚑ commit 3712aaa Watch: content-diff…
─────────────────────────────────────────────────
● live   speed 160c/s   space pause · ↑↓ scrollback · …   ← status bar
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
| `⚑` yellow | commit | a new commit reached HEAD |
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

### The status bar

`● live` or `▮▮ paused — 12 queued`, the active filter, whether stat mode is
on, the current typing speed, and the key hints. When something surprises you,
read this line first — it always says which mode you're in.

## 4. The keys

Nothing is modal and nothing needs confirmation; every key is a toggle or a
jump. Grouped by what you're trying to do:

### Stop the world

| Key | Does |
|---|---|
| `space` | pause / resume |
| `End` or `G` | go live |

Pausing freezes the display, not the stream. Events keep arriving and queue up
behind the scenes — the status bar counts them (`▮▮ paused — 12 queued`).
Resuming lands the whole backlog **instantly**, with no animation: catching up
matters more than the typewriter.

`G` (capital G, or the `End` key) is the panic button: it unpauses, flushes the
queue, drops your selection, and scrolls to the bottom. One key back to now.

### Look back

| Key | Does |
|---|---|
| `↑` | select the previous event |
| `↓` | select the next event |
| `PageUp` | scroll up a page |
| `PageDown` | scroll down a page |
| mouse click | select the clicked event and expand / collapse it |

**Any scrollback gesture implies a pause** — `↑`, `PageUp`, the mouse wheel,
and clicking an event all pause for you, so the feed doesn't yank itself out
from under you while you're reading. (`PageDown` deliberately does *not*
unpause; use `G` for that.)

The first `↑` selects the newest event and highlights it. Selection is what
`enter` and `s` act on. A mouse click selects the clicked event directly — no
walking — and expands it in the same gesture, exactly as if you'd pressed
`enter` on it; clicking it again collapses it. Clicks anywhere else (empty
feed space, the vitals header, the status bar) do nothing. Walk `↓` past the
last event and the Watch takes it as "I'm done reading" and goes live.

The feed keeps the last 500 events. Scroll far enough back and the oldest ones
are simply gone — the Watch is a live view, not an archive. The **Transcript**
(`s`, or `standup show`) is where the full history lives.

### See more of one thing

| Key | Does |
|---|---|
| `enter` | expand / collapse the selected event |
| `d` | stat mode: headers only |

`enter` on a file event drops the 12-line/4-line collapse and shows the whole
added and removed text. On a prompt it shows your full message; on a Bash event
it shows the untruncated command. Expanding also finishes any in-progress
typing immediately — if you want to *read* it, you've stopped wanting to watch
it appear.

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
`q`, and the Watch resumes exactly where it was (paused, if you'd paused it).

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
`↑` (this pauses you), `↑` again until it's highlighted, `enter` to see the
whole diff, `s` to read what the agent was told. Then `G` — one key, back to
live, backlog flushed. Or do it all with the mouse: wheel up to it (pauses),
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

**5. Commit while watching.** `git commit` in the repo. A `⚑` event appears. If
the commit's hash was captured in a session's log it carries that session's
handle; otherwise `········`. Push, and `⇧` follows within ten seconds.

## 6. Behaviors that surprise people

**The first screenful is dimmed.** At launch the Watch backfills the newest
Live Session from its *current* user prompt — prompts are chapter breaks, so
you get the chapter in progress, not the whole book. Backfill is dimmed and
never animated, precisely so you can tell replay from now. Other live sessions
start from their end-of-log; they only appear once they do something new.

**A session that says `28m ago` is still listed.** Live means "appended within
30 minutes", nothing more. Standup never inspects processes, so it shows you
the recency and lets you judge.

**Events pause but don't stop.** `▮▮ paused — 12 queued` is a display state.
Nothing is dropped, and resuming lands everything at once.

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
| typing looks instant | the staleness bound is compressing it; the log is ahead of the display |

## 8. One-page key reference

```
q            quit (prints a parting snapshot)
space        pause / resume            G, End   go live (flush + scroll to now)
↑ ↓          select prev / next event  PageUp   scroll up a page (pauses)
             (↑ and PageUp pause)      PageDown scroll down a page
click        select + expand the clicked event; click again collapses (pauses)
enter        expand / collapse         d        stat mode (headers only)
1-9          filter to that session    Tab      next session
Esc, 0       all sessions              s        open the Transcript in less
+ =          type faster               -        type slower
```
