# 0003 — Out-of-Band Artifacts: the Session Brief and the Audit

Date: 2026-07-30

Two LLM-authored artifacts: the **Session Brief** (what a session set out to do)
and the **Audit** (where a session burned money a script could save). Decided a
week apart; one shared model.

## The shared model

Standup's render path is deterministic and derived-only, and **Resume** is
defined as *never LLM-generated at render time*. A render-time summarizer would
make the same command at the same moment produce different output.

The unlock: **the summary need not be produced when standup runs.** Produced
offline — like commits and edits are offline — standup stays a pure reader.

Both artifacts have all six properties:

| Property | Why |
|---|---|
| Generated out of band, never at render time | keeps the render path deterministic; standup only reads, and marks staleness rather than regenerating |
| Stored in the durable `~/.standup/` root, never `cache/` | neither is recomputable, so a cache wipe must not destroy them |
| Rendered `~`-marked as a claim | both are fallible self-reports (the Audit is one squared: experts + concluder) |
| Staled, not refreshed | a session continuing past generation time (beyond debounce tolerance) tags it `(stale)`; nothing auto-regenerates |
| Priced by the Rate Card, surfaced as labelled **Overhead** | real token work that touches no repo, so a mis-tuned generator is caught by the view it inflates |
| Pruned with the session log | orphans go the way of a cache row |

**Staleness is measured against the session log's mtime**, on every surface —
never a view's own notion of activity. Trustworthiness is a property of the
*artifact*, so two views must never disagree about it, and the question
staleness asks ("did the session advance past the claim?") is answered
literally by "did the file grow?". A view-local clock answers a narrower
question and always under-reports: `cost`'s activity timestamp, for instance,
advances only on *priced assistant turns inside its window*, so a session that
continued with unpriced turns, or with a prompt not yet answered, looks frozen
to it. Rejected: giving each view its own clock — cheaper per view, but it lets
the same Brief render `(stale)` in the inbox and unhedged in `cost`, which is
the one failure the `~`-marking exists to prevent.

Both generate by **shelling out to Claude Code, not the Anthropic API**, so the
user's existing login pays. Verified 2026-07-22 (Claude Code 2.1.201): headless
`claude -p` authenticates with no `ANTHROPIC_API_KEY` and no TTY — turnkey for
subscription users, who mostly have no API key. `--bare` is avoided: it skips
the keychain/OAuth read that makes keyless auth work. Neither generator does the
shelling out itself: both reach a model through the one **LLM Pass** seam below.

A missing artifact is never an error; the view degrades to what it showed before
the artifact existed.

## The Artifact store

The shared model above is **one implementation**, not a shape two modules
re-derive: `artifacts.py` owns the durable-root location, the frontmatter
round-trip, the atomic write, orphan pruning, the generation lock, and the
staleness comparison. `brief.py` and `audit.py` are adapters — each owns only
the fields its kind carries and the object they become; neither reaches into
the other. A third kind of artifact is a `Store(dirname, suffix)` plus a mapping
function.

- **One tolerance, by identity.** The generation debounce and the staleness
  threshold are the same constant (`artifacts.TOLERANCE`), because they are the
  same statement: an artifact is allowed to lag its session by that much, so
  hedging earlier would hedge every artifact written on time. They were
  previously two constants in two modules kept equal by a comment, which is a
  drift waiting to happen — a debounce raised without the tolerance would mark
  correct artifacts stale. The two measure different quantities on purpose —
  the debounce compares the *artifact's mtime* to now (it must decide before
  parsing the file), the hedge compares its `generated` to the *log's* mtime —
  so they are not each other's inverse at the boundary. The invariant the
  shared constant buys is one-directional and is what the tests pin: the window
  in which the generator declines to rewrite sits wholly inside the window in
  which a reader trusts the claim.
- **The clock is chosen inside the seam.** `log_advanced_past(generated,
  log_path)` stats the log itself; callers pass a *path*, never a timestamp, so
  no view can substitute its own notion of activity. Exactly *at* the tolerance
  is not yet stale.
- **A naive `generated` is read as UTC.** Every writer stamps UTC, so a naive
  timestamp is a hand edit that dropped the offset. Accepted cost: a hand-edited
  artifact from a non-UTC author can be misjudged by the local offset. Rejected:
  refusing the comparison (the shipped behaviour before this) — it left every
  hand-edited artifact permanently unhedged, which is the failure mode the
  hedge exists to prevent, and made "naive vs aware" untestable behaviour.
- **The lock creates the directory.** A generation's lockfile is the first thing
  written on a machine that has produced no artifact yet; with the directory
  created only at write time, every generation failed before the first write
  could ever create it.
- **`ROOT` is read at call time**, never captured in a default argument, so the
  durable root is rebindable in one place (which is also what keeps a test run
  out of the real `~/.standup`).

## The LLM-pass seam

Reaching a model is **one named thing** (`llmpass`), not a habit each generator
picks up: an **LLM Pass** is prompt and model in, text plus priceable `usage`
out. Brief generation, the Expert Panel fan-out and `standup install`'s
doctor-check go through it, and nothing else in `src/` may.

The point is testability of the *generation logic*, which is where the bugs and
the money are: locking, digesting, output parsing, storing and Overhead
itemisation are exercised against a fake transport returning canned text
(`tests/support/llm.py`), so the suite needs no `claude` binary and no network —
the standing rule the conftest cannot enforce. Overhead recording is unchanged
by the seam: a `Result`'s `usage` is what a Brief's `gen_usage` and an Audit's
`overhead` rows already carried.

- **A Pass is `prompt` + `context`, not one string.** The instruction is the
  prompt; bulk evidence (a transcript digest, the siblings table, the Experts'
  claims) is context. The `claude -p` transport pipes context on **stdin** — a
  120 KB digest must never become an argv — and the split says which half a
  prompt change touched.
- **Two rules live in the seam**, because both are statements about a pass
  rather than about a Brief or an Audit: an **empty result is a failure** (a pass
  that answered nothing produced no claim, and nothing partial is ever stored),
  and a pass that **outruns its timeout is a failure named by its label**. Every
  transport failure — a missing binary, a broken pipe, an SDK error, a malformed
  reply — arrives as one `PassError`, so no generator matches on a transport's
  own exception type.
- **The transport is chosen from a pass's shape, never by a caller.** A single
  pass runs through `claude -p`; a fan-out runs through the Agent SDK. Both drive
  the same Claude Code binary on the same login, so the subscription keeps paying
  either way (verified 2026-07-22 for `claude -p`, 2026-07-30 for the SDK).
- **Why not one transport for all three.** Each call path needs something the
  other cannot express, and both needs are load-bearing:
  `--no-session-persistence` keeps a Brief's generation out of
  `~/.claude/projects`, where Standup would otherwise read its own pass back as a
  Session — a phantom row in the very inbox the pass exists to describe — and the
  CLI has no flag that turns tools *off* (only an enumerated
  `--disallowed-tools` denylist, which rots as the tool set grows), while the SDK
  states an Expert's bounds exactly (`max_turns=1`, `allowed_tools=[]`,
  `setting_sources=[]`), which is what makes a panel's cost predictable. Pushing
  either onto the other transport would trade a verified property for a shorter
  module.
- **The fan-out is threads over blocking passes**, not an event loop the
  generator owns: `run_all` preserves the caller's order (the concluder's
  sections and the Overhead itemisation are read off it) and reports each pass as
  it lands. Accepted cost: the first failure fails the fan-out, but the failing
  call still waits for its siblings — a running subprocess cannot be cancelled
  the way a coroutine could.
- **One digest, two artifacts.** `transcript.digest` renders a Session as plain
  text for a pass; an Audit asks for the `t<N>` markers, per-turn costs and Loop
  marks its evidence coordinates need. They were two near-identical bodies in two
  generators, differing only in numbering and cost tags — which is how they came
  to disagree, and how both came to crash on any session containing a tool call.

## The Session Brief

Native titles are terse, sometimes wrong, and one session may span several
objectives. The Brief is a small LLM-authored file describing a Session's
objective, generated during the session and merely read by standup.

**A Stop hook triggers generation** — Stop fires deterministically at every turn
boundary and has no matcher. The hook **injects nothing into the live
conversation**: it detaches a background job and exits 0, so the interactive
session pays zero extra tokens and zero extra round-trips even if summarization
outlives the hook's timeout.

**The hook command is a thin shim into the package** (`standup _brief`), not a
generated script, so gate/debounce/prompt/write logic stays versioned and
testable rather than a copy that rots on upgrade. The runtime `PATH` dependency
this adds is satisfied by construction — `standup install` placed the hook — and
if `standup` is absent the hook exits 0 harmlessly.

**Gated and debounced.** A fast LLM-free check for a git footprint (an
`Edit`/`Write`, or a `cwd` that is a git repo) aligns Brief population with what
standup displays and avoids paying to summarize throwaway chats. A lockfile plus
staleness check limits generation to roughly once per active session, not per
response.

**The Brief augments, never replaces.** The derived title stays the aligned hard
anchor; the objective renders as an additional `~`-marked line. A multi-objective
session records the dominant *through-line* — chosen at write time, where full
context exists — with secondary threads in the body, surfaced in `standup
session`.

`standup install` merges the hook entry into user-scope settings idempotently,
without clobbering existing hooks, and runs a doctor-check that warns if
headless auth fails. `standup uninstall` removes it.

## The Audit

Sessions burn money on mechanical tool-call grinds and **LLM-as-CPU** turns
where the model computes in its head. Three tensions:

1. **Determinism vs judgment.** Repetition of tool-call shapes is derivable from
   the JSONL — free, deterministic. Judging "mechanical computation" or shared
   objectives is semantic: it needs a model, costs money, and yields a *claim*.
2. **The irony ceiling.** An analyzer watching for wasted money must keep its
   standing cost at zero and its per-use cost visible.
3. **Ownership of the fix.** Standup is a read-only analyst; generating scripts
   means owning untestable code in repos it does not manage.

The feature splits along the deterministic/semantic line.

**Loops — always-on, free, derived.** A **Loop** is a repeated n-gram (n ≤ 3) of
tool calls matched on tool name plus argument shape, ≥ 5 iterations, detected by
pure JSONL derivation and cached. Its **Loop Cost** is the summed per-turn
Notional Cost of its turns — a **measured carve-out, never a projected saving**.
A display floor hides noise while below-floor loops stay in the data. Loops
surface on `cost`-view session lines and as Transcript gutter marks, never in
the Triage Inbox — retrospective analytics, like Notional Cost itself.

**Audits — on-demand, paid, a claim.** `standup audit <handle>` produces the
semantic judgment. No Stop-hook piggyback: the cheap, boring 90% of sessions
must not pay an analysis tax. **The free Loop layer is the triage that says
which sessions deserve an Audit.**

**The panel is code, not model discretion.** A fixed **Expert Panel** of four
parallel narrow Sonnet passes — Loop Expert (are these Loops scriptable?),
LLM-as-CPU Expert (scan the target transcript), Prompt-Structure Expert (the
human's side: context waste, re-explained instructions, missing
CLAUDE.md/skills), Recurrence Expert (match siblings, total Loop Costs) — then
one Opus concluder receiving the experts' claims plus deterministic data,
**never the raw transcript**. Standup's code does the fan-out; no model decides
the panel's shape. This keeps structure deterministic, cost predictable, each
pass's usage attributable, and the expensive model's context small.

**Sibling recruitment splits gather from match.** Standup *gathers*
deterministically — same Repo Entry, each sibling carrying derived title, Brief
if present, Loop fingerprints and costs. The Recurrence Expert *matches*
semantically. Only the target is deep-read, so cost does not scale with sibling
count, and recurrence is reported as measured fact ("recurred in 4 sessions,
combined Loop Cost $23") — the only honest basis for talking about recurring
money.

**Findings, not scripts.** An Audit ends in a paste-ready **Handoff Prompt**
("build a script that does X; evidence in session `<handle>` turns N–M") for a
fresh session in the target repo. Standup never writes the script.

**Transport theirs, orchestration ours.** Panel passes ride the fan-out half of
the LLM-pass seam above, which for a fan-out is the Python `claude-agent-sdk` —
the programmatic face of `claude -p`, same binary, same login, and the one
transport that can state an Expert's bounds. No agent framework: they speak model
APIs natively (breaking subscription billing absent a permanently-maintained
adapter), and their core value is owning orchestration, which is exactly what
this keeps in Standup's code. Lifecycle monitoring, if ever wanted, comes from
data not framework — OTel spans around each Expert pass feed any OTel UI without
ceding control flow.

**Cost honesty.** Every pass's usage is priced and itemised per Expert as **Audit
Overhead**, printed after each run. No confirmation prompt — typing the command
is the consent — but the price is always shown. The panel roster is versioned
code: adding an Expert is a code change with an itemised cost trail, not a
prompt tweak.

## Tried and retracted

- **The Transcript computing its own staleness** — `standup session` compared
  the Brief against the newest `timestamp` *inside* the JSONL, because it holds
  a log path and no Session. That is a view-local clock by another name: a log
  can grow lines that carry no timestamp the view reads, and the two surfaces
  could then disagree about the same Brief. Retracted in favour of the one
  comparison above; the Transcript now passes its log path to the store.

## Alternatives considered

- **One transport for every pass** — either `claude -p` everywhere (the panel
  loses tool suppression, so an Expert can read files and a panel's cost stops
  being predictable) or the SDK everywhere (Brief generation loses
  `--no-session-persistence`, littering `~/.claude/projects` with passes Standup
  then reads back as Sessions). One module fewer, one verified property gone. The
  seam keeps callers ignorant of the choice, which is what the shorter version
  was really buying.
- **A transport argument on every generator call** — the caller would name a
  transport, so "which transport" would spread back out to the call sites the
  seam exists to keep ignorant of it. `transport=` survives only as a test seam,
  defaulted from the pass's shape.
- **Mocking `subprocess.run` / the SDK's `query` to test generation** — pins the
  invocation, not the behaviour: the tests would break on a flag change and pass
  on a wrong prompt. A fake transport is asserted against the *Pass* — which
  Expert saw which evidence — which is the thing that can be wrong. The two
  transports are consequently the deliberately untested edge: one function each,
  verified by hand against a real login on the dates above.
- **A seam per artifact** (a Brief transport and an Audit transport) — the two
  rules that matter (empty result, timeout) would be written twice and drift, the
  way the two digests did.
- **Render-time LLM summarizer** — slow, costly, on the interactive path,
  non-deterministic. Breaks derived-only and the Resume rule.
- **Passive global instruction, or a voluntary "log-session" tool** —
  best-effort, not deterministic. The model drops passive instructions in long
  sessions; an uninvoked tool is as empty as an unfollowed instruction. Neither
  *forces* the write.
- **Stop hook injecting an instruction into the conversation** — forces one
  extra round-trip of the expensive model per fire, for content the out-of-band
  variant gets free.
- **SessionEnd hook** as primary trigger — missable on hard kills, and the model
  is out of the loop at end-of-session, so a separate call is needed anyway.
- **A `standup log-session` subcommand as the write path** — couples every
  Claude Code session everywhere to standup's presence and CLI contract. A
  sidecar file keeps the producer ignorant of the consumer.
- **A self-contained generated hook script** — debounce races, JSONL parsing and
  prompt would be an untested copy drifting on every upgrade.
- **Storing artifacts in the Derived Cache** — neither is recomputable, so a
  wipe would destroy real data.
- **Replacing the title with the objective** — titles must align for
  at-a-glance scanning, and an unverified claim must not hold the anchor slot.
- **Anthropic API with a key, or `claude --bare`** — most users are on a
  subscription with no key, and `--bare` skips the keychain read.
- **Persisting the generation session and parsing it for cost** — litters
  `~/.claude/projects`, grouping depends on launch-cwd realpath, and identifying
  generation sessions is fragile (a deterministic `--session-id` errors with
  "already in use" on the second run). Recording the run's own usage in the
  artifact and pricing it ourselves is self-contained and cheaper.
- **Stop-hook auto-audit of every session** — a standing tax on the cheap
  majority; the irony ceiling. Loops are the free always-on layer.
- **Projected-savings figures** — a forecast Standup cannot stand behind. Only
  measured carve-outs and measured recurrence are reported.
- **Standup generating the script** — ownership of untestable code in unmanaged
  repos. Finding + Handoff Prompt keeps it a read-only analyst.
- **Single-prompt audit** — narrow parallel experts are cheaper per token of
  judgment, and the concluder gets clean claims instead of one model's tangle.
- **Model-driven orchestration** — every audit takes a different shape, cost is
  unbounded, per-expert attribution is lost, and Opus carries the whole
  transcript. Code owns structure; models own judgment.
- **Agent frameworks** (LangGraph, CrewAI, Pydantic AI) — native API billing
  breaks the subscription constraint, a custom adapter is a permanent tax, and
  the framework's purpose contradicts this decision.
- **Feeding sibling transcripts in** — multiplies cost by sibling count;
  metadata plus Loop fingerprints suffice.
- **Brief-only sibling matching** — Briefs are optional (hook not installed
  everywhere; historic sessions predate them), so matching uses title plus
  Brief-when-present.
