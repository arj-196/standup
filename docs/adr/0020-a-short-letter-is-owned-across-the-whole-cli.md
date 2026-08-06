# 20. A short letter is owned across the whole CLI

Date: 2026-08-06

## Status

Accepted

## Context

Only two options had short forms: `-a`/`--all` on the inbox, and `-U`/`--context`
on `diff`, the latter borrowed wholesale from `git diff -U6`. Everything else
cost its full word, including `--since`, which the two most-typed views take.

Giving every option a letter is easy right up to the first collision, and there
are two. `diff --stat` and `--since` both want `s`; `session --raw` and
`audit --refresh` both want `r`. Which one wins is not really the question —
the question is whether a letter *has* a single winner at all.

git says no: `-a` is `--all` in `git add`, `--amend` in `git commit`,
`--annotate` in `git branch`. Each subcommand is its own namespace, so every
flag gets the obvious letter and the meaning is read from the word before it.
That is the well-trodden path, and it scales without ever forcing a compromise.

It also sits badly beside ADR 0009. Subcommand aliases here are a *fixed table*
precisely so that each letter is owned forever and a later subcommand can never
quietly steal one — chosen over unique-prefix dispatch, which is
self-maintaining but turns a memorised keystroke into an error the day the
namespace grows. Per-subcommand flag letters reintroduce exactly what that
decision rejected, one level down: the same keystroke, a different meaning,
decided by context the typist has to hold in their head.

## Decision

**One letter, one meaning, across every parser.** A letter is owned by an
option globally, so a flag that loses a fight gets a different letter or none:

```
-a --all       -s --since     -j --json      -q --quiet     -i --in
-t --thinking  -r --raw       -n --stat      -U --context
-P --no-pager  -W --no-wrap
```

Three rules decide the rest, and each one is a refusal to spend something:

1. **An uppercase *boolean* is the negation of its lowercase.** `-P` is
   `--no-pager`, `-W` is `--no-wrap`, and lowercase `-p`/`-w` stay permanently
   unclaimed, so a future affirmative `--pager`/`--wrap` can have the honest
   letter instead of inheriting a lie. `-U` takes a *value*, so it is not in
   this class and is not an exception to it.
2. **Dashed and undashed are separate namespaces.** `-a` is `--all` while a
   bare `a` is `audit`; the dash is the separator, so this table and ADR 0009's
   alias table can never contend. `-s` is `--since` even though `s` is
   `session`.
3. **A flag that spends money gets no letter.** `audit --refresh` re-runs the
   Expert Panel against your subscription, so it costs the whole word — the
   rule that already leaves `install`/`uninstall` unaliased and keeps `audit`
   out of the two-word object-first grammar. `--projects-dir` gets none either:
   it is a hidden entry point, and hidden is a decision, not an omission.

The losers of the two fights follow from this: `--stat` takes `-n` (per-file
*numbers*, git's own `--numstat` spelling for the same idea, and clear of the
`-U`/`--context` that sits beside it in `diff`), and `--refresh` takes nothing.

One consequence needed fixing in the same change. `_normalize` bailed the
moment `argv[0]` started with `-`, so `standup --all st diff` errored with
`unrecognized arguments: diff` — a form nobody typed until the letters made
`standup -a st diff` natural. It now finds the repo/view pair by **positional
scan**: the first view name whose preceding token is not an option. That keeps
the rewrite ignorant of which flags take a value, which is the property that
makes ADR 0015's grammar one small function rather than a shadow parser.

## Consequences

- Leading options belong to the parser that runs, because there is no other
  parser to give them to. `standup -j st cost` works; `standup -a st diff`
  fails on `-a`, which `diff` has never had. The complaint at least names the
  flag now instead of the view.
- The positional scan misreads input that was already an error: `standup -s 3d
  diff` names no repo, so `3d` is read as one. Both spellings error, with
  different messages. The alternative — a table of every value-taking flag
  across six parsers — buys a better message for malformed input at the cost
  of a silent misparse the day someone adds a flag and forgets the table.
- A flag added later may find its obvious letter taken by a different
  subcommand's option. That is the cost of the rule, and the same cost ADR 0009
  accepted: it surfaces at authoring time, as a naming decision, rather than at
  typing time, as a surprise.
- `--stat` is reachable as `-n`, which nobody guesses cold. It is documented in
  the README's letter table and in `standup diff -h`; the mnemonic is
  `--numstat`.
