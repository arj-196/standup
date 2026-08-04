# 0011 — Activity State is inferred from the log tail, and motion never outlives the data

Date: 2026-08-03
Status: accepted

## Context

The **Watch** was built to show changes arriving, and it is now used to monitor
an agent (see [TODO.md](../../TODO.md)). Monitoring has one question the feed
cannot answer: *is it still working, or is it waiting on me?* Nothing appears in
the feed when a turn ends — the agent simply stops — so the only way to find out
was to switch to the Claude Code terminal and look. That round trip is the cost
this decision removes.

The signal is available. Every assistant line in the session JSONL carries a
`stop_reason`: `tool_use` while the turn continues, `end_turn` when the agent
hands control back (56 of 60 recent logs end on exactly
`assistant / end_turn / text`). A pending `tool_use` names the tool that is
running. An interrupt is recorded too — `interruptedMessageId` for `Esc`,
`interruptedByShutdown` when the session quits mid-turn.

But one state has no line of its own. Log lines are appended *after* a message
completes, so the interval where the model is composing is written nowhere. It
exists only as the gap between a `tool_result` and the next assistant line.

Standup's whole discipline is that a claim is marked as a claim, and that
**Live Session** is a recency reading rather than a process fact — Standup never
inspects processes. Admitting a state that is read from an *absence* of data
needs to be recorded, because it is a new kind of claim in a tool that has so
far only ever derived from things that were written down.

## Decision

**Activity State is derived from the tail of a Session's own log, mid-turn only.**
A settled session has no Activity State, and the Watch says nothing about it.
The reading is:

| From the log | State |
|---|---|
| `stop_reason: tool_use` | the tool's verb — `reading` / `writing` / `running`, or `acting` unmapped |
| any other `stop_reason` | none: the turn is over |
| `interruptedMessageId` / `interruptedByShutdown` | none: the turn was cut short |
| a `tool_result`, or a user prompt, with no assistant line yet | `thinking` — **inferred from silence** |

Three consequences we are committing to:

1. **`thinking` is an inference, and is documented as one** — in CONTEXT.md and
   in the manual, not only in code comments. It is not `~`-marked: the tilde in
   this codebase marks claims about *what happened or was intended* (a
   **Session Brief** objective, an attribution guess), and tilde-ing one verb of
   five would imply the other four are certain in a stronger sense than holds.
   The honesty is carried structurally instead, by points 2 and 3.

2. **No stall threshold, ever.** A non-terminal state is displayed with its own
   age and nothing else. `thinking 14m` is left to speak for itself rather than
   being re-labelled `stalled`, because "stalled" would be Standup inferring
   that a process died — precisely the claim **Live Session** exists to refuse.
   The reader knows whether they asked for something that takes fourteen
   minutes; Standup does not.

3. **Motion may never outlive the data.** The spinner beside the verb animates
   only while the log is still being appended (the same `FRESH` window the
   header uses to colour recency); past that it freezes to a static glyph in
   muted colour. This is a general rule for the Watch, not a detail of this
   feature: a moving glyph asserts liveness, and a session killed mid-turn
   leaves a log tail indistinguishable from one still working. Without the
   freeze, the single worst outcome of the feature is reachable — watching a
   spinner turn for ten minutes on a session that is already dead. Motion is
   therefore bound to arriving bytes, never to a state word.

Also decided, as consequences rather than separate calls:

- **Not a Feed Event.** A state is not something that happened, and one row per
  transition would have added ~248 rows to a real 124-tool-call turn observed in
  this repo's own logs — three-quarters of the feed becoming state chatter with
  the edits buried in it. The Activity State lives in the status bar only.
- **Main thread only.** `isSidechain` lines are skipped: a subagent's reads are
  not the session's, and several subagents at once have no single answer.
- **Every acting session is named**, in lane order, because a settled one costs
  zero cells — the display only grows when work is genuinely parallel, which is
  when seeing all of it matters most. It degrades to a bare count on a narrow
  terminal rather than truncating a verb.

## Consequences

- The question "is it done?" is answerable without leaving the Watch, which was
  the whole point.
- A killed session shows a frozen spinner and a growing age rather than a lie.
  It is *not* labelled dead — that remains unknowable — but it stops looking
  alive, which is the honest half of the distinction.
- `thinking` is wrong in one specific way we accept: a session killed in the gap
  after a `tool_result` reads as `thinking` until it ages out of the **Live
  Session** window. The frozen spinner is what makes this visible; nothing in
  the log can make it precise.
- The rule in point 3 binds the next animated thing added to the Watch. The
  existing typing animation already honours it from the other direction (it
  compresses so the display can't lag the log); this states the general form.
- No CLI surface change: no flag, no subcommand, nothing to configure. The
  state appears because a session is acting, and disappears because it stopped.

## Amendment — a tool verb has a display floor (2026-08-04)

Two things in the reading above were wrong once it met real logs.

**`stop_reason` is the message's, not the line's.** Claude Code flushes an
assistant message's `text` and `thinking` blocks as their own JSONL lines, each
carrying that message's `stop_reason: tool_use`. The table's first row was
therefore matched by lines announcing no tool at all, which fell through to
`acting` — the verb reserved here for a tool absent from the table. Over eight of
this repo's own sessions, 315 of 838 `tool_use`-stop-reason lines named no tool
(231 `thinking` blocks, 84 `text`), and every one was read as `acting`. The row
now requires a `tool_use` block on the line; a line naming no tool leaves the
state and its age untouched, because the model is still composing.

**Binding a verb to its tool's execution window made two verbs unobservable.**
`reading` held the state for 7 seconds in total across those eight sessions and
`writing` for 8, against 6740 for `thinking` — a local `Read` returns in ~25ms,
below even the Watch's 250ms poll. The band therefore read `thinking` in
essentially every frame, which is *true* and useless: the one question it exists
to answer was answered "composing" while the turn was in fact fourteen file
reads. A tool verb now holds the band for at least `ACT_FLOOR` (1s) before
`thinking` may replace it.

That floor is a **word** briefly outliving its tool, and point 3 above bans
exactly that for **motion** — so the boundary is worth stating rather than
leaving to be inferred. Motion asserts *liveness*, which is why it may never be
synthesised; a verb asserts what the last thing was, and a floor on it spends one
second of staleness to buy a legible answer. The distinction is held by three
yields, all load-bearing:

- a settled or interrupted turn clears the held verb, so the bar still goes blank
  the instant the agent hands control back — the asymmetry that makes absence the
  answer is untouched (verified: replaying those logs at the UI's poll rate
  leaves the blank-frame count identical, 302284 either way);
- the next tool verb overwrites immediately — the floor never delays fresher
  news, only staler silence;
- the age displayed is the verb's real age, never the floor's, so a held
  `reading` reads `0s` rather than an invented figure.

Resolved where the state is *read* (`WatchStream._activity_of`), not where it is
tracked, so the tailer's record of what the log said stays what the log said.
The floor is one constant in one place if it ever wants tuning.
