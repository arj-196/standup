# 0013 — A body line folds, a header line clips

Date: 2026-08-03
Status: accepted

## Context

Every row in the **Watch** was a single terminal row. `_css()` said so in one
line — *nothing reflows — rows only truncate, so the grid never breaks* — and
the whole feed was built on it: `no_wrap=True` on every `Text`, `text-wrap:
nowrap; text-overflow: clip` on every widget. The invariant bought real things.
An event has a row count you can predict, the gap gutter and the session lane
sit at fixed columns on every row, the typing animation grows a body downward
by whole lines, and a resize repaints without relaying out.

It also meant that a line wider than the terminal was **unreadable past the
right edge**, with nothing saying so. That is tolerable for a header, which is a
summary Standup composes and can shorten honestly (`_one_line`, `▸ N lines`,
`… ▸ N more files`). It is not tolerable for a body: a diff body is not a
summary of the change, it *is* the change, and the Watch's purpose is to show
what an agent did to the code. A 200-character line — a long call, a URL, a
generated line, a `Bash` one-liner with a dozen paths — showed its first 80
characters and silently dropped the rest. `enter` did not help: expansion adds
*more lines*, never more of a line. The only remedy was leaving the Watch for
`standup show`, which is the wrong answer to "what did it just write".

## Decision

**Wrap is on by default, for body lines only.** A body line too long for the row
folds onto as many further rows as it needs; a header line still clips.

The split is the point. Headers are Standup's own prose about an event and are
truncated *visibly*, by code that knows what it is dropping. Bodies are the
agent's work, quoted, where truncation is a lie about the content. So the rule
is not "wrap everything" but: **the feed's own text may be shortened, the code
it is quoting may not.**

Folding is done by the widget, not by the terminal or by Textual's CSS. Every
row it emits is a whole row, built through `_prefix()` like any other, so the
gap gutter and the session lane are present on continuation rows and the
`no_wrap`/`clip` CSS is unchanged — with wrap off, it is still what clips.
That is what keeps the old invariant's *layout* guarantees while giving up its
row-count guarantee.

Four bounds:

1. **The `↳` carrier.** A continuation row shows a faint `↳` where the `+`/`−`
   would be. A fold is one source line, so the sign is stated once; and the
   glyph — not a colour — is what says "still the same line", which is what
   makes it survive `NO_COLOR`. Continuation rows start in the *same* code
   column, so indentation still reads.

2. **The wash spans folds.** ADR 0012's removed-code field is a rectangle from
   the sign column to `width`; it is painted per row, so a folded removed line
   stays part of that rectangle. The scanning aid survives the reflow.

3. **A hard row bound, counted not dropped.** One line may occupy at most
   `WRAP_ROWS` (40) rows; past that the tail is reported as `… +N chars`. A
   minified bundle is one line of 200 KB, and an unbounded fold would hand a
   single event the entire feed. The bound is a *stated* loss — the number is
   the rest of the line — which is the difference between this and the clipping
   it replaces.

4. **Word-aware, folding inside a token only when it must.** `rich`'s wrap with
   `overflow="fold"`, against a module-level measuring `Console` that never
   prints.

`w` toggles it, `standup watch --no-wrap` starts without it, and the status bar
names the *off* state (`no wrap — long lines clip`) because on is the default.
Clipping remains genuinely useful — it gives every event a fixed row count for
reading the shape of the last few minutes — so this is a change of default, not
a removal of a mode.

## Consequences

- `_css()`'s "nothing reflows" comment is void and rewritten, as is the
  manual's fixed-grid framing. A later reader finding folded rows next to a
  comment promising truncation would read one of the two as a bug.
- `_sign_row` emits *n* rows instead of one; `_fold` is the single place the
  decision lives, and the prompt body goes through it too (`PROMPT_COL` rather
  than `CODE_COL` — a prompt's text starts left of the code column).
- An event's height is no longer predictable from its line count. Nothing
  depended on that arithmetic: scrollback works in rows, the DOM cap counts
  *events*, and the typing animation counts *characters*.
- A long-line file event can now dominate the screen. That is the honest
  rendering of a change to a long-line file; `w` and `d` are both one keystroke
  away, and the troubleshooting table says so.
- The `Bash` body's "was it truncated" test now also asks whether the header row
  itself was wider than the terminal, so a command that `_one_line` left intact
  but the row could not show is still expandable.
- Headers keeping their clip is now a documented promise, not an accident: any
  future "wrap the header too" change has to argue with this record.
