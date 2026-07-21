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
An inbox entry requiring action — uncommitted dirt or unpushed commits. Ageless: never filtered by any time window.

**Checkpoint**:
The timestamp of the previous standup run (a single state file). The "done" section shows work completed since the Checkpoint; `--since` overrides it ad hoc.
_Avoid_: last run, window start

**Attribution Tier**:
The strength of a change→Session claim — `exact` (commit hash captured in the Session log), `likely` (file-path overlap with the Session's Edit/Write calls, displayed with `~`), or unattributed. Multiple plausible Sessions are all listed; Standup never fakes a single winner.

**Repo Entry**:
One top-level item in the Triage Inbox, identified by `git rev-parse --git-common-dir` — worktrees roll up under their main checkout as branch sub-lines; independent clones stay separate.

## Relationships

- A **Session** belongs to exactly one working directory (`cwd`), which may be a repo checkout or a worktree
- A **Repo Entry** aggregates one main checkout plus its worktrees; each pending/committed change carries one **Attribution Tier**
- The **Triage Inbox** is derived state — computed fresh from git + JSONL; the only stored state is the **Checkpoint**

## Example dialogue

> **Dev:** "Do we mark an item as reviewed once Arjun has seen it?"
> **Domain expert:** "No — the **Triage Inbox** has no read-state of its own. Committing, discarding, or pushing is what removes an item; git is the source of truth."

## Flagged ambiguities

- "report" vs "inbox" — resolved: Standup is a **Triage Inbox**, not a passive report. Output ordering is by required action (uncommitted → unpushed → done), not chronology.
