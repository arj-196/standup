"""The one Session log reader (ADR 0001 § the one log reader).

`claude_logs.parse_log` is the typed reading every consumer is meant to share:
edit blocks, prompt text, and per-turn usage out of one pass over one file.
These tests pin the readings the views used to re-derive privately — the shapes
that drifted when three modules each parsed a `tool_use` block their own way.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from standup import cache as cache_mod
from standup import claude_logs, rates

from tests.support.sessions import SessionLog, fixture_session


def test_edit_blocks_carry_both_sides_of_every_recorded_edit(projects_dir):
    """An Edit records what went in and what came out; a Write records only
    what went in. Both are one EditBlock, in log order."""
    log = (SessionLog(cwd="/tmp/tt")
           .edit("/tmp/tt/alpha.py", old_string="x = 1", new_string="x = 2")
           .edit("/tmp/tt/beta.py", tool="Write", content="print('two')\n")
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [(e.tool, e.path, e.new, e.old) for e in parsed.edits] == [
        ("Edit", "/tmp/tt/alpha.py", "x = 2", "x = 1"),
        ("Write", "/tmp/tt/beta.py", "print('two')\n", ""),
    ]


def test_a_multiedit_fans_out_to_one_block_per_hunk_under_one_tool_id(projects_dir):
    """The hunks of a MultiEdit were a single action by the agent, and only the
    shared `tool_id` remembers that once they are separate blocks."""
    log = (SessionLog(cwd="/tmp/tt")
           .edit("/tmp/tt/alpha.py", tool="MultiEdit",
                 edits=[{"old_string": "a", "new_string": "A"},
                        {"old_string": "b", "new_string": "B"}])
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [(e.old, e.new) for e in parsed.edits] == [("a", "A"), ("b", "B")]
    assert len({e.tool_id for e in parsed.edits}) == 1


def test_a_multiedit_with_no_readable_hunks_still_records_the_path(projects_dir):
    """Attribution is path-overlap first (CONTEXT.md → Attribution Tier): the
    call says the Session touched this file, whatever the reader can make of
    its hunks. Losing the path here would quietly drop a Session Rollup."""
    log = (SessionLog(cwd="/tmp/tt")
           .edit("/tmp/tt/alpha.py", tool="MultiEdit")
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [(e.path, e.new, e.old) for e in parsed.edits] == [
        ("/tmp/tt/alpha.py", "", "")]
    assert set(parsed.session.edited_files) == {"/tmp/tt/alpha.py"}


def test_the_path_alias_and_the_new_text_fallback_read_as_one_shape(projects_dir):
    """A NotebookEdit names its target `notebook_path` and its text
    `new_source`. A relative path attributes nothing, so it is dropped rather
    than joined to a guess at a repo."""
    log = (SessionLog(cwd="/tmp/tt")
           .edit("/tmp/tt/nb.ipynb", tool="NotebookEdit", new_source="cell = 1")
           .edit("beta.py")
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [(e.tool, e.path, e.new) for e in parsed.edits] == [
        ("NotebookEdit", "/tmp/tt/nb.ipynb", "cell = 1")]


def test_a_prompt_is_what_you_typed_stripped_of_injected_noise(projects_dir):
    """System reminders are injected, not typed, and a turn that only carries a
    tool result is not a prompt at all."""
    log = (SessionLog(cwd="/tmp/tt")
           .prompt("fix the parser<system-reminder>be careful</system-reminder>")
           .turn("on it")
           .commit("abc1234")              # a tool result rides a user line
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [p.text for p in parsed.prompts] == ["fix the parser"]
    assert parsed.prompts[0].when is not None


def test_prose_beside_a_tool_result_is_a_prompt(projects_dir):
    """The one shape the Watch's old reading and the Transcript's parted on,
    settled here (ADR 0001 § the one log reader): prose makes a prompt whatever
    else rides the line, because what you typed while a call was in flight is
    something you typed. A result with no prose is still not a prompt."""
    log = (SessionLog(cwd="/tmp/tt")
           .prompt("run the suite")
           .call("Bash", command="pytest")
           .tool_result("all green")
           .tool_result("2 failed", prose="stop — try the other suite")
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [p.text for p in parsed.prompts] == ["run the suite",
                                                "stop — try the other suite"]


def test_every_tool_call_on_a_line_is_read_once_in_order(projects_dir):
    """The reading the Loop detector shapes and the Watch renders: every
    `tool_use` block, silent and file-touching ones included, carrying the id
    that joins it to its result and the uuid of the turn it lives in."""
    log = (SessionLog(cwd="/tmp/tt")
           .call("Read", file_path="/tmp/tt/alpha.py")
           .edit("/tmp/tt/alpha.py", old_string="x = 1", new_string="x = 2")
           .save(projects_dir))
    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    assistant = [obj for obj in lines if obj.get("type") == "assistant"]

    calls = [c for obj in assistant for c in claude_logs.tool_calls_in(obj)]

    assert [c.name for c in calls] == ["Read", "Edit"]
    assert calls[0].input == {"file_path": "/tmp/tt/alpha.py"}
    assert all(c.tool_id and c.turn_uuid for c in calls)
    # the file-touching one is the only one that reads as an edit, and it reads
    # as the block `parse_log` collected (which stamps it with the line's time)
    def _shape(e):
        return (e.tool, e.path, e.new, e.old, e.tool_id)

    assert [_shape(e) for c in calls for e in claude_logs.edits_of(c)] == \
        [_shape(e) for e in claude_logs.parse_log(log).edits]


def test_a_slash_command_reads_back_as_the_line_you_typed(projects_dir):
    """The command arrives wrapped in tags around a body nobody typed, and the
    body Claude Code splices in behind it rides an `isMeta` line — neither is a
    prompt."""
    log = (SessionLog(cwd="/tmp/tt")
           .prompt("<command-name>/tdd</command-name>"
                   "<command-args>the parser</command-args>")
           .meta("# Test-Driven Development\nthe injected skill body")
           .save(projects_dir))

    parsed = claude_logs.parse_log(log)

    assert [p.text for p in parsed.prompts] == ["/tdd the parser"]


def test_usage_totals_price_the_fixture_through_the_rate_card(projects_dir, session_log):
    """Notional Cost off the parsed log, with no `claude` binary and no
    network: the Rate Card prices what the reader typed."""
    session = session_log(cwd="/tmp/tt")
    log = session.save(projects_dir)

    parsed = claude_logs.parse_log(log)
    totals = parsed.totals

    assert [t.model for t in parsed.turns] == [m for m, _ in session.usages]
    assert totals.turns == len(session.usages)
    assert totals.unpriced_turns == 0
    assert totals.cost == pytest.approx(
        sum(rates.turn_cost(m, u) for m, u in session.usages))
    assert totals.tokens["output"] == sum(
        u["output_tokens"] for _, u in session.usages)
    assert totals.by_model == {
        session.usages[0][0]: pytest.approx(totals.cost)}


def test_an_unpriced_model_is_counted_never_priced_at_zero(projects_dir):
    """A model the Rate Card has never heard of is a gap in the card, not a
    free turn."""
    log = (SessionLog(cwd="/tmp/tt")
           .turn("future model", model="claude-nova-6")
           .turn("a priced one")
           .save(projects_dir))

    totals = claude_logs.parse_log(log).totals

    assert (totals.turns, totals.unpriced_turns) == (1, 1)
    assert totals.cost > 0


def test_the_per_turn_pricing_modifiers_survive_the_typed_reading(projects_dir):
    """Fast mode, the batch tier, US inference geo and web search each move a
    turn's price, and each rides the turn's own `usage` — a reading that keeps
    only token counts prices this turn wrong."""
    usage = {
        "input_tokens": 1_200, "output_tokens": 900,
        "cache_read_input_tokens": 40_000,
        "cache_creation_input_tokens": 3_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1_000,
                           "ephemeral_1h_input_tokens": 2_000},
        "speed": "fast", "service_tier": "batch", "inference_geo": "us",
        "server_tool_use": {"web_search_requests": 3},
    }
    log = (SessionLog(cwd="/tmp/tt")
           .turn("expensive", model="claude-opus-5", usage=usage)
           .save(projects_dir))

    (turn,) = claude_logs.parse_log(log).turns

    assert turn.cost == pytest.approx(rates.turn_cost("claude-opus-5", usage))
    assert turn.tokens == rates.turn_tokens(usage)


def test_the_parsed_log_carries_the_session_the_inbox_scan_reads(
        projects_dir, null_cache):
    """The typed reading is a superset of the Triage Inbox's scan, not a rival
    dialect: same cwd, same title, same edited files, same captured hashes."""
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)

    parsed = claude_logs.parse_log(log).session
    (scanned,) = claude_logs.scan_sessions(projects_dir, null_cache)

    assert parsed.cwd == scanned.cwd == "/tmp/tt"
    assert parsed.title == scanned.title == "Teach the inbox to read"
    assert parsed.branches == scanned.branches == {"main"}
    assert parsed.edited_files == scanned.edited_files
    assert set(parsed.edited_files) == {"/tmp/tt/alpha.py", "/tmp/tt/beta.py"}
    assert parsed.commit_hashes == scanned.commit_hashes
    assert set(parsed.commit_hashes) == {"abc1234"}


def test_the_full_reading_sees_branches_the_prefiltered_sweep_misses(
        projects_dir, null_cache):
    """The one place the two readings of a Session differ, pinned so the
    migration onto the full reading is a decision rather than a surprise: the
    inbox's sweep only ever parses lines its prefilter admits, so a branch
    that moved between an edit and a title is invisible to it."""
    log = (SessionLog(cwd="/tmp/tt", branch="main")
           .edit("/tmp/tt/alpha.py")
           .save(projects_dir))
    moved = SessionLog(cwd="/tmp/tt", branch="feature").turn("on the branch")
    with open(log, "a") as fh:
        fh.write(moved.to_jsonl())

    (scanned,) = claude_logs.scan_sessions(projects_dir, null_cache)

    assert scanned.branches == {"main"}
    assert claude_logs.parse_log(log).session.branches == {"main", "feature"}


def test_last_activity_means_the_logs_mtime_in_every_reading(
        projects_dir, null_cache):
    """One meaning on the parsed Session (ADR 0001 § the one log reader): when
    the log last grew. "When the model last spoke" is a different question and
    carries a different name."""
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)
    mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)

    parsed = claude_logs.parse_log(log)
    (scanned,) = claude_logs.scan_sessions(projects_dir, null_cache)

    assert parsed.session.last_activity == scanned.last_activity == mtime
    assert parsed.last_turn is not None and parsed.last_turn < mtime


def test_an_unchanged_log_is_read_once_and_served_from_the_cache(
        projects_dir, monkeypatch):
    """The Derived Cache is an accelerator over the *whole* reading
    (ADR 0001 § the Derived Cache): a warm read reproduces every typed list
    without opening the file again."""
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)
    cache = cache_mod.open_cache()
    cold = claude_logs.read_log(log, cache)
    cache.flush()

    def _never(*a, **kw):
        raise AssertionError("a warm read must not reparse the log")

    monkeypatch.setattr(claude_logs, "parse_log", _never)
    warm = claude_logs.read_log(log, cache_mod.open_cache())

    assert warm.edits == cold.edits
    assert warm.prompts == cold.prompts
    assert warm.turns == cold.turns
    assert warm.totals.cost == pytest.approx(cold.totals.cost)
    assert warm.session.title == cold.session.title
    assert warm.session.edited_files == cold.session.edited_files
    assert warm.session.commit_hashes == cold.session.commit_hashes
    assert warm.session.last_activity == cold.session.last_activity


def test_a_log_that_grew_is_read_again(projects_dir):
    """Keyed on (size, mtime_ns), so an appended turn is never missed."""
    session = fixture_session(cwd="/tmp/tt")
    log = session.save(projects_dir)
    cache = cache_mod.open_cache()
    claude_logs.read_log(log, cache)
    cache.flush()

    session.prompt("and now the second half")
    session.save(projects_dir)

    reread = claude_logs.read_log(log, cache_mod.open_cache())
    assert [p.text for p in reread.prompts][-1] == "and now the second half"


def test_the_reading_survives_a_cache_that_cannot_hold_it(projects_dir, null_cache):
    """A cache failure costs a reparse and nothing else — the reading is the
    same with the cache switched off."""
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)

    assert claude_logs.read_log(log, null_cache) == claude_logs.parse_log(log)


def test_a_malformed_cached_row_costs_a_reparse_and_nothing_else(projects_dir):
    """A row this version cannot read is not an error the user ever sees."""
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)
    st = log.stat()
    cache = cache_mod.open_cache()
    cache.put_log(log.stem, st.st_size, st.st_mtime_ns, {"session": {}, "junk": 1})
    cache.flush()

    parsed = claude_logs.read_log(log, cache_mod.open_cache())

    assert parsed == claude_logs.parse_log(log)


def test_a_reading_too_large_to_store_is_declined_not_truncated(
        projects_dir, monkeypatch):
    """The cache may refuse a row — it is a pure accelerator
    (ADR 0001 § the Derived Cache) — and refusing changes no output."""
    log = fixture_session(cwd="/tmp/tt").save(projects_dir)
    monkeypatch.setattr(cache_mod, "MAX_BLOB_BYTES", 1)
    cache = cache_mod.open_cache()
    first = claude_logs.read_log(log, cache)
    cache.flush()

    assert first == claude_logs.parse_log(log)
    st = log.stat()
    assert cache_mod.open_cache().get_log(log.stem, st.st_size, st.st_mtime_ns) is None


def test_an_older_cache_db_gains_the_new_table_and_keeps_its_rows(fake_home):
    """The Derived Cache is disposable, but it is not thrown away for a new
    slot: a DB written before the reader had one is upgraded in place."""
    old = cache_mod.open_cache()
    old.put_session("sid", 1, 1, {"cwd": "/tmp/tt"})
    old.flush()
    conn = sqlite3.connect(str(cache_mod.CACHE_PATH))
    conn.execute("DROP TABLE logs")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()

    upgraded = cache_mod.open_cache()

    assert not isinstance(upgraded, cache_mod.NullCache)
    assert upgraded.get_session("sid", 1, 1) == {"cwd": "/tmp/tt"}
    assert upgraded.get_log("sid", 1, 1) is None      # the new table is there
