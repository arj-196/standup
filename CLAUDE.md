# Standup — working notes for Claude

Read `CONTEXT.md` first: it is the glossary and the source of truth for every
domain term (Triage Inbox, Session, Loop, Audit, Notional Cost, …). Use its
vocabulary exactly; when a design decision is architectural, check `docs/adr/`.

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
