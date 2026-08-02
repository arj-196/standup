# 9. Derived Project Handles, not an alias registry

Date: 2026-08-02

## Status

Accepted

## Context

Addressing a project on the CLI cost as many characters as its name:
`standup cost ProjectManagement`. The three views that take a `<repo>`
argument — the Triage Inbox drill-down, `cost`, and `watch` — each hand-rolled
their own two-line matcher (exact name, else substring of the path), and all
three ended in `matches[0]`: a fragment fitting several projects silently
resolved to one of them, with no indication that a choice had been made. That
is the opposite of the honesty rule every other view follows, and the opposite
of what Session Handles already do (`resolve_handle` errors on an ambiguous
prefix, like `git`).

The obvious fix is an alias registry: a stored `alias -> project` map,
allocated once, so `pm` means ProjectManagement forever and a later
`ProjectManager` is handed `pm2`. It is also what a user asks for, because
permanence is the property that makes an alias worth memorising.

There is a middle path that looks like it gets permanence for free: derive the
allocation order from each project's *first-seen* time, so the older project
keeps `pm`. It does not survive contact with the environment. The Scan Universe
is derived from `~/.claude/projects`, and Claude Code prunes those logs after
`cleanupPeriodDays` (default 30). A project that goes quiet for a month leaves
the Scan Universe entirely and returns later as "new", so "older" is not a
durable fact and `pm` would silently rebind.

## Decision

No registry. A **Project Handle** is *derived* from the names currently in the
Scan Universe, by one shared resolver used by every view:

1. exact name (case-insensitive)
2. prefix of name
3. acronym — first letter of each camelCase/`-`/`_` word, **multi-word names
   only** (single-word names yield one-letter acronyms that collide constantly:
   `r` for both Recruitment and Robin)
4. substring of name
5. substring of path

The first tier with any match decides; **more than one match inside that tier
is an error listing the candidates**, replacing the silent `matches[0]`. Within
a tier, **live beats dead**: a project whose directory no longer exists drops
out if anything live matched, so a deleted worktree can never block a live
project, but stays reachable by full name.

The handle is *displayed* by underlining its letters inside the project's name
wherever a project is named in an overview — zero extra width, which the
never-wrap rule makes the scarcest resource. Every displayed candidate is
round-tripped through the resolver, so the display can never advertise a handle
that would error. Subcommand aliases (`c`, `w`, `s`, `a`) are reserved from the
*display* namespace, since `standup c` dispatches before any resolution.

A bare word is always a handle; a filesystem path is recognised by its shape
(`/` in it, or `.`/`..`, or a leading `~`) and resolved through git.

## Considered Options

- **Durable alias ledger** in `~/.standup/` (a third tenant beside Briefs and
  Audits) — rejected. It buys stability across log pruning, which nothing else
  can, and it is the only way to deliver `pm2`. But it collides with
  CONTEXT.md's Scan Universe rule (*"never configured by hand"*, `_Avoid_:
  registry`); it needs a lifecycle nothing else in the tool has (when is a dead
  project's handle reclaimed?); and `pm2` is a *worse* address than an error —
  six months on, "which one is `pm2`?" is a lookup, and a lookup costs more
  than the two characters it saved. An ambiguity error is self-documenting.
  Measured against the real Scan Universe (20 projects), acronym-plus-prefix
  produces **zero** collisions and reaches every project in two characters, so
  the registry would be machinery for a case that has not occurred.
- **First-seen ordering as a stability source** — rejected: 30-day log pruning
  makes "first seen" non-durable, and the failure mode is a *silent* rebind.
- **Unique-prefix dispatch for subcommands** (`c`, `co`, `cos` → `cost`),
  instead of a fixed alias table — rejected: self-maintaining, but the day a
  subcommand starting with `c` is added, a memorised keystroke turns into an
  error. A fixed table means each letter is owned forever. This decision paid
  off immediately: `completion` was added in the same change.

## Consequences

- Handles are not stable by construction. A new colliding project can make a
  handle grow a letter; the display always shows what currently resolves, so
  the screen and the CLI never disagree. This is the accepted price of having
  no stored state.
- `standup <repo>` for a repo addressed by a bare `c`/`w`/`s`/`a` is no longer
  reachable at the root — those dispatch to subcommands. Such projects are
  displayed with a two-character handle instead.
- A bare word no longer resolves as a directory in `watch` (it did, by luck,
  whenever a same-named folder sat in the cwd). Forcing the path reading needs
  `./name`. In exchange, a scratch directory can never shadow a project.
- Ambiguity is now an error where it used to be a silent pick. That is strictly
  more typing for a fragment that fits several projects, and strictly more
  honest.
