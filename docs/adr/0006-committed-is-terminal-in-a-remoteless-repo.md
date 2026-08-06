# 0006 — Committed is terminal in a Remoteless Repo

Date: 2026-08-02

## Context

Standup showed `unpushed · 8 commits on main` for a repo with **no remote at
all** — a label asserting a pending action that cannot exist. The count was an
artifact too: the unpushed query falls back to "everything not on a remote" when
a branch has no upstream, and with zero remotes that matches nothing, so
"unpushed" silently meant *the entire history*, capped at 30.

The codebase had already made this judgement once and half-applied it: the Done
query bailed out empty without a remote, on the reasoning that nothing can be
*pushed*. So a Remoteless Repo's committed work was stuck permanently in a
Needs-Decision tier it could never leave, and absent from the retrospective.

Merely suppressing the unpushed line is wrong on its own: a Needs-Decision Item
that vanishes leaves the repo invisible unless dirty, swapping a false label for
silence about real work.

## Decision

**Terminal state is repo-relative.** With a remote, work is done when *pushed*.
For a **Remoteless Repo** (`git remote` empty), work is done when *committed*,
because committing is the last action available.

- A Remoteless Repo has **no Unpushed tier**.
- The Done query **inverts** its remote check rather than short-circuiting,
  selecting `--branches` instead of `--remotes` — the local mirror of
  "everywhere this repo's work has landed". Recent Window and `user.email`
  filter apply identically either way.
- The tier is `DONE`, not `PUSHED`, since "pushed" is true of only one path into
  it. Each line keeps the precise verb: `N commits pushed`, or `N commits
  committed · no remote`.
- The drill-down header carries `· no remote` **unconditionally**, so the repo
  says so even when clean and even after its commits age out. The Triage Inbox
  stays silent about it.

The classification is **repo-level**. A branch with no upstream in a repo that
*does* have a remote is genuinely pending a push and keeps the old fallback.

## Consequences

- **A Remoteless Repo's Needs-Decision surface collapses to Active Work alone.**
  Its commits move from ageless to windowed (7d, `-a` only), then disappear.
  Intended: not adding a remote is a decision, and the inbox should not
  re-litigate it daily. **The cost is real — committed work there is
  unreviewable once the window closes.**
- Adding a remote flips it back: commits become Unpushed and ageless again,
  correctly, since a push is now possible and pending.
- Terminal work in a Remoteless Repo inherits the `user.email` filter it never
  had as Unpushed. Justified by uniformity (*Done is my recent work*) and because
  `git init` over a template or a vendored import carries a stranger's commits.
  Failure mode: silent loss if a repo's configured email diverges from its
  commits' author — pre-existing for the pushed tier, but now reaching repos
  where Done is the *only* place they could appear.
- **`standup watch` is deliberately unchanged.** Its unpushed count is internal,
  never displayed, and drives only push-detection, which already emits nothing
  for a Remoteless Repo. Special-casing it would forfeit a true event: add a
  remote and push mid-watch, and `pushed N commits` fires correctly.
- "local-only" was deliberately **not** used: the glossary spends it on unpushed
  commits in a repo that *has* a remote. `no remote` is also the verifiable git
  fact rather than an inference about intent — the same discipline as `~` on
  Attribution claims and the refusal to estimate Real Spend.

## Alternatives considered

- **A branch-level rule** (no upstream ⇒ done) — would swallow unpushed scratch
  branches in repos that have a remote, exactly the work most needing a decision.
- **Keeping `PUSHED` with a `no remote` marker** — the header would still assert
  something untrue of the rows beneath it, trading one false label for another.
