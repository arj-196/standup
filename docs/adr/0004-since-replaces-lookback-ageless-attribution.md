# 4. --since is the only time knob; attribution is ageless

Date: 2026-07-22

## Status

Accepted

## Context

Standup had two overlapping time knobs. `--since` (the Recent Window, default
7d) gates the recently-pushed retrospective shown by `-a`. `--lookback`
(default 30d) gated how far back session logs were *parsed* for attribution
evidence — a session older than the lookback contributed only its cwd and could
attribute nothing. The two were `min`'d into a single parse horizon. `--lookback`
existed almost entirely as a **cost guard**: parsing all logs was slow.

The Derived Cache (ADR 0003) stores every session's full parse, so that cost is
gone and the knob has lost its reason to exist. Two time knobs with different
defaults were also genuinely confusing.

## Decision

Delete `--lookback`. `--since` becomes the only time knob and keeps its single
job: the Recent Window for the retrospective. **Attribution becomes ageless** —
it runs against the full cached history, so a pending or unpushed change is
attributed to whatever session explains it, however old. This is consistent with
the existing rule that Needs-Decision Items are ageless and ignore the Recent
Window (ADR 0002); their attribution is now ageless too.

## Consequences

- One knob, meaning exactly what the glossary says the Recent Window means.
- A single knob cannot serve both jobs with one default: the retrospective wants
  a short window, attribution wants a long reach. Splitting by *what is being
  timed* (ageless items vs. the retrospective) rather than by a second flag is
  what lets one knob suffice.
- `_match_pending` has no time window, so ageless attribution can, in principle,
  tag a currently-pending file with a "likely ~" claim from a very old session
  that once touched that path. Attributions sort recent-first so it ranks last;
  if stale tags surface in practice, the fix is an *internal* attribution horizon
  (~60–90d), never a resurrected user knob.
- Widening `--since` no longer triggers a reparse — the full parse is already
  cached.
