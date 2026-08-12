"""The cost view's aggregation rules.

Subagent transcripts carry their own per-turn `usage`, and the parent log
never echoes it — counting the parent alone under-counts subagent-heavy work.
These tests pin the fold (ADR 0002 § subagent usage): into the parent
Session's line, never a separate row, marked rather than silent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from standup import cache as cache_mod
from standup import claude_logs, cost, rates, render

from tests.support.sessions import SessionLog


def _yesterday() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


def test_a_warm_cost_scan_reparses_nothing(projects_dir, session_log, monkeypatch):
    """The cost view prices from the shared cached reading, so it inherits the
    Derived Cache the inbox already had (ADR 0001 § the one log reader): a
    second run over unchanged logs opens neither the parent nor the subagent
    transcript, and answers the same."""
    parent = session_log(cwd="/tmp/tt")
    agent = SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True).turn("delegated")
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    cache = cache_mod.open_cache()
    (cold,) = cost.scan_session_costs(projects_dir, _yesterday(), cache)
    cache.flush()

    def _never(*a, **kw):
        raise AssertionError("a warm cost scan must not reparse a log")

    monkeypatch.setattr(claude_logs, "parse_log", _never)
    (warm,) = cost.scan_session_costs(projects_dir, _yesterday(),
                                      cache_mod.open_cache())

    assert warm.cost == pytest.approx(cold.cost)
    assert (warm.turns, warm.unpriced_turns) == (cold.turns, cold.unpriced_turns)
    assert warm.tokens == cold.tokens
    assert warm.subagents == cold.subagents == 1
    assert warm.title == cold.title


def test_a_subagent_reading_survives_the_inboxs_prune(
        projects_dir, session_log, monkeypatch):
    """The Derived Cache prunes the rows of logs that are gone
    (ADR 0001 § the Derived Cache). A subagent transcript is a log the inbox's
    sweep never enumerates — it globs `*/*.jsonl` and a transcript sits a level
    below that — so a prune that knew only the sweep's ids would throw away the
    cost view's subagent readings on every inbox run (`standup -a` does both in
    one command; ADR 0001 § the one log reader)."""
    parent = session_log(cwd="/tmp/tt")
    agent = SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True).turn("delegated")
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    cache = cache_mod.open_cache()
    claude_logs.scan_sessions(projects_dir, cache)      # the inbox: it prunes
    cost.scan_session_costs(projects_dir, _yesterday(), cache)
    cache.flush()

    def _never(*a, **kw):
        raise AssertionError("the subagent's reading was pruned and reparsed")

    monkeypatch.setattr(claude_logs, "parse_log", _never)
    (warm,) = cost.scan_session_costs(projects_dir, _yesterday(),
                                      cache_mod.open_cache())

    assert warm.subagents == 1


def test_a_turns_cache_write_is_read_the_same_way_priced_and_displayed(
        projects_dir, session_log, null_cache):
    """A log can carry both spellings of the cache write — the flat
    `cache_creation_input_tokens` and the `cache_creation` sub-object — and
    they need not agree. Pricing reads the sub-object (the two lifetimes are
    priced apart, ADR 0002), so display reads it too: one number priced and a
    different one shown is the drift a single reading exists to prevent."""
    disagreeing = {"input_tokens": 10, "output_tokens": 20,
                   "cache_read_input_tokens": 0,
                   "cache_creation_input_tokens": 9_000,
                   "cache_creation": {"ephemeral_5m_input_tokens": 1_000,
                                      "ephemeral_1h_input_tokens": 2_000}}

    assert rates.turn_tokens(disagreeing)["cache_write"] == 3_000

    (SessionLog(cwd="/tmp/tt").turn("wrote cache", usage=disagreeing)
     .save(projects_dir))
    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)
    assert sc.tokens["cache_write"] == 3_000


def test_the_cost_view_leaves_last_activity_meaning_the_logs_mtime(
        projects_dir, session_log, null_cache):
    """One meaning everywhere (ADR 0001 § the one log reader): the Session the
    cost view carries says when its log last grew, exactly as the inbox's does.
    The window-bounded reading the view ranks and shows is a different fact
    under its own name — `SessionCost.last_turn`."""
    log = session_log(cwd="/tmp/tt").save(projects_dir)
    mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)

    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)

    assert sc.session.last_activity == mtime
    assert sc.last_turn is not None and sc.last_turn < mtime


def test_a_worktree_session_groups_under_its_main_checkout(
        scratch_repo, tmp_path, projects_dir, session_log, null_cache):
    """The cost view groups by Repo Entry, and worktrees fold into their parent
    checkout (CONTEXT.md → the `cost` view) — so a project whose only Sessions
    ran in a worktree is still named after the main checkout, never after the
    worktree that happened to be seen first."""
    repo = scratch_repo("tt")
    wt = repo.add_worktree(tmp_path / "wt-branchy")
    session_log(cwd=str(wt.path)).save(projects_dir)

    scs = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)
    (proj,) = cost.group_by_project(scs)

    assert proj.name == "tt"
    assert proj.path == str(repo.path)


def test_subagent_usage_folds_into_the_parent_session(projects_dir, session_log,
                                                      null_cache):
    parent = session_log(cwd="/tmp/tt")
    # a worktree agent: its own cwd is the worktree, not the parent checkout —
    # the fold still lands on the parent Session, whose cwd decides the project
    agent = (SessionLog(session_id="a1", cwd="/tmp/tt/.claude/worktrees/x",
                        sidechain=True)
             .turn("reading the tree").turn("reporting back"))
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    # one row, not two: a subagent is the Session's work, not a Session
    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)

    all_usages = parent.usages + agent.usages
    assert sc.session.cwd == "/tmp/tt"
    assert sc.turns == len(all_usages)
    assert sc.cost == pytest.approx(
        sum(rates.turn_cost(m, u) for m, u in all_usages))
    assert sc.tokens["output"] == sum(u["output_tokens"] for _, u in all_usages)
    assert sc.subagents == 1


def test_an_unpriced_subagent_turn_is_flagged_never_zero_dollars(
        projects_dir, session_log, null_cache):
    parent = session_log(cwd="/tmp/tt")
    agent = (SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True)
             .turn("future model", model="claude-nova-6"))
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)

    assert sc.unpriced_turns == 1
    assert sc.cost == pytest.approx(
        sum(rates.turn_cost(m, u) for m, u in parent.usages))


def test_delegated_work_alone_keeps_a_session_in_the_window(projects_dir, null_cache):
    """Turns are window-filtered by their own timestamps on both logs: a
    parent that went quiet before the window still earns its row when its
    subagent worked inside it."""
    now = datetime.now(timezone.utc)
    parent = (SessionLog(cwd="/tmp/tt", start=now - timedelta(days=30))
              .prompt("fan the work out").turn("spawning"))
    agent = (SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True,
                        start=now - timedelta(hours=1)).turn("late work"))
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)

    assert sc.turns == len(agent.usages)  # the parent's own turn fell outside
    assert sc.subagents == 1
    assert sc.last_turn >= _yesterday()


def test_a_subagent_outside_the_window_neither_counts_nor_marks(projects_dir,
                                                                null_cache):
    now = datetime.now(timezone.utc)
    parent = (SessionLog(cwd="/tmp/tt", start=now - timedelta(hours=2))
              .prompt("small ask").turn("done directly"))
    agent = (SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True,
                        start=now - timedelta(days=30)).turn("old work"))
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)

    assert sc.turns == len(parent.usages)
    assert sc.subagents == 0


def test_recent_orders_sessions_and_projects_by_their_newest_turn(projects_dir,
                                                                  null_cache):
    """`--recent` ranks both levels by their newest counted turn, newest first — the
    review-my-latest-sessions lens, where cheap and fresh beats expensive and
    stale. Default stays by cost."""
    now = datetime.now(timezone.utc)
    big = {"input_tokens": 0, "output_tokens": 1_000_000,
           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    (SessionLog(session_id="old-expensive", cwd="/tmp/tt",
                start=now - timedelta(days=5))
     .prompt("the heavy one").turn("big", usage=big).save(projects_dir))
    (SessionLog(session_id="new-cheap", cwd="/tmp/tt",
                start=now - timedelta(hours=1))
     .prompt("the fresh one").turn("small").save(projects_dir))
    (SessionLog(session_id="other-fresh", cwd="/tmp/other",
                start=now - timedelta(minutes=30))
     .prompt("elsewhere").turn("tiny").save(projects_dir))

    scs = cost.scan_session_costs(projects_dir, now - timedelta(days=30),
                                  null_cache)

    by_cost = cost.group_by_project(scs)
    assert [p.path for p in by_cost] == ["/tmp/tt", "/tmp/other"]
    assert [s.handle for s in by_cost[0].sessions] == ["old-expe", "new-chea"]

    by_recent = cost.group_by_project(scs, order="recent")
    assert [p.path for p in by_recent] == ["/tmp/other", "/tmp/tt"]
    tt = next(p for p in by_recent if p.path == "/tmp/tt")
    assert [s.handle for s in tt.sessions] == ["new-chea", "old-expe"]


def test_a_recent_ordered_view_says_so(projects_dir, session_log, null_cache):
    """The header gains `by recency` in both views, and an overview line shows
    the recency that ranked it — a re-ordered money column must never read as
    mis-sorted."""
    parent = session_log(cwd="/tmp/tt")
    parent.save(projects_dir)
    now = datetime.now(timezone.utc)

    scs = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)
    projects = cost.group_by_project(scs, order="recent")

    overview = render.render_cost_overview(projects, "this month", now, order="recent")
    assert "by recency" in overview.splitlines()[0]
    detail = render.render_cost_detail(projects[0], "this month", now, order="recent")
    assert "by recency" in detail.splitlines()[0]
    # the default view stays unmarked
    assert "by recency" not in render.render_cost_overview(projects, "this month", now)


def test_the_drill_down_marks_the_fold(projects_dir, session_log, null_cache):
    """The fold is visible, never silent: the token line says how many
    subagent transcripts it includes."""
    parent = session_log(cwd="/tmp/tt")
    agent = SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True).turn("delegated")
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    (sc,) = cost.scan_session_costs(projects_dir, _yesterday(), null_cache)
    (proj,) = cost.group_by_project([sc])
    text = render.render_cost_detail(proj, "this month", datetime.now(timezone.utc))

    assert "incl 1 subagent" in text
