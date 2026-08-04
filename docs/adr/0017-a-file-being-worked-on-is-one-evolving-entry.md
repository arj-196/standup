# Watch: a file being worked on is one evolving entry

The Watch emitted one **Feed Event** per witnessed change, and rendered one row
per event. A file the agent actually works on therefore arrived as a stack of
near-identical rows — same path, same `modify`, four or five headers deep — and
the reader paid a header, a gap gutter and a repeated path for each one. The
information was there; the shape buried it.

Two different mechanisms produced that stack, and only one of them was ever
about the agent.

**The git watcher's rows were a sampling artifact.** `_GitWatcher` polls every
`GIT_POLL_INTERVAL` and diffed each dirty file against *the previous poll's
snapshot*. One continuous burst of writing was therefore chopped into one delta
per two-second window, and a run reading `+8 +4 +1 +1 +1` described the poll
rate rather than the change. So the watcher now retains a **reference snapshot**
per dirty file and emits the *cumulative* delta against it. Each event restates
the path instead of adding to it, and the counts are the true net figure: a line
added and then removed inside the run cancels rather than being counted twice.
The reference is HEAD's version for a file that dirties while the Watch runs,
and the *launch* snapshot for dirt that predates it — startup dirt is old news,
and replaying it as one giant event would bury the live narrative.

**A Session's rows were real, and still too many.** Each is one `Edit` call, and
a `MultiEdit` emits one per hunk. Granularity that is honest is not automatically
useful: five hunks landing on one file inside ten seconds is one act of work to
the person watching. Those events therefore accumulate into one entry too.

The unifying decision: consecutive file events for the same path and the same
witness render as one **Change Run** — one header, one body that evolves. The
fold is *presentation*, exactly as the typing animation is (ADR 0008): a Feed
Event remains one tool call, and no event is discarded. Rejected: coalescing in
the stream, which would have required inventing a revision protocol so the
stream could say "revise what I gave you" — and still needed the same UI code to
apply it.

Four bounds keep the fold from lying.

**Strict adjacency.** A run only ever grows at the *tail* of the feed. Any event
between two same-file events — a Bash one-liner, another file, a prompt chapter
— closes the run. So a row above the reader never changes shape, which is the
Watch's sticky-scroll idiom (the view holds still, the data never does), and when
two rows *don't* merge the reason is visible on screen. Rejected: a time window
that merges across intervening events, which would have to rewrite a widget
scrolled off above the reader, or reorder the feed.

**The header always describes its own body.** A restating run states the net
delta; an accumulating one states the sum of the hunks it holds. The net delta is
not reachable for a Session's claim — the log carries hunks, never file content —
and borrowing it from the git watcher would have produced a header that
disagreed with the body beneath it and silently revised itself two seconds after
being read. A count is trustworthy because you can verify it against what it is
showing you, so that property wins over uniformity. A claimed run additionally
carries `×N`, counting **tool calls** (one `MultiEdit` is `×1`), because the
merge is what makes the counts bigger than any single edit and the reader is owed
the reason. A restating run carries no such mark: N would be the number of polls
that happened to catch the file, which is a fact about `GIT_POLL_INTERVAL`.

**The window sits where the news is.** A collapsed body shows `HEAD_LINES` of its
added text, and folding could easily hide the very content being watched for. An
accumulating run is chronological, so once it holds more than one contribution it
shows its **tail**, with the earlier lines counted above it. A lone event and a
restating run show their **head** — a single hunk reads top-down, and a cumulative
body is a file-ordered snapshot with no newest end to sit at. A run therefore
stays a bounded height however much it absorbs, which is the actual noise cap;
otherwise a long run merely converts several short blocks into one tall one.
Appends type in from where the last contribution stopped rather than restarting,
and restatements land instantly — re-typing rows already on screen every two
seconds is a flicker, not an animation.

**A run closes after `RUN_WINDOW`.** Strict adjacency alone would let one block
absorb a three-minute burst, and a feed that stops producing rows while the agent
works hardest reads as dead. The cap is `FRESH` — the threshold the header
already uses to mean "recent" — so sustained work on one file yields a row every
30 seconds instead of one every two, and a run's displayed timestamp (its
first event's, never revised) can never be staler than that.

One consequence is worth stating plainly rather than engineering around: because
an empty cumulative diff emits nothing, a run whose file is reverted all the way
back to its reference keeps its last figures. A **Change Run** states the delta
*as of its last update* — which is what every other entry in the feed does too.
The Watch is a feed of what happened, not a dashboard of current state, and
making one entry type self-correcting would make it the odd one out.
