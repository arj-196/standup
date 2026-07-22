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
The rolling time window (default: last 7 days) that gates the recently-pushed retrospective shown by `-a`; `--since` overrides it ad hoc. Needs-Decision Items ignore it — they remain ageless. Standup stores no state: the same command at the same moment always prints the same inbox.
_Avoid_: checkpoint, last run (retired concepts — see ADR 0002)

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

## Relationships

- A **Session** belongs to exactly one working directory (`cwd`), which may be a repo checkout or a worktree
- A **Repo Entry** aggregates one main checkout plus its worktrees; each pending/committed change carries one **Attribution Tier**
- The **Triage Inbox** is fully derived — computed fresh from git + JSONL; there is no stored state (ADR 0002)

- The drill-down (`standup <repo>`) is the Triage Inbox at higher magnification — the same session-major model, with each Session Rollup expanded into its file/commit evidence. A multi-attributed file is listed under every plausible Session, marked `also ~"…"`.

## Example dialogue

> **Dev:** "Do we mark an item as reviewed once Arjun has seen it?"
> **Domain expert:** "No — the **Triage Inbox** has no read-state of its own. Committing, discarding, or pushing is what removes an item; git is the source of truth."

## Flagged ambiguities

- "report" vs "inbox" — resolved: Standup is a **Triage Inbox**, not a passive report. Output ordering is by required action (uncommitted → unpushed → done), not chronology.
- file-major vs session-major — resolved (2026-07-22): the Session is the display unit at every altitude; files are evidence shown only in the drill-down. Within a section, repos order by most recent activity, not alphabetically.
