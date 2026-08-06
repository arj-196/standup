# 0007 — A hunk is attributed verbatim, or not at all

Date: 2026-08-04

## Context

The inbox attributes at **file** granularity by path overlap — did this session
ever `Edit`/`Write` this path. Coarse but *reliable*. Each file is then assigned
to its **latest** session, with others named inline as `also ~"…"`.

Harmless in the drill-down, where the mis-placed thing is a *filename* and the
annotation sits beside it. **At diff granularity it lies.** If session A rewrote
200 lines at 10:00 and B changed one line at 14:00, the whole 200-line body
renders under B's header with a quiet `also ~"A"` above a screenful of code. You
credit A's work to B — the exact confident misattribution the `~` discipline
exists to prevent.

The material for a sharper answer is in the logs: a session records the literal
text of every edit. So "who wrote *this*" can be asked of content, not paths.

The hazard: any matcher scoring *similarity* will sometimes be confidently
wrong, and a wrong attribution presented without hedging is worse than an
admitted gap.

## Decision

**Per hunk, by verbatim match, with no similarity scoring.** The matcher either
recognises a block of lines or reports that it cannot account for it.

**The unit is the run, not the hunk's added-line list.** A run is a maximal
consecutive block of same-sign rows. Forced, not stylistic: an *added-only* line
list is generally **not** contiguous in the recorded text — an `Edit` inserting
two lines around a kept one produces `+` rows separated by context — whereas each
maximal run of `+` rows *is* contiguous in the new file, and therefore inside the
fragment that produced it.

1. Split the hunk into runs. Added runs match what a session *wrote*; removed
   runs what it *replaced*.
2. **Drop whitespace-only runs.** A blank line sits in nearly every fragment, so
   counting them turns every hunk `shared`. Trailing whitespace is stripped
   before comparing; **leading** whitespace is kept — indentation is content.
3. A session **accounts for** a run when one of its fragments contains that run's
   lines as a contiguous, exact line block.
4. All surviving runs accounted for, one session → **`likely`**.
5. All accounted for, two or more sessions → **`shared`**: the hunk genuinely
   holds more than one session's work, so the view declines to split it rather
   than picking a winner.
6. Any surviving run accounted for by nobody → **`unaccounted`**, naming the
   sessions that account for the rest.
7. No run survives step 2 → inherit the file-level verdict rather than invent a
   sharper one.
8. No candidate session ever touched the path → **unattributed**, no matching
   attempted.

**`unaccounted` is not `unattributed`, and they are never rendered as one.**
`unattributed` rests on the path-overlap test and is reliable. `unaccounted` sits
inside a file a session **did** touch and has two indistinguishable causes: your
hand edit, or a false negative where a later edit or reformat moved the text so
the fragment no longer matches. Calling it "unattributed" would assert a hand
edit that may not have happened. Naming it separately is honest, and it flags
exactly the hunks worth a second look.

**A brand-new file is attributed at file level.** An untracked file has no old
side, so its whole content is one run, and one later edit would make the file
read `unaccounted`. A file that did not exist was *created* by whoever wrote it —
true and stable under further editing.

**A commit's own hash stays the one attribution that is a fact.** Where a
session's `git commit` stdout recorded the hash, the commit header carries
`exact`. Hunks inside are still matched individually, because a commit can bundle
more than one session's work, and a single name on the header would be a
half-truth.

**Evidence recorded after a commit is not evidence about it.** Only fragments
timestamped at or before the commit (plus 30 min slack for clock skew and
`--amend`) are considered. Not a softening of the verbatim rule but a second
strictness: a session writing the same lines a day later cannot have authored the
commit. Without the bound it was reported as co-author — observed, where one
session's own README edit made it a `shared` author of the previous day's commit.
Working-tree change has no such bound.

**Marks print only where a hunk disagrees with its header.** Silence means "as
the group header says". A note on every hunk would read as decoration and get
skipped, costing the marks that matter their force.

**Cache boundary.** The per-session fragment index is derived deterministically
from logs, so it lives in the **Derived Cache**. The **match** never does: it runs
against the live working tree, which the cache excludes. An index too large even
compressed is simply not stored — skipping a write costs a reparse and changes no
output, which is what a pure accelerator may do.

## Consequences

- `--stat` cannot carry a hunk verdict, having no hunks. Its file line carries
  the *shape* of what the bodies would say (`~ 2 shared · 1 unaccounted`, plus
  any other session named) rather than a single name they would contradict.
- `standup <repo> diff <handle>` can filter to **hunks**, not just files — the
  first time one session's work is visible inside a file two sessions touched. A
  partially-`unaccounted` hunk the named session accounts for part of is
  included, still marked.
- **`unaccounted` will be common in sessions that iterate.** Two successive
  `Edit`s to the same region leave a run contiguous in neither fragment, so a
  change the agent genuinely made reads as unaccounted. This is the verbatim rule
  working as specified — the price of never being confidently wrong. Loosening it
  (a run covered piecewise by several fragments) would close these gaps and
  reintroduce the false-positive risk, so it is deliberately not done.

## Alternatives considered

- **Accept the drill-down's model, trust `also ~"…"`** — free, consistent, and
  wrong in exactly the case that matters: a marker above a screenful of code is
  skimmed past.
- **Mark every multi-session file unsplittable** — honest and cheap, and strictly
  less informative than the logs support: most hunks *are* cleanly one session's.
- **Fuzzy matching** (similarity ratio, token overlap) — closes the `unaccounted`
  gaps, and its failure mode is a confident wrong attribution with no signal that
  it failed. Rejected on that alone.
- **Line-level attribution** — sharper, and it candy-stripes a diff into
  unreadability. The hunk is the unit a reviewer reads.
