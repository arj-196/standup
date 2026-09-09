# 0002 — Notional Cost from a single dated Rate Card, not real spend

Date: 2026-07-22

The `cost` view reports **Notional Cost** — tokens priced at published API rates
— as a *comparison weight*, explicitly not money paid. On a subscription,
per-session dollars are notional; the only real money is account-level **Real
Spend**, which Anthropic does not attribute to any session.

**Standup reports no Real Spend at all** — no number, no pointer, no estimate.
`xu` in `plan-usage-history.json` was investigated as a local source and
rejected: it does not match the authoritative claude.ai figure (two machines
read ~$80 against an account meter of ~$202). With no trustworthy local source,
Standup reports only what it can derive truthfully from the logs.

Pricing comes from a **single hardcoded, dated Rate Card** (`rates.py`): base
input and output per model, cache rates *derived* by the published multipliers,
per-turn modifiers read from each turn's own `usage` rather than assumed. Every
model in current use has an exact public rate, so nothing is estimated.

**The four display buckets read a turn the way pricing does.** A cache write
has two spellings in the logs — the flat `cache_creation_input_tokens` and the
`cache_creation` sub-object that splits it by lifetime — and both `turn_cost`
and `turn_tokens` take it from `cache_write_split`, which prefers the
sub-object because the two lifetimes are priced apart. Display used to prefer
the flat field, the opposite way round: harmless on every log seen (they
agreed), and a printed figure that disagreed with the priced one the day they
did not.

**An unknown or future model is shown with its tokens but excluded from the
dollar total and flagged `unpriced`** — never silently counted as $0. That is
the cost view's analogue of "never hide dirt".

The figures track the rate card, not your invoice; they will not reconcile to
Real Spend and are not meant to. The Rate Card is a maintained constant — on a
price change or a new model, update `rates.py` and its date.

## Subagent usage folds into the parent Session

*(2026-08-12)* A subagent transcript
(`<project>/<sessionId>/subagents/agent-<id>.jsonl`) carries its own per-turn
`usage`, and **none of it is echoed into the parent log** — verified against a
real worktree agent whose ~55k output tokens appear nowhere in the parent's
JSONL. A scan of top-level logs alone therefore systematically under-counts
exactly the sessions that delegate most.

**The cost view also reads each Session's `subagents/` transcripts and folds
their usage into the parent Session's line** — same window (turns filtered by
their own timestamps), same Rate Card, same unpriced rule. Each transcript is
its own `read_log` reading with its own cache row (ADR 0001 § the one log
reader); only usage crosses over, because a subagent log carries no title and
no `cwd` of the parent's. The fold is marked,
never silent: the drill-down's token line appends `incl N subagents`, and the
JSON carries a `subagents` count per session. A session whose only in-window
work was delegated still earns its row.

Folded rather than surfaced as rows or as an overhead, because a subagent is
neither:

- **Not a Session.** It has no title lines, no `cwd` discovery role, and
  `standup session` cannot address it (a Watch lane's agent-id address is not a
  Session Handle). A row that cannot be drilled into would be a dead end in the
  one view built for drilling.
- **Not an overhead.** Brief Overhead and Audit Overhead are *Standup's own*
  spend, kept separate so the tool's tax is never hidden. A subagent's tokens
  are the Session's work, delegated — folding them in is what makes the
  Session's weight true.

Accepted costs:

- **The parent log's mtime gates the whole Session, subagents included.** A
  subagent still appending after its parent's last write can slip a window
  edge; each subagent file is additionally mtime-gated, but only as a read
  saver. Turn timestamps still filter exactly once a file is read.
- **`standup session <handle>` renders the parent conversation only**, so its
  per-turn cost annotations no longer sum to the cost view's session figure
  when subagents ran. The `incl N subagents` mark is what accounts for the
  difference.
- **Loops are detected in the parent log only.** Loop Cost never includes
  subagent turns, so a Loop-heavy subagent is invisible to the Audit's
  fingerprints.

*Rejected: one row per subagent* — not addressable, and multiplies rows the
reader can act on nowhere. *Rejected: an "agent overhead" figure* — the
overhead idiom marks Standup's own spend, not the session's delegated work.
*Rejected: reading the parent's task notifications instead* — they carry the
subagent's text result, never its usage.

## Codex usage

*(2026-09-09)* A Codex Session's turns are priced from the same Rate Card,
through the same `turn_cost`, with OpenAI rows added to `rates.CARD` (source:
developers.openai.com/api/docs/pricing, captured 2026-09-09, standard tier,
short context). Three facts about Codex's counts are the reader's to convert,
so the card stays one formula (ADR 0001 § two dialects, one reading):

- **`input_tokens` includes the cached share.** OpenAI's `cached_input_tokens`
  is a subset of `input_tokens`; Anthropic's `cache_read_input_tokens` is
  disjoint from it. `TurnUsage.input_tokens` means *uncached* input, so the
  Codex reader subtracts — a row then prices a Codex turn exactly as it prices
  a Claude one.
- **the multipliers hold.** Every OpenAI row lists cached input at 10% of input,
  and every row that lists a short-context cache write lists it at 1.25× — the
  two constants the card already carries. Codex logs `cache_write_input_tokens`
  (zero on every log seen); it reads as a 5m write.
- **reasoning tokens are output tokens.** OpenAI bills `reasoning_output_tokens`
  inside `output_tokens`, so the reader takes `output_tokens` whole.

Not modelled, stated: the long-context rows (prompts over 272K tokens, 2×
input / 1.5× output) — no Codex log seen carries one, and the card would need a
per-turn context-length reading to apply them; `codex-auto-review`, the model of
Codex's review threads, which are no Session and are counted nowhere
(ADR 0001 § two dialects, one reading). An unpriced-model rule needs no change:
a Codex model the card lacks is flagged `unpriced`, never $0.

Real Spend stays unreported for Codex as for Claude: a ChatGPT plan's usage is
account-level and attributed to no thread.

## Alternatives considered

- **Live pricing API** — adds a network dependency and non-determinism to a
  stateless local CLI; rates move rarely.
- **Historical per-turn rates** — Notional Cost is a comparison weight, not a
  bill, so one current snapshot keeps rankings honest and the code simple.
  Revisit only if a mid-window price change distorts comparisons.
- **Allocating Real Spend proportionally** — fabricates an authoritative-looking
  number from an unattributable total.
