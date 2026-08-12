"""Audit adapter (ADR 0003 § the Audit).

An Audit is the on-demand, LLM-authored judgment of one Session: which turns
are LLM-as-CPU work, whether the pattern recurs in sibling Sessions, and a
Handoff Prompt for scripting it away. The Expert Panel in `auditgen` produces
it; the read path only ever *reads*, exactly like a Session Brief.

Location, frontmatter, atomic write, pruning and staleness are the Artifact
store's (`artifacts.py`) — a Brief and an Audit differ only in their fields:

    ---
    session_id: <the Session this judges>
    target_title: <its derived title at generation time>
    generated: 2026-07-30T09:12:00Z
    siblings_considered: 12
    overhead: [{"label": …, "model": …, "usage": {…}}]   # one per panel pass
    ---
    <the concluder's markdown — the Audit proper>

`overhead` is priced by the Rate Card as Audit Overhead, itemised per Expert.
An Audit is staled by its *target* Session continuing past generation, never by
sibling drift: the siblings are evidence the panel weighed, not the claim.
A missing or unreadable Audit is never an error: `standup audit` simply offers
to generate one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import artifacts, rates

STORE = artifacts.Store("audits", ".audit.md")


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
        """(label, cost) per pass — the itemisation
        ADR 0003 § the Audit requires."""
        out = []
        for o in self.overhead:
            u = o.get("usage")
            c = rates.turn_cost(o.get("model"), u) or 0.0 if isinstance(u, dict) else 0.0
            out.append((o.get("label", "?"), c))
        return out


def load_one(session_id: str) -> Audit | None:
    doc = STORE.read(session_id)
    if doc is None:
        return None
    return Audit(
        session_id=session_id,
        body=doc.body.strip(),
        generated=doc.frontmatter.timestamp("generated"),
        target_title=doc.frontmatter.text("target_title"),
        siblings_considered=doc.frontmatter.count("siblings_considered"),
        overhead=doc.frontmatter.records("overhead"),
    )


def save(audit: Audit) -> Path:
    """Write one Audit — the inverse of `load_one`. A pass that recorded no
    `usage` is not written: it could not be itemised as Audit Overhead, and an
    Audit's frontmatter is exactly what the cost views can price."""
    return STORE.write(audit.session_id, {
        "session_id": audit.session_id,
        "target_title": audit.target_title,
        "generated": audit.generated,
        "siblings_considered": audit.siblings_considered,
        "overhead": [{"label": o["label"], "model": o["model"], "usage": o["usage"]}
                     for o in audit.overhead if o.get("usage")],
    }, audit.body)
