# Standup — working notes for Claude

Read `CONTEXT.md` first: it is the glossary and the source of truth for every
domain term (Triage Inbox, Session, Loop, Audit, Notional Cost, …). Use its
vocabulary exactly; when a design decision is architectural, check `docs/adr/`
— [`docs/adr/README.md`](docs/adr/README.md) indexes it.

## ADRs are a rationale reference, not an append-only log

Each ADR states what is *currently* true and why, and is rewritten when a later
decision changes it. Three rules, all of them recoverable from
`docs/adr/README.md`, and all of them easy to break by accident:

- **numbering is contiguous, `0001..N`, no gaps.** Merging or deleting an ADR
  renumbers the set, and renumbering is safe *only* when every citation is
  rewritten in the same change — one mechanical pass over `src/`, `README.md`,
  `CONTEXT.md`, `TODO.md` and `docs/`. The check: the set of numbers referenced
  anywhere must equal the set of files present — run the pinned command in
  `docs/adr/README.md` § Numbering, which tolerates a citation wrapped across
  a line break. A line-anchored grep misses wrapped citations, and that is
  exactly how stale numbers survive a renumbering pass.
- **cite the section, not just the file.** The larger ADRs cover several
  decisions, so a bare `(ADR 0004)` in a Watch module tells a reader nothing:
  write `(ADR 0004 § the removed-row field)`. Section names are part of the
  contract — renaming a `##` heading means updating the citations to it.
- **write for agents, not for prose.** Dense and structured. Keep the decision,
  rationale not recoverable from the code, rejected alternatives with reasons,
  and accepted costs; drop narrative build-up and anything a docstring already
  says.
- **prefer extending an ADR to opening a new number**, and when a rule is
  reversed, move it to that ADR's *Tried and retracted* section instead of
  deleting it. The code still carries a retracted rule's shape, and a reader
  diffing against an older spec would otherwise read current behaviour as a bug.

## CLI help must stay complete

`standup -h` is the single place a user discovers what the tool can do. Any
change to the CLI surface — a new subcommand, a new flag, changed behavior of
an existing one — must update the help in the same change:

- the `epilog` subcommand list in `main()` (`src/standup/cli.py`) names every
  user-facing subcommand with a one-line description;
- each subcommand's `ArgumentParser` `description` explains what the view
  shows, in glossary terms;
- deliberately hidden entry points (e.g. `_brief`, the Stop-hook shim) stay
  out of the help — hidden is a decision, not an omission.

Before finishing any CLI change, run `standup -h` and each touched
`standup <sub> -h` and check the output still tells the whole truth.

## README and user manuals must stay current

The help is the discovery surface; `README.md` and the manuals in `docs/` are
the *explanation* surface. They are part of the change, not follow-up work — a
change that lands without them has left the docs lying:

- `README.md` lists every user-facing subcommand in its `Use` block and
  explains each view's output in glossary terms. A new subcommand, flag, or
  changed output belongs there in the same change.
- a subcommand with an interactive surface gets a manual in `docs/` (e.g.
  `docs/watch-manual.md` for `standup watch`), linked from the README. Every
  keybinding, mode, and on-screen marking is documented there — including the
  non-obvious behaviors (what implies a pause, what a filter binds to, which
  settings a hard bound can override).
- when you touch a keybinding, an event mark, a status line, an error message,
  or a default, update the manual's tables in the same change. Stale key
  tables are worse than no key tables.
- documentation follows `CONTEXT.md`'s vocabulary exactly, and marks claims as
  claims (a **Session Brief** objective is `~`-marked, never stated as fact).

Before finishing, re-read the sections you touched against the code you
changed — the docs must describe the behavior as shipped, not as intended.
