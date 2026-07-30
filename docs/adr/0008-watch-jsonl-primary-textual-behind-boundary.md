# Watch: JSONL-primary event stream, Textual UI behind a stream/UI boundary

`standup watch <repo>` is Standup's first live view. Two decisions worth recording:

**Event source.** The obvious source is git polling — but git only ever says "the
tree differs," anonymously and after the fact. The Claude Code session JSONL is
appended live while the agent works and carries the actual tool call (Edit/Write,
with the exact text), the Session identity, and the intent. So the Watch tails
the JSONL as its *primary* stream and uses git as the *ground-truth layer*
(confirming tree state, and alone able to reveal live Unattributed Changes) —
the same claims-vs-truth split Attribution already uses. Rejected: git-only
(anonymous, poll-latency, cannot narrate "what the agent is doing").

**UI technology.** Watch needs alt-screen, keystrokes, scrollback, expand/collapse,
and timed animation — none of which the plain-stdout snapshot views needed.
We adopt **Textual** as Standup's second dependency rather than hand-rolling
ANSI/termios (a weekend-eating mini-framework we'd own forever) or using rich
alone (rendering without input handling — half the problem). The containment
rule: the event stream (JSONL tail + git observation → typed Feed Events) is
plain Python with no textual imports; textual is imported only by the watch UI
module, so the inbox/cost/show path never grows a TUI dependency.

Also fixed here: the typing animation is presentation-only under a hard
staleness bound (display lags the log by at most a few seconds; typing speed
compresses, down to instant, to honor it). A watch view that shows the past
while claiming "live" would be lying.
