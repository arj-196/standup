# 0001 — What Standup reads, what it keeps, and what time means

Date: 2026-07-23

Four decisions in sequence: the second retires the only state the tool had, the
third reintroduces a store and must justify itself against the second, the
fourth deletes a knob the third made pointless. A fifth says where all four are
implemented.

## The Scan Universe

**Auto-discovered from Session `cwd` values in `~/.claude/projects/*/*.jsonl`.**
Zero configuration, no disk walking. Within a discovered repo the inbox **never
filters by attribution**: dirt from a hand edit or another tool appears as an
**Unattributed Change**.

Discovery must parse the JSONL `cwd` field, not decode directory names — the
encoding is ambiguous (`-` means `/`, `.`, and literal `-`).

Cost: a repo Claude never touched is invisible. Escape hatch if it ever hurts is
an additive config of extra roots, which layers on without rework. In exchange
the inbox cannot silently omit pending work in a repo it *does* show.

Rejected:
- **Claude-attributed only** — hides hand-made dirt; the inbox could lie about a
  repo's state.
- **Configured roots** — complete, but needs config maintenance and slow scans.

## The Recent Window, and the retired Checkpoint

The **Checkpoint** was a timestamp of the previous run with read-marker
semantics. Two faults: **peeking was destructive** (any run advanced it, so a
Friday glance ate Monday's summary — `--no-checkpoint` existed because the
default was a trap), and **Needs-Decision Items never used it** (they are ageless
by design, so it gated only the retrospective).

**Retired.** The `-a` retrospective covers the **Recent Window** (default 7d);
`--since` overrides. Needs-Decision Items stay ageless.

Standup is therefore **idempotent** — same command, same moment, same inbox.
Cost: pushed work reappears on every `-a` run until it ages out, instead of
once. Accepted; `-a` is opt-in and no longer competes with triage. "Since I last
looked" is now approximated by `--since <then>`.

## The Derived Cache

~300 MB of session logs with uniformly recent mtimes meant full-parsing every
file every run (~0.58s, identical run to run). Having just made "no stored
state" a point of pride, a `~/.standup` store looked like the thing we deleted.

**`~/.standup/cache/cache.db`** (stdlib SQLite, WAL): one row per session file
holding its full parse, plus immutable per-commit file lists.

It is a **pure accelerator** and *not* the Checkpoint:
- keyed on `(size, mtime_ns)`, so a stale entry is always detected and reparsed;
- output **byte-identical** whether warm, cold, or deleted;
- holds no run-history, no "since I last looked" semantics;
- stores the *complete* parse unconditionally — `--since` is applied at query
  time, decoupling caching from what a command exposes;
- **never caches live git state**; git stays the source of truth.

**It lives in `cache/`, not at the `~/.standup` root**, because the root holds
durable data Standup cannot recompute (Briefs, Audits). `rm -rf
~/.standup/cache` is always safe; deleting the root is not.

A read error or parser-version mismatch triggers a silent cold rebuild — a
broken cache is never a user-visible error. Rows for deleted logs are pruned
each run.

Rejected:
- **Single JSON / per-session JSON** — full-file rewrite each run, or hundreds
  of tiny files. SQLite gives atomic per-row upserts and crash-safety.
- **Content-hash keys** — reading 300 MB to hash it defeats the point.
  `(size, mtime_ns)` is safe because logs are append-only.
- **Incremental append-parse** from a stored byte offset — *deferred*.
  Stat-caching already covers unchanged files; the residual sub-100ms isn't
  worth the offset/partial-line/truncation machinery. The append-only property
  is recorded so this stays available.

## Ageless attribution, and one time knob

`--lookback` (30d) gated how far back logs were *parsed* for attribution
evidence. It existed almost entirely as a cost guard, which the cache removed;
two time knobs with different defaults were also confusing.

**Deleted.** `--since` is the only time knob. **Attribution is ageless** — it
runs against the full cached history, so a pending or unpushed change is
attributed however old the session. Consistent with Needs-Decision Items, which
were already ageless.

One knob suffices only because the split is by *what is being timed* — ageless
items vs. the retrospective — not by a second flag. The retrospective wants a
short window; attribution wants long reach.

Cost: pending-change matching has no window, so an old session that once touched
a path can tag it with a `likely ~` claim. Attributions sort recent-first so it
ranks last. If stale tags surface, the fix is an *internal* horizon (~60–90d),
never a resurrected user knob.

## One module owns the scan

**`universe.py` answers "what does Standup see"**, and every view is a consumer
of it. The pipeline — open the Derived Cache → scan Sessions → discover repos →
attribute → flush — exists in exactly one place, `open_universe()`, a context
manager. Views are thin: a subcommand parses flags, asks the Universe, renders.

Four things it hides, each of which had been copied per view:

- **the cache's lifecycle.** Flushed on *every* path out, exceptions included.
  A skipped flush costs no correctness (the cache is a pure accelerator) and is
  therefore invisible — it just silently reparses next run, which is exactly why
  it cannot be left to each view to remember.
- **where the logs live and how looking for them fails.** The hidden
  `--projects-dir` and the "no Claude Code logs found" message are declared once
  (`add_projects_dir_argument`, `UniverseError`). A view *raises*; `main`
  prints it once, so no view carries its own print-and-return-1.
- **Repo Entry identity.** `owner_of(cwd)` — realpath of `git-common-dir` —
  replaces four hand-rolled copies. A cwd outside git **owns itself**
  (`is_repo=False`, key = its realpath) rather than each caller re-inventing the
  fallback that `cost` needs for a Session with no repo.
- **the main checkout.** An Owner reports the Repo Entry's *main* checkout, so a
  Session that ran in a worktree names the entry the same way discovery does.
  Derived from the common dir (`<main>/.git` → its parent), not from
  `git worktree list`: free, where the list is a subprocess per repo on the
  shell-completion path that was tuned to avoid exactly that. A separate git dir
  or a bare repo fails the shape test and keeps git's reported toplevel.

`handles.py` consequently knows **no git**: a Project Handle is name matching
over Targets, and turning a *path* into a Target is a Universe question. Handle
resolution is therefore testable without a checkout.

Rejected:
- **a scan per view** (the status quo it replaced) — the identity rule drifted
  between its copies: the Session-target list named a Repo Entry after whichever
  cwd it saw first, so a worktree could name the entry while discovery named the
  main checkout.
- **a process-wide Owner cache** — identity is per-command state; a Watch
  running for an hour would pin an answer git had moved on from.

Cost: a Universe memoizes, so it is a *command's* view of the world, not a live
one. The Watch reads one at launch and lets it go rather than holding the cache
open for the minutes it stays on screen.
