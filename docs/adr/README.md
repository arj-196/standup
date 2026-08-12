# Architecture decisions

A **rationale reference**, not an append-only log. Each file states what is
currently true and why, and is rewritten when a later decision changes it.
History lives in git — `git log -p docs/adr/` recovers any earlier wording.

Written for agents: dense, structured, no narrative build-up. Keep decisions,
rationale that is not recoverable from the code, rejected alternatives with
their reasons, and accepted costs. Drop everything else.

| # | Decision | Covers |
|---|---|---|
| [0001](0001-what-standup-reads-and-what-it-keeps.md) | What Standup reads, what it keeps, and what time means | Scan Universe; retired Checkpoint and the Recent Window; Derived Cache and the durable-root split; `--since` as the only knob; ageless attribution |
| [0002](0002-notional-cost-rate-card.md) | Notional Cost from a single dated Rate Card | why no Real Spend is reported at all; subagent usage folds into the parent Session |
| [0003](0003-out-of-band-artifacts.md) | Out-of-Band Artifacts | the model both share; the one Artifact store behind them (tolerance, clock, naive timestamps); the Session Brief and its Stop hook; the Audit, Loops, and the fixed Expert Panel |
| [0004](0004-the-watch.md) | The Watch | event source; stream/UI boundary; motion never outliving data; Activity State; Change Runs; removed-row field; fold vs clip; selection in the lane; mouse gestures |
| [0005](0005-addressing-on-the-command-line.md) | Addressing on the command line | Project Handles; verb-first vs object-first; `@hash`; short option letters; the reserved-letter rule |
| [0006](0006-committed-is-terminal-in-a-remoteless-repo.md) | Committed is terminal in a Remoteless Repo | repo-relative terminal state |
| [0007](0007-a-hunk-is-attributed-verbatim-or-not-at-all.md) | A hunk is attributed verbatim, or not at all | run matching, the `unaccounted` tier, the commit-time bound |

## Citing from code

Cite number **and section** — a bare `(ADR 0004)` in a Watch module says nothing
a reader did not know:

```python
# the removed-row wash (ADR 0004 § the removed-row field): a red tint one step
```

A **prefix** of the heading is fine when the full one is unwieldy
(`§ Project Handles` for *Project Handles are derived, not registered*), as long
as it is unambiguous within that ADR. Section names are part of the contract:
renaming a `##` heading means updating its citations. `grep -rn "§ <old>"` finds
them.

## Numbering

**Contiguous, `0001..N`, no gaps.** Merging or deleting an ADR renumbers the set
— which is safe only because renumbering and rewriting every citation happen in
the *same* change, as one mechanical pass over `src/` and the docs. Never leave
a citation pointing at a number that moved; the check is that the set of numbers
referenced anywhere equals the set of files present. Empty diff is a pass:

```bash
diff <(grep -rhozE --exclude-dir=__pycache__ "ADR[[:space:]#]*[0-9]{4}" src/ tests/ *.md docs/ \
        | grep -aoE "[0-9]{4}" | sort -u) \
     <(ls docs/adr/ | grep -oE "^[0-9]{4}" | sort -u)
```

Each flag closes a hole a stale citation has escaped through, or could:
`-z` matches a citation wrapped across a line break (`ADR\n0004`) — a
line-anchored grep is blind to those, which is exactly how four wrapped
citations once survived a renumbering pass; `[[:space:]#]*` also absorbs a
comment prefix on the continuation line. `docs/` is recursive, because
`docs/*.md` misses `docs/adr/` itself. `tests/` is in scope because a test that
pins a decision cites the section it pins, and a citation the check cannot see
is exactly how a stale number survives. `--exclude-dir=__pycache__` keeps stale
compiled docstrings from resurrecting fixed numbers (`-I` does not help:
binary detection is off under `-z`).

Commit messages naming an ADR by number are the one thing renumbering falsifies
and cannot fix. That is the accepted cost of a contiguous sequence.

## Retracted rules

When a shipped rule is reversed, move it to that ADR's **Tried and retracted**
section rather than deleting it. The code still carries a retracted rule's
shape, and a reader diffing against an older spec would otherwise read current
behaviour as a bug. *Alternatives considered* is the other thing: options never
shipped.

## When to write one

All three must hold: **hard to reverse**, **surprising without context**, **the
result of a real trade-off**. Prefer extending an existing ADR to opening a new
number — eight separate Watch ADRs became one because they were one design
written in eight sittings, each later one amending an earlier one to stay true.
