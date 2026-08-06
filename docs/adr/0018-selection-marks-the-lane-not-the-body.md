# 0018 — Selection marks the session lane, not the body

Date: 2026-08-06
Status: accepted

## Context

The **Watch** marked the selected event by painting a background across the
whole widget: `EventWidget.selected { background: <selection> }`, two columns of
gap gutter through to the right edge of the feed, every row of the block.

That is the conventional list-selection idiom, and it is wrong for this list.
An event in the Watch is not a row you pick from a menu — it is a diff you
*read*. Selecting a file event and pressing `enter` is the gesture for reviewing
code, so the widest, longest-lived selection band in the app lands on exactly
the content that most needs a neutral substrate. Three costs followed:

- **the substrate moved when the reading started.** Monokai is calibrated
  against a near-black page. Reviewing an expanded diff on `selection`
  (`#2C3A4D`) meant reviewing it on a different, lighter surface than the
  identical code one row up, for no reason the reader chose;
- **the removed-row field had to yield** ([ADR 0012](0012-removed-code-sits-on-a-faint-red-field.md),
  bound 1). Two backgrounds collided, so the red field was dropped on the
  selected event — removing the scanning aid from the one event under review;
- **it marked the wrong thing.** The block already reads as one unit through
  the session lane running down its left edge. The band restated a boundary the
  layout was drawing anyway, at the cost of the surface inside it.

## Decision

**The selection is drawn into the session lane's bar column — col 8, one cell —
and nowhere else.** On the selected event that cell carries the `selection`
background and the bar thickens from `▏` to `▎`, on every row the event
occupies: header, diff body, and the continuation rows a fold produces. There is
no widget background at all; `EventWidget.selected` no longer exists as a style
rule.

Two geometric bounds are load-bearing, and both were learned by getting them
wrong first:

- **the bar grows in place.** `▏` and `▎` are both left-aligned eighth-blocks,
  so thickening moves no pixel of the line's left edge. The first attempt used
  `┃`, which a terminal centres in its cell: selecting an event slid the lane
  sideways, and the spine of the feed jumping is a worse defect than no mark at
  all. A selection may change a column's weight, never its position;
- **the rail has no holes.** It is one cell wide rather than two, and it is
  present on *every* row the event occupies — including fold continuations,
  which `sign_rows` used to blank (the promise in
  [ADR 0013](0013-a-body-line-folds-a-header-line-clips.md) that continuation
  rows keep the lane was true of the prompt path and false of the diff path).
  A broken rail reads as several blocks, not one, which is the opposite of what
  the mark is for. The Watch now passes its own gutter as `cont_gutter`, so
  wrapped code carries the lane whether the event is selected or not.

Three things fall out of the decision:

1. **The feed's surface is never repainted under an event.** Code is read on the
   terminal's own background whether it is selected or not, which is the
   substrate monokai is calibrated against.

2. **The removed-row field no longer yields to anything.** ADR 0012's first
   bound is void: the collision it resolved cannot happen, because selection and
   the field no longer share any cell. `sign_rows`' `wash` parameter existed
   solely to express that bound and is deleted — a knob with one caller and one
   reason to exist outlives its reason badly.

3. **The mark rides the channel that already means "this block".** The lane is
   the Watch's per-event spine; thickening it says *this one* in the same
   vocabulary the layout already speaks, rather than in a competing one.

The band is one column wide, so it reads as a rail rather than a wash, and the
glyph change (`▏` → `▎`) is the carrier that survives `NO_COLOR` — where the old
rule degraded to reverse video across the whole widget, which had the same
substrate problem in a louder form. At 16 colors the cell falls back to reverse
video; one inverted column is a rail, not a page.

## Consequences

- `selection`'s hex is unchanged but its job is not: it was calibrated as a wash
  under a whole block and is now a two-column rail. It is the value most likely
  to need a brightening step; the geometry, not the hue, is what this ADR fixes.
- ADR 0012's bound 1 and its "quieter than `selection`" reasoning (bound 5) are
  superseded here rather than edited there. `removed_bg` keeps its value: it is
  still deliberately quiet, now for its own sake and not out of deference.
- Fold continuation rows in a diff body now carry the gap gutter and the lane,
  selected or not — a pre-existing gap between ADR 0013's text and the code,
  found only because a rail with holes in it is visible where a missing thin bar
  was not.
- `standup <repo> diff` is untouched — it never had a selection — but it gains
  the guarantee that the Watch's diff rows and its own can no longer differ,
  since the one Watch-only bound on `sign_rows` is gone.
- The manual's selection paragraph, its color-role table and its removed-field
  section are rewritten; the "one background wash in the feed" claim is void.
