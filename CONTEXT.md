# Standup

A personal CLI that joins Claude Code session logs with git repo state to produce a morning triage list: what changed, what still needs a decision, and which session did it.

## Language

**Triage Inbox**:
The default `standup` output — a list of items sorted by "needs my decision", where git state itself is the read-marker and handled items disappear.
_Avoid_: report, digest, dashboard

**Session**:
One Claude Code conversation, identified by its JSONL file (`sessionId`), carrying a native title (`aiTitle`/`customTitle`/`slug`).
_Avoid_: conversation, chat, run

**Scan Universe**:
The set of repos Standup inspects — auto-discovered from the `cwd` fields inside `~/.claude/projects` logs; never configured by hand, never walked from disk roots.
_Avoid_: watched repos, registry

**Unattributed Change**:
A pending or committed change in a scanned repo that no Session explains (made by hand or by another tool). Always shown, labeled as such — the inbox must not hide dirt.

**Needs-Decision Item**:
An inbox entry requiring action — uncommitted dirt or unpushed commits. Ageless: never filtered by any time window. Two tiers, both in the default view:
- **Active Work** — uncommitted changes; the special case the inbox leads with, rendered as Session Rollups.
- **Unpushed** — committed but local-only; compressed to one line per repo in the overview (detail lives in the drill-down).

**Recent Window**:
The rolling time window (default: last 7 days) that gates the recently-pushed retrospective shown by `-a`; `--since` overrides it ad hoc. It is the *only* time knob — `--lookback` is retired (ADR 0004). Needs-Decision Items ignore it — they remain ageless, and their attribution is ageless too: it reaches back over the full cached history regardless of the window. Standup keeps no run-state; the only store is the Derived Cache, a pure accelerator that never changes output. The same command at the same moment always prints the same inbox.
_Avoid_: checkpoint, last run, lookback (retired concepts — see ADR 0002, 0004)

**Derived Cache**:
The `~/.standup/cache` store: a pure accelerator holding results derived deterministically from the session logs (parsed Sessions). Keyed so any stale entry is detected and recomputed; output is byte-identical whether the cache is warm, cold, or deleted. `rm -rf ~/.standup/cache` is always safe. Never holds run-history — it is not the retired Checkpoint (ADR 0002). It caches log parsing only; live git state is never cached (git is the source of truth and de-facto read-marker). It lives in a `cache/` subdirectory precisely so the `~/.standup` root can also hold *durable, non-recomputable* data (**Session Briefs**) without exposing it to a cache wipe — the root is never deleted; only `cache/` is (ADR 0003).
_Avoid_: state, checkpoint, index

**Attribution Tier**:
The strength of a change→Session claim — `exact` (commit hash captured in the Session log), `likely` (file-path overlap with the Session's Edit/Write calls, displayed with `~`), or unattributed. Multiple plausible Sessions are all listed; Standup never fakes a single winner.

**Repo Entry**:
One top-level item in the Triage Inbox, identified by `git rev-parse --git-common-dir` — worktrees roll up under their main checkout as branch sub-lines; independent clones stay separate.

**Session Rollup**:
One line under a Repo Entry in the overview: a Session plus the scale of its footprint. The Session is the display unit; individual files never appear in the overview. Each file belongs to exactly one Session — its **latest** — so a file touched across several Sessions appears once, not once per Session. Older Sessions that also touched it are not lost: they surface as the `also ~"…"` annotation in the drill-down. Per-Session file counts therefore sum to the Repo Entry header's true git total ("N files uncommitted across M sessions").

**Resume**:
The content of a Session Rollup: Session title + scale (file/commit counts) + Touched Areas + recency. Derived offline from the log and git — never LLM-generated at render time (its authored companion is the **Session Brief**, which is LLM-written but still only *read* at render time). Rendered as a two-line stanza — title line first (titles must align for at-a-glance scanning), metadata indented below. No emitted line may exceed the terminal width: content grows vertically, never wraps.

**Touched Areas**:
The top-level directories a Session's footprint lives in (max ~3 shown, then `+N more`). The one-line replacement for the per-file listing of the old overview.

**Session Brief**:
An LLM-authored, best-effort account of a **Session**'s *objective(s)* — what the session set out to do — generated **out-of-band** while the session runs (a Claude Code Stop hook spawns a cheap model pass over the JSONL; the live conversation pays no extra tokens or round-trips), stored durably at `~/.standup/briefs/<sessionId>.brief.md` (the `~/.standup` root, *not* the deletable `cache/` — see **Derived Cache**), and read by Standup as just another log input. Standup prunes orphan Briefs whose Session log no longer exists, the same way it prunes the cache. A *claim*, not a derived fact: it can be stale, wrong, or hallucinated, so Standup always renders it **marked as a claim** (the `~`-style honesty carried over from Attribution) and lets it *augment* — never replace — the derived title in a **Session Rollup** (title stays the aligned anchor; the Brief adds one "objective" line). Standup only ever *reads* it: generation is offline, so the "never LLM-generated at render time" rule (see **Resume**) still holds and the inbox stays deterministic given the files on disk.
_Avoid_: metadata (overloaded — titles/`cwd`/branches are *derived* metadata), summary, description, log

**Notional Cost**:
The API-equivalent dollar *weight* of a Session or project: its logged token usage (input / output / cache-write / cache-read) priced at the published pay-as-you-go **Rate Card**. A comparison unit for load — explicitly not money paid.
_Avoid_: spend, bill, "what it cost" (those imply real money — see Real Spend)

**Real Spend**:
The actual money charged for going over the subscription — account-level, and the *only* real cost. Standup has **no trustworthy local source** for it: the `xu` field in `plan-usage-history.json` does not match the authoritative claude.ai figure (two machines read ~$80 while the account meter showed ~$202), so it is not real spend by any usable definition. Therefore Standup does **not report Real Spend at all** — no number, no pointer, no estimate. It lives only on claude.ai. Named here solely to mark the boundary: what the tool must never pretend to know.
_Avoid_: credits, overage, and above all — do not equate it with `xu` or with **Notional Cost**

**Rate Card**:
The model → price table that converts tokens into **Notional Cost**. Stored as base input + output per model; cache-read (0.1×), 5m-write (1.25×) and 1h-write (2×) are *derived* from base input, and per-turn modifiers (`speed:"fast"`, `service_tier:"batch"` ×0.5, `inference_geo:"us"` ×1.1, web-search +$0.01/req) are read from the turn's own `usage`. A dated, sourced constant (see ADR 0005). Every model in use has a public rate; a future/unknown model is shown with its tokens but **excluded from the dollar total and flagged unpriced** — never silently $0.

**Brief Overhead**:
The **Notional Cost** of producing **Session Brief**s — the token load of the background `claude -p` (Haiku) runs the Stop hook spawns. Each generation reports its own `usage` (via `claude -p --output-format json`), which is recorded in the Brief and priced by the **Rate Card** like any turn, then attributed to the **Repo Entry** whose session the Brief describes (the Brief is keyed by that session's id). Surfaced as a *separate, labelled* figure in the `cost` view — never folded silently into a repo's work cost — so the price of keeping Briefs current is always visible (and debounce tuning is self-evident). It never reaches the **Triage Inbox**: generation runs with `--no-session-persistence` and makes no Edit/Write, so it has no footprint.
_Avoid_: folding it into Notional Cost silently, hiding it

**Loop**:
A run of repeated same-shape tool calls inside one **Session**, found by a free, deterministic detector over the JSONL (always-on, cacheable in the Derived Cache). A measured fact, not a judgment — a Loop may be perfectly legitimate work.
_Avoid_: pattern (vague), waste (judgmental), repetition

**Loop Cost**:
A **Loop**'s share of its Session's **Notional Cost** — the summed per-turn cost of the turns identified as Loop iterations. Strictly retrospective and measured; never a projected saving. (Mirrors the Brief Overhead move: a subset of Notional Cost carved out and labelled.)
_Avoid_: savings, waste

**Handoff Prompt**:
The paste-ready prompt a finding ends with — "build a script that does X; evidence in session `<handle>` turns N–M" — for starting a fresh Claude Code session that writes the automation script. Standup itself never writes code: it derives, it claims, it hands off.
_Avoid_: generated script, fix

**Audit**:
The on-demand, LLM-authored judgment of one **Session** (`standup audit <handle>`): which turns are **LLM-as-CPU** work, whether the pattern recurs in sibling Sessions, and a **Handoff Prompt** for scripting it away. Produced by a fixed **Expert Panel** whose claims a concluder (Opus) weighs into solutions — the panel's *shape* lives in Standup's code, never in a model's discretion. Sibling recruitment is split honestly: Standup *gathers* deterministically (same **Repo Entry**), the Recurrence Expert *matches* semantically — from each sibling's derived title plus its **Session Brief** when one exists (Briefs are optional; titles always exist). Only the target Session gets the deep read; siblings contribute title, Brief, and **Loop** fingerprints/costs only, so recurrence is reported as measured fact ("recurred in 4 sessions, combined Loop Cost $23") without multiplying audit cost. A *claim*, `~`-marked like the Brief, stored durably (never in `cache/`).
_Avoid_: analysis (vague), report

**Expert Panel**:
The fixed set of narrow, parallel Sonnet passes that produce an **Audit**'s raw claims — each Expert answers one question over only the evidence it needs (Loop Expert, LLM-as-CPU Expert, Prompt-Structure Expert, Recurrence Expert). Fix-proposing belongs to the Opus concluder, not an Expert: Experts claim, the concluder judges and drafts the **Handoff Prompt**. Standup's code owns the fan-out and records each Expert's own `usage`, so **Audit Overhead** is itemised per Expert.
_Avoid_: orchestrator-driven delegation, subagents (implies the model chooses the panel)

**LLM-as-CPU**:
A turn where the model performs mechanical data transformation in its head (parsing, reformatting, arithmetic) that a script would do for ~$0. Only detectable by judging *content* — hence only ever claimed by an **Audit**, never by the deterministic **Loop** detector.

**Audit Overhead**:
The **Notional Cost** of producing Audits — each **Expert Panel** member's and the concluder's own `usage`, recorded and itemised per pass, surfaced as a separate, labelled figure in the `cost` view, exactly mirroring **Brief Overhead**. Every `standup audit` run prints the overhead it just incurred.

**Session Handle**:
The 8-character `sessionId` prefix used to address a **Session** on the CLI (the git-short-hash idiom). Intrinsic to the Session, so it is stable across runs — never a positional index. An ambiguous prefix errors, like `git`. Rendered leading and dimmed on every **Session Rollup** title line — in the **Triage Inbox** overview and the drill-down alike — so a session is addressable (`standup show <handle>`) straight from the inbox; fixed 8-char width keeps titles aligned. Omitted from the compressed unpushed/pushed lines, which name a *dominant* session plus `+N` and so address no single Session.

**Transcript**:
The `standup show <handle>` rendering of a **Session**'s conversation — user prompts and assistant responses in reading order. It **leads with the Session Brief** (when one exists): the objective as a headline, the freeform body beneath, and the `status` — so the reader gets an instant understanding of the session *before* the conversation, and reads on only for more detail. The brief block is marked as a **claim** (the `~` idiom carried from Attribution) and hedged when stale — staleness computed in `show` from the session's own last-activity timestamp in the JSONL, against the same tolerance the inbox uses. A briefless (or body-less) session degrades silently — no placeholder, straight to the conversation — exactly as before Briefs existed. The brief carries no cost tag: its **Brief Overhead** stays a `cost`-view concern, never scattered here. Below the brief: tool calls collapse to one-liners; thinking is hidden (`--thinking` reveals); injected noise (system-reminders, hook output) is stripped so "you" is what you typed; each assistant turn is annotated with its per-turn **Notional Cost**; `--raw` dumps untouched JSONL (brief excluded — it lives in a separate file, not the JSONL). Free-flowing prose, and the one output **exempt** from the inbox's never-wrap rule. A general session-inspection view, reachable from the `cost` drill-down (its first entry point) and, later, the Triage Inbox — not a `cost`-only feature.

**Watch**:
The `standup watch <repo>` live view — an interleaved, chronological narrative of one **Repo Entry**'s activity (worktrees included), derived by tailing **Session** logs (the claim stream) and observing git (ground truth). Read-only and stateless like every view; exempt from the snapshot idioms (it renders time passing) but never from the honesty ones — claims stay claims, and **Unattributed Change**s are shown live, not hidden.
_Avoid_: monitor, dashboard, tail

**Feed Event**:
One entry in the **Watch**: an animated file change (session-claimed, or `~`-marked unattributed with its git-diff content when git is the only witness), a Bash one-liner (command + exit status), a user-prompt chapter break, a commit/push/branch switch, a live **Unattributed Change**, or a **Live Session** appearing or going idle. A commit event carries the commit's own per-file diff, in the same added/removed shape a file change has — committing must not make a change unreadable. Every event is tagged with its **Session Handle**. Assistant prose and thinking never appear — reading the conversation is the **Transcript**'s job.

**Live Session**:
A **Session** whose log was appended within a recency threshold (~30 minutes). A *recency claim*, not a process fact — Standup never inspects processes, and the **Watch** always displays how long ago the last append happened rather than asserting "running". Distinct from **Active Work**, which is a git dirt tier.
_Avoid_: active session (collides with Active Work), running session

## Relationships

- A **Session** belongs to exactly one working directory (`cwd`), which may be a repo checkout or a worktree
- The `cost` drill-down prints each Session's **Session Handle**; `standup show <handle>` renders its **Transcript**
- A **Session**'s **Notional Cost** is the sum of its turns' token usage priced by the **Rate Card**; a project's Notional Cost is the sum of its Sessions'
- The `cost` view spans **all** Sessions (any git footprint or none) and groups them by **Repo Entry** — worktrees fold into their parent checkout — falling back to the raw `cwd` for Sessions whose directory is not a git repo
- The `cost` view defaults to the **current calendar month** — chosen to sit alongside the per-cycle **Real Spend** — and stays stateless: recomputed from logs each run, never cached (ADR 0002)
- **Notional Cost** is the *only* cost figure Standup reports; **Real Spend** is deliberately excluded — there is no local source of truth for it
- **Notional Cost** is retrospective analytics, not a **Needs-Decision Item**: it lives in the `cost` view and never in the **Triage Inbox** (an optional notional-load summary line may appear only in the `-a` retrospective)
- **Brief Overhead** is a subset of **Notional Cost** carved out and labelled: the cost of the Stop hook's brief-generation Sessions, attributed to the **Repo Entry** whose Sessions they summarise, shown separately so the tax of keeping **Session Brief**s current is never hidden
- A **Repo Entry** aggregates one main checkout plus its worktrees; each pending/committed change carries one **Attribution Tier**
- The **Triage Inbox** is fully derived — computed fresh from git + JSONL; there is no stored state (ADR 0002)
- **Loop** detection is always-on, free, and deterministic (Derived Cache); the **Audit** is on-demand and paid — Loops are the free triage layer that tells you which Sessions are worth auditing
- Loops surface as a marker on the `cost` view's Session lines and as gutter marks on the **Transcript**'s looped turns; neither Loops nor Audits ever enter the **Triage Inbox** (retrospective analytics, like Notional Cost)
- An **Audit** consumes the target Session's transcript, its **Loops**, and its siblings' titles/**Session Brief**s/Loops; it produces `~`-marked claims, a solution, and a **Handoff Prompt** — never a script (Standup reads, it doesn't code)
- An Audit is stored at `~/.standup/audits/`, staled by the Session continuing (same tolerance as Briefs), never by sibling drift; `--refresh` regenerates, nothing auto-regenerates
- The **Watch** consumes the same two sources as the **Triage Inbox** (Session logs + git) with the same split: the log claims *who and what*, git confirms *ground truth* (and alone reveals live **Unattributed Change**s). It interleaves all **Live Session**s of one **Repo Entry** into a single feed, filterable down to one Session interactively
- A repo with no **Live Session**s (any git checkout, even outside the **Scan Universe**) still narrates: git alone is the witness, and the Watch content-diffs its dirty files so every change appears with its added/removed text — `~`-marked unattributed, since no Session claims it
- The **Watch**'s typing animation is presentation only, under a hard staleness bound: the display may never lag the log by more than a few seconds — the animation compresses (down to instant) to honor it. Delight never outranks truth
- On launch the **Watch** backfills, unanimated and dimmed, from *each* **Live Session**'s current user prompt (prompts are the narrative's chapter breaks) — every session's chapter, interleaved chronologically, so a Session that finished its work and went quiet is still legible when you filter to it. It sits beneath a vitals header (repo, branch, Live Sessions with recency, dirt count)
- A committed change stays readable in the **Watch**: the commit event carries its own diff, so the two ways a change can be witnessed — a **Session**'s claimed edit while dirty, and git's commit after the tree goes clean — both render as added/removed text rather than one of them degrading to a subject line

- The drill-down (`standup <repo>`) is the Triage Inbox at higher magnification — the same session-major model, with each Session Rollup expanded into its file/commit evidence. A file appears under its latest Session only; the older Sessions that also touched it are named inline as `also ~"…"`.

## Example dialogue

> **Dev:** "Do we mark an item as reviewed once Arjun has seen it?"
> **Domain expert:** "No — the **Triage Inbox** has no read-state of its own. Committing, discarding, or pushing is what removes an item; git is the source of truth."

## Flagged ambiguities

- "report" vs "inbox" — resolved: Standup is a **Triage Inbox**, not a passive report. Output ordering is by required action (uncommitted → unpushed → done), not chronology.
- file-major vs session-major — resolved (2026-07-22): the Session is the display unit at every altitude; files are evidence shown only in the drill-down. Within a section, repos order by most recent activity, not alphabetically.
- "cost" / "money" / "spend" — resolved (2026-07-22): per-Session and per-project figures are **Notional Cost** (API-equivalent load, not money). Real money is **Real Spend** — account-level and unattributable. The tool must never present Notional Cost as spend.
- Can `xu` (`plan-usage-history.json`) stand in for **Real Spend**? — resolved (2026-07-22): no. It refreshes fine (~5 min while the app runs) but does not match the authoritative claude.ai meter (~$80 local vs ~$202 account-wide). Standup reports no Real Spend rather than a wrong one.
- Does an LLM-authored **Session Brief** break the "derived, deterministic, never-LLM" model? — resolved (2026-07-22): no (ADR 0006). Generation happens *offline*, during the coding session (a Stop hook, out-of-band), exactly as commits and edits happen offline; Standup only ever *reads* the Brief at render time, so output stays deterministic given the files on disk. The Brief is a fallible *claim*, never a derived fact — always marked as such, hedged when stale, and it augments but never replaces the derived title.
