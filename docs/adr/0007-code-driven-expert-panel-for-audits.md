# 7. The Audit: a code-driven Expert Panel, findings not scripts

Date: 2026-07-30

## Status

Accepted

## Context

Sessions burn real money doing work a script could do for free: mechanical
tool-call grinds (read → transform → write, 30 times over a directory) and
**LLM-as-CPU** turns where the model reformats or computes data in its head.
Standup should detect this and help the user script it away.

Three tensions shaped the design:

1. **Determinism vs judgment.** Repetition of tool-call shapes is derivable
   from the JSONL (free, deterministic — fits ADR 0002). Judging whether a
   turn is "mechanical computation" or which sessions share an objective is
   inherently semantic — it needs a model, which means cost, and means the
   result is a *claim*, not a fact.
2. **The irony ceiling.** An analyzer that spends money watching for wasted
   money must keep its own standing cost at zero and its per-use cost visible.
3. **Ownership of the fix.** Standup is a read-only analyst. Generating
   runnable scripts would mean owning correctness of code it cannot test, in
   repos it does not manage.

## Decision

Split the feature along the deterministic/semantic line, and keep every
semantic step on-demand, code-orchestrated, and honestly priced.

**Loops: always-on, free, derived.** A **Loop** is a repeated n-gram (n ≤ 3)
of tool calls matching on tool name + argument shape (directory/glob for file
tools, command head for Bash), ≥ 5 iterations. Detection is pure JSONL
derivation, cached in the Derived Cache. Its **Loop Cost** is the summed
per-turn Notional Cost of the loop's turns — a measured carve-out, **never a
projected saving**. A display floor (~$1 or 10% of session cost) keeps noise
(e.g. retry storms) out of view while below-floor loops remain in the data.
Loops surface as a marker on `cost`-view session lines and as gutter marks on
Transcript turns — never in the Triage Inbox (retrospective analytics, like
Notional Cost itself).

**Audits: on-demand, paid, a claim.** `standup audit <handle>` produces the
semantic judgment: which turns are LLM-as-CPU work, whether the pattern recurs
in sibling sessions, and what to do about it. No Stop-hook piggyback — the 90%
of sessions that are cheap and boring must not pay an analysis tax. The free
Loop layer is the triage that tells the user *which* sessions deserve an
Audit.

**The panel is code, not model discretion.** The Audit runs as a **fixed
Expert Panel**: four parallel, narrow Sonnet passes — Loop Expert (are the
detected Loops scriptable or legitimate?), LLM-as-CPU Expert (scan the target
transcript), Prompt-Structure Expert (the human's side: context waste,
re-explained instructions, missing CLAUDE.md/skills), Recurrence Expert (match
siblings, total their Loop Costs) — then one Opus concluder that receives the
experts' claims plus the deterministic data (never the raw transcript) and
owns the solution and the **Handoff Prompt**. Standup's own code performs the
fan-out with `asyncio`; no model ever decides the panel's shape. This keeps
the audit's structure deterministic, its cost predictable, each pass's `usage`
individually attributable, and the expensive model's context small.

**Sibling recruitment splits gather from match.** Standup *gathers*
deterministically: all sessions of the same Repo Entry, carrying their derived
title, their Session Brief when one exists (Briefs are optional; titles always
exist), and their Loop fingerprints/costs. The Recurrence Expert *matches*
semantically. Only the target session is deep-read — siblings contribute
metadata only, so audit cost does not multiply with sibling count, and
recurrence is reported as measured fact ("recurred in 4 sessions, combined
Loop Cost $23"), the only honest basis for talking about recurring money.

**Findings, not scripts.** The Audit ends in a paste-ready **Handoff Prompt**
("build a script that does X; evidence in session `<handle>` turns N–M") for a
fresh Claude Code session in the target repo. Standup never writes the script.

**Transport: Claude Agent SDK, orchestration: ours.** The panel calls run
through the Python `claude-agent-sdk` — the programmatic face of `claude -p`,
driving the same Claude Code binary and inheriting its login, so the user's
subscription keeps paying (same keyless-auth ground verified in ADR 0006). No
agent framework (LangGraph, CrewAI, Pydantic AI, …): they speak model APIs
natively (breaking subscription billing without a permanently-maintained
subprocess adapter) and their core value is owning orchestration — the very
thing this decision keeps in Standup's code. The future wish for lifecycle
monitoring/UI is answered by data, not framework: Standup already records
per-Expert usage, timing, and artifacts; if a visual UI is ever wanted,
OpenTelemetry spans around each Expert call feed any OTel UI without ceding
control flow.

**Lifecycle mirrors Briefs.** Stored durably at
`~/.standup/audits/<sessionId>.audit.md` (never `cache/` — an Audit is not
recomputable for free). Staled by the session continuing past generation time
(same tolerance as Briefs); sibling drift does *not* stale it. Re-running
re-renders; `--refresh` regenerates; nothing auto-regenerates. Orphans pruned
with the session log. Rendered `~`-marked as a claim throughout.

**Cost honesty.** Every pass's `usage` is recorded and priced by the Rate
Card; the total surfaces as **Audit Overhead** in the `cost` view, itemised
per Expert, mirroring Brief Overhead. Each `standup audit` run prints the
overhead it just incurred. No confirmation prompt before generation — typing
the command is the consent — but the price is always shown after.

## Considered Options

- **Stop-hook auto-audit of every session** — rejected: a standing tax on the
  cheap majority; the irony ceiling. Loops are the free always-on layer.
- **Projected-savings figures ("scripting this saves $X/mo")** — rejected: a
  forecast Standup cannot stand behind. Only measured carve-outs (Loop Cost)
  and measured recurrence are reported.
- **Standup generates the script** — rejected: ownership of untestable code in
  unmanaged repos. Finding + Handoff Prompt keeps Standup a read-only analyst
  while making the fix one paste away.
- **Single-prompt audit (one Sonnet pass)** — rejected in favor of the panel:
  narrow parallel experts are cheaper per token of judgment, and the concluder
  gets clean claims instead of one model's tangle.
- **Model-driven orchestration (agentic Opus spawning subagents)** — rejected:
  every audit takes a different shape, cost is unbounded, per-expert
  attribution is lost, and Opus carries the whole transcript in an expensive
  context. Code owns structure; models own judgment.
- **Agent frameworks (LangGraph/CrewAI/Pydantic AI) for the panel** —
  rejected: native API billing breaks the subscription constraint, the custom
  `claude -p` adapter is a permanent maintenance tax, and the framework's
  raison d'être (owning orchestration) contradicts this ADR. Their monitoring
  UIs are re-obtainable later via OTel spans over data Standup already keeps.
- **Feeding sibling transcripts to the Audit** — rejected: multiplies cost by
  sibling count. Metadata + Loop fingerprints suffice for honest recurrence.
- **Brief-only sibling matching** — rejected: Briefs are optional (hook not
  installed everywhere; historic sessions predate them). Derived titles always
  exist, so matching uses title + Brief-when-present.

## Consequences

- Two new read-path artifacts: Loops (derived, cached, recomputable) and
  Audits (durable claims, like Briefs). The render path stays deterministic —
  `standup audit` without `--refresh` only reads.
- A new dependency (`claude-agent-sdk`) on the generation path only; the
  display path gains none.
- The panel roster is versioned code: adding/removing an Expert is a code
  change with an itemised cost trail, not a prompt tweak.
- The Audit is a fallible self-report squared (experts + concluder). It is
  `~`-marked everywhere, hedged when stale, and its every dollar of overhead
  is visible — so a mis-tuned panel is caught by the very view it inflates.
