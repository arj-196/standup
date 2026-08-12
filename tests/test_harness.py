"""The harness testing itself.

A fixture Session that no scanner can read, or a scratch repo `gitstate` does
not recognise, is worse than no harness: every test built on it would be
green about nothing. These tests pin the two helpers against the readers they
exist to feed — `claude_logs`/`cost` for the log, `gitstate` for the repo — and
pin the isolation that keeps a test run away from the real `~/.claude`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from standup import cache as cache_mod
from standup import audit as audit_mod
from standup import brief as brief_mod
from standup import claude_logs, cost, gitstate, install, rates

from tests.support.repos import make_repo
from tests.support.sessions import SessionLog, fixture_session, project_dir_name


# --- the fixture Session ------------------------------------------------


def test_project_dir_name_mimics_claude_code():
    """Claude Code names a project directory after its cwd, every non-alphanumeric
    character replaced by a dash."""
    assert project_dir_name("/Users/arjun/Personal/apps/standup") == \
        "-Users-arjun-Personal-apps-standup"


def test_fixture_session_lands_where_the_scanner_globs(projects_dir):
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)
    assert log.parent.parent == projects_dir
    assert log in set(projects_dir.glob("*/*.jsonl"))
    assert log.stem == fixture_session(cwd="/tmp/tt").session_id


def test_the_inbox_scanner_reads_the_fixture_session(projects_dir, null_cache):
    fixture_session(cwd="/tmp/tt").save(projects_dir)

    sessions = claude_logs.scan_sessions(projects_dir, null_cache)

    assert len(sessions) == 1
    s = sessions[0]
    assert s.cwd == "/tmp/tt"
    assert s.title == "Teach the inbox to read"          # the ai-title
    assert s.branches == {"main"}
    assert set(s.edited_files) == {"/tmp/tt/alpha.py", "/tmp/tt/beta.py"}
    assert set(s.commit_hashes) == {"abc1234"}


def test_titles_follow_their_precedence(projects_dir, null_cache):
    """custom > ai > slug > last prompt (models.Session.title)."""
    SessionLog(session_id="0" * 36, cwd="/tmp/tt").prompt("do the thing") \
        .ai_title("An AI title").custom_title("What Arj called it").save(projects_dir)

    (s,) = claude_logs.scan_sessions(projects_dir, null_cache)
    assert s.title == "What Arj called it"
    assert s.ai_title == "An AI title"
    assert s.last_prompt == "do the thing"


def test_the_cost_scanner_prices_the_fixture_turns(projects_dir, session_log):
    session = session_log(cwd="/tmp/tt")
    session.save(projects_dir)
    window_start = datetime.now(timezone.utc) - timedelta(days=1)

    (sc,) = cost.scan_session_costs(projects_dir, window_start)

    assert sc.turns == len(session.usages)
    assert sc.unpriced_turns == 0
    assert sc.cost == pytest.approx(
        sum(rates.turn_cost(m, u) for m, u in session.usages))
    assert sc.tokens["output"] == sum(u["output_tokens"] for _, u in session.usages)
    # the two scanners must agree about what a Session is *called*
    assert sc.title == "Teach the inbox to read"


def test_a_session_can_be_pointed_at_a_scratch_repo(
        scratch_repo, projects_dir, session_log, null_cache):
    """The join the whole domain rests on: a Session's `cwd` is a real repo, and
    the two halves of the harness meet there. Written through the conftest
    fixtures, which is how most tests will reach the builders."""
    repo = scratch_repo("tt")
    session_log(cwd=str(repo.path)).save(projects_dir)

    (s,) = claude_logs.scan_sessions(projects_dir, null_cache)
    assert s.cwd == str(repo.path)
    # the fixture Session's edits name files this repo could hold
    assert set(s.edited_files) == {f"{repo.path}/alpha.py", f"{repo.path}/beta.py"}


def test_a_subagent_transcript_lands_beside_its_parent(projects_dir, session_log):
    """`.subagent()` writes where Claude Code does — under the parent's own
    `<sessionId>/subagents/` directory — so the cost scanner folds it into the
    parent (ADR 0002 § subagent usage) and no top-level glob mistakes it for a
    Session of its own."""
    parent = session_log(cwd="/tmp/tt")
    agent = parent.subagent("abc123").turn("delegated work")
    parent_log = parent.save(projects_dir)
    agent_log = agent.save(projects_dir)

    assert agent_log == (parent_log.parent / parent_log.stem
                         / "subagents" / "agent-abc123.jsonl")
    # both scanners glob */*.jsonl for Sessions; the transcript is out of reach
    assert set(projects_dir.glob("*/*.jsonl")) == {parent_log}
    # sidechain-marked and parent-keyed, like the real ones
    line = json.loads(agent_log.read_text().splitlines()[0])
    assert line["isSidechain"] is True
    assert line["agentId"] == "abc123"
    assert line["sessionId"] == parent.session_id


# --- the scratch repo ---------------------------------------------------


def test_a_scratch_repo_has_no_remote_by_default(tmp_path):
    repo = make_repo(tmp_path / "tt")
    repo.write("alpha.py", "print('one')\n")
    repo.commit("Add alpha")

    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())

    assert entry.name == "tt"
    assert entry.has_remote is False
    assert len(entry.checkouts) == 1
    assert entry.checkouts[0].branch == "main"
    # committed is terminal in a Remoteless Repo (ADR 0006)
    assert entry.checkouts[0].unpushed == []
    assert [c.subject for c in entry.done] == ["Add alpha", "Initial commit"]


def test_a_repo_with_a_remote_reports_unpushed_commits(tmp_path):
    repo = make_repo(tmp_path / "tt", remote=True)
    repo.write("alpha.py", "print('one')\n")
    sha = repo.commit("Add alpha")

    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())

    assert entry.has_remote is True
    assert [c.short for c in entry.checkouts[0].unpushed] == [sha]
    # Done is "reached its terminal state": the pushed initial commit, and not
    # the one still sitting in the Unpushed tier
    assert [c.subject for c in entry.done] == ["Initial commit"]

    repo.push()
    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())
    assert entry.checkouts[0].unpushed == []
    assert [c.subject for c in entry.done] == ["Add alpha", "Initial commit"]


def test_pending_files_show_up_as_pending(scratch_repo):
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('dirty')\n")

    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())

    assert [(f.code, f.path) for f in entry.checkouts[0].pending] == [("??", "alpha.py")]
    assert entry.needs_decision is True


def test_a_worktree_folds_into_its_parent_repo_entry(tmp_path):
    repo = make_repo(tmp_path / "tt")
    wt = repo.add_worktree(tmp_path / "tt-feature", branch="feature")
    wt.write("beta.py", "print('two')\n")

    entries = gitstate.discover_repos([str(repo.path), str(wt.path)], _yesterday())

    assert len(entries) == 1                       # one Repo Entry, two checkouts
    (entry,) = entries
    assert [c.branch for c in entry.checkouts] == ["main", "feature"]
    assert entry.checkouts[0].is_main is True
    assert [f.path for f in entry.checkouts[1].pending] == ["beta.py"]


def test_the_commit_helper_returns_the_short_sha_the_log_would_carry(tmp_path):
    """The sha a Session's `git commit` stdout would announce — the join key
    between a fixture Session and a scratch repo."""
    repo = make_repo(tmp_path / "tt")
    repo.write("alpha.py", "x = 1\n")
    sha = repo.commit("Add alpha")

    assert repo.git("rev-parse", "--short", "HEAD") == sha
    (entry,) = gitstate.discover_repos([str(repo.path)], _yesterday())
    assert entry.done[0].short == sha


# --- isolation ----------------------------------------------------------


def test_the_real_home_is_out_of_reach(fake_home):
    assert Path(os.path.expanduser("~")) == fake_home
    assert Path.home() == fake_home
    assert not (fake_home / ".claude" / "projects").exists() or \
        list((fake_home / ".claude" / "projects").iterdir()) == []


@pytest.mark.parametrize("path", [
    lambda: cache_mod.CACHE_PATH,
    lambda: brief_mod.BRIEFS_DIR,
    lambda: audit_mod.AUDITS_DIR,
    lambda: install.SETTINGS,
], ids=["cache", "briefs", "audits", "settings"])
def test_durable_roots_point_inside_the_fake_home(path, fake_home):
    assert fake_home in Path(path()).parents


def test_open_cache_defaults_into_the_fake_home(fake_home):
    c = cache_mod.open_cache()
    c.put_session("sid", 1, 1, {"cwd": "/tmp/tt"})
    c.flush()
    assert not isinstance(c, cache_mod.NullCache)
    assert fake_home in cache_mod.CACHE_PATH.parents
    assert cache_mod.CACHE_PATH.exists()


def _yesterday() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)
