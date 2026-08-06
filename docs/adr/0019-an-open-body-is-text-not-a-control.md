# 0019 — An open body is text, not a control

Date: 2026-08-06
Status: accepted

## Context

A click on a Feed Event selects it and toggles its expansion in one gesture
(the manual's "click to expand, click again to collapse"). The whole widget was
the target: header row, preview rows, and — once open — every row of the body.

That is the right target for a *closed* entry and the wrong one for an open one.
What you do with an expanded entry is **read** it, and reading is done with the
mouse too: you drag through a paragraph of a prompt to copy it, double-click to
take the whole entry, click a URL so the terminal opens it. Every one of those
gestures ends in a `Click`, and every one of them collapsed the thing being
read. The body could be opened or interacted with, never both.

Two mechanics make it worse than a single misfire. Textual synthesises a `Click`
whenever the button goes down and up on the same *widget* — the cells may be far
apart, so a **drag** through a body is delivered as a click on it, carrying the
cell it was *released* on. And a double click sends two clicks: `chain=1` then
`chain=2`, the second of which is Textual selecting the widget's text.

## Decision

**A click acts on an entry while it is closed; once it is open, only the entry's
own furniture does — its header row and its left rail.** And **a gesture that was
about reading the text is not a click on anything**: a drag (pressed on one cell,
released on another) and any click with `chain > 1` are discarded at the same
seam.

The state split is the point. A closed entry is entirely the feed's own prose
about an event — header plus a few preview rows — so pressing anywhere on it
means "open this". An open entry's body is the code, command or prompt you asked
to see, quoted verbatim; it belongs to the reader, and the Watch takes no gesture
inside it.

The furniture is the part of an open entry that is still the Watch talking: the
header row, and the left rail — the gap gutter and session lane of cols 1–9,
which ADR 0013 already repeats on every continuation row and ADR 0018 already
makes the block's spine. That repetition is what makes it the right handle. A
body can be taller than the screen, and then the header is off the top and a
click-to-collapse that only lived there would be unreachable without scrolling
back to it; the rail is beside *every* line of the body, in the same columns on
each, so folding an open entry is always one click to the left of whatever line
you stopped reading on. `enter` on the selected entry does the same thing
from the keyboard.

The guards live on `WatchApp.click_select` and `WatchApp.click_vitals_row`,
beside the refocus guard of ADR 0014, for the reason that ADR gave: the widgets
only forward, so the app methods are the one place every mouse gesture passes
through. Telling a press from a drag needs the cell the button went *down* on,
which a `Click` does not carry; `WatchApp.on_mouse_down` stamps it.

## Consequences

- Inside an open body, drag-select works, and `ctrl+c` copies the selection
  through the terminal's clipboard escape (Textual's binding; Apple Terminal
  does not support it). A double click selects the whole entry.
- A click on a URL in an open body is no longer swallowed — whether it *opens*
  is the terminal's business (`⌘-click` in iTerm2, Ghostty, kitty). The Watch
  does not emit hyperlinks; it only stops competing for the click.
- Collapsing an open entry costs a click on its header, a click on its rail, or
  a press of `enter`. Clicking the body *between* the rail and the right edge
  now does nothing at all, which is a deliberate silence: any other answer
  (select it, scroll to it) would be a state change made by a gesture that was
  aimed at text.
- The rail is a fixed nine columns (`RAIL_COLS`), which is `_prefix`'s width and
  therefore true of every row the widget draws — header, body, folded
  continuation, `… ▸ N more`. If the left columns are ever re-sized, the handle
  moves with them only if that constant moves too.
- The drag rule also covers the vitals band, where a drag across a session row
  used to toggle a filter on release.
- A double click on a *closed* entry opens it — its first click is a real click
  on a control. Only the second is discarded.
- The rule is stated in terms of the widget's own row 0, which is exactly the
  header because header lines clip to one row and never fold (ADR 0013). A body
  that folds cannot push the handle out from under the pointer.
