# TODO

Work that is *decided but not done*. Anything architectural graduates to an ADR
in [docs/adr/](docs/adr/) before it gets built; anything shipped leaves here and
lands in the [README](README.md), the manuals, and [CONTEXT.md](CONTEXT.md).
Domain words in **bold** are defined in CONTEXT.md.

---

## The Watch is becoming an agent monitor

The **Watch** was designed to show *new* changes arriving. It is now used to
**monitor what an agent is actually doing**, which makes already-happened
activity — previous actions, committed work — part of the job rather than
`standup session`'s problem. Per-session backfill, commit diffs, and the
**Activity State** in the status bar (ADR 0011 — the monitor now answers "is it
still working?", which was the reason for the terminal round trip) were the
first three steps. The items below are the rest.

Two guardrails hold across all of them:

- **The live default stays recent-only.** The vitals header lists **Live
  Session**s (a log appended within 30 minutes), not every session. History
  review is a *mode over the same feed*, never a relaxed threshold in the
  default view — the moment the Watch lists everything it stops being a monitor.
- **No modal display states.** No pause, no buffering, no frozen view. The
  sticky-scroll idiom is the rule: the view holds still, the data never does.

### 1. Commits that predate launch never appear

`_GitWatcher.__init__` seeds `head[co]` from the current HEAD and emits a commit
**Feed Event** only when HEAD *moves*
([watchstream.py](src/standup/watchstream.py)). So a commit made two minutes
before you ran `standup watch` is invisible — the session's edits replay from
backfill, but the commit, and now its diff, do not.

This is the biggest hole for the monitoring use and the cheapest to close: seed
the feed from `git log` over the same span backfill covers, marked `backfill`
like every other replayed event, attributed through the existing `_attribute()`
sha join.

### 2. Backfill stops at the chapter break

`_Tailer.backfill()` keeps only the events since a session's latest user prompt —
prompts are chapter breaks, so you get the chapter in progress. Earlier chapters
are read and thrown away on the same pass.

Make the depth a count of chapters rather than a hard "latest one", still bounded
by `BACKFILL_CAP`. Worth deciding whether the default depth is 1 (today) or
more; more chapters means a longer dimmed prologue on every launch.

### 3. `now` is the only time anchor

The 30-minute **Live Session** threshold and git polling both assume the
present. There is no way to point the feed at a closed past range, which is the
real work behind history review and the one item here that earns an ADR.

The shape that preserves both guardrails: the same feed, the same widgets, the
same keys, sourced from a closed range instead of a tail — `standup watch
--since 3h`, or a replay of one **Session Handle**'s whole log. `watchstream`
already turns a whole JSONL file into typed **Feed Event**s and the Textual
layer only ever consumes those (ADR 0008), so this is mostly *removing*
truncation plus a `git log`-driven commit source. Open questions: whether it is
a flag on `watch` or its own subcommand; what the vitals header means with no
live session; whether the **Transcript** (`standup session`) and a replayed feed
are two views of one thing.

This item now has a neighbour rather than a rival. `standup <repo> diff` (ADR
0015, ADR 0016) covers the *net* question — what the tree says changed, and
which session accounts for each hunk — deliberately **not** the narrative one a
replay answers: what the agent did, in order, including the edits it later
undid. The two are complements, and the grammar decision makes the open question
above easier: `standup <repo> watch --since 3h` is already a spelling the CLI
accepts, so "flag on `watch`" no longer costs a new subcommand.

### 4. Revisit the Watch's domain framing once history lands

CONTEXT.md's **Watch** entry ("renders *time passing*", exempt from the snapshot
idioms) and the manual's "a live view, not an archive — the **Transcript** is
where the full history lives" were both written for the original purpose. They
are still true *as shipped*, so they are not lying yet. When item 3 lands they
will be, and both need rewriting in the same change.
