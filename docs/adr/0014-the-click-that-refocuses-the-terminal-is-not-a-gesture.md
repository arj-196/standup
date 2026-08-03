# 0014 — The click that refocuses the terminal is not a gesture

Date: 2026-08-03
Status: accepted

## Context

The **Watch** is a monitor: it runs in a terminal you leave and come back to
while an agent works. Coming back is usually a *mouse* act — the window is
behind a browser, so you click it to bring it forward.

macOS terminals deliver that click through to the application. Textual turns it
into a `Click`, `EventWidget.on_click` forwards it, and `click_select` does what
a click on the feed means: select the event under the pointer and expand it. So
returning to the Watch expanded a **random** event — whichever one the pointer
happened to be resting over, which is unrelated to what you came back to read.
Worse, it scrolls: selecting an event releases the bottom anchor, so a glance at
the live feed became a scrolled view of an arbitrary diff, and you had to press
`G` to undo an interaction you never made.

The same click on the vitals header would toggle a **session filter**, hiding
other sessions' work — a state change you did not ask for and might not notice.

Two facts make the fix available. Terminals implement focus reporting
(`CSI ?1004h`) and Textual's driver enables it unconditionally, surfacing
`AppFocus`/`AppBlur`. And the focus report arrives in the *same read burst* as
the click that caused it — the window is focused first, then the click is
dispatched.

## Decision

**A click that arrives with the terminal regaining focus is a window gesture and
is discarded.** On the blurred → focused transition the Watch stamps a monotonic
clock; `_refocus_click()` is true for `REFOCUS_GRACE` (0.35s) after it, and both
click seams — `click_select` and `click_vitals_row` — return early while it is.

The guard sits on the two `WatchApp` methods, not in the widget handlers. The
widgets only forward, so the app methods are the one place every mouse gesture
passes through, and a future clickable surface gets the behavior by using that
seam rather than by remembering this decision.

Three properties worth stating:

1. **A window, not a flag.** "Swallow the next click" would arm indefinitely: focus
   the terminal with `⌘-Tab` and your first *intended* click, a minute later,
   would vanish. Tying the discard to a 0.35s window ties it to the click that
   actually carried the focus change. No human clicks a target 350ms after
   deciding to switch windows, and a `⌘-Tab` return has nothing to discard.

2. **Only a real transition arms it.** The stamp is taken only when the app was
   blurred. A terminal that re-reports focus while already focused cannot make
   the Watch deaf to clicks.

3. **Degrading is doing nothing.** Apple Terminal does not report focus, so
   `AppFocus` never arrives, the stamp stays at zero, and every click counts —
   exactly today's behavior. The guard cannot make a terminal worse than it was;
   it can only fail to help.

## Consequences

- The Watch now has state that is *about the terminal window* rather than about
  the stream (`_focused`). It is the first such state, and it is deliberately
  not shown: there is no on-screen "unfocused" marking, because the Watch has no
  modal states and a window's focus is not the Watch's business to display.
- A click genuinely intended within 0.35s of returning by keyboard-then-mouse is
  dropped. The cost of that miss is one extra click; the cost of the opposite
  error is an expanded random event and a released anchor.
- The rule assumes `FocusIn` precedes the mouse report. That ordering is what
  terminals do but is not promised by the spec. If a terminal is ever found to
  reverse it, the forward-looking window simply never fires — the failure mode is
  the old behavior, not a new one.
- The manual documents the discard, including the `⌘-Tab` alternative and the
  terminal-support caveat. A dropped click that nothing explains reads as a bug.
