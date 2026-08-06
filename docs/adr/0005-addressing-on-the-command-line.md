# 0005 — Addressing on the command line

Date: 2026-08-06

What word addresses a project, where it sits relative to the verb, and which
letters are spoken for.

## Project Handles are derived, not registered

Addressing a project cost as many characters as its name, and the three views
taking a `<repo>` argument each hand-rolled a matcher ending in `matches[0]` —
**a fragment fitting several projects silently resolved to one**, with no sign a
choice had been made. That is the opposite of what Session Handles already do.

The obvious fix is an alias registry: a stored `alias -> project` map so `pm`
means ProjectManagement forever and a later `ProjectManager` gets `pm2`.
Permanence is what makes an alias worth memorising.

Deriving allocation order from *first-seen* time looks like permanence for free,
and does not survive the environment: the Scan Universe comes from
`~/.claude/projects`, which Claude Code prunes after `cleanupPeriodDays`
(default 30). **A project quiet for a month leaves the Scan Universe and returns
as "new"**, so "older" is not durable and `pm` would silently rebind.

**No registry.** A **Project Handle** is *derived* from the names currently in
the Scan Universe, by one shared resolver used by every view:

1. exact name (case-insensitive)
2. prefix of name
3. acronym — first letter of each camelCase/`-`/`_` word, **multi-word names
   only** (single-word names give one-letter acronyms that collide constantly:
   `r` for both Recruitment and Robin)
4. substring of name
5. substring of path

**The first tier with any match decides; more than one match inside that tier is
an error listing the candidates**, replacing the silent `matches[0]`. Within a
tier, **live beats dead**: a project whose directory is gone drops out if
anything live matched, but stays reachable by full name.

The handle is *displayed* by underlining its letters inside the project's name —
zero extra width, which the never-wrap rule makes the scarcest resource. Every
displayed candidate is round-tripped through the resolver, so **the display can
never advertise a handle that would error**.

A bare word is always a handle; a path is recognised by shape (`/` in it, or
`.`/`..`, or a leading `~`) and resolved through git.

Costs: handles are not stable by construction — a new colliding project can make
one grow a letter. The display always shows what currently resolves, so screen
and CLI never disagree; that is the price of no stored state. A bare word also no
longer resolves as a directory (it did by luck whenever a same-named folder sat
in the cwd) — forcing the path reading needs `./name`, and in exchange a scratch
directory can never shadow a project.

## Two grammars

The Attributed Diff forced the question. Every existing subcommand is verb-first,
but a universe-wide diff is meaningless: the view is the drill-down at higher
magnification, so `standup tt diff` — "for this project, show me the diff" —
matches how it is actually reached. Position 2 after a repo was **free**:
`standup <repo>` accepted flags and nothing else.

- **Verb-first is a lens over the whole Scan Universe.** `standup cost` prices
  every project; a repo argument merely *filters*.
- **A second positional is a deeper magnification of one Repo Entry.**
  `standup` → `standup tt` → `standup tt diff`.

Both spellings work for every free, read-only view (`diff`, `cost`, `watch`,
`session`) and are the same code: the object-first form is **rewritten to
verb-first before dispatch**. One implementation, no parallel command tree.

`session` is rewritten onto a flag rather than the positional, because its
positional is a **Session Handle**: `standup tt session` means "the newest
session in tt", generalising what bare `standup session` does for the cwd.

Given **both** a repo and its own argument, the repo is a **scope check**: the
handle is read, and an error says so if it is not that repo's session. The
rejected alternatives are the informative ones — refusing the pair breaks the
object-first spelling the moment a handle is pasted in, and ignoring the repo
answers about a different project without saying so.

**`audit` is excluded.** Every view above is free and read-only; `audit` spawns a
four-Expert panel plus an Opus concluder against the subscription. **A paid
action must name its target explicitly**, so it stays verb-first with a mandatory
handle, and `standup tt audit` is a clear error rather than a shorthand that
spends money on a session you never named.

Two spellings of four commands is real surface, so the help carries the *rule*,
not just the list — a reader who cannot recover the rule reads the asymmetry as
an accident.

`standup <repo>` is no longer the deepest magnification and says so: with Active
Work present the drill-down prints one dim line naming the next command — the
same idiom that puts a Session Handle on every rollup title line.

`show` was **removed**, not aliased. `session` is the noun the object-first
grammar wants (`standup tt session` reads; `standup tt show` does not) and is the
glossary's word. Accepted cost: `standup show` now fails as an unknown Project
Handle, rather than keeping a shadow command the help could never honestly list.
The `s` alias moved with it.

## A commit hash is a scoped reference

CONTEXT.md closes the obvious door: a Session Handle is an *address*, a commit
hash only a *reference* — **a commit hash addresses nothing in Standup**. The two
short hexes must not be confusable: `standup diff 45e52476` is genuinely
ambiguous, and resolving it by looking in both stores would make the view's
meaning depend on which store happened to hold that prefix.

**A commit hash becomes addressable — only inside a named Repo Entry, only in the
diff view, and only wearing its `@` sigil**: `standup tt diff @abc1234`. A
narrowing, not a repeal:

- **The ambiguity is gone by construction.** A bare hex is a Session Handle
  everywhere, here included (`standup tt diff 45e5247` filters to that session's
  hunks). The sigil, not a lookup, decides.
- **Rank is preserved.** A handle is spoken bare; a commit must be announced —
  address over reference, expressed on the command line instead of in colour.
- **It is paste-ready.** `@abc1234` is what the drill-down prints, and `@` needs
  no shell quoting.

**Hash prefixes only.** `@main` and `@HEAD~2` are rejected rather than passed to
`git rev-parse`: they are revision expressions and the glossary has no word for
what they would mean. A hash not found in the named repo is an **error**, never a
widened search — the repo was named, and answering about a different one is the
misdirection every other view avoids.

## Short option letters

Only `-a`/`--all` and `-U`/`--context` had short forms; everything else cost its
full word, including `--since`, which the two most-typed views take. Adding
letters is easy until the first collision, and there are two: `diff --stat` and
`--since` both want `s`; `session --raw` and `audit --refresh` both want `r`. The
real question is whether a letter *has* a single winner at all.

**One letter, one meaning, across every parser.** A letter is owned globally, so
a flag that loses gets a different letter or none:

```
-a --all       -s --since     -j --json      -q --quiet     -i --in
-t --thinking  -r --raw       -n --stat      -U --context
-P --no-pager  -W --no-wrap
```

Three rules, each a refusal to spend something:

1. **An uppercase *boolean* is the negation of its lowercase.** `-P` is
   `--no-pager`, `-W` is `--no-wrap`, and `-p`/`-w` stay permanently unclaimed so
   a future affirmative `--pager`/`--wrap` can have the honest letter instead of
   inheriting a lie. `-U` takes a *value*, so it is not in this class and not an
   exception to it.
2. **Dashed and undashed are separate namespaces.** `-a` is `--all` while bare
   `a` is `audit`; `-s` is `--since` even though `s` is `session`.
3. **A flag that spends money gets no letter.** `audit --refresh` re-runs the
   Expert Panel against your subscription, so it costs the whole word — the same
   rule leaving `install`/`uninstall` unaliased and `audit` out of the
   object-first grammar. `--projects-dir` gets none either: it is a hidden entry
   point, and hidden is a decision, not an omission.

The two fights resolve: `--stat` takes `-n` (per-file *numbers*, git's own
`--numstat` spelling, and clear of the `-U` beside it in `diff`), `--refresh`
takes nothing.

Consequences:
- **Leading options belong to the parser that runs**, since there is no other to
  give them to. `standup -j st cost` works; `standup -a st diff` fails on `-a`,
  which `diff` never had — but the complaint now names the flag, not the view.
- **A later flag may find its obvious letter taken by another subcommand's
  option.** That cost surfaces at authoring time as a naming decision, rather
  than at typing time as a surprise.
- `-n` is unguessable cold. Documented in the README letter table and `standup
  diff -h`; the mnemonic is `--numstat`.

## The reserved-letter rule

Three namespaces contend for single letters. One sentence: **the dash is the
separator, and inside the undashed namespace a subcommand alias always wins.**

- **Subcommand aliases are a fixed table** — `c` `w` `s` `a` `d` — so each letter
  is owned forever and a future subcommand cannot quietly steal one. `install`
  and `uninstall` are deliberately unaliased: a mistyped letter must not rip out
  the machine-wide Stop hook.
- **Those letters are reserved out of the Project Handle *display* namespace**,
  since `standup c` dispatches before any resolution. Such a project is displayed
  with a two-character handle and is not reachable at the root by one letter.
- **Option letters are the dashed namespace** and never contend with either.

The argument rewrite finds the repo/view pair by **positional scan** — the first
view name whose preceding token is not an option — rather than consulting a table
of value-taking flags. The letters forced this: the rewrite used to bail the
moment the first argument started with `-`, so `standup --all st diff` errored
with `unrecognized arguments: diff`, a form nobody typed until `standup -a st
diff` became natural.

That ignorance is the point. The alternative is a table of every value-taking
flag across six parsers, kept in sync forever, and a silent misparse the day
someone forgets. It keeps the two-grammar rewrite one small function rather than
a shadow parser. The cost falls only on input already an error: `standup -s 3d
diff` names no repo, so `3d` is read as one.

## Alternatives considered

- **Per-subcommand flag namespaces, the way git does it** — `-a` is `--all` in
  `git add`, `--amend` in `git commit`, `--annotate` in `git branch`. Every flag
  gets the obvious letter and the meaning is read from the word before it; it
  scales without ever forcing a compromise. Rejected because it sits badly beside
  the alias table, which is fixed precisely so each letter is owned forever:
  per-subcommand letters reintroduce one level down exactly what that rejected —
  the same keystroke, a different meaning, decided by context the typist must
  hold in their head.
- **Unique-prefix dispatch for subcommands** (`c`, `co`, `cos` → `cost`) —
  self-maintaining, but the day a subcommand starting with `c` is added a
  memorised keystroke becomes an error. This paid off immediately: `completion`
  was added in the same change.
- **A durable alias ledger** in `~/.standup/` — buys stability across log
  pruning, which nothing else can, and is the only way to deliver `pm2`. But it
  collides with the Scan Universe rule (*never configured by hand*), needs a
  lifecycle nothing else has (when is a dead project's handle reclaimed?), and
  **`pm2` is a *worse* address than an error**: six months on, "which one is
  `pm2`?" is a lookup, and a lookup costs more than the two characters it saved.
  Measured against the real 20-project Scan Universe, acronym-plus-prefix gives
  **zero** collisions and reaches every project in two characters.
- **First-seen ordering for stability** — 30-day log pruning makes it
  non-durable, and the failure mode is a *silent* rebind.
- **`standup diff <repo>` only** — consistent, but makes `diff` a universe-wide
  lens it can never be and loses the magnification reading.
- **Object-first for `diff` alone** — leaves the CLI inconsistent for no
  recoverable reason.
- **A bare hex resolved by lookup** — revives the confusability the rank rule
  prevents.
- **`--commit abc1234` / `--session 45e5`** — unambiguous and boring; throws away
  a paste-ready sigil the tool already renders.
