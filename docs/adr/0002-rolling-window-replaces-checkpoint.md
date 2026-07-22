# 2. Rolling Recent Window replaces the Checkpoint

Date: 2026-07-22

## Status

Accepted

## Context

Standup originally kept one piece of state: the Checkpoint, a timestamp of the
previous run (`~/.local/state/standup/checkpoint`). The "DONE" section showed
work pushed since the Checkpoint — read-marker semantics: look once, the
section drains.

In practice this had two problems:

1. **Peeking is destructive.** Any plain `standup` run silently advances the
   Checkpoint, so a casual glance on Friday eats Monday morning's summary.
   The `--no-checkpoint` escape hatch existed precisely because the default
   behavior was a trap.
2. **Needs-Decision Items never used it.** Uncommitted and unpushed work is
   ageless by design, so the Checkpoint only ever gated the retrospective
   section — a lot of state machinery for the least important part of the
   output.

The restructure toward Session Rollups (2026-07-22) split the output into a
default Needs-Decision view and an `-a` retrospective, which forced the
question: what time window does the retrospective use?

Alternatives:

1. **Keep the Checkpoint** — "since I last looked" semantics for `-a`.
2. **Rolling window** — retrospective covers a fixed recent period
   (default 7 days), `--since` overrides ad hoc. No state at all.

## Decision

Option 2. The Checkpoint is retired: `checkpoint.py`, the state file, and the
`--no-checkpoint` flag are deleted. The `-a` retrospective shows work pushed
within the **Recent Window** (default: last 7 days); `--since` overrides the
window. Needs-Decision Items remain ageless and unaffected.

## Consequences

- Standup is now stateless and idempotent: the same command at the same moment
  prints the same inbox. Peeking costs nothing.
- Recently pushed work reappears on every `-a` run until it ages out of the
  window, instead of appearing exactly once. That repetition is accepted — the
  retrospective is opt-in via `-a`, so it no longer competes with triage.
- "What happened since I last looked" is no longer answerable precisely; the
  approximation is `--since <when I last looked>`.
- `--since` changes meaning: it previously overrode the Checkpoint timestamp,
  now it overrides the Recent Window. User-visible semantics are near-identical.
