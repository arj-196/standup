# 0001 — What Standup reads, what it keeps, and what time means

Date: 2026-07-23

Decisions in sequence: the second retires the only state the tool had, the third
reintroduces a store and must justify itself against the second, the fourth
gives that store one protocol, the sixth deletes a knob the third made
pointless, the seventh says where all of them are implemented. The last admits
a second agent's logs without teaching any view a second schema.

## The Scan Universe

**Auto-discovered from Session `cwd` values in the logs under two roots:
`~/.claude/projects/*/*.jsonl` and `~/.codex/sessions/**/rollout-*.jsonl`
(plus `~/.codex/archived_sessions/`).** Zero configuration, no disk walking.
Either root may be absent; the Universe reads the ones present and errors only
when neither is (§ two dialects, one reading). Within a discovered repo the inbox **never
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

## The accelerator protocol

Every derived reading had grown its own copy of one dance: a `get_x`/`put_x`
pair on `Cache` *and* on `NullCache`, a buffer dict, a branch in `flush`, a
table name in `prune`, a `<kind>_version()` shim, and a caller-side
get → decode → compute → encode → put. The fifth artifact (the typed reading)
meant editing six places, and a place forgotten is invisible: a table missing
from `prune` leaks rows forever, a branch missing from `flush` costs a silent
reparse every run.

**A derived artifact is declared once — `cache.Derivation` — and the protocol
supplies the rest.** The declaration carries its kind (which is its table), its
codec, the version that invalidates its rows and which module owns that version,
whether it is stat-keyed, and how its live keys are enumerated. Callers ask for
one `cache.derive(spec, key, stamp, compute, load=, dump=)`.

- **the DDL is generated from the declarations** and applied on every open
  (idempotent), so a derivation declared later gains its table in place instead
  of costing a rebuild of the rows beside it. `SCHEMA_VERSION` remains the lever
  for a change the DDL cannot make in place; a DB stamped with a *newer* schema
  is deleted and rebuilt rather than half-read through columns this version may
  not know.
- **a version stays with its owner** — `READER_VERSION`, `DETECTOR_VERSION` —
  read through the declaration at query time, so the cache holds no copy to keep
  in step. `PARSER_VERSION` stays here because the sweep's row shape is the
  cache's own.
- **the typed round trip stays with its owner too.** `load`/`dump` belong to the
  module that owns the artifact, so nothing in the cache knows what a Session, a
  Loop or a commit *is*. Either side may bow out: `dump` returning None declines
  the row (an empty commit-file list, a reading over `MAX_BLOB_BYTES`), `load`
  returning None or raising means a row this version cannot read. Both cost a
  recompute and change no output — precisely what a pure accelerator may do.
- **liveness is declared, not passed in.** `prune` is handed the log roots
  (`logs.Roots`, § two dialects, one reading) and each declaration enumerates
  its own keys under them. The rule it replaces
  was a hand-maintained coupling: the sweep computed one `live_ids` set for all
  tables, so the first artifact keyed on something the sweep's glob does not
  produce — a subagent transcript's reading (ADR 0002 § subagent usage) — had
  every inbox run deleting the rows the cost view had just written (`standup -a`
  does both). A future artifact keyed on something other than a Session id
  declares its own enumerator rather than remembering to extend that set.
- **the write buffer is read back before the DB is**, for every derivation, so
  one command derives a value once however many views ask for it. That was true
  of commit files alone before, by hand.

Cost: a caller passes two small functions where it used to write two lines, and
a declaration sits between `read_log` and its SQL. Accepted — that indirection
is what makes the fifth artifact cost a declaration and the sixth cost nothing
new.

Rejected:
- **a generic `get`/`put` pair over the declaration** — collapses the tables and
  leaves the five caller-side dances, which is where the version check, the
  stat key and the malformed-row tolerance were each written five times.
- **one table with a `kind` column** — kinds differ in key (a sha is not a stat
  key) and in codec, so the row shape would be the loosest of them and every
  read would re-assert what a declaration states once.

## The one log reader

Six modules parsed the same JSONL, each with its own idea of what it says:
`claude_logs` (titles, edited paths, captured hashes), `cost` (per-turn
`usage`), `fragments` (edit text), `loops` (tool-call shapes), `watchstream`
and `transcript` (prompt text). The duplication was not theoretical — a
mistyped prefilter in `cost`'s copy of the title reading silently demoted every
session title to its last prompt.

**One pass over one log yields one typed `ParsedLog`** — the shape is `logs`'
and the Claude Code schema behind it is `claude_logs`' (since § two dialects,
one reading, which added Codex's beside it):

| reading | type | the schema detail it hides |
| --- | --- | --- |
| edits | `EditBlock` | `file_path`/`notebook_path`; MultiEdit fans out, one block per hunk under a shared `tool_id`; new text is `new_string`/`new_source`/`content` |
| prompts | `Prompt` | injected material is not a prompt: system reminders, `isMeta` bodies, tool-result-only turns, an interrupt's `[Request interrupted by user]` — the one of those that reads as prose, so `is_interrupt` names it and every consumer inherits the refusal |
| per-turn usage | `TurnUsage` | counts *and* the modifiers that price them (fast mode, batch tier, US geo, web search); undifferentiated `cache_creation_input_tokens` reads as 5m |
| the Session | `models.Session` | titles and their precedence, `cwd`, branches, edited files, captured commit hashes |

Notional Cost is derived, never stored: `UsageTotals` prices **turn by turn**
through the Rate Card, because the modifiers are per-turn and a session that
mixed fast and standard turns would mis-price if its tokens were summed first.
Cached rows therefore hold token counts, never dollars — a Rate Card edit
(ADR 0002) re-prices old logs without a cache bump.

**Cached as one row** in `logs` — one row per `session_id`, served only when
`(size, mtime_ns, READER_VERSION)` all match — zlib-compressed and capped,
because the reading carries the text of every edit and every prompt. A row too
large, malformed, or written by another reader version costs a reparse and
changes no output. It is the *only* cached reading of a log's contents: what
hunk attribution matches against is a projection of this row (ADR 0007
§ Decision), not a second index of the same text.

**The line readings are public too** — `tool_calls_in`, `edits_of`/`edits_in`,
`prompt_in`/`prompt_text`, `turn_usage`, `apply_title_fields` — because a
whole-file reading is the wrong shape for a consumer that never holds the whole
file. The Watch tails a log as it grows and the Loop detector prefilters lines
it will not count; both read a line exactly as `parse_log` reads it, so they
are projections of the one reading rather than rivals to it. A consumer that
reached instead for the module's privates would be a rival again, which is why
there are none left to reach for.

A `ParsedLog` is **one file**, so its totals are one log's. A Session's usage
also includes its subagent transcripts (ADR 0002 § subagent usage), which are
separate files and therefore separate readings; folding them is the caller's.

**`last_activity` on a parsed Session means the log file's mtime** — when the
log last grew — and nothing else. It is the staleness clock for out-of-band
artifacts (ADR 0003), which ask whether the session moved on after an artifact
was written; only the file's own clock answers that. "When the model last
spoke" is a different question and has its own name, `ParsedLog.last_turn`;
its window-bounded form, which the cost view ranks `--recent` by and prints as
a row's recency, is `cost.SessionCost.last_turn`. No view overwrites the field.

**The cost view is migrated** (its scanner is deleted): it reads each log with
`read_log`, filters `turns` by the window, and folds them with
`usage_totals` — one fold over the parent's turns concatenated with its
subagents', because pricing is per-turn (ADR 0002) and one Session's turns are
one list however many files they came from. A subagent transcript is a log
too, so it gets a cache row of its own, and being no Session it costs two
rules of its own: it lives below the sweep's `*/*.jsonl` glob, so the `logs`
declaration enumerates its liveness with a glob that reaches it (§ the
accelerator protocol) or the prune discards the row on every inbox run — its
`loops` and `sessions` siblings are keyed on Sessions and enumerate only those;
and its file name (`agent-<id>`) is unique only inside its
parent's directory, so `cache_id` qualifies it with the parent. Unqualified,
two parents' identically-named transcripts share a row as soon as size and
mtime agree, and one Session is priced with another's turns.

Expand first, then contract: the typed reading landed beside the existing
scanners rather than under them, so no view changed behaviour on the day the
seam appeared. The contraction followed — the Loop detector, hunk attribution,
the Watch's tailer and the Transcript all read through it now — and the two
differences it had left open were decided rather than discovered:

- **prompts** — settled on the reader's rule: **prose makes a prompt**,
  whatever else rides the line. The Watch's extra condition (drop any user line
  carrying a `toolUseResult`) parted from it on one shape — what you typed
  while a call was in flight — and dropping that lost a real prompt from the
  feed. The Watch adopted the reader's rule; the Transcript already had it, so
  the two now agree by construction rather than by review.
- **branches** — still a superset in the full reading, because the inbox
  **keeps its prefiltered sweep** (`_full_scan`). That is the call this
  contraction had to make: the sweep is not a rival reading — it lives inside
  this module and shares every line reading with `parse_log` — it is the same
  reading with a prefilter in front, and folding it away would cost
  `json.loads` on every line of ~300 MB of logs to gain a branch name the inbox
  does not print. A consumer that switches to the full reading gains the
  superset, which is the more correct answer.

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

## One module owns the scan

**`universe.py` answers "what does Standup see"**; every view is a consumer.
The pipeline (open cache → scan Sessions → discover repos → attribute → flush)
exists once, in `open_universe()`. A view parses flags, asks, renders.

Why these particular things are hidden there, and not left to the views:

- **the cache's lifecycle** — a missed flush breaks no output (pure
  accelerator), so it is *invisible*: it silently reparses next run. Invisible
  duties do not survive being copied per view. Hence a context manager, flushing
  on exception paths too.
- **the "no logs found" failure** — a view *raises* (`UniverseError`) and `main`
  prints it, because six copies of one print-and-return-1 drift in wording.
- **Repo Entry identity** — was four hand-rolled copies, and they had already
  drifted: the CLI's Session-target list named an entry after whichever cwd it
  saw first, so a worktree could name it while discovery named the main
  checkout. `owner_of` promotes to the main checkout, so one answer stands.

Two consequences worth stating:

- **a non-repo cwd owns itself** (`is_repo=False`). `cost` spans Sessions with
  no git at all, and it needed that fallback; expressing it in the one rule
  keeps the callers from re-inventing it four ways.
- **`handles.py` knows no git**, so Project Handle resolution is testable
  without a checkout. Name matching is handles'; turning a *path* into a Target
  is the Universe's.

Rejected:
- **`git worktree list` to find the main checkout** — a subprocess per repo, on
  the shell-completion path that was tuned to avoid exactly that. The common dir
  already names it (`<main>/.git` → its parent); layouts that break the shape
  test (separate git dir, bare repo) fall back to git's reported toplevel.
- **a process-wide identity cache** — identity is per-command state; a Watch
  running for an hour would pin an answer git had moved on from.

Cost: a Universe is a *command's* view of the world, not a live one — it
memoizes. The Watch therefore reads one at launch and lets it go rather than
holding the cache open for the minutes it stays on screen.

## Two dialects, one reading

*(2026-09-09)* Codex writes its Sessions too — `~/.codex/sessions/YYYY/MM/DD/
rollout-<ts>-<id>.jsonl`, one thread per file, every line `{timestamp,
ordinal, type, payload}` — and nothing in that schema resembles Claude Code's:
usage rides an `event_msg`/`token_count` line that names no model (a
`turn_context` line did), edits are `apply_patch` custom tool calls with
cwd-relative paths in a patch body, the shell is `exec_command`, an interrupt
is a `turn_aborted` event, and the user's side carries `<environment_context>`
and plugin lists spliced in as separate items.

**The typed reading is the seam. `logs` owns the shape every view consumes —
`ParsedLog`, `EditBlock`, `Prompt`, `TurnUsage`, `ToolCall` — and the
`LineReader` contract a streaming consumer reads through; a dialect module
(`claude_logs`, `codex_logs`) owns one schema and nothing else; no consumer
imports a dialect.** `logs.dialect_of(path)` decides by the file's own shape
(`rollout-*.jsonl` is Codex), so a path handed to a view — a Session Handle's
log — needs no root context. `logs.read_log`, `logs.parse_log` and
`logs.reader` dispatch through it; `logs.Roots` names the roots and is the one
enumeration the Universe's scan, the cache's prune, the cost sweep and the
Watch's discovery share.

Rules the seam imposes, each because a view would otherwise learn a schema:

- **a reader is made per file, and may keep state.** A Codex line does not
  name its model or its cwd; an earlier line did. Every public method of the
  Codex reader absorbs those facts from the line it is handed, so a consumer
  that asks only some of the questions (the Watch never calls `note_session`)
  still reads a patch against the right cwd. Claude's reader is the old public
  functions wrapped; the functions stay public and tested.
- **the reading's meaning is fixed by `logs`, and a dialect converts to it.**
  `TurnUsage.input_tokens` is *uncached* input: Claude counts it so, Codex
  counts cached inside `input_tokens`, so the Codex reader subtracts
  (ADR 0002 § Codex usage). `EditBlock.path` is absolute: Claude records
  absolute paths and drops a relative one as unattributable; Codex's patch
  format *defines* its paths as cwd-relative, so joining them is reading the
  format, not guessing.
- **the shell tool, the silent tools and the Activity State verbs are the
  dialect's.** `shell_command(call)`, `is_silent(name)` and `activity(obj)`
  answer for `Bash`/`Read`/`stop_reason` on one side and
  `exec_command`/nothing/`task_complete` on the other (ADR 0004 § Calls,
  § the Activity State). `ACT_VERBS` moved out of the Watch and into each
  dialect; the two states no table names (`SETTLED`, `THINKING`) are `logs`'.
- **one `READER_VERSION`, in `logs`.** The cache row is the shape, whichever
  schema it was read from; a dialect that changes what it reads bumps the shared
  version. `Session.agent` rides the row, so a row without it is a reparse.
- **a Codex rollout is one Session, id from its file name.** A resumed thread
  writes a second rollout `…-<thread>_<fork>.jsonl` carrying the parent thread's
  id inside; the Session id is the *last* id in the name — the one unique to the
  file — so a handle names one log. Codex's internal threads (`thread_source`
  `subagent`/`guardian_review`, its auto-reviewer) get no `cwd` and leave the
  Universe the way every cwd-less Session does.
- **the Codex sweep is the full reading.** Claude's inbox sweep keeps its line
  prefilter (§ the one log reader); a rollout's facts ride lines no cheap scan
  tells apart, and the cache makes the full reading free after the first run.
- **a Codex Session's title is its first prompt.** Rollouts carry no title
  field; `Session.first_prompt` exists for them, ranked above `last_prompt` in
  the fallback, because a thread's last prompt is usually `y`.
- **`Session.agent` is a fact, not a claim.** Read off the file's shape, printed
  bare (`termout.agent_tag`) after a title wherever a Codex Session is named;
  Claude Code's carry nothing, because a tag on every line says less than a
  tag on the exception.

Accepted costs:

- **the Loop detector's turn cost is coarser on Codex.** A Claude usage line
  carries the uuid the calls on it carry; a Codex call names its turn and its
  usage lines each carry their own id, so a Loop's cost there sums over the
  turns its calls fall in. `detect` now accumulates per uuid instead of taking
  the first — identical for Claude, where a uuid appears once.
- **Session Briefs stay Claude Code's.** The Stop hook is Claude Code's; a Codex
  Session renders briefless, which every view already handles.
- **Codex's review threads are counted nowhere.** Their usage is Codex's own
  overhead, not the user's delegated work (the distinction ADR 0002 § subagent
  usage draws), and no parent link is written into them reliably enough to fold
  on. Stated in `codex_logs`; revisit if the auto-reviewer's spend matters.
- **a Codex fork reads as a new Session**, with the thread's earlier prompts in
  the other file. Correct for attribution and cost (each file's usage is its
  own) and honest about what was read; a joined view of a thread across its
  rollouts is a Transcript question, not a reading one.

Rejected:
- **teaching each consumer both schemas** — six consumers, two schemas, and
  the drift § the one log reader was written to end.
- **normalising Codex lines into Claude's line shape** — a fake `tool_use`
  block is a third schema nobody writes, and the Watch's activity rule reads
  `stop_reason`, which Codex has no analogue of.
- **`session_meta.payload.id` as the Session id** — a fork repeats its parent's,
  so two files would answer one handle.
- **deciding the dialect by root** — a view handed a path (a Session Handle's
  log) would need the root passed alongside it everywhere.

## Tried and retracted

- **The sweep names the live ids of every cached artifact.** `scan_sessions`
  built one `live_ids` set — Session log stems, plus the subagent transcript ids
  it globbed a level deeper for — and `prune` applied that one set to every
  table. Retracted: liveness is a property of an artifact's *keys*, so it is
  declared per derivation (§ the accelerator protocol). The sweep still calls
  `prune`, and still tells the cache where the logs are; what it no longer does
  is answer that question on every other artifact's behalf.
