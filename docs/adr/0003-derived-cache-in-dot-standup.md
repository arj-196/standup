# 3. A derived cache in ~/.standup, not resurrected state

Date: 2026-07-22

## Status

Accepted (amended 2026-07-22: the cache moved from the `~/.standup` root into a
`~/.standup/cache/` subdirectory, reserving the root for durable, non-recomputable
data — the first tenant being the Session Brief. See CONTEXT.md → Session Brief.)

## Context

An active user's `~/.claude/projects` holds ~300 MB of session logs whose mtimes
are all recent, so the two-speed scan (ADR 0001) full-parses *every* file on
*every* run — ~0.58 s of pure log parsing that is identical run to run. The
retired Checkpoint (ADR 0002) made "no stored state" a point of pride, so a
`~/.standup` store looks, superficially, like the thing we just deleted.

## Decision

Add a **Derived Cache** at `~/.standup/cache/cache.db` (stdlib SQLite, WAL). It holds
one row per session file — the fully parsed Session (cwd, branches, titles,
edited files, commit hashes, and any per-session token totals) — plus an
immutable `commit_files(sha)` table. It is a **pure accelerator**: keyed on
`(size, mtime_ns)` so a stale entry is always detected and reparsed, output is
**byte-identical** whether the cache is warm, cold, or deleted, and it never
stores run-history. It is *not* the Checkpoint — it holds no "since I last
looked" semantics.

The cache stores the *complete* parse of every file unconditionally; the
`--since` horizon is re-applied at query time (see ADR 0004), so caching is
decoupled from what any given command exposes. Live git state is never cached —
git remains the source of truth and de-facto read-marker.

The cache lives in a `cache/` **subdirectory**, not at the `~/.standup` root,
because the root also holds durable data that standup *cannot* recompute — the
Session Brief (`~/.standup/briefs/<sessionId>.brief.md`), authored out-of-band by
a Claude Code Stop hook. Splitting the two keeps "disposable accelerator" and
"authored, non-recomputable artifact" on opposite sides of a `rm -rf` boundary.

## Considered Options

- **Single JSON / per-session JSON files** — rejected: full-file rewrite each
  run (JSON) or hundreds of tiny files (per-session). SQLite gives atomic
  per-row upserts, crash-safety, and scales to thousands of sessions.
- **Content-hash keys** — rejected: reading 300 MB to hash it defeats the point;
  `(size, mtime_ns)` is safe because session logs are append-only.
- **Incremental append-parse** (resume from a stored byte offset) — deferred:
  stat-caching already eliminates reparsing for unchanged files; the residual
  sub-100 ms on hot files isn't worth the offset/partial-line/truncation
  machinery. The append-only property is recorded so this can be added later.

## Consequences

- The cache is disposable: `rm -rf ~/.standup/cache` is always safe and correct.
  Deleting the `~/.standup` **root** is no longer safe — it would take the durable
  Session Briefs with it. Any read error, or a `parser_version` mismatch (bumped
  when parse logic changes), triggers a silent cold rebuild — a broken cache is
  never a user-visible error.
- Rows for deleted session files are opportunistically pruned each run.
- Warm runs drop from ~2.35 s to roughly the git cost alone; parallelizing the
  per-repo git subprocesses and caching immutable `commit_files(sha)` cut that
  residue further, all while preserving byte-identical output.
