"""Loop detection (ADR 0007): repeated same-shape tool-call n-grams in a Session.

A Loop is a *measured fact*, not a judgment — a run of >= MIN_ITERATIONS
repetitions of an n-gram (n <= MAX_NGRAM) of tool calls matching on tool name +
argument shape (dirname for file tools, command head for Bash). Its Loop Cost is
the summed per-turn Notional Cost of the assistant turns the loop's calls live
in — a carve-out of the Session's Notional Cost, never a projected saving.

Detection is free, deterministic, and always-on: derived purely from the JSONL
and cached in the Derived Cache keyed on (size, mtime_ns, DETECTOR_VERSION).
Whether a Loop is *scriptable* is a semantic question that belongs to the Audit
(the Loop Expert), never to this module.

A display floor keeps noise (retry storms, cheap grinds) out of view: a Loop
prints only when its cost clears min($1, 10% of the session's Notional Cost).
Below-floor Loops still exist in the data — the Audit may see them.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import rates

DETECTOR_VERSION = 3  # bump when detection logic changes (invalidates cache rows)

MIN_ITERATIONS = 5
MAX_NGRAM = 3
GAP_TOLERANCE = 2  # non-matching calls tolerated between iterations
FLOOR_DOLLARS = 1.0
FLOOR_FRACTION = 0.10

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep"}
_PATH_KEYS = ("file_path", "notebook_path", "path")


@dataclass
class Loop:
    label: str            # "Read→Edit src/standup", "Bash pytest"
    ngram: list[str]      # the shape keys, one per gram position
    iterations: int
    turns: int            # distinct assistant turns spanned
    cost: float           # Loop Cost — priced turns only
    unpriced_turns: int
    tool_ids: list[str] = field(default_factory=list)  # tool_use ids, for gutter marks


@dataclass
class LoopScan:
    loops: list[Loop] = field(default_factory=list)  # every detected Loop, cost desc
    session_cost: float = 0.0  # full-session Notional Cost (the floor's base)


def significant(scan: LoopScan) -> list[Loop]:
    """The above-floor Loops — the only ones any view prints."""
    floor = (min(FLOOR_DOLLARS, FLOOR_FRACTION * scan.session_cost)
             if scan.session_cost > 0 else FLOOR_DOLLARS)
    return [l for l in scan.loops if l.cost >= floor]


# ── shapes ──────────────────────────────────────────────────────────────────

def _shape(name: str, inp: dict) -> str:
    """The shape key of one tool call: what must match for calls to be 'the same'."""
    if name == "Bash":
        cmd = (inp.get("command") or "").strip()
        head = os.path.basename(cmd.split()[0]) if cmd.split() else ""
        return f"{name}:{head}"
    if name in FILE_TOOLS:
        fp = next((inp[k] for k in _PATH_KEYS if isinstance(inp.get(k), str)), "")
        return f"{name}:{os.path.dirname(fp)}"
    return f"{name}:"


def _short_dir(d: str) -> str:
    segs = [s for s in d.split("/") if s]
    return "/".join(segs[-2:])


def _short_tool(name: str) -> str:
    # mcp__<server-id>__notion-fetch → notion-fetch — the server id is identity
    # (kept in the shape key) but noise in a label
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def _label(gram: tuple[str, ...]) -> str:
    tools = [_short_tool(k.split(":", 1)[0]) for k in gram]
    ctxs = [k.split(":", 1)[1] for k in gram]
    head = "→".join(tools)
    uniq = list(dict.fromkeys(c for c in ctxs if c))
    if uniq:
        shown = _short_dir(uniq[0]) if "/" in uniq[0] else uniq[0]
        if len(uniq) > 1:
            shown += " +"
        head += f" {shown}"
    return head


# ── detection ───────────────────────────────────────────────────────────────

@dataclass
class _Elem:
    shape: str
    tool_id: str
    turn_uuid: str


def _runs_of(gram: tuple[str, ...], seq: list[str]) -> list[tuple[int, list[int]]]:
    """All runs of `gram` in `seq` with >= MIN_ITERATIONS iterations, tolerating
    up to GAP_TOLERANCE non-matching elements between iterations. Returns
    (iterations, covered indexes) per run."""
    n, N = len(gram), len(seq)
    runs: list[tuple[int, list[int]]] = []
    i = 0
    while i <= N - n:
        if tuple(seq[i:i + n]) != gram:
            i += 1
            continue
        idxs = list(range(i, i + n))
        iters, j, gap = 1, i + n, 0
        while j <= N - n:
            if tuple(seq[j:j + n]) == gram:
                idxs.extend(range(j, j + n))
                iters += 1
                j += n
                gap = 0
            elif gap < GAP_TOLERANCE:
                j += 1
                gap += 1
            else:
                break
        if iters >= MIN_ITERATIONS:
            runs.append((iters, idxs))
            i = j
        else:
            i += 1
    return runs


def _find_loops(elements: list[_Elem], turn_cost: dict[str, float | None]) -> list[Loop]:
    seq = [e.shape for e in elements]
    N = len(seq)

    candidates: list[tuple[tuple[str, ...], int, list[int]]] = []
    for n in range(1, MAX_NGRAM + 1):
        counts = Counter(tuple(seq[i:i + n]) for i in range(N - n + 1))
        for gram, cnt in counts.items():
            if cnt < MIN_ITERATIONS:
                continue
            for iters, idxs in _runs_of(gram, seq):
                candidates.append((gram, iters, idxs))

    # greedy by coverage: the run explaining the most calls wins its elements;
    # deterministic tiebreaks (earliest start, then the gram itself)
    candidates.sort(key=lambda c: (-len(c[2]), c[2][0], c[0]))
    consumed: set[int] = set()
    # separate runs of the same gram are one Loop recurring — merge them
    by_gram: dict[tuple[str, ...], tuple[int, list[int]]] = {}
    for gram, iters, idxs in candidates:
        if consumed.intersection(idxs):
            continue
        consumed.update(idxs)
        prev_iters, prev_idxs = by_gram.get(gram, (0, []))
        by_gram[gram] = (prev_iters + iters, prev_idxs + idxs)

    loops: list[Loop] = []
    for gram, (iters, idxs) in by_gram.items():
        uuids = list(dict.fromkeys(elements[i].turn_uuid for i in idxs))
        priced = [turn_cost[u] for u in uuids if turn_cost.get(u) is not None]
        loops.append(Loop(
            label=_label(gram),
            ngram=list(gram),
            iterations=iters,
            turns=len(uuids),
            cost=sum(priced),
            unpriced_turns=len(uuids) - len(priced),
            tool_ids=[elements[i].tool_id for i in idxs if elements[i].tool_id],
        ))
    loops.sort(key=lambda l: (-l.cost, -l.iterations, l.label))
    return loops


def detect(path: Path) -> LoopScan:
    """Parse one session JSONL and detect its Loops. Pure derivation."""
    elements: list[_Elem] = []
    turn_cost: dict[str, float | None] = {}
    session_cost = 0.0
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"tool_use"' not in line and '"usage"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            uuid = obj.get("uuid") or ""
            msg = obj.get("message") or {}
            u = msg.get("usage")
            if isinstance(u, dict) and uuid not in turn_cost:
                c = rates.turn_cost(msg.get("model"), u)
                turn_cost[uuid] = c
                if c is not None:
                    session_cost += c
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    elements.append(_Elem(
                        shape=_shape(item.get("name") or "tool", item.get("input") or {}),
                        tool_id=item.get("id") or "",
                        turn_uuid=uuid,
                    ))
    return LoopScan(loops=_find_loops(elements, turn_cost), session_cost=session_cost)


# ── cache round-trip ────────────────────────────────────────────────────────

def _to_cache(scan: LoopScan) -> dict:
    return {
        "session_cost": scan.session_cost,
        "loops": [{
            "label": l.label, "ngram": l.ngram, "iterations": l.iterations,
            "turns": l.turns, "cost": l.cost, "unpriced_turns": l.unpriced_turns,
            "tool_ids": l.tool_ids,
        } for l in scan.loops],
    }


def _from_cache(d: dict) -> LoopScan:
    return LoopScan(
        loops=[Loop(**l) for l in d.get("loops") or []],
        session_cost=d.get("session_cost", 0.0),
    )


def for_session(path: Path, cache) -> LoopScan:
    """Detect via the Derived Cache: unchanged files are never re-read."""
    try:
        st = path.stat()
    except OSError:
        return LoopScan()
    sid = path.stem
    cached = cache.get_loops(sid, st.st_size, st.st_mtime_ns)
    if cached is not None:
        try:
            return _from_cache(cached)
        except (TypeError, KeyError):
            pass  # malformed row — recompute
    scan = detect(path)
    cache.put_loops(sid, st.st_size, st.st_mtime_ns, _to_cache(scan))
    return scan
