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
- **Unpushed** — committed but local-only; compressed to one line per repo in the overview (detail lives in the drill-down). A **Remoteless Repo** has no Unpushed tier at all: committing is already its terminal state, so its commits are **Done**, not Needs-Decision (ADR 0006). Its Needs-Decision surface is therefore Active Work alone.

**Remoteless Repo**:
A **Repo Entry** with no remote configured — `git remote` is empty. Repo-level, never branch-level: a branch with no upstream in a repo that *does* have a remote is genuinely pending a push and stays **Unpushed**. Worktrees share `git-common-dir`, so the classification can never split across a Repo Entry. Its consequence is a redefinition of **Done**: terminal is repo-relative, so committing ends the work here exactly as pushing does elsewhere (ADR 0006). Rendered as the verifiable git fact `no remote` — never as an inference about intent ("local-only", "private", "unbacked"), and never as a warning; declining to add a remote is a decision, not dirt. Marked on the **Done** line and unconditionally in the drill-down header; the **Triage Inbox** stays silent about it.
_Avoid_: local-only (already means *unpushed commits in a repo that has a remote* — see Needs-Decision Item), offline, unbacked

**Done**:
Work that reached its terminal state within the **Recent Window** — the `-a` retrospective, never a decision queue. Terminal is *repo-relative*: pushed (reachable from a remote ref) for a normal repo, merely committed for a **Remoteless Repo**, where `--branches` is the local mirror of `--remotes` — "everywhere this repo's work has landed" (ADR 0006). The tier name is neutral so it can hold both; each line keeps the precise verb (`N commits pushed` / `N commits committed · no remote`) rather than flattening them into one claim. Filtered to the repo's own `user.email` in both cases.
_Avoid_: pushed (true of only one of the two cases — the tier was renamed away from it)

**Recent Window**:
The rolling time window (default: last 7 days) that gates the **Done** retrospective shown by `-a`; `--since` overrides it ad hoc. It is the *only* time knob — `--lookback` is retired (ADR 0001 § ageless attribution). Needs-Decision Items ignore it — they remain ageless, and their attribution is ageless too: it reaches back over the full cached history regardless of the window. Standup keeps no run-state; the only store is the Derived Cache, a pure accelerator that never changes output. The same command at the same moment always prints the same inbox.
_Avoid_: checkpoint, last run, lookback (retired concepts — see ADR 0001 § the Recent Window, ADR 0001 § ageless attribution)

**Derived Cache**:
The `~/.standup/cache` store: a pure accelerator holding results derived deterministically from the session logs — parsed Sessions, the typed full reading of a log (ADR 0001 § the one log reader), and detected **Loops**. What hunk attribution matches against is a projection of the typed reading, so that row serves it rather than a second index of the same text (ADR 0007 § Decision). Keyed so any stale entry is detected and recomputed; output is byte-identical whether the cache is warm, cold, or deleted. `rm -rf ~/.standup/cache` is always safe. Never holds run-history — it is not the retired Checkpoint (ADR 0001 § the Recent Window). It caches log parsing only; live git state is never cached (git is the source of truth and de-facto read-marker). It lives in a `cache/` subdirectory precisely so the `~/.standup` root can also hold *durable, non-recomputable* data (**Session Brief**s and **Audit**s) without exposing it to a cache wipe — the root is never deleted; only `cache/` is (ADR 0001 § the Derived Cache).
_Avoid_: state, checkpoint, index

**Attribution Tier**:
The strength of a change→Session claim, and the *granularity* it was decided at. Multiple plausible Sessions are all listed; Standup never fakes a single winner.

| tier | test | granularity |
| --- | --- | --- |
| `exact` | commit hash captured in the Session log — the one attribution that is a *fact* | commit |
| `likely` | file-path overlap with the Session's Edit/Write calls, or (in an **Attributed Diff**) a hunk whose every run matches one Session's recorded edit text verbatim. Displayed with `~` | file · hunk |
| `shared` | a hunk whose runs match two or more Sessions: it genuinely holds more than one Session's work, so it is **not split** | hunk |
| `unaccounted` | a hunk no Session's recorded edits account for, **in a file a Session did touch** | hunk |
| unattributed | no Session ever touched this path — see **Unattributed Change** | file |

`unaccounted` and unattributed are **not the same claim** and are never rendered as one. Unattributed rests on the *path-overlap* test and is reliable. `unaccounted` has two causes the matcher cannot distinguish — a hand edit, or a false negative where the agent wrote the line and a later edit moved the text past verbatim recognition — so it says exactly that, and asserts no hand edit. Hunk-level tiers are decided by verbatim matching only, never by similarity scoring: a confidently wrong attribution is worse than an admitted gap (ADR 0007). When the change is a **commit**, only edits recorded at or before its own timestamp count as evidence — a Session that writes the same lines a day later cannot have authored it.
_Avoid_: calling an `unaccounted` hunk unattributed (it is a gap in the matcher's reach, not a verdict about who typed it)

**Repo Entry**:
One top-level item in the Triage Inbox, identified by `git rev-parse --git-common-dir` — worktrees roll up under their main checkout as branch sub-lines; independent clones stay separate.

**Checkout**:
One working tree of a **Repo Entry** — the main checkout, or a linked worktree of it — carrying its own branch, its own pending files and its own **Unpushed** commits. The unit every git question is asked about ("current branch", "diff of this path", "unpushed count"), and every one of them is asked through `gitstate`, which is the only module that runs git. Repo-level facts are *not* per-Checkout: whether a remote exists is a property of the repository (see **Remoteless Repo**), so two Checkouts of one Repo Entry can never disagree about it.

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
The API-equivalent dollar *weight* of a Session or project: its logged token usage (input / output / cache-write / cache-read) priced at the published pay-as-you-go **Rate Card**. A **Session**'s usage includes its subagent transcripts (`<project>/<sessionId>/subagents/agent-*.jsonl`) — their per-turn `usage` is never echoed into the parent log, so the parent alone under-counts subagent-heavy work. Folded into the Session's own figure, because a subagent's tokens are the Session's work, delegated — never a separate row (a subagent is not addressable by `standup session`, so a row for it could not be drilled into) and never an overhead (that idiom marks Standup's *own* spend — contrast **Brief Overhead**). The fold is marked, not silent: `incl N subagents` on the drill-down's token line (ADR 0002 § subagent usage). A comparison unit for load — explicitly not money paid.
_Avoid_: spend, bill, "what it cost" (those imply real money — see Real Spend)

**Real Spend**:
The actual money charged for going over the subscription — account-level, and the *only* real cost. Standup has **no trustworthy local source** for it: the `xu` field in `plan-usage-history.json` does not match the authoritative claude.ai figure (two machines read ~$80 while the account meter showed ~$202), so it is not real spend by any usable definition. Therefore Standup does **not report Real Spend at all** — no number, no pointer, no estimate. It lives only on claude.ai. Named here solely to mark the boundary: what the tool must never pretend to know.
_Avoid_: credits, overage, and above all — do not equate it with `xu` or with **Notional Cost**

**Rate Card**:
The model → price table that converts tokens into **Notional Cost**. Stored as base input + output per model; cache-read (0.1×), 5m-write (1.25×) and 1h-write (2×) are *derived* from base input, and per-turn modifiers (`speed:"fast"`, `service_tier:"batch"` ×0.5, `inference_geo:"us"` ×1.1, web-search +$0.01/req) are read from the turn's own `usage`. A dated, sourced constant (see ADR 0002). Every model in use has a public rate; a future/unknown model is shown with its tokens but **excluded from the dollar total and flagged unpriced** — never silently $0.

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
The fixed set of narrow, parallel Sonnet **LLM Pass**es that produce an **Audit**'s raw claims — each Expert answers one question over only the evidence it needs (Loop Expert, LLM-as-CPU Expert, Prompt-Structure Expert, Recurrence Expert). Fix-proposing belongs to the Opus concluder, not an Expert: Experts claim, the concluder judges and drafts the **Handoff Prompt**. Standup's code owns the fan-out and records each Expert's own `usage`, so **Audit Overhead** is itemised per Expert.
_Avoid_: orchestrator-driven delegation, subagents (implies the model chooses the panel)

**LLM Pass**:
One paid call to a model — prompt and model in, text plus priceable `usage` out — and the only way Standup reaches a model at all. A **Session Brief** is one pass, an **Audit** is five (the **Expert Panel** plus its concluder), `standup install`'s doctor-check is one more; every pass is **out-of-band**, so the render path stays deterministic (see **Resume**). The unit **Brief Overhead** and **Audit Overhead** are itemised in: a pass carries its own `usage`, which the **Rate Card** prices like any turn. Two rules hold for every pass wherever it was made (ADR 0003 § the LLM-pass seam): an *empty result is a failure* — a pass that answered nothing produced no claim, and nothing partial is ever stored — and a pass that *outruns its timeout* is a failure named by its label. Which transport carried it is not a property of the pass and is never a caller's choice.
_Avoid_: request, query, prompt (the prompt is one half of a pass's input), call (overloaded — a **Call** is a tool call in the Watch)

**LLM-as-CPU**:
A turn where the model performs mechanical data transformation in its head (parsing, reformatting, arithmetic) that a script would do for ~$0. Only detectable by judging *content* — hence only ever claimed by an **Audit**, never by the deterministic **Loop** detector.

**Audit Overhead**:
The **Notional Cost** of producing Audits — each **Expert Panel** member's and the concluder's own `usage`, recorded and itemised per pass, surfaced as a separate, labelled figure in the `cost` view, exactly mirroring **Brief Overhead**. Every `standup audit` run prints the overhead it just incurred.

**Session Handle**:
The 8-character `sessionId` prefix used to address a **Session** on the CLI (the git-short-hash idiom). Intrinsic to the Session, so it is stable across runs — never a positional index; contrast the derived **Project Handle**. Eight characters is the *display* width: any unambiguous prefix is accepted, and an ambiguous one errors, like `git`. Rendered leading and in cyan on every **Session Rollup** title line — in the **Triage Inbox** overview and the drill-down alike — so a session is addressable (`standup session <handle>`) straight from the inbox; fixed 8-char width keeps titles aligned. Omitted from the compressed unpushed/done lines, which name a *dominant* session plus `+N` and so address no single Session. Because the idiom is borrowed, the two short hexes must not be confusable, and the pair is coloured by **rank**: a Session Handle is an *address* and carries the colour; a **git commit hash** is only a *reference*, written `@<short>` in grey so it recedes behind the handle beside it. The `@` sigil carries the distinction on its own where colour cannot (piped output, `NO_COLOR`). A commit hash addresses nothing *globally* in Standup, and `session` says so when handed one — it is addressable only inside a **named Repo Entry**, only in the **Attributed Diff**, and only wearing its sigil: `standup <repo> diff @abc1234` (ADR 0005 § two grammars). That narrowing preserves the rank rather than repealing it — a Session Handle is spoken bare, a commit has to be announced — and it is what keeps the two hexes unconfusable on the command line: a bare hex is a Session Handle everywhere, the sigil decides, never a lookup.

**Project Handle**:
The short address of a project on the CLI — the acronym of a multi-word name (`pm` for ProjectManagement, `cd` for ClientDeployment), the shortest unique prefix otherwise (`st` for standup). Accepted anywhere a `<repo>` argument is (the **Triage Inbox** drill-down, `cost`, `watch`), resolved by one shared tiered matcher: exact name, then prefix, then acronym, then substring of name, then substring of path. The first tier with any match decides, and more than one match inside it **errors and lists the candidates** — the same `git` idiom as the **Session Handle**, replacing a silent first-match pick. A project whose directory no longer exists never blocks a live one, but stays reachable by full name. Rendered by **underlining the handle's letters inside the name** wherever a project is named in an overview — zero extra width, which the never-wrap rule makes scarce. Crucially *unlike* the Session Handle, a Project Handle is **derived, not intrinsic**: it is computed from the names currently in the **Scan Universe**, so it can grow a letter when a colliding project appears, and the display always shows what currently resolves. Nothing is registered, configured, or stored — there is no alias file (ADR 0005 § Project Handles). A bare word is always a Project Handle, never a directory; a filesystem path (`.`, `../x`, `~/y`) is recognised by its shape and resolved through git instead. Subcommand names and their aliases (`c`, `w`, `s`, `a`, `d`) are reserved: a project is never *displayed* with a handle the dispatcher would eat.
_Avoid_: alias, registry, shortcut (an alias is assigned and stored; a handle is derived — see **Scan Universe**)

**Transcript**:
The `standup session <handle>` rendering of a **Session**'s conversation — user prompts and assistant responses in reading order. It **leads with the Session Brief** (when one exists): the objective as a headline, the freeform body beneath, and the `status` — so the reader gets an instant understanding of the session *before* the conversation, and reads on only for more detail. The brief block is marked as a **claim** (the `~` idiom carried from Attribution) and hedged when stale — by the same comparison against the same clock the inbox uses (the session log's mtime), because trustworthiness is a property of the Brief and no two surfaces may disagree about it (ADR 0003 § the shared model). A briefless (or body-less) session degrades silently — no placeholder, straight to the conversation — exactly as before Briefs existed. The brief carries no cost tag: its **Brief Overhead** stays a `cost`-view concern, never scattered here. Below the brief: tool calls collapse to one-liners — through the renderer the **Watch**'s **Call**s use, so the two surfaces cannot drift; `--tools` prints each call's whole input beneath its one-liner, the static counterpart to expanding a **Call** in the Watch and bound by the same refusal to show a result; thinking is hidden (`--thinking` reveals); injected noise (system-reminders, hook output) is stripped so "you" is what you typed; each assistant turn is annotated with its per-turn **Notional Cost**; `--raw` dumps untouched JSONL (brief excluded — it lives in a separate file, not the JSONL). Free-flowing prose, and the one output **exempt** from the inbox's never-wrap rule. A general session-inspection view, reachable from the `cost` drill-down (its first entry point) and, later, the Triage Inbox — not a `cost`-only feature. With **no handle** it renders the most recently appended Session of the **Repo Entry** the cwd belongs to — or of the Repo Entry named by `--in`, equivalently `standup <repo> session` (ADR 0005 § two grammars) — naming its choice in a dim header line; standing outside the **Scan Universe** is an error, never a fallback to the newest session elsewhere. The **Session Handle** remains the canonical address — the bare form is an omitted-argument default, not a second addressing scheme, and a **Repo Entry** named *alongside* a handle (`standup <repo> session <handle>`) is a scope *check* on it, never a second way to address it: naming a repo means the answer must come from that repo, exactly as it does for a commit hash (ADR 0005 § two grammars).

**Attributed Diff**:
The `standup <repo> diff` rendering of a **Repo Entry**'s **Active Work** as reviewable unified diffs, with each hunk carrying the **Session** that authored it — the last magnification of the session-major model (`standup` → `standup <repo>` → `standup <repo> diff`, ADR 0005 § two grammars). Grouping is the drill-down's, unchanged: a file sits under its *latest* Session, unattributed files trail last, and the **Session Brief** objective heads each group. What is new is *inside* the file — the hunks carry their own **Attribution Tier**, so a body of code is never silently credited to the later of two Sessions, and a hunk that disagrees with the header it sits under is marked (`shared`, `unaccounted`, or another Session's handle). Silence means "as the header says": the marks are printed only where they carry news. A **snapshot**, so unlike the **Watch** it is not exempt from the snapshot idioms — paged static text, never a live view. It takes the Watch's *typography* (the ±gutter, per-token syntax, the removed-row wash of ADR 0004 § the removed-row field, the fold of ADR 0004 § fold, don't clip) and git's *structure* (located hunks with context) — the Watch's added-block/removed-block shape narrates one landing edit but tells a reviewer neither where a change sits nor what replaced what. Scope is uncommitted change, read as `git diff HEAD` so staged and unstaged both appear and staging cannot blank the view; committed change is reached by naming a commit (`@<hash>`, see **Session Handle**). `--stat` drops the bodies for per-file counts and each file's tier digest.
_Avoid_: "git diff with colors" (the attribution is the view, not the formatting), session diff (the view is repo-scoped; a Session Handle *filters* it), review (implies a verdict Standup never gives)

**Watch**:
The `standup watch <repo>` live view — an interleaved, chronological narrative of one **Repo Entry**'s activity (worktrees included), derived by tailing **Session** logs (the claim stream) and observing git (ground truth). Worktree coverage is live, not launch-time: a worktree created mid-watch (Claude Code's `.claude/worktrees/…` agents) joins the watched set on the discovery cadence, and an agent's subagent transcript is tailed as its own lane — a Watch-only discovery that never widens the **Scan Universe** (ADR 0004 § the worktree lane). Read-only and stateless like every view; exempt from the snapshot idioms (it renders time passing) but never from the honesty ones — claims stay claims, and **Unattributed Change**s are shown live, not hidden.
_Avoid_: monitor, dashboard, tail

**Feed Event**:
One entry in the **Watch**: an animated file change (session-claimed, or `~`-marked unattributed with its git-diff content when git is the only witness), a **Call**, a user-prompt chapter break, a commit/push/branch switch, a live **Unattributed Change**, a **Live Session** appearing or going idle, or a worktree joining or leaving the watched set (ADR 0004 § the worktree lane). A commit event carries the commit's own per-file diff, in the same added/removed shape a file change has — committing must not make a change unreadable. Every event is tagged with its **Session Handle**. Assistant prose and thinking never appear — reading the conversation is the **Transcript**'s job. One event is **one tool call** (or one git observation) and stays so: consecutive file events for the same file are *rendered* as a single **Change Run**, which is a display fold and never a discarded event.

**Call**:
One **Feed Event** for a tool call that changes no file — a shell command, an MCP request, a web fetch, a subagent spawn, anything the agent invokes that the feed does not already narrate as a file change. Carries the tool's display name, as much of its input as the row can hold — the most readable key's value first, the remaining keys after it as compact JSON — and the `✓`/`✗` verdict patched on when the result lands. **Bash is a Call**, and the only one whose argument is a shell command, so it alone renders `$ …` with shell lexing. Expanded, a Call shows its input **entire**: every key by path, unclipped, with the line structure the log recorded, so the request the agent actually made is readable rather than summarised. The Watch shows what was *asked*, never what came back — on the header and in the expanded body alike: a result's content belongs to the **Transcript**, which renders the same tool call through the same header renderer, and under `--tools` through the same whole-input renderer, so the two surfaces cannot drift. **Local reads are silent** (`Read`, `Grep`, `Glob`, `NotebookRead`, `BashOutput`, `KillShell`) — they change nothing and the **Activity State**'s `reading` already answers for them; every other tool earns a row, *including one that does not exist yet*, the same call `ACT_VERBS` makes when an unmapped tool falls to `acting` (ADR 0004 § Calls). Not folded: ten fetches are ten rows, because unlike a **Change Run**'s hunks they are ten different acts with ten different arguments. A tool whose server the log names only as a UUID is shown by its bare tool name — Standup will not print an identifier that names nothing to the reader.
_Avoid_: tool event (every Feed Event comes from a tool call, so it distinguishes nothing), request, action

**Change Run**:
A run of consecutive **Feed Event**s for the same file and the same witness, which the **Watch** renders as **one entry that evolves** rather than a row per tool call or per git poll (ADR 0004 § the Change Run). The reason a file being worked on reads as one act of work: five hunks landing inside ten seconds cost one header, not five. Strictly *adjacent* — any event in between (a **Call**, another file, a prompt chapter) closes the run — so a run only ever grows at the tail of the feed and a row above the reader never changes shape. It also closes ~30s after it opens, so sustained work on one file still produces rows and the run's displayed time (its first event's, never revised) stays honest. The two witnesses fold differently, and each way the header describes its own body: a **Session**'s claim *accumulates* hunks and carries `×N` for the **tool calls** folded in (one `MultiEdit` is `×1`), while git's observation *restates* the whole delta of the path and carries no such count — N would be the number of polls that happened to catch the file. A run states its delta **as of its last update**, not perpetually: like every other entry in the feed, it is a statement about what happened, not a dashboard of current state.
_Avoid_: group, rollup, change block ("block" already names a run of added or removed lines), merged event (nothing is merged away — the fold is presentation)

**Live Session**:
A **Session** whose log was appended within a recency threshold (~30 minutes; the **Watch**'s `--since` widens it for one run, and states the widened window in its header). A *recency claim*, not a process fact — Standup never inspects processes, and the **Watch** always displays how long ago the last append happened rather than asserting "running". Distinct from **Active Work**, which is a git dirt tier.
_Avoid_: active session (collides with Active Work), running session

**Activity State**:
What a **Live Session**'s agent is doing *right now*, derived from the tail of
its own log: `thinking`, `reading`, `writing`, `running`, or `acting` for a tool
with no mapped verb. Strictly a mid-turn notion — a session that has handed
control back **has no Activity State at all**, and the **Watch** says nothing
about it. That asymmetry is the point: the question it answers is "has it
*not* finished yet", so silence is the answer for "done" and no word is needed
for it. Read from the main thread only (a subagent's reads are not the
session's, and several at once have no single answer). Three of the verbs are
facts — a pending `tool_use` names its own tool, and `stop_reason` says whether
the turn continues — with one trap: `stop_reason` belongs to the assistant
*message*, whose text and reasoning blocks are flushed as their own log lines
carrying it too, so a verb is read only off a line that actually holds a
`tool_use` block, and a line naming no tool leaves the state untouched. But
**`thinking` is inferred from silence**: the log
records a tool call and its result, never the pause between them, so the pause
is all there is to read. An **interrupt** (`Esc`, or the session quitting
mid-turn) is a *fact* in the log and settles the state; without reading it,
`thinking` would be claimed forever. A tool verb carries a **display floor** —
it holds the bar for a second before `thinking` may replace it, because a local
`Read` returns in milliseconds and a verb no one can see answers nothing. The
floor yields to a settled turn (the bar goes blank the instant control is handed
back) and to the next tool verb, so it lengthens no claim but the one it makes
legible, and the age displayed is always the real one. Displayed with the age of the state and
never with a threshold — a long `thinking 14m` is left to speak for itself
rather than being re-labelled "stalled", which would be Standup inferring that
a process died (see **Live Session**: recency, never a process claim). Its
spinner obeys the same rule: motion may never outlive the data (ADR 0004 § the Activity State).
_Avoid_: status (the Watch's status bar), active/idle (collides with Active
Work), running session (a process claim), progress (implies a known end)

## Relationships

- A **Session** belongs to exactly one working directory (`cwd`), which may be a repo checkout or a worktree
- The `cost` drill-down prints each Session's **Session Handle**; `standup session <handle>` renders its **Transcript**
- A **Session**'s **Notional Cost** is the sum of its turns' token usage — the parent transcript's *and* its subagent transcripts' (ADR 0002 § subagent usage) — priced by the **Rate Card**; a project's Notional Cost is the sum of its Sessions'
- The `cost` view spans **all** Sessions (any git footprint or none) and groups them by **Repo Entry** — worktrees fold into their parent checkout — falling back to the raw `cwd` for Sessions whose directory is not a git repo
- The `cost` view defaults to the **current calendar month** — chosen to sit alongside the per-cycle **Real Spend** — and stays stateless: recomputed from the logs each run, with no stored figures (ADR 0001 § the Recent Window). It reads them through the one log reader, so the **Derived Cache** accelerates it exactly as it does the **Triage Inbox** — a pure accelerator over the parse, never a cache of dollars (ADR 0001 § the one log reader)
- **Notional Cost** is the *only* cost figure Standup reports; **Real Spend** is deliberately excluded — there is no local source of truth for it
- every cost surface carries the not-money caveat, and the caveat **shortens rather than truncates** under the never-wrap rule: a figure clamped free of its caveat reads as spend, so a narrow terminal drops detail (the per-family split, the pointer to claude.ai) and keeps the words that disclaim
- **Notional Cost** is retrospective analytics, not a **Needs-Decision Item**: it lives in the `cost` view and never in the **Triage Inbox** (an optional notional-load summary line may appear only in the `-a` retrospective)
- **Brief Overhead** is a subset of **Notional Cost** carved out and labelled: the cost of the Stop hook's brief-generation Sessions, attributed to the **Repo Entry** whose Sessions they summarise, shown separately so the tax of keeping **Session Brief**s current is never hidden
- A **Repo Entry** aggregates one main checkout plus its worktrees; each pending/committed change carries one **Attribution Tier**
- The **Triage Inbox** is fully derived — computed fresh from git + JSONL; there is no stored state (ADR 0001 § the Recent Window)
- **Loop** detection is always-on, free, and deterministic (Derived Cache); the **Audit** is on-demand and paid — Loops are the free triage layer that tells you which Sessions are worth auditing
- Loops surface as a marker on the `cost` view's Session lines and as gutter marks on the **Transcript**'s looped turns; neither Loops nor Audits ever enter the **Triage Inbox** (retrospective analytics, like Notional Cost)
- An **Audit** consumes the target Session's transcript, its **Loops**, and its siblings' titles/**Session Brief**s/Loops; it produces `~`-marked claims, a solution, and a **Handoff Prompt** — never a script (Standup reads, it doesn't code)
- An Audit is stored at `~/.standup/audits/`, staled by the Session continuing (the same tolerance as Briefs — one constant, not two kept equal), never by sibling drift; `--refresh` regenerates, nothing auto-regenerates
- The **Watch** consumes the same two sources as the **Triage Inbox** (Session logs + git) with the same split: the log claims *who and what*, git confirms *ground truth* (and alone reveals live **Unattributed Change**s). It interleaves all **Live Session**s of one **Repo Entry** into a single feed, filterable down to one Session interactively
- A repo with no **Live Session**s (any git checkout, even outside the **Scan Universe**) still narrates: git alone is the witness, and the Watch content-diffs its dirty files so every change appears with its added/removed text — `~`-marked unattributed, since no Session claims it
- **Activity State** is the **Watch**'s only forward-looking reading: every other output describes what already happened, while this one says whether more is coming. It is a per-Session claim shown in the status bar for *acting* sessions only, never a **Feed Event** — a state is not something that happened, and a transition per tool call would bury the narrative it sits under
- The **Watch**'s typing animation is presentation only, under a hard staleness bound: the display may never lag the log by more than a few seconds — the animation compresses (down to instant) to honor it. Delight never outranks truth
- The **Change Run** is presentation only for the same reason: a **Feed Event** is still one tool call, nothing is discarded, and the fold is bounded by strict adjacency and a ~30s lifetime so it can never rewrite a row above the reader or let one entry stand in for a whole burst of work (ADR 0004 § the Change Run). Granularity that is honest is not automatically useful — but the *counts* must always describe the body printed beneath them, which is why a claimed run sums its hunks and a git-witnessed one states its net delta
- On launch the **Watch** backfills, unanimated and dimmed, from *each* **Live Session**'s current user prompt (prompts are the narrative's chapter breaks) — every session's chapter, interleaved chronologically, so a Session that finished its work and went quiet is still legible when you filter to it. It sits beneath a vitals header (repo, branch, Live Sessions with recency, dirt count)
- A committed change stays readable in the **Watch**: the commit event carries its own diff, so the two ways a change can be witnessed — a **Session**'s claimed edit while dirty, and git's commit after the tree goes clean — both render as added/removed text rather than one of them degrading to a subject line

- The drill-down (`standup <repo>`) is the Triage Inbox at higher magnification — the same session-major model, with each Session Rollup expanded into its file/commit evidence. A file appears under its latest Session only; the older Sessions that also touched it are named inline as `also ~"…"`. The **Attributed Diff** is the magnification after it, and the drill-down names it (one dim line, only when there is Active Work to read) the same way a rollup title line names its **Session Handle**: a view prints the address of the view after it
- The CLI has two axes, and an argument's *position* says which (ADR 0005 § two grammars). **Verb-first** is a lens over the whole **Scan Universe** — `standup cost` prices every project, and a repo merely filters it. **A second positional is one Repo Entry at higher magnification** — `standup tt diff`. Both spellings reach the same code (`standup <repo> <view>` is rewritten to `standup <view> <repo>` before dispatch), for every *free, read-only* view: `diff`, `cost`, `watch`, `session`. **`audit` is excluded**: it is the one view that spends, so it always names its Session explicitly
- The **Attributed Diff** and the **Watch** are the two surfaces that render changed code, and they share exactly one thing: the row shape (the ±gutter, per-token syntax, the removed-row wash and its bounds, the fold rule — ADR 0004 § the removed-row field and § fold, don't clip), implemented once so it cannot drift. They share no data path — the Watch replays a Session's *claimed* edits as they land, the Attributed Diff reads git's *ground truth* and attributes it afterwards

## Example dialogue

> **Dev:** "Do we mark an item as reviewed once Arjun has seen it?"
> **Domain expert:** "No — the **Triage Inbox** has no read-state of its own. Committing, discarding, or pushing is what removes an item; git is the source of truth."

## Flagged ambiguities

- "report" vs "inbox" — resolved: Standup is a **Triage Inbox**, not a passive report. Output ordering is by required action (uncommitted → unpushed → done), not chronology.
- file-major vs session-major — resolved (2026-07-22): the Session is the display unit at every altitude; files are evidence shown only in the drill-down. Within a section, repos order by most recent activity, not alphabetically.
- "cost" / "money" / "spend" — resolved (2026-07-22): per-Session and per-project figures are **Notional Cost** (API-equivalent load, not money). Real money is **Real Spend** — account-level and unattributable. The tool must never present Notional Cost as spend.
- Can `xu` (`plan-usage-history.json`) stand in for **Real Spend**? — resolved (2026-07-22): no. It refreshes fine (~5 min while the app runs) but does not match the authoritative claude.ai meter (~$80 local vs ~$202 account-wide). Standup reports no Real Spend rather than a wrong one.
- Does an LLM-authored **Session Brief** break the "derived, deterministic, never-LLM" model? — resolved (2026-07-22): no (ADR 0003 § the Session Brief). Generation happens *offline*, during the coding session (a Stop hook, out-of-band), exactly as commits and edits happen offline; Standup only ever *reads* the Brief at render time, so output stays deterministic given the files on disk. The Brief is a fallible *claim*, never a derived fact — always marked as such, hedged when stale, and it augments but never replaces the derived title.
