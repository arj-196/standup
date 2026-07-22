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
The `~/.standup` store: a pure accelerator holding results derived deterministically from the session logs (parsed Sessions). Keyed so any stale entry is detected and recomputed; output is byte-identical whether the cache is warm, cold, or deleted. Never holds run-history — it is not the retired Checkpoint (ADR 0002). It caches log parsing only; live git state is never cached (git is the source of truth and de-facto read-marker).
_Avoid_: state, checkpoint, index

**Attribution Tier**:
The strength of a change→Session claim — `exact` (commit hash captured in the Session log), `likely` (file-path overlap with the Session's Edit/Write calls, displayed with `~`), or unattributed. Multiple plausible Sessions are all listed; Standup never fakes a single winner.

**Repo Entry**:
One top-level item in the Triage Inbox, identified by `git rev-parse --git-common-dir` — worktrees roll up under their main checkout as branch sub-lines; independent clones stay separate.

**Session Rollup**:
One line under a Repo Entry in the overview: a Session plus the scale of its footprint. The Session is the display unit; individual files never appear in the overview. Footprints may overlap — a multi-attributed change counts under every plausible Session (no fake winner) — so the Repo Entry header carries the true git totals ("N files uncommitted across M sessions") and per-Session counts are honest even when they don't sum to it.

**Resume**:
The content of a Session Rollup: Session title + scale (file/commit counts) + Touched Areas + recency. Derived offline from the log and git — never LLM-generated at render time. Rendered as a two-line stanza — title line first (titles must align for at-a-glance scanning), metadata indented below. No emitted line may exceed the terminal width: content grows vertically, never wraps.

**Touched Areas**:
The top-level directories a Session's footprint lives in (max ~3 shown, then `+N more`). The one-line replacement for the per-file listing of the old overview.

**Notional Cost**:
The API-equivalent dollar *weight* of a Session or project: its logged token usage (input / output / cache-write / cache-read) priced at the published pay-as-you-go **Rate Card**. A comparison unit for load — explicitly not money paid.
_Avoid_: spend, bill, "what it cost" (those imply real money — see Real Spend)

**Real Spend**:
The actual money charged for going over the subscription — account-level, and the *only* real cost. Standup has **no trustworthy local source** for it: the `xu` field in `plan-usage-history.json` does not match the authoritative claude.ai figure (two machines read ~$80 while the account meter showed ~$202), so it is not real spend by any usable definition. Therefore Standup does **not report Real Spend at all** — no number, no pointer, no estimate. It lives only on claude.ai. Named here solely to mark the boundary: what the tool must never pretend to know.
_Avoid_: credits, overage, and above all — do not equate it with `xu` or with **Notional Cost**

**Rate Card**:
The model → price table that converts tokens into **Notional Cost**. Stored as base input + output per model; cache-read (0.1×), 5m-write (1.25×) and 1h-write (2×) are *derived* from base input, and per-turn modifiers (`speed:"fast"`, `service_tier:"batch"` ×0.5, `inference_geo:"us"` ×1.1, web-search +$0.01/req) are read from the turn's own `usage`. A dated, sourced constant (see ADR 0005). Every model in use has a public rate; a future/unknown model is shown with its tokens but **excluded from the dollar total and flagged unpriced** — never silently $0.

**Session Handle**:
The 8-character `sessionId` prefix used to address a **Session** on the CLI (the git-short-hash idiom). Intrinsic to the Session, so it is stable across runs — never a positional index. An ambiguous prefix errors, like `git`.

**Transcript**:
The `standup show <handle>` rendering of a **Session**'s conversation — user prompts and assistant responses in reading order. Tool calls collapse to one-liners; thinking is hidden (`--thinking` reveals); injected noise (system-reminders, hook output) is stripped so "you" is what you typed; each assistant turn is annotated with its per-turn **Notional Cost**; `--raw` dumps untouched JSONL. Free-flowing prose, and the one output **exempt** from the inbox's never-wrap rule. A general session-inspection view, reachable from the `cost` drill-down (its first entry point) and, later, the Triage Inbox — not a `cost`-only feature.

## Relationships

- A **Session** belongs to exactly one working directory (`cwd`), which may be a repo checkout or a worktree
- The `cost` drill-down prints each Session's **Session Handle**; `standup show <handle>` renders its **Transcript**
- A **Session**'s **Notional Cost** is the sum of its turns' token usage priced by the **Rate Card**; a project's Notional Cost is the sum of its Sessions'
- The `cost` view spans **all** Sessions (any git footprint or none) and groups them by **Repo Entry** — worktrees fold into their parent checkout — falling back to the raw `cwd` for Sessions whose directory is not a git repo
- The `cost` view defaults to the **current calendar month** — chosen to sit alongside the per-cycle **Real Spend** — and stays stateless: recomputed from logs each run, never cached (ADR 0002)
- **Notional Cost** is the *only* cost figure Standup reports; **Real Spend** is deliberately excluded — there is no local source of truth for it
- **Notional Cost** is retrospective analytics, not a **Needs-Decision Item**: it lives in the `cost` view and never in the **Triage Inbox** (an optional notional-load summary line may appear only in the `-a` retrospective)
- A **Repo Entry** aggregates one main checkout plus its worktrees; each pending/committed change carries one **Attribution Tier**
- The **Triage Inbox** is fully derived — computed fresh from git + JSONL; there is no stored state (ADR 0002)

- The drill-down (`standup <repo>`) is the Triage Inbox at higher magnification — the same session-major model, with each Session Rollup expanded into its file/commit evidence. A multi-attributed file is listed under every plausible Session, marked `also ~"…"`.

## Example dialogue

> **Dev:** "Do we mark an item as reviewed once Arjun has seen it?"
> **Domain expert:** "No — the **Triage Inbox** has no read-state of its own. Committing, discarding, or pushing is what removes an item; git is the source of truth."

## Flagged ambiguities

- "report" vs "inbox" — resolved: Standup is a **Triage Inbox**, not a passive report. Output ordering is by required action (uncommitted → unpushed → done), not chronology.
- file-major vs session-major — resolved (2026-07-22): the Session is the display unit at every altitude; files are evidence shown only in the drill-down. Within a section, repos order by most recent activity, not alphabetically.
- "cost" / "money" / "spend" — resolved (2026-07-22): per-Session and per-project figures are **Notional Cost** (API-equivalent load, not money). Real money is **Real Spend** — account-level and unattributable. The tool must never present Notional Cost as spend.
- Can `xu` (`plan-usage-history.json`) stand in for **Real Spend**? — resolved (2026-07-22): no. It refreshes fine (~5 min while the app runs) but does not match the authoritative claude.ai meter (~$80 local vs ~$202 account-wide). Standup reports no Real Spend rather than a wrong one.
