# 0015 — A repo can lead, and a commit is a scoped reference

Date: 2026-08-04
Status: accepted

## Context

The drill-down `standup tt` names three modified files and stops. There was no
way to read *what* changed in them. `standup watch` cannot help — the changes
are not recent, and the Watch is a monitor of what is happening now — and the
**Transcript** answers a different question (what the agent *said*, not what the
tree *is*). So a new view was needed, and with it two grammar questions the CLI
had never had to answer.

**Where does the repo go?** Every existing subcommand is verb-first: `standup
cost tt`, `standup watch tt`. But the new view is not a lens over the Scan
Universe the way `cost` is — a universe-wide diff is meaningless. It is the
drill-down at higher magnification, and CONTEXT.md already frames the drill-down
that way: *"the Triage Inbox at higher magnification"*. Reading it as
`standup tt diff` — "for this project, show me the diff" — matches how the view
is actually reached, one step deeper than the command you just ran.

Position 2 after a repo was **free**: `standup <repo>` accepted flags and
nothing else, so there was no ambiguity to resolve, only a rule to pick.

**How do you name a commit?** The new view has to reach committed change too,
and CONTEXT.md's **Session Handle** entry closes the obvious door:

> a Session Handle is an *address* and carries the colour; a **git commit hash**
> is only a *reference* … **A commit hash addresses nothing in Standup.**

That rule exists because the two short hexes must not be confusable. It is a
real constraint, not an accident: `standup diff 45e52476` is genuinely ambiguous
between a Session Handle and a commit hash, and resolving it by looking in both
stores would make the view's meaning depend on which one happened to contain
that prefix.

## Decision

**Two axes, and the position says which one you are on.**

- **Verb-first** is a lens over the whole **Scan Universe**: `standup cost`
  prices every project, and a repo argument merely *filters* it.
- **A second positional is a deeper magnification of one Repo Entry**:
  `standup` → `standup tt` → `standup tt diff`.

Both spellings are accepted for every free, read-only view (`diff`, `cost`,
`watch`, `session`), and they are the same code: `standup <repo> <view> <rest…>`
is rewritten to `standup <view> <repo> <rest…>` in `cli._normalize` before
anything is dispatched. One implementation, no parallel command tree.

`session` is rewritten onto a flag rather than the positional, because its
positional is a **Session Handle**, not a repo: `standup tt session` becomes
`standup session --in tt`, meaning "the newest session in tt". That generalises
what bare `standup session` already does for the current directory.

Where such a view is given **both** a repo and its own argument
(`standup tt session <handle>`), the repo is a **scope check**, not a conflict:
the handle is read, and an error says so if it is not that repo's session. The
two rejected alternatives are the informative ones — refusing the pair makes the
object-first spelling break the moment a handle is pasted into it, and ignoring
the repo answers about a different project without saying so. This is the same
rule `@<hash>` follows: naming a repo means the answer must come from it.

**`audit` is excluded from the object-first grammar.** Every view above is free
and read-only; `audit` spawns a four-Expert panel plus an Opus concluder against
the subscription. A paid action must name its target explicitly, so it stays
verb-first with a mandatory handle, and `standup tt audit` is a clear error
rather than a shorthand that spends money on a session you never named.

**A commit hash becomes addressable — but only inside a named Repo Entry, only
in the diff view, and only wearing its `@` sigil.** `standup tt diff @abc1234`.
This is a *narrowing* of the Session Handle rule, not a repeal: a commit hash
still addresses nothing globally. Three properties fall out of it:

- **the ambiguity is gone by construction.** A bare hex is a Session Handle
  everywhere, including here (`standup tt diff 45e5247` filters to that
  session's hunks). The sigil, not a lookup, decides.
- **rank is preserved.** A Session Handle is spoken bare; a commit has to be
  announced. That is exactly the ordering CONTEXT.md gives them — address over
  reference — expressed on the command line instead of in colour.
- **it is paste-ready.** `@abc1234` is what the drill-down already prints, so
  the thing you read is the thing you type. `@` needs no shell quoting.

**Hash prefixes only.** `@main` and `@HEAD~2` are rejected rather than passed to
`git rev-parse`: they are revision expressions, and the glossary has no word for
what they would mean here. A hash not found in the named repo is an **error**,
never a widened search — the repo was named, and answering about a different one
is the misdirection every other view is built to avoid.

`diff` and `d` join `c`/`w`/`s`/`a` in the reserved-handle set, so no project is
ever *displayed* with a handle the dispatcher would eat (ADR 0009).

## Consequences

- `standup <repo>` is no longer the deepest magnification, and it says so: with
  Active Work present, the drill-down prints one dim line naming the command
  after it. This is the same idiom that puts a **Session Handle** on every
  rollup title line — a view names the address of the next view.
- `show` is **removed**, not aliased. `session` is the noun the object-first
  grammar wants (`standup tt session` reads; `standup tt show` does not), and it
  is the glossary's own word. `standup show` now fails as an unknown Project
  Handle, which is the accepted cost of not keeping a permanent shadow command
  the help could never honestly list.
- The `s` alias moves to `session`, so the muscle memory that matters is intact.
- Two spellings of four commands is real surface, and the help carries the rule
  rather than just the list — the epilog explains *why* the orders differ, since
  a reader who cannot recover the rule will read the asymmetry as an accident.

## Alternatives considered

**`standup diff <repo>` only, verb-first like everything else.** Consistent, and
rejected: it makes `diff` a universe-wide lens it can never be, and it loses the
"one magnification deeper" reading that is how the view is actually reached.

**Object-first for `diff` alone.** Minimal, but leaves the CLI inconsistent for
no recoverable reason — `cost` before the repo, `diff` after it. Generalising
the grammar and stating the axis rule costs one function and explains both.

**Bare hex resolved by lookup** (try sessions, then commits). Fewer keystrokes,
and it revives precisely the confusability the rank rule exists to prevent.

**`--commit abc1234` / `--session 45e5`.** Unambiguous and boring; it throws
away a paste-ready sigil the tool already renders.
