"""Loop detection over a Session log (ADR 0003 § the Audit).

A **Loop** is a measured fact: a run of repeated same-shape tool calls, priced
by the turns its calls live in. Both halves are read through the one log reader
(ADR 0001 § the one log reader) — the tool calls and the per-turn usage — so
what is pinned here is that the detector still sees every call, still shapes it
the same way, and still prices the run off the same Rate Card the rest of the
tool uses.
"""

from __future__ import annotations

import pytest

from standup import cache as cache_mod
from standup import loops, rates

from tests.support.sessions import SessionLog


def _grinding(cwd: str = "/tmp/tt", iterations: int = 5) -> SessionLog:
    """A Session that reads and edits the same directory over and over — the
    shape a Loop is made of, at exactly `iterations` repetitions."""
    log = SessionLog(cwd=cwd).prompt("make the tests pass")
    for i in range(iterations):
        log.call("Read", file_path=f"{cwd}/alpha.py")
        log.edit(f"{cwd}/alpha.py", old_string=f"x = {i}", new_string=f"x = {i + 1}")
    return log


def test_a_repeated_ngram_of_tool_calls_is_a_loop_priced_off_its_turns(projects_dir):
    """`Read→Edit` five times over is one Loop, and its Loop Cost is the summed
    Notional Cost of the assistant turns those calls live in — a carve-out of
    the Session's cost, never a projection."""
    session = _grinding()
    log = session.save(projects_dir)

    scan = loops.detect(log)

    (loop,) = loops.significant(scan)
    assert loop.label == "Read→Edit tmp/tt"
    assert (loop.iterations, loop.turns, loop.unpriced_turns) == (5, 10, 0)
    assert loop.cost == pytest.approx(
        sum(rates.turn_cost(m, u) for m, u in session.usages))
    assert scan.session_cost == pytest.approx(loop.cost)


def test_a_loop_names_the_tool_calls_it_is_made_of(projects_dir):
    """The `tool_use` ids are the evidence a Transcript gutter-marks with, so
    the detector carries them out of the log rather than leaving a reader to
    match on labels."""
    log = _grinding().save(projects_dir)

    (loop,) = loops.significant(loops.detect(log))

    assert len(loop.tool_ids) == 10
    assert len(set(loop.tool_ids)) == 10
    assert all(tid.startswith("toolu_") for tid in loop.tool_ids)


def test_calls_of_different_shapes_are_not_a_loop(projects_dir):
    """Shape is tool name plus argument shape — the directory for a file tool,
    the command head for Bash. Ten calls that never repeat a shape are ten
    pieces of work, not a grind."""
    log = SessionLog(cwd="/tmp/tt").prompt("do ten different things")
    for i in range(10):
        log.call("Bash", command=f"tool-{i} --run")
    saved = log.save(projects_dir)

    assert loops.detect(saved).loops == []


def test_an_unchanged_log_is_detected_once_and_served_from_the_cache(projects_dir):
    """Detection is a pure derivation, so it belongs in the Derived Cache
    (ADR 0001 § the Derived Cache) — and a warm run answers identically."""
    log = _grinding().save(projects_dir)
    cache = cache_mod.open_cache()
    cold = loops.for_session(log, cache)
    cache.flush()

    warm = loops.for_session(log, cache_mod.open_cache())

    assert warm == cold
    assert warm.loops[0].tool_ids == cold.loops[0].tool_ids
