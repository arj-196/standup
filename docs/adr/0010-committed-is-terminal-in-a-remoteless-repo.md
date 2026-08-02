# 10. Committed is terminal in a Remoteless Repo

Date: 2026-08-02

## Status

Accepted

## Context

Standup showed `unpushed · 8 commits on main` for a repo with **no remote
configured at all**. There is nothing to push to, so the label asserted a
pending action that could not exist. Worse, the count was an artifact: `_unpushed`
falls back to `HEAD --not --remotes` when a branch has no upstream, and with zero
remotes `--remotes` matches nothing — so "unpushed" silently meant *the entire
history*, capped at 30.

The codebase had already made this exact judgement once and only half-applied it:
`_done` bailed out with `[]` when the repo had no remote, on the reasoning that
nothing can be *pushed* without a remote. The result was a repo whose committed
work was permanently stuck in a Needs-Decision tier it could never leave, and
absent from the retrospective entirely.

The tempting fix — suppress the unpushed line — is wrong on its own. A
Needs-Decision Item that simply vanishes leaves the repo invisible unless dirty,
and swaps a false label for silence about real work.

## Decision

Terminal state is **repo-relative**. For a repo with a remote, work is done when
it is *pushed*. For a **Remoteless Repo** — `git remote` empty — work is done
when it is *committed*, because committing is the last action available.

Concretely:

- `_unpushed` returns `[]` for a Remoteless Repo. It has no Unpushed tier.
- `_done`'s remote check **inverts** rather than short-circuiting: it selects
  `--branches` instead of `--remotes`. `--branches` is the local mirror of
  `--remotes` — "everywhere this repo's work has landed". The Recent Window and
  the `user.email` filter apply identically in both cases.
- The rendered tier is renamed `PUSHED` → `DONE`, because "pushed" is now true of
  only one of the two paths into it. Each line keeps the precise verb:
  `N commits pushed`, or `N commits committed · no remote`.
- The drill-down header carries `· no remote` unconditionally, so a Remoteless
  Repo says so even when clean and even when its commits have aged out of the
  window. The **Triage Inbox** stays silent about it.

The classification is repo-level, using the existing `_has_remote`. A branch with
no upstream in a repo that *does* have a remote is genuinely pending a push and
keeps the existing `HEAD --not --remotes` fallback.

## Consequences

- A Remoteless Repo's Needs-Decision surface collapses to Active Work alone. Its
  commits move from **ageless** (shown forever) to **windowed** (7d, `-a` only),
  then disappear. This is intended: not adding a remote is a decision, and the
  inbox should not re-litigate it daily. The cost is real — committed work in
  such a repo is unreviewable after the window closes.
- Adding a remote flips a repo back: its commits become Unpushed and ageless
  again, which is correct, since a push is now possible and pending.
- Terminal-state work in a Remoteless Repo inherits the `user.email` filter it
  never had as Unpushed. Justified for uniformity (one rule: Done is *my* recent
  work) and because `git init` over a template or a vendored import carries a
  stranger's commits. The failure mode is silent loss if a repo's configured
  email diverges from its commits' author — pre-existing for the pushed tier,
  now reaching repos where Done is the *only* place they could appear.
- `standup watch` is deliberately unchanged. Its `_unpushed_count` is internal,
  never displayed, and drives only push-detection — which already emits nothing
  for a Remoteless Repo. Special-casing it would forfeit a true event: add a
  remote and push mid-watch, the count drops and `pushed N commits` fires
  correctly.
- Rejected: a **branch-level** rule (no upstream ⇒ done). It would swallow
  unpushed scratch branches in repos that have a remote — exactly the work that
  most needs a decision.
- Rejected: keeping `PUSHED` with a `no remote` marker. The header would still
  assert something untrue of the rows beneath it; trading one false label for
  another was the thing this ADR set out to stop.
- The term "local-only" was deliberately **not** used for this concept: the
  glossary already spends it on unpushed commits in a repo that *has* a remote.
  `no remote` is also the verifiable git fact rather than an inference about
  intent — the same discipline as `~` on Attribution claims and the refusal to
  estimate Real Spend.
