# 1. Claude-seeded scan universe, but show all dirt

Date: 2026-07-21

## Status

Accepted

## Context

Standup must decide which repos it inspects. Alternatives considered:

1. **Claude-attributed only** — show only changes a Session explains. Hides hand-made dirt in a Claude repo; the inbox can lie about a repo's real state.
2. **Configured roots** — walk user-listed directories for `.git`. Complete, but requires config maintenance and slow scans.
3. **Claude-seeded + all dirt** — discover repos from `cwd` fields inside `~/.claude/projects/*/*.jsonl`; for each discovered repo, show *all* pending and committed changes in scope, labeling those no Session explains as Unattributed Changes.

Empirical constraints (measured 2026-07-21): 482 session files / 284 MB total, raw read 0.15 s; the encoded project-directory names are ambiguous (`-` encodes `/`, `.`, and literal `-`), so `cwd` inside entries is the only reliable repo path.

## Decision

Option 3. The scan universe is auto-discovered from Session `cwd` values — zero configuration, no disk walking. Within a discovered repo, the inbox never filters by attribution: dirt made by hand or by other tools appears as an Unattributed Change.

## Consequences

- A repo Claude has never touched is invisible to Standup, by design. If that ever hurts, the escape hatch is an additive config of extra roots (the rejected option 2), which layers on without rework.
- The inbox is trustworthy for the repos it shows: it cannot silently omit pending work in a scanned repo.
- Repo discovery must parse JSONL `cwd` fields, not decode directory names.
