# 0001 — What Standup reads, what it keeps, and what time means

Date: 2026-07-23

Four decisions in sequence: the second retires the only state the tool had, the
third reintroduces a store and must justify itself against the second, the
fourth deletes a knob the third made pointless.

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

## The one log reader

Five modules parsed the same JSONL, each with its own idea of what it says:
`claude_logs` (titles, edited paths, captured hashes), `cost` (per-turn
`usage`), `fragments` (edit text), `loops` (tool-call shapes), `watchstream`
and `transcript` (prompt text). The duplication was not theoretical — a
mistyped prefilter in `cost`'s copy of the title reading silently demoted every
session title to its last prompt.

**`claude_logs` owns the reading. One pass over one log yields one typed
`ParsedLog`:**

| reading | type | the schema detail it hides |
| --- | --- | --- |
| edits | `EditBlock` | `file_path`/`notebook_path`; MultiEdit fans out, one block per hunk under a shared `tool_id`; new text is `new_string`/`new_source`/`content` |
| prompts | `Prompt` | injected material is not a prompt: system reminders, `isMeta` bodies, tool-result-only turns |
| per-turn usage | `TurnUsage` | counts *and* the modifiers that price them (fast mode, batch tier, US geo, web search); undifferentiated `cache_creation_input_tokens` reads as 5m |
| the Session | `models.Session` | titles and their precedence, `cwd`, branches, edited files, captured commit hashes |

Notional Cost is derived, never stored: `UsageTotals` prices **turn by turn**
through the Rate Card, because the modifiers are per-turn and a session that
mixed fast and standard turns would mis-price if its tokens were summed first.
Cached rows therefore hold token counts, never dollars — a Rate Card edit
(ADR 0002) re-prices old logs without a cache bump.

**Cached as one row** in `logs`, keyed `(size, mtime_ns, READER_VERSION)`,
zlib-compressed and capped like the fragment index — the reading carries the
text of every edit and every prompt. A row too large, malformed, or written by
another reader version costs a reparse and changes no output.

**`last_activity` on a parsed Session means the log file's mtime** — when the
log last grew — and nothing else. It is the staleness clock for out-of-band
artifacts (ADR 0003), which ask whether the session moved on after an artifact
was written; only the file's own clock answers that. "When the model last
spoke" is a different question and has its own name, `ParsedLog.last_turn`.
The cost view still overwrites the field with a window-bounded reading of its
own; removing that divergence is what migrating it onto this reader is for.

Expand first, then contract: the typed reading landed beside the existing
scanners rather than under them, so no view changed behaviour on the day the
seam appeared.

Rejected:
- **A reading per consumer, kept in sync by review** — the status quo, and the
  thing that already drifted once in a way no test could see.
- **Caching the reading as priced dollars** — smaller rows, but a Rate Card
  change would then be invisible until every row aged out.
- **Folding the reading into `scan_sessions`' prefiltered pass** — the prefilter
  (`_interesting`) exists to skip `json.loads` on ~99% of lines for the inbox's
  cheap facts. Prompts and usage live on ordinary conversation lines, so a
  union prefilter admits nearly every line; keeping both paths lets the inbox
  stay cheap while the full reading pays for what it asks for.

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
