"""Audit generator (ADR 0003 § the Audit) — the code-driven Expert Panel.

`standup audit <handle>` runs this on demand. Standup's own code performs the
fan-out: four narrow Sonnet Experts run in parallel over only the evidence each
needs, then one Opus concluder receives their claims plus the deterministic
data (never the raw transcript) and owns the solution and the Handoff Prompt.
No model ever decides the panel's shape.

Transport is the Claude Agent SDK — the programmatic face of `claude -p`,
driving the same Claude Code binary and inheriting its login, so the user's
subscription keeps paying (verified 2026-07-30; same keyless-auth ground as
ADR 0003 § the Session Brief). Imported lazily so the display path gains no
dependency.

Every pass's `usage` is recorded and itemised in the Audit's frontmatter as
Audit Overhead; each `standup audit` run prints the overhead it just incurred.

The Audit's location, frontmatter and atomic write are the Artifact store's
(`artifacts.py`), reached through `audit.save` — this module owns the panel,
never the file format.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from . import audit as audit_mod
from . import brief as brief_mod
from . import loops, transcript, universe
from .audit import Audit
from .models import Session

EXPERT_MODEL = "claude-sonnet-5"
CONCLUDER_MODEL = "claude-opus-5"
PASS_TIMEOUT = 420        # seconds per panel pass
MAX_DIGEST = 120_000      # chars of target transcript fed to transcript-reading Experts
HEAD_DIGEST = 70_000      # over budget: keep this much head + the rest tail
MAX_SIBLINGS = 40         # most recent same-repo sessions offered to the Recurrence Expert


class AuditError(Exception):
    pass


# ── deterministic evidence ─────────────────────────────────────────────────

def _digest(path: Path, looped_ids: set[str]) -> str:
    """Numbered plain-text rendering of the target session. Assistant turns get
    t<N> markers (the Handoff Prompt's evidence coordinates) and their per-turn
    Notional Cost; tool calls belonging to a detected Loop are marked ⟳."""
    from . import rates
    parts: list[str] = []
    turn = 0
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = obj.get("type")
                msg = obj.get("message") or {}
                if etype == "user" and not obj.get("isMeta"):
                    text = transcript._user_text(msg.get("content"))
                    if text:
                        parts.append("USER: " + text)
                elif etype == "assistant":
                    texts, tools = transcript._assistant_parts(
                        msg.get("content"), show_thinking=False, looped_ids=looped_ids)
                    if not texts and not tools:
                        continue
                    turn += 1
                    u = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
                    c = rates.turn_cost(msg.get("model"), u) if u else None
                    tag = f" (${c:.2f})" if c else ""
                    head = f"[t{turn}{tag}]"
                    for t in texts:
                        parts.append(f"{head} CLAUDE: {t}")
                        head = f"[t{turn}]"
                    for tl, looped in tools:
                        parts.append(f"  {'⟳' if looped else '·'} {tl}")
    except OSError:
        return ""
    text = "\n".join(parts)
    if len(text) > MAX_DIGEST:
        text = text[:HEAD_DIGEST] + "\n… [middle elided] …\n" + text[-(MAX_DIGEST - HEAD_DIGEST):]
    return text


def _loop_table(scan: loops.LoopScan) -> str:
    if not scan.loops:
        return "(no Loops detected)"
    lines = ["| loop | iterations | turns | loop cost |", "|---|---|---|---|"]
    for l in scan.loops:
        lines.append(f"| {l.label} | {l.iterations} | {l.turns} | ${l.cost:.2f} |")
    return "\n".join(lines)


def _repo_key(cwd: str | None) -> str | None:
    owner = universe.owner_of(cwd)
    return owner.key if owner else None


def _gather_siblings(target: Session, sessions: list[Session], cache) -> list[dict]:
    """Deterministic gathering only (ADR 0003 § the Audit): same Repo Entry,
    capped, with
    title + Brief (when present) + Loop fingerprints/costs. Matching is the
    Recurrence Expert's job."""
    key = _repo_key(target.cwd)
    if key is None:
        return []
    sibs = [s for s in sessions
            if s.session_id != target.session_id and _repo_key(s.cwd) == key]
    sibs.sort(key=lambda s: s.last_activity or datetime.min.replace(tzinfo=timezone.utc),
              reverse=True)
    out = []
    for s in sibs[:MAX_SIBLINGS]:
        b = brief_mod.load_one(s.session_id)
        scan = loops.for_session(Path(s.log_path), cache)
        out.append({
            "handle": s.session_id[:8],
            "title": s.title,
            "objective": b.objective if b else None,
            "loops": [{"label": l.label, "iterations": l.iterations,
                       "cost": round(l.cost, 2)}
                      for l in loops.significant(scan)],
            "session_cost": round(scan.session_cost, 2),
        })
    return out


# ── the panel ──────────────────────────────────────────────────────────────

_CLAIM_RULES = (
    "Rules: be skeptical and concrete. Every claim must cite evidence — turn "
    "markers (t<N>) or Loop labels from the material below. Dollar figures may "
    "only restate numbers given to you; never project savings. Do NOT propose "
    "fixes or scripts — a concluder owns solutions. Reply in terse markdown "
    "bullets. If you find nothing, reply exactly: No findings."
)


def _expert_prompts(digest: str, loop_table: str, target_label: str,
                    siblings: list[dict], target_brief: str | None) -> dict[str, str]:
    return {
        "loop-expert": (
            "You are the Loop Expert on a fixed audit panel judging one Claude "
            f"Code session ({target_label}) for automatable waste.\n"
            "A deterministic detector found these Loops (repeated same-shape "
            "tool-call runs):\n\n" + loop_table + "\n\n"
            "For EACH Loop, judge: scriptable (a plain script could have done "
            "this work), legitimate (retries, exploration, iterative dev — not "
            "automatable), or unclear. Justify from the transcript.\n"
            + _CLAIM_RULES + "\n\n--- TRANSCRIPT DIGEST ---\n" + digest
        ),
        "llm-as-cpu": (
            "You are the LLM-as-CPU Expert on a fixed audit panel judging one "
            f"Claude Code session ({target_label}).\n"
            "Find turns where the assistant performs mechanical data "
            "transformation IN ITS HEAD — parsing, reformatting, arithmetic, "
            "copying values between formats, generating repetitive boilerplate "
            "— work a script would do for ~$0. Distinguish from genuine "
            "reasoning/design, which is what the model is for.\n"
            + _CLAIM_RULES + "\n\n--- TRANSCRIPT DIGEST ---\n" + digest
        ),
        "prompt-structure": (
            "You are the Prompt-Structure Expert on a fixed audit panel judging "
            f"one Claude Code session ({target_label}).\n"
            "Judge the HUMAN's side: instructions re-explained that belong in "
            "CLAUDE.md, repeated boilerplate prompts that should be a skill or "
            "slash command, context pasted that a tool could fetch, vague asks "
            "that caused expensive exploration.\n"
            + _CLAIM_RULES + "\n\n--- TRANSCRIPT DIGEST ---\n" + digest
        ),
        "recurrence": (
            "You are the Recurrence Expert on a fixed audit panel.\n"
            f"Target session: {target_label}\n"
            + (f"Target objective (LLM-authored claim): {target_brief}\n" if target_brief else "")
            + "Target Loops:\n" + loop_table + "\n\n"
            "Below are sibling sessions from the SAME repository (title, "
            "objective when known, detected Loops with measured Loop Costs). "
            "Identify which siblings plausibly share the target's objective or "
            "exhibit the same Loop shapes. Report recurrence as MEASURED FACT "
            "only: name the matching siblings and sum the Loop Costs you were "
            "given (e.g. 'recurred in 4 sessions, combined Loop Cost $23'). "
            "Never forecast future costs.\n" + _CLAIM_RULES + "\n\n"
            "--- SIBLINGS (JSON) ---\n" + json.dumps(siblings, indent=1)
        ),
    }


def _concluder_prompt(target_label: str, repo_path: str | None,
                      loop_table: str, reports: dict[str, str]) -> str:
    sections = "\n\n".join(
        f"### {label}\n{text.strip() or '(empty)'}" for label, text in reports.items())
    return (
        "You are the concluder of a fixed audit panel for `standup`, a CLI that "
        "analyses Claude Code sessions for automatable cost. Four Experts "
        f"examined session {target_label}"
        + (f" in repo {repo_path}" if repo_path else "") + ". Their raw claims "
        "follow, plus the deterministic Loop table. You did NOT read the "
        "transcript — weigh the claims against each other, discard weak or "
        "contradicted ones, and conclude.\n\n"
        "Write the final Audit as markdown with EXACTLY these sections:\n\n"
        "## Verdict\n2-4 sentences: how much of this session's cost is "
        "automatable work, and what kind.\n\n"
        "## Findings\nBullet list. Each: `~` prefix (every finding is a claim), "
        "the claim, the expert it came from, the evidence (t<N> / Loop label), "
        "and measured dollars where given. Keep only claims that survive "
        "scrutiny.\n\n"
        "## Recurrence\nMeasured facts only, from the Recurrence Expert. If no "
        "siblings match, say so in one line.\n\n"
        "## Handoff Prompt\nA single fenced ```text block: a paste-ready prompt "
        "for a FRESH Claude Code session in the target repo that builds the "
        "automation script(s). Self-contained: name the repo, cite the evidence "
        "(`standup session <handle>`, turns t<N>), describe exactly what the "
        "script must do, and require the session to verify the script against "
        "a real example. If nothing is worth scripting, write `(no handoff — "
        "nothing scriptable found)` instead of the fenced block.\n\n"
        "Dollar figures are measured carve-outs of past cost — NEVER present "
        "them as projected savings.\n\n"
        "--- DETERMINISTIC LOOP TABLE ---\n" + loop_table + "\n\n"
        "--- EXPERT CLAIMS ---\n" + sections
    )


async def _run_pass(label: str, prompt: str, model: str) -> dict:
    """One panel pass via the Agent SDK: no tools, no user settings, one turn.
    Returns {label, model, text, usage}."""
    from claude_agent_sdk import ClaudeAgentOptions, query  # lazy: generation path only

    opts = ClaudeAgentOptions(model=model, max_turns=1,
                              allowed_tools=[], setting_sources=[])
    text, usage = "", None
    async for msg in query(prompt=prompt, options=opts):
        if type(msg).__name__ == "ResultMessage":
            text = msg.result or ""
            usage = msg.usage if isinstance(msg.usage, dict) else None
    if not text.strip():
        raise AuditError(f"{label}: empty result from {model}")
    return {"label": label, "model": model, "text": text, "usage": usage}


async def _run_panel(prompts: dict[str, str], progress) -> list[dict]:
    async def timed(label: str, prompt: str, model: str) -> dict:
        res = await asyncio.wait_for(_run_pass(label, prompt, model), PASS_TIMEOUT)
        progress(res)
        return res

    tasks = [timed(label, p, EXPERT_MODEL) for label, p in prompts.items()]
    return list(await asyncio.gather(*tasks))


# ── entry point ────────────────────────────────────────────────────────────

def generate(log_path: Path, u: universe.Universe, progress=lambda r: None) -> Path:
    """Run the full panel for one session and store the Audit. Synchronous
    facade over the async fan-out; raises AuditError on failure (nothing
    partial is ever stored)."""
    from . import claude_logs

    sid = log_path.stem
    cache = u.cache
    sessions = u.sessions()
    target = next((s for s in sessions if s.session_id == sid), None)
    if target is None:  # footprint-less session: parse minimally for cwd/title
        target = Session(session_id=sid, log_path=str(log_path))
        claude_logs._full_scan(target, log_path)

    scan = loops.for_session(log_path, cache)
    looped_ids = {tid for l in loops.significant(scan) for tid in l.tool_ids}
    digest = _digest(log_path, looped_ids)
    if not digest.strip():
        raise AuditError("no readable turns in this session")

    loop_table = _loop_table(scan)
    siblings = _gather_siblings(target, sessions, cache)
    b = brief_mod.load_one(sid)
    target_label = f'{sid[:8]} "{target.title}"'
    prompts = _expert_prompts(digest, loop_table, target_label, siblings,
                              b.objective if b else None)

    async def run() -> tuple[list[dict], dict]:
        reports = await _run_panel(prompts, progress)
        concluder = await asyncio.wait_for(
            _run_pass("concluder",
                      _concluder_prompt(target_label,
                                        target.cwd, loop_table,
                                        {r["label"]: r["text"] for r in reports}),
                      CONCLUDER_MODEL),
            PASS_TIMEOUT + 180)
        progress(concluder)
        return reports, concluder

    try:
        reports, concluder = asyncio.run(run())
    except asyncio.TimeoutError as e:
        raise AuditError("a panel pass timed out") from e
    except AuditError:
        raise
    except Exception as e:  # SDK/transport errors: surface, never store partials
        raise AuditError(str(e)) from e

    return audit_mod.save(Audit(
        session_id=sid, body=concluder["text"],
        generated=datetime.now(timezone.utc), target_title=target.title,
        siblings_considered=len(siblings), overhead=reports + [concluder]))
