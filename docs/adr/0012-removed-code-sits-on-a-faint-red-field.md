# 0012 — The surface is a third channel: removed code sits on a faint red field

Date: 2026-08-03
Status: accepted

## Context

The **Watch** was built around a two-channel rule, stated in `theme.py`,
in two `watchui.py` docstrings, and in the manual: *the gutter says what
changed, the text says what it is*. The rule exists to protect the syntax
palette. Monokai is a licensed third system whose colours mean *token kinds*,
so change-semantics were kept out of the code text entirely — `_token_style`
strips monokai's own page background precisely so that no line wash competes
with the surface. The manual promised this in as many words: highlighting is
"foreground only — no line washes".

The rule has one cost, which shows up in the Watch's actual use as an agent
monitor. Additions are the expected case — an agent mostly writes — so the
thing a reader most needs to *catch* is a deletion, and a deletion is
announced by a single `−` glyph in a 2-column gutter. Finding removed code in
a long feed means reading the gutter row by row. Strengthening the existing
channels doesn't fix it: a bolder `−` is still a glyph you have to look at, and
dimming removed code would break the "identical full strength on added and
removed lines alike" commitment as hard as a wash does, while also making the
code you most need to read the hardest to read.

## Decision

**Removed rows carry a faint red background wash — a third channel that
restates the gutter rather than replacing it.** The two existing channels are
untouched: monokai stays foreground-only at full strength, and the `−` remains
the carrier. Because the wash is redundant by construction, nothing lives in
it alone, so `NO_COLOR` and 16-colour terminals lose no information by not
having it.

Five bounds make it a scanning aid rather than a new meaning:

1. **It yields to the selection.** *(Superseded by
   [ADR 0018](0018-selection-marks-the-lane-not-the-body.md): the selection no
   longer paints the body, so the collision below cannot happen and the field is
   painted on the selected event like any other.)* The selection band is also a
   background, and
   a `Text` bgcolor beats a widget's CSS background — so the naive version
   punches red holes in the selection wash on exactly the event the reader is
   inspecting. On the selected row the wash is dropped. The rule is general:
   when two backgrounds collide, the one carrying unique information wins, and
   the redundant one gives way.

2. **It is a rectangle, from the sign column to the full content width.** Diff
   lines are ragged, so a wash that ends with the text is a torn shape, and a
   torn shape is harder to spot than no shape — the eye locks onto rectangles.
   The right edge is padded to `width`; the cost is trailing spaces on a
   mouse-drag copy, which is accepted.

3. **It never reaches the gap gutter or the session lane.** Cols 1–8 stay on
   clean surface: the lane's per-session identity hue is its own channel, and
   tinting the field under it would muddy the one mark that says *which agent
   did this*. The five blank columns between the lane and the sign become a
   deliberate margin isolating the block.

4. **Deletions only.** Added lines are sliced by character count for the
   type-on animation. A full-width wash behind a line that hasn't typed yet
   telegraphs the line's existence and spoils the animation; a wash that grows
   with the characters is the torn shape of point 2, crawling. Removed lines
   are emitted whole, which is *why* they can hold a clean rectangle. The
   asymmetry falls out of the animation model rather than being imposed on it.

5. **A tint of `surface`, not a shade of `removed`, and quieter than
   `selection`.** Monokai's foregrounds are bright and need a dark substrate,
   so the dark value darkens (`#291A1E`) and only the light value lightens
   (`#FCEBEB`, carried for completeness — the light variant is not reachable,
   see theme.py). Deliberately below the selection's luminance step, because
   the wash is subordinate to it (point 1) — calibrated by eye against a
   rendered frame, quiet enough to sit under monokai and bright enough to find.

The wash rides the existing `paints_backgrounds` gate, so it exists at
truecolor and 256 colours only. 256 is the one place the intent can't be
honoured: the colour cube's darkest red is `#5f0000`, dark in luminance but
saturated, so a 256-colour terminal shows a louder wash than a truecolor one.
This is recorded in `ROLES` rather than worked around — the alternative was
dropping the feature for those terminals entirely.

## Consequences

- The manual's "no line washes" promise is void and has been rewritten, along
  with `theme.py`'s doctrine, both `watchui.py` docstrings, and the README. The
  palette now diverges from the "Watch Redesign v2 — Color" spec by one role
  and one amended rule; without this record, a later reader diffing code
  against spec would read the wash as a bug and delete it.
- `_sign_row` takes `width` as a required argument, so a caller cannot silently
  disable the wash by forgetting to pass it. `_commit_body` threads it through.
  Every `−` row is washed — collapsed preview, expanded body, and expanded
  commit diffs alike — so the rule states simply, with no exceptions to
  memorise.
- `_more_row` (`… ▸ N more lines`) is **not** washed. It carries no `−` and is a
  count rather than removed code, and leaving it out gives the block a clean
  bottom edge.
- The chosen strength is at the quiet end deliberately, and the value is the
  part expected to need revisiting. If the tint proves invisible on a
  low-contrast monitor, the fix is the hex — the geometry is what makes a faint
  tint legible as a signal, so it is not the thing to trade away.
