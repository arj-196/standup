"""Project Handles — the short, derived address of a project on the CLI.

A Project Handle is *derived*, never registered: it falls out of the names
currently in the Scan Universe, so it can grow a letter when a colliding
project appears (CONTEXT.md → Project Handle; ADR 0009). Resolution is tiered
and **errors on ambiguity** — the `git` idiom Standup already uses for Session
Handles — so a fragment that fits two projects never silently picks one.

One resolver serves the Triage Inbox, `cost`, and `watch`, so a handle means
the same thing in every view.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Sequence

# Subcommand names and their aliases. `standup c` always dispatches to `cost`,
# so a project may never *display* a handle the dispatcher would eat — it is
# bumped to the next length instead. Reservation is a display rule only:
# `standup cost c` resolves normally, because there `c` is an argument.
RESERVED = frozenset({
    "c", "w", "s", "a",
    "cost", "watch", "show", "audit", "install", "uninstall", "completion",
})

_WORD = re.compile(r"[A-Za-z0-9]+")
_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+")


class HandleError(Exception):
    """No project matches the fragment, or more than one does."""


@dataclass(frozen=True)
class Target:
    """One addressable project: its display name and its checkout path."""
    name: str
    path: str


def looks_like_path(arg: str) -> bool:
    """True for a filesystem path, false for a bare handle.

    A bare word is *always* a handle, never whatever directory happens to sit
    in the cwd — otherwise a scratch dir named `pm` would silently shadow
    ProjectManagement.
    """
    return "/" in arg or arg in (".", "..") or arg.startswith("~")


def components(name: str) -> list[tuple[int, str]]:
    """(offset, word) for each word in a name, splitting separators and camelCase."""
    out: list[tuple[int, str]] = []
    for w in _WORD.finditer(name):
        for p in _PART.finditer(w.group()):
            out.append((w.start() + p.start(), p.group()))
    return out


def acronym(name: str) -> tuple[str, tuple[int, ...]] | None:
    """First letter of each non-numeric word — multi-word names only.

    Single-word names would yield one-letter acronyms that collide constantly
    (`r` for both Recruitment and Robin); they get a prefix handle instead.
    Returns the handle and the offsets it occupies in the name.
    """
    parts = [(i, t) for i, t in components(name) if not t.isdigit()]
    if len(parts) < 2:
        return None
    return "".join(t[0] for _, t in parts).lower(), tuple(i for i, _ in parts)


def _live(t: Target) -> bool:
    return os.path.isdir(t.path)


def _narrow(matches: list[Target]) -> list[Target]:
    """Live beats dead: a deleted project never blocks an existing one, but
    stays reachable when nothing live matched."""
    live = [t for t in matches if _live(t)]
    return live or matches


def _tiers(needle: str, targets: Sequence[Target]) -> list[list[Target]]:
    n = needle
    return [
        [t for t in targets if t.name.lower() == n],
        [t for t in targets if t.name.lower().startswith(n)],
        [t for t in targets if (a := acronym(t.name)) is not None and a[0] == n],
        [t for t in targets if n in t.name.lower()],
        [t for t in targets if n in t.path.lower()],
    ]


def _shorten_home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def resolve(arg: str, targets: Sequence[Target], prog: str = "standup") -> Target:
    """A fragment -> exactly one project, or a HandleError that names the
    alternatives. The first tier with any match decides; more than one match
    inside that tier is ambiguous and never silently resolved."""
    needle = arg.rstrip("/").lower()
    for tier in _tiers(needle, targets):
        if not tier:
            continue
        hit = _narrow(tier)
        if len(hit) == 1:
            return hit[0]
        listing = "\n".join(f"  {t.name}  {_shorten_home(t.path)}" for t in hit)
        raise HandleError(f"{prog}: {arg!r} is ambiguous:\n{listing}")
    known = ", ".join(sorted({t.name for t in targets}))
    raise HandleError(f"{prog}: no project matches {arg!r}\nknown projects: {known}")


def resolve_target_path(arg: str, targets: Sequence[Target],
                        prog: str = "standup") -> Target:
    """A filesystem path -> the project that owns it (worktrees included)."""
    from . import gitstate

    p = os.path.abspath(os.path.expanduser(arg))
    if not os.path.isdir(p):
        raise HandleError(f"{prog}: no such directory: {arg}")
    res = gitstate._resolve(p)
    if not res:
        raise HandleError(f"{prog}: {arg!r} is not inside a git repo")
    toplevel, key = res
    real = os.path.realpath(toplevel)
    for t in targets:
        if os.path.realpath(t.path) == real:
            return t
    # A worktree: its toplevel is its own directory, so fall back to the repo
    # key (git-common-dir), which worktrees share with their main checkout.
    for t in targets:
        other = gitstate._resolve(t.path)
        if other and other[1] == key:
            return t
    raise HandleError(f"{prog}: {_shorten_home(toplevel)} has no Claude Code sessions")


def _usable(cand: str) -> bool:
    """A displayable handle must survive the CLI: not a subcommand the
    dispatcher would eat, and not something the path tier would claim first
    (`.agents` must not advertise `.`)."""
    return cand not in RESERVED and not looks_like_path(cand)


def _resolves_to(arg: str, target: Target, targets: Sequence[Target]) -> bool:
    try:
        return resolve(arg, targets) is target
    except HandleError:
        return False


def handle_of(target: Target,
              targets: Sequence[Target]) -> tuple[str, tuple[int, ...]] | None:
    """The handle to *display* for a project: its acronym when that is
    multi-word and unambiguous, else its shortest unique prefix. Returns the
    handle and the offsets it occupies in the name, or None when nothing short
    resolves (two live projects sharing a name).

    Every candidate is checked by round-tripping it through `resolve`, so the
    display can never advertise a handle that would error.
    """
    acr = acronym(target.name)
    if acr and _usable(acr[0]) and _resolves_to(acr[0], target, targets):
        return acr
    name = target.name
    for k in range(1, len(name) + 1):
        cand = name[:k].lower()
        if not _usable(cand):
            continue
        if _resolves_to(cand, target, targets):
            return cand, tuple(range(k))
    return None


def mark(name: str, positions: tuple[int, ...], enabled: bool) -> str:
    r"""Underline the handle's letters inside the name.

    Underline rather than bold because project names already render bold, and
    `\033[24m` ends the underline *without* ending the bold the caller wraps
    around it (a bare `\033[0m` would drop the weight for the rest of the name).
    """
    if not enabled or not positions:
        return name
    pos = set(positions)
    out: list[str] = []
    on = False
    for i, ch in enumerate(name):
        want = i in pos
        if want and not on:
            out.append("\033[4m")
            on = True
        elif not want and on:
            out.append("\033[24m")
            on = False
        out.append(ch)
    if on:
        out.append("\033[24m")
    return "".join(out)


def marked(target: Target, targets: Sequence[Target], enabled: bool) -> str:
    """A project's name with its Project Handle underlined — zero extra width,
    which is what the inbox's never-wrap rule makes scarce."""
    h = handle_of(target, targets)
    return mark(target.name, h[1] if h else (), enabled)
