# 0016 — A hunk is attributed verbatim, or not at all

Date: 2026-08-04
Status: accepted

## Context

The inbox attributes at **file** granularity, by path overlap: did this session
ever `Edit`/`Write` this path. That test is coarse but *reliable* — if no session
touched the path, nothing a session did explains the change.

`join.rollups()` then assigns each file to exactly one session — its **latest**
— and names the others inline as `also ~"…"`. In the drill-down that is
harmless: the thing placed under the wrong header is a *filename*, and the
`also` annotation is right beside it.

At diff granularity it stops being harmless. If session A rewrote 200 lines at
10:00 and session B changed one line at 14:00, the whole 200-line body renders
under B's header with a quiet `also ~"A"` above a screenful of code. You read it
and credit A's work to B. That is the exact class of confident misattribution the
`~` discipline exists to prevent, and it is the one place where inheriting the
drill-down's model actively lies.

The material for a sharper answer is already in the logs. A session records the
literal text of every edit it made: `Edit.old_string`/`new_string`,
`Write.content`, `MultiEdit.edits[]`, `NotebookEdit.new_source`. So the question
"who wrote *this*" can be asked of content rather than of paths.

The hazard is obvious. Any matcher that scores *similarity* will sometimes be
confidently wrong, and a wrong attribution presented without hedging is worse
than an admitted gap — it is the failure mode Standup's whole vocabulary
(`~`-marks, claim-vs-fact, Notional-vs-Real) is built to avoid.

## Decision

**Attribution is per hunk, by verbatim match, with no similarity scoring.** The
matcher either recognises a block of lines or reports that it cannot account for
it.

**The unit is the run, not the hunk's added-line list.** A run is a maximal
consecutive block of same-sign rows. This is forced, not stylistic: an
*added-only* line list is generally **not** contiguous in the text a session
recorded — an `Edit` that inserts two lines around a kept one produces a hunk
whose `+` rows are separated by context — whereas each maximal run of `+` rows
*is* contiguous in the new file, and therefore contiguous inside the
`new_string` that produced it.

The rule, in order:

1. Split the hunk into runs. Added runs are matched against what a session
   *wrote* (`new_string`, `content`, `new_source`); removed runs against what it
   *replaced* (`old_string`).
2. **Drop whitespace-only runs.** A blank line is contained in nearly every
   fragment, so counting them would turn every hunk `shared`. Whitespace carries
   no identity. Trailing whitespace is stripped before comparing; **leading**
   whitespace is kept — indentation is content.
3. A session **accounts for** a run when one of its fragments contains that
   run's lines as a contiguous, exact line block.
4. Every surviving run accounted for, one session throughout → **`likely`**.
5. Every surviving run accounted for, two or more sessions → **`shared`**: the
   hunk genuinely holds more than one session's work, so the view declines to
   split it rather than picking a winner.
6. Any surviving run accounted for by nobody → **`unaccounted`**, naming the
   sessions that account for the rest when some do.
7. No run survives step 2 (a whitespace-only hunk) → inherit the file-level
   verdict rather than invent a sharper one.
8. No candidate session ever touched the path → **unattributed**, and no
   matching is attempted.

**`unaccounted` is a new tier and is not `unattributed`.** They must never be
rendered as the same thing. `unattributed` rests on the *path-overlap* test and
is reliable. `unaccounted` sits inside a file a session **did** touch, and it has
two indistinguishable causes: your hand edit, or a false negative — the agent
wrote it, then a later edit or a reformat moved the text so the fragment no
longer matches verbatim. Calling that "unattributed" would assert a hand edit
that may not have happened. Naming it separately is the honest move, and it
happens to flag exactly the hunks worth a second look.

**A brand-new file is attributed at file level, not by run matching.** An
untracked file has no old side, so its entire content is one run, and a single
later edit to any line would make the whole file read `unaccounted`. A file that
did not exist before was *created* by whoever wrote it: that verdict is both true
and stable under further editing.

**A commit's own hash stays the one attribution that is a fact.** Where a
session's `git commit` stdout recorded the hash, the commit header carries
`exact`. The hunks inside are still matched individually, because a commit can
bundle more than one session's work — and where it does, a single name on the
header would be a half-truth.

**Evidence recorded after a commit is not evidence about that commit.** When
attributing a commit's hunks, only fragments timestamped at or before the commit
(plus 30 minutes of slack for clock skew and `--amend`) are considered. This is
not a softening of the verbatim rule, it is a second *strictness*: a session that
happens to write the same lines a day later cannot have authored the commit, and
without the bound it was reported as a co-author — observed in practice, where
this session's own README edit made it a `shared` author of a commit from the day
before. Working-tree change has no such bound; every recorded edit is a
candidate.

**Marks are printed only where a hunk disagrees with the header it sits under.**
Silence means "as the group header says". A note on every hunk of an
unremarkable file would be read as decoration and skipped, which would cost the
marks that matter their force.

**Cache boundary.** The per-session fragment index is derived deterministically
from the logs, so it lives in the **Derived Cache** — a `fragments` table keyed
on `(session_id, size, mtime_ns, index_version)`, zlib-compressed because the
index is literal source text. The **match** never does: it runs against the live
working tree, which ADR 0003 keeps out of the cache entirely. An index too large
even compressed is simply not stored — skipping a write costs a reparse and
changes no output, which is what a pure accelerator may do.

## Consequences

- The `--stat` view cannot carry a hunk verdict, having no hunks. Its file line
  carries the *shape* of what the bodies would have said (`~ 2 shared ·
  1 unaccounted`, plus any other session named) rather than a single name the
  bodies would contradict.
- `standup <repo> diff <handle>` can filter to **hunks**, not just files — the
  first time Standup can show one session's work inside a file two sessions
  touched. A partially-`unaccounted` hunk that the named session accounts for
  part of is included, still marked.
- **`unaccounted` will be common in sessions that iterate.** Two successive
  `Edit`s to the same region leave a current run that is contiguous in neither
  fragment, so a change the agent genuinely made reads as unaccounted. This is
  the verbatim rule working as specified, and it is the price of never being
  confidently wrong. Loosening it — allowing a run to be covered piecewise by
  several fragments — would reduce these gaps and reintroduce the false-positive
  risk, so it is deliberately not done here.
- The rendering of a diff row now has exactly one home (`diffrows.sign_rows`),
  shared by the Watch and the Attributed Diff, so the three channels, the
  removed-row wash and its bounds (ADR 0012) and the fold rule (ADR 0013) cannot
  drift between the two surfaces. This *implements* ADR 0008's boundary — a
  Textual-free renderer both a live app and a static view can call — and needs no
  decision of its own.

## Alternatives considered

**Accept the drill-down's model, trust `also ~"…"`.** Free, consistent, and
wrong in exactly the case that matters: a marker above a screenful of code is
skimmed past.

**Mark every multi-session file as unsplittable and stop there.** Honest and
cheap, and strictly less informative than what the logs can support: most hunks
*are* cleanly one session's.

**Fuzzy matching (similarity ratio, token overlap).** Would close the
`unaccounted` gaps described above, and its failure mode is a confident wrong
attribution with no signal that it failed. Rejected on that alone.

**Line-level attribution.** Sharper than hunks, and it candy-stripes a diff into
unreadability. The hunk is the unit a reviewer reads.
