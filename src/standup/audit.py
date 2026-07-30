"""Audit reader (ADR 0007).

An Audit is the on-demand, LLM-authored judgment of one Session: which turns
are LLM-as-CPU work, whether the pattern recurs in sibling Sessions, and a
Handoff Prompt for scripting it away. Produced by the Expert Panel in
auditgen.py; this module only *reads* — rendering stays deterministic given
the files on disk, exactly like Session Briefs (ADR 0006).

Audits live in the durable root (never the disposable cache/):

    ~/.standup/audits/<sessionId>.audit.md

Format: markdown with a small frontmatter contract. `overhead` is a one-line
JSON array of {label, model, usage} — one entry per Expert Panel pass — priced
by the Rate Card as Audit Overhead, itemised per Expert.

A missing or unreadable Audit is never an error: `standup audit` simply offers
to generate one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import rates
from .brief import STALE_TOLERANCE, _parse_ts, _split_frontmatter

AUDITS_DIR = Path(os.path.expanduser("~/.standup")) / "audits"


@dataclass
class Audit:
    session_id: str
    body: str = ""                 # the concluder's markdown — the Audit proper
    generated: datetime | None = None
    target_title: str | None = None
    siblings_considered: int = 0
    # one {label, model, usage} per panel pass (4 Experts + concluder)
    overhead: list[dict] = field(default_factory=list)
    stale: bool = False

    @property
    def overhead_cost(self) -> float:
        return sum(
            rates.turn_cost(o.get("model"), o["usage"]) or 0.0
            for o in self.overhead
            if isinstance(o.get("usage"), dict)
        )

    def overhead_items(self) -> list[tuple[str, float]]:
        """(label, cost) per pass — the itemisation ADR 0007 requires."""
        out = []
        for o in self.overhead:
            u = o.get("usage")
            c = rates.turn_cost(o.get("model"), u) or 0.0 if isinstance(u, dict) else 0.0
            out.append((o.get("label", "?"), c))
        return out


def path_for(session_id: str) -> Path:
    return AUDITS_DIR / f"{session_id}.audit.md"


def load_one(session_id: str) -> Audit | None:
    p = path_for(session_id)
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return None
    fm, body = _split_frontmatter(text)
    overhead: list[dict] = []
    if fm.get("overhead"):
        try:
            parsed = json.loads(fm["overhead"])
            if isinstance(parsed, list):
                overhead = [o for o in parsed if isinstance(o, dict)]
        except (ValueError, TypeError):
            pass
    try:
        siblings = int(fm.get("siblings_considered", "0"))
    except ValueError:
        siblings = 0
    return Audit(
        session_id=session_id,
        body=body.strip(),
        generated=_parse_ts(fm.get("generated")),
        target_title=(fm.get("target_title") or "").strip() or None,
        siblings_considered=siblings,
        overhead=overhead,
    )


def stamp_staleness(audit: Audit, last_activity: datetime | None) -> None:
    """An Audit of a session that continued past generation is stale (same
    tolerance as Briefs). Sibling drift deliberately does NOT stale it."""
    try:
        if audit.generated and last_activity and last_activity > audit.generated + STALE_TOLERANCE:
            audit.stale = True
    except TypeError:  # naive vs aware in a hand-edited file — never crash a reader
        pass


def prune_orphans(live_ids: set[str]) -> None:
    """Delete Audits whose Session log no longer exists (mirrors Briefs)."""
    try:
        entries = list(AUDITS_DIR.glob("*.audit.md"))
    except OSError:
        return
    for p in entries:
        sid = p.name[: -len(".audit.md")]
        if sid not in live_ids:
            try:
                p.unlink()
            except OSError:
                pass
