# 6. The Session Brief: an out-of-band, LLM-authored objective per session

Date: 2026-07-22

## Status

Accepted

## Context

A Session's native title (`aiTitle`/`customTitle`/`slug`) often fails to convey
what the session was actually *for*: titles are terse, sometimes wrong, and a
single session may span several distinct objectives. The triage inbox would be
far more useful if each **Session Rollup** could say, in one line, what the
session set out to do.

The obvious implementation — have `standup` run an LLM over each session's
transcript at render time — is rejected on two grounds. It is **slow and costly**
(dozens of model calls on every `standup` invocation, on the interactive path),
and it **violates the tool's core ethos**: `standup` is deterministic and
derived-only (ADR 0002), and **Resume** is defined as *"never LLM-generated at
render time."* An on-demand summarizer would make the same command at the same
moment produce different output run to run.

The insight that unblocks this: the summary does not have to be produced *when
standup runs*. If it is produced *while the coding session runs* — offline, like
commits and edits are offline — then standup remains a pure reader that only
*joins and renders*, and determinism is preserved.

## Decision

Introduce the **Session Brief**: a small, LLM-authored file describing a
Session's objective, generated **out-of-band during the session** and merely
*read* by standup.

**Generation.** A Claude Code **Stop hook** (fires deterministically at each turn
boundary; Stop has no matcher, so it fires on every turn) triggers generation.
The hook does **not** inject any instruction into the live conversation — it is
registered `async: true` and its command **`setsid`/`nohup`-detaches** a
background job and exits 0, so the interactive session pays *zero* extra tokens
and *zero* extra round-trips even if the summarization outlives the hook's
timeout. The hook receives `transcript_path`, `session_id`, and `cwd` on stdin,
so it can locate the log and compute the output path. Loop-guard
(`stop_hook_active`) is irrelevant here because the hook never blocks.

The background job runs the cheap model by **shelling out to `claude -p --model
claude-haiku-4-5`** over the session JSONL — *not* the Anthropic API. Verified on
2026-07-22 (Claude Code 2.1.201): headless `claude -p` authenticates via the
user's existing Claude Code login with **no `ANTHROPIC_API_KEY`, no `--bare`, and
no TTY** — so it is turnkey for subscription users, who are the majority and have
no API key. (`--bare` is explicitly *avoided*: it skips the keychain/OAuth read
that makes keyless auth work.)

**Where the logic lives.** The hook command is a thin shim into the standup
package — `standup _brief` — not a generated standalone script. The gate,
debounce, prompt, and write logic thus live in versioned, unit-testable code
rather than a copy that rots on upgrade. This reintroduces a *runtime* dependency
on `standup` being on `PATH`, which is acceptable because `standup install` is
what put the hook there in the first place; if `standup` is ever absent, the hook
exits 0 and harms nothing.

**Gating.** Before spending a model call, the hook does a fast, LLM-free check on
the JSONL: skip generation unless the session has a git footprint (made an
`Edit`/`Write`, or its `cwd` is a git repo). This aligns the Brief population
with what standup actually displays and avoids paying to summarize throwaway
chats.

**Debounce.** A lockfile + staleness check ensures the summarizer runs at most
once per idle gap, not literally every turn — roughly one Haiku call per active
session rather than per response.

**Storage.** `~/.standup/briefs/<sessionId>.brief.md` — the **durable root** of
`~/.standup`, deliberately *not* the disposable `cache/` subdirectory (ADR 0003),
because a Brief is not recomputable by standup: only the original session, with
full context, can author it well. Format is markdown with a YAML frontmatter
contract:

```markdown
---
objective: <one declarative line — what the session set out to do>
status: done | in-progress | blocked | abandoned
generated: <ISO 8601 timestamp>
model: <model id used>
---

<freeform bullets: what happened, secondary objectives — shown only in `show`>
```

A session with multiple objectives records the dominant *through-line* in
`objective` (chosen at write time, where full context exists) and drops secondary
threads into the body. The overview renders one line; the body surfaces in
`standup show`.

**Rendering (a claim, not a fact).** The Brief *augments* a Session Rollup: the
derived title stays the aligned hard anchor, and the Brief's `objective` renders
as an additional soft line, **marked as a claim** in the `~`-family used for
`likely` attribution — never impersonating derived truth. If the session log
advanced past the Brief's `generated` time (beyond debounce tolerance), the line
is tagged `(stale)`. A missing Brief is never an error — the Rollup renders
title-only, exactly as today.

**Standup never generates.** Even on detecting staleness, standup only reads and
marks; the Stop hook is the sole writer. This preserves "never LLM-generated at
render time" and keeps output deterministic given the files on disk.

**Cost tracking (Brief Overhead).** Brief generation does real token work but
touches no repo, so its cost is made visible deliberately. `claude -p
--output-format json` returns the run's own `usage` object — verified 2026-07-22
to include the full per-token counts standup already prices (`input_tokens`,
`output_tokens`, `cache_read_input_tokens`, `cache_creation` with the 5m/1h
split, `service_tier`, `speed`). The summarizer runs with
`--no-session-persistence` (no litter in `~/.claude/projects`, no double-count)
and records that `usage` verbatim into the Brief's frontmatter as `gen_usage`.
Standup prices it with the *same* Rate Card it uses for every turn (Haiku 4.5 is
carded, ADR 0005) and attributes it to the **Repo Entry** whose session the Brief
describes — the Brief is keyed by that session's id, so attribution needs no
`cwd` guessing and no fragile gen-session detection. It surfaces as a *separate,
labelled* **Brief Overhead** figure in the `cost` view, and never enters the
Triage Inbox. This reuses the tokens→Rate Card→Notional Cost pipeline end-to-end
with no new trust surface.

**Install.** `standup install` merges the Stop-hook entry into
`~/.claude/settings.json` (user scope, so it fires for every session on the
machine), idempotently (re-running never duplicates) and without clobbering
existing hooks. Claude Code's file watcher picks it up with no restart. It also
runs a one-shot `claude -p` **doctor-check** and warns if headless auth fails.
`standup uninstall` removes the entry — a clean lifecycle owned by the one tool.

**Lifecycle.** Standup prunes orphan Briefs (whose Session log no longer exists)
the same way it prunes the cache (`prune(live_ids)`).

## Considered Options

- **Render-time LLM summarizer in standup** — rejected: slow, costly, on the
  interactive path, and non-deterministic. Breaks ADR 0002 and the Resume rule.
- **Passive global instruction / a voluntary "log-session" tool the model calls**
  — rejected: best-effort, not deterministic. The model drops passive
  instructions in long sessions, and a tool that is never invoked is as empty as
  an instruction that is never followed. Neither *forces* the write.
- **Stop hook that injects an instruction into the live conversation** — rejected:
  however short the instruction, it forces one extra round-trip of the expensive
  interactive model on every fire. The out-of-band variant achieves the same
  content with zero interactive cost.
- **SessionEnd hook** — rejected as the primary trigger: it can be missed on hard
  kills, and the model is not in the loop at end-of-session, so a separate
  summarizer call is needed anyway. Stop fires reliably after each response.
- **A `standup log-session` subcommand as the write path** — rejected: couples
  every Claude Code session everywhere to standup being installed and to its CLI
  contract. A plain sidecar file keeps the producer ignorant of the consumer.
- **Store Briefs in the Derived Cache (`~/.standup/cache`)** — rejected: Briefs
  are not recomputable, so a cache wipe would destroy real data. Hence the
  durable-root/deletable-cache split (ADR 0003).
- **Replace the title with the objective** — rejected: titles must align for
  at-a-glance scanning (Resume), and an unverified claim must not occupy the
  anchor slot.
- **A self-contained generated hook script** (vs a shim into `standup _brief`) —
  rejected: the logic (debounce races, JSONL parsing, prompt) would be an
  untested copy that drifts from the installed package on every upgrade. The
  shim keeps it versioned and testable; the runtime `PATH` dependency it adds is
  satisfied by construction (standup installed the hook).
- **Anthropic API + `ANTHROPIC_API_KEY`, or `claude --bare`** — rejected: most
  Claude Code users are on a subscription with no API key, and `--bare` skips the
  keychain read that makes keyless auth work. Plain `claude -p` was verified to
  authenticate headless with neither, so it stays turnkey.
- **Leaving the gen session persisted and parsing it for cost (Approach Y)** —
  rejected: it litters `~/.claude/projects` with a tiny session per generation,
  its grouping depends on the launch cwd's realpath, and identifying which
  sessions are brief-generations is fragile (a deterministic `--session-id`
  errors with "already in use" on the second run, ruling out stable tagging).
  An earlier draft rejected the frontmatter approach on the belief that
  `--output-format json` exposed only `total_cost_usd`; testing disproved that —
  it returns the full `usage` object — so recording `gen_usage` and pricing it
  with our own Rate Card (the Decision above) is both self-contained and cheaper.

## Consequences

- Standup's **render path** gains only a Brief *reader* and stays deterministic
  and derived-only: it never generates at render time. Generation is a *separate*
  code path — the `standup _brief` subcommand — invoked by the hook, never by
  `standup` the display command.
- The package now owns three responsibilities that ship together: the reader, the
  generator (`standup _brief`), and installation (`standup install` /
  `uninstall`). A session with no Brief renders title-only, so display degrades
  gracefully whenever generation hasn't run.
- Cost is bounded and now *visible*: gated to footprint sessions, debounced to
  ~once per session, on Haiku, entirely off the interactive path — and the
  residual spend surfaces as **Brief Overhead**, so mis-tuned debounce is caught
  by the very tool it inflates.
- New trust surface: the Brief is a fallible self-report. It is always marked as a
  claim and hedged when stale, so it informs triage without ever overriding git.
