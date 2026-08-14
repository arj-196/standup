"""Session Brief adapter (ADR 0003 § the Session Brief).

A Session Brief is an LLM-authored, out-of-band account of a Session's
objective, written *during* the session by a Claude Code Stop hook (installed
via `standup install`). The render path only ever *reads* one — generation
lives in `briefgen` — so rendering stays deterministic and the "never
LLM-generated at render time" rule (CONTEXT.md → Resume) holds.

Location, frontmatter, atomic write, pruning and staleness are the Artifact
store's (`artifacts.py`); this module owns only what is Brief-shaped — the
fields the frontmatter carries and the `Brief` they become:

    ---
    objective: <one declarative line — what the session set out to do>
    status: in-progress        # done | in-progress | blocked | abandoned
    generated: 2026-07-22T21:40:00Z
    model: claude-haiku-4-5
    gen_usage: {"input_tokens": …}   # priced as Brief Overhead
    ---
    <freeform body — shown only in `standup session`>

A missing, unreadable, or objective-less Brief is never an error: the Session
simply renders title-only, exactly as before Briefs existed.
"""

from __future__ import annotations

from pathlib import Path

from . import artifacts, rates
from .models import Brief, Session

STORE = artifacts.Store("briefs", ".brief.md")


def _to_brief(session_id: str, doc: artifacts.Document) -> Brief | None:
    objective = doc.frontmatter.text("objective")
    if not objective:
        return None  # nothing worth showing
    return Brief(
        session_id=session_id,
        objective=objective,
        status=doc.frontmatter.text("status"),
        generated=doc.frontmatter.timestamp("generated"),
        model=doc.frontmatter.text("model"),
        body=doc.body.strip(),
        gen_usage=doc.frontmatter.mapping("gen_usage"),
    )


def load_one(session_id: str) -> Brief | None:
    """Read a single Session's Brief by id, or None if absent/unreadable."""
    doc = STORE.read(session_id)
    return _to_brief(session_id, doc) if doc is not None else None


def load_for_sessions(sessions: list[Session]) -> dict[str, Brief]:
    """Attach each Session's Brief (if any) and hedge the ones its Session has
    moved past. Returns {session_id: Brief} for the ones found."""
    briefs: dict[str, Brief] = {}
    for s in sessions:
        b = load_one(s.session_id)
        if b is None:
            continue
        artifacts.stamp_staleness(b, s.log_path)
        s.brief = b
        briefs[s.session_id] = b
    return briefs


def save(brief: Brief) -> Path:
    """Write one Brief — the inverse of `load_one`, so the frontmatter contract
    is stated once in each direction. `briefgen` decides *what* to claim; this
    decides what a Brief looks like on disk."""
    return STORE.write(brief.session_id, {
        "objective": brief.objective,
        "status": brief.status,
        "generated": brief.generated,
        "model": brief.model,
        "gen_usage": brief.gen_usage if isinstance(brief.gen_usage, dict) else None,
    }, brief.body or "")


def overhead_cost(brief: Brief) -> float:
    """Notional Cost of generating this Brief (Brief Overhead,
    ADR 0003 § the Session Brief), priced
    from the recorded generation usage by the Rate Card. 0.0 if unknown/unpriced.
    """
    if not brief.gen_usage:
        return 0.0
    return rates.turn_cost(brief.model, brief.gen_usage) or 0.0
