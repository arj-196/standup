"""The Codex dialect (ADR 0001 § two dialects, one reading).

`codex_logs` is the one module that knows a rollout's schema; everything above
it holds the typed reading `logs` defines and cannot tell a Codex Session from a
Claude Code one. These tests pin the schema facts that dialect hides — the
model on a different line from the usage, cwd-relative patch paths, cached
input counted inside `input_tokens`, injected user items, the fork-suffixed
file name — and then that the views built on the shared reading show a Codex
Session exactly as they show a Claude one, tagged.

Everything runs over temp rollouts under a temp `~/.codex` (`codex_dir`) plus a
scratch git repo: no real `~/.codex`, no Derived Cache unless a test is about
it, no `codex` binary.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from standup import cache as cache_mod
from standup import cli, codex_logs, cost, logs, rates, transcript, universe
from standup.watchstream import WatchStream

from tests.support.codex_sessions import MODEL, CodexLog, fixture_codex_session
from tests.support.sessions import SessionLog


# --- the reading ------------------------------------------------------------


def test_a_rollout_is_read_into_the_same_typed_reading_a_claude_log_is(codex_dir):
    """`logs.parse_log` dispatches by the file's shape and comes back with a
    `ParsedLog`: the Session tagged `codex`, its cwd and branch from
    `session_meta`, its edits, prompts and turns."""
    log = fixture_codex_session(cwd="/tmp/tt", sha="abc1234").save(codex_dir)

    parsed = logs.parse_log(log)

    assert logs.dialect_of(log).name == "codex"
    assert parsed.session.agent == "codex"
    assert parsed.session.session_id == "01a00000-0000-7000-8000-000000000001"
    assert parsed.session.cwd == "/tmp/tt"
    assert parsed.session.branches == {"main"}
    assert [p.text for p in parsed.prompts] == ["teach the inbox to read a rollout"]
    assert parsed.session.commit_hashes.keys() == {"abc1234"}
    assert set(parsed.session.edited_files) == {"/tmp/tt/alpha.py", "/tmp/tt/beta.py"}
    assert len(parsed.turns) == 4 and parsed.totals.unpriced_turns == 0


def test_apply_patch_hunks_are_edit_blocks_with_paths_joined_to_the_cwd(codex_dir):
    """The patch format is cwd-relative by definition, so joining is reading
    the format; each `@@` section is one block under the call's one tool id,
    and an added file is a block with nothing taken out."""
    log = (CodexLog(cwd="/tmp/tt")
           .prompt("edit two things")
           .patch("src/alpha.py", hunks=[("x = 1", "x = 2"), ("y = 1", "y = 2")])
           .add_file("/tmp/tt/beta.py", "print('two')\n")
           .save(codex_dir))

    edits = logs.parse_log(log).edits

    assert [(e.tool, e.path, e.new, e.old) for e in edits] == [
        ("apply_patch", "/tmp/tt/src/alpha.py", "x = 2", "x = 1"),
        ("apply_patch", "/tmp/tt/src/alpha.py", "y = 2", "y = 1"),
        ("apply_patch", "/tmp/tt/beta.py", "print('two')", ""),
    ]
    assert len({e.tool_id for e in edits[:2]}) == 1
    assert edits[0].tool_id != edits[2].tool_id


def test_a_deleted_file_is_a_path_only_block():
    hunks = codex_logs.parse_patch(
        "*** Begin Patch\n*** Delete File: gone.py\n*** End Patch", "/tmp/tt")
    assert hunks == [("/tmp/tt/gone.py", "", "", True)]


def test_injected_user_items_are_not_prompts(codex_dir):
    """Codex splices its own items into the user's side — an environment block,
    the plugin list, AGENTS.md instructions, an attachment's closing tag — and
    none of that is what you typed."""
    log = (CodexLog(cwd="/tmp/tt")
           .environment()
           .prompt("fix the thing", injected=(
               "<recommended_plugins>\nHere is a list…\n</recommended_plugins>",
               "# AGENTS.md instructions for /tmp/tt\n\n- be nice",
               "\n# Files mentioned by the user:\n\n## shot.png: /tmp/shot.png",
               '<image name=[Image #1] path="/tmp/shot.png">', "</image>"))
           .save(codex_dir))

    parsed = logs.parse_log(log)

    assert [p.text for p in parsed.prompts] == ["fix the thing"]
    assert parsed.session.title == "fix the thing"


def test_the_title_is_the_first_prompt_because_the_last_is_usually_y(codex_dir):
    log = (CodexLog(cwd="/tmp/tt").prompt("build the exporter").turn("shall I?")
           .prompt("y").save(codex_dir))
    s = logs.parse_log(log).session
    assert (s.first_prompt, s.last_prompt, s.title) == ("build the exporter", "y",
                                                        "build the exporter")


def test_an_aborted_turn_is_an_interrupt_and_never_a_prompt(codex_dir):
    log = (CodexLog(cwd="/tmp/tt").prompt("run it").shell("sleep 600").aborted()
           .prompt("stop", injected=(
               "<turn_aborted>\nThe user interrupted the previous turn.\n</turn_aborted>",))
           .save(codex_dir))
    reader = logs.reader(log)
    import json
    objs = [json.loads(l) for l in log.read_text().splitlines()]
    aborted = [o for o in objs if reader.is_interrupt(o)]
    assert len(aborted) == 1
    assert [p.text for p in logs.parse_log(log).prompts] == ["run it", "stop"]


def test_usage_reads_the_uncached_input_and_the_model_from_the_turn_context(codex_dir):
    """`input_tokens` includes the cached share in Codex's counts, and the usage
    line names no model — an earlier `turn_context` did (ADR 0002 § Codex
    usage). The Rate Card then prices the turn as it prices any other."""
    log = (CodexLog(cwd="/tmp/tt")
           .prompt("price me")
           .turn("ok", model="gpt-5.4", usage={
               "input_tokens": 30_000, "cached_input_tokens": 20_000,
               "cache_write_input_tokens": 0, "output_tokens": 400,
               "reasoning_output_tokens": 100, "total_tokens": 30_400})
           .save(codex_dir))

    (turn,) = logs.parse_log(log).turns

    assert (turn.model, turn.input_tokens, turn.cache_read_tokens,
            turn.output_tokens) == ("gpt-5.4", 10_000, 20_000, 400)
    expected = (10_000 * 2.5 + 20_000 * 2.5 * rates.CACHE_READ + 400 * 15.0) / 1e6
    assert turn.cost == pytest.approx(expected)


def test_a_model_the_rate_card_lacks_is_unpriced_never_zero(codex_dir):
    log = (CodexLog(cwd="/tmp/tt", model="gpt-9-nova").prompt("hi").turn("hello")
           .save(codex_dir))
    totals = logs.parse_log(log).totals
    assert (totals.turns, totals.unpriced_turns, totals.cost) == (0, 1, 0.0)


def test_a_review_thread_is_no_session(codex_dir):
    """`thread_source` `guardian_review` / `subagent` are Codex reading your
    session, not you: no cwd, so the scan drops the rollout."""
    (CodexLog(cwd="/tmp/tt", thread_source="guardian_review").prompt("review this")
     .turn("looks fine").save(codex_dir))
    (CodexLog(cwd="/tmp/tt", session_id="01a00000-0000-7000-8000-000000000002")
     .prompt("a real one").save(codex_dir))

    sessions = logs.scan_sessions(logs.Roots(codex=codex_dir), cache_mod.NullCache())

    assert [s.session_id[-1] for s in sessions] == ["2"]


def test_a_forked_rollout_takes_its_own_id_from_the_file_name(codex_dir):
    """A resumed thread writes `rollout-<ts>-<parent>_<own>.jsonl` with the
    parent's id inside; the Session Handle is the file's own, so two files
    never answer to one handle."""
    parent = CodexLog(cwd="/tmp/tt", session_id="01a00000-0000-7000-8000-00000000aaaa")
    parent.prompt("start").save(codex_dir)
    fork = CodexLog(cwd="/tmp/tt", session_id="01a00000-0000-7000-8000-00000000bbbb",
                    forked_from=parent.session_id)
    fork.prompt("continue").save(codex_dir)

    roots = logs.Roots(codex=codex_dir)
    ids = sorted(logs.session_id_of(p) for p in roots.session_logs())

    assert ids == [parent.session_id, fork.session_id]
    assert transcript.resolve_handle(roots, "01a00000-0000-7000-8000-00000000bb").name \
        == fork.file_name()


def test_archived_rollouts_are_sessions_too(codex_dir):
    log = CodexLog(cwd="/tmp/tt").prompt("old").save(codex_dir, archived=True)
    assert logs.Roots(codex=codex_dir).session_logs() == [log]


# --- the line reader, as the Watch and the Transcript use it -----------------


def test_the_shell_call_carries_its_command_and_its_exit_status(scratch_repo, codex_dir):
    """`exec_command` is Codex's `Bash`: the Call renders `$ …`, and the result
    line's `Process exited with code N` is the ✓/✗ (ADR 0004 § Calls). An MCP
    function carries its server, shortened the way Claude's are."""
    repo = scratch_repo("tt")
    (CodexLog(cwd=str(repo.path))
     .prompt("run the suite")
     .shell("pytest -q").result("1 failed", exit_code=1)
     .call("notion-fetch", namespace="mcp__notion", id="abc")
     .result('{"ok": true}', exit_code=None)
     .save(codex_dir))

    with universe.open_universe(codex_dir=codex_dir) as u:
        events = WatchStream.discover(u, str(repo.path)).start()

    calls = [(e.tool, e.command, e.args) for e in events if e.kind == "call"]
    assert calls == [("exec_command", "pytest -q", ""), ("notion·notion-fetch", None, "abc")]
    verdicts = [e.ok for e in events if e.kind == "call_result"]
    assert verdicts == [False, True]


def test_a_patch_is_a_file_event_in_the_watch(scratch_repo, codex_dir):
    repo = scratch_repo("tt")
    repo.write("alpha.py", "one\n")
    (CodexLog(cwd=str(repo.path))
     .prompt("teach alpha to count")
     .patch("alpha.py", old="one", new="two")
     .save(codex_dir))

    with universe.open_universe(codex_dir=codex_dir) as u:
        events = WatchStream.discover(u, str(repo.path)).start()

    assert [(e.kind, e.message) for e in events if e.kind == "prompt"] == \
        [("prompt", "teach alpha to count")]
    assert [(e.path, e.added, e.removed) for e in events if e.kind == "file"] == \
        [("alpha.py", "two", "one")]


def test_the_activity_state_reads_codexs_turn_edges(scratch_repo, codex_dir, monkeypatch):
    """Codex states its edges outright: a shell call is `running`, its result
    leaves the model `thinking`, `task_complete` settles it
    (ADR 0004 § the Activity State)."""
    from standup import watchstream
    repo = scratch_repo("tt")
    running = CodexLog(cwd=str(repo.path)).prompt("go").shell("pytest -q")
    thinking = (CodexLog(cwd=str(repo.path), session_id="01a00000-0000-7000-8000-000000000002")
                .prompt("go").shell("pytest -q").result("ok"))
    settled = (CodexLog(cwd=str(repo.path), session_id="01a00000-0000-7000-8000-000000000003")
               .prompt("go").turn("done").complete())
    for log in (running, thinking, settled):
        log.save(codex_dir)

    with universe.open_universe(codex_dir=codex_dir) as u:
        ws = WatchStream.discover(u, str(repo.path), live_window=timedelta(days=1))
    ws.start()
    verbs = {sid[-1]: (i.activity.verb if i.activity else None)
             for sid, i in ((s, ws.session_info(s)) for s in ws.tailers)}

    assert verbs == {"1": "running", "2": "thinking", "3": None}


# --- the views, over both agents at once -----------------------------------


def test_the_inbox_shows_a_codex_session_tagged(scratch_repo, projects_dir, codex_dir,
                                                capsys):
    """One Scan Universe from two roots: a Claude Code Session and a Codex one
    in the same repo both claim their files, and the Codex one wears the
    `codex` tag where it is named."""
    repo = scratch_repo("tt")
    repo.write("alpha.py", "print('one')\n")
    repo.write("beta.py", "print('two')\n")
    repo.commit("Add both")
    repo.write("alpha.py", "print('one, changed')\n")
    repo.write("beta.py", "print('two, changed')\n")
    (SessionLog(cwd=str(repo.path)).prompt("claude here")
     .edit(f"{repo.path}/alpha.py").save(projects_dir))
    (CodexLog(cwd=str(repo.path)).prompt("codex here").patch("beta.py", old="a", new="b")
     .save(codex_dir))

    assert cli.main(["--projects-dir", str(projects_dir), "--codex-dir", str(codex_dir),
                     "tt"]) == 0
    out = capsys.readouterr().out

    claude_line = next(l for l in out.splitlines() if "claude here" in l)
    codex_line = next(l for l in out.splitlines() if "codex here" in l)
    assert '~ "codex here"  codex' in codex_line       # the tag follows the title
    assert "claude" not in claude_line.split('"claude here"')[1]   # no tag


def test_the_cost_view_prices_both_agents_from_one_rate_card(projects_dir, codex_dir,
                                                             null_cache):
    claude = SessionLog(cwd="/tmp/tt").prompt("a").turn("b")
    claude.save(projects_dir)
    codex = fixture_codex_session(cwd="/tmp/tt")
    codex.save(codex_dir)

    roots = logs.Roots(claude=projects_dir, codex=codex_dir)
    costs = {sc.session.agent: sc for sc in cost.scan_session_costs(
        roots, cli._EPOCH, null_cache)}

    assert set(costs) == {"claude", "codex"}
    expected = sum(rates.turn_cost(m, u) for m, u in codex.usages)
    assert costs["codex"].cost == pytest.approx(expected)
    assert costs["codex"].dominant_model == MODEL
    assert costs["codex"].title == "teach the inbox to read a rollout"


def test_the_transcript_renders_a_codex_session(codex_dir, capsys):
    """Prompts, the agent's prose under a `codex` rule, tool one-liners through
    the shared renderer, and the per-turn cost — with the usage read off the
    line *after* the response it prices."""
    log = (CodexLog(cwd="/tmp/tt")
           .prompt("say hi then run ls")
           .turn("Hi.")
           .shell("ls -la").result("total 0")
           .usage()
           .complete()
           .save(codex_dir))

    text = transcript.render_transcript(log, show_tools=True)

    assert "── you" in text and "say hi then run ls" in text
    assert "── codex" in text and "Hi." in text
    assert "⏺ exec_command ls -la" in text
    assert "$0." in text                     # priced, from the trailing usage lines
    assert "total 0" not in text             # a result is never shown


def test_the_digest_labels_the_agent(codex_dir):
    log = CodexLog(cwd="/tmp/tt").prompt("hello").turn("hi there").save(codex_dir)
    text = transcript.digest(log, max_chars=10_000, head_chars=5_000, numbered=True)
    assert "USER: hello" in text
    assert "] CODEX: hi there" in text


def test_a_codex_row_survives_the_prune_and_is_served_warm(codex_dir, monkeypatch):
    """The Derived Cache's liveness enumerates every root
    (ADR 0001 § the accelerator protocol): a Codex reading is live while its
    rollout is, and a warm run does not reparse it."""
    log = fixture_codex_session(cwd="/tmp/tt").save(codex_dir)
    roots = logs.Roots(codex=codex_dir)

    cache = cache_mod.open_cache()
    cold = logs.read_log(log, cache)
    logs.scan_sessions(roots, cache)          # the sweep is where prune is asked
    cache.flush()

    def _never(*a, **kw):
        raise AssertionError("a warm read must not reparse the rollout")
    monkeypatch.setattr(codex_logs, "parse_log", _never)

    warm = logs.read_log(log, cache_mod.open_cache())
    assert warm.session.title == cold.session.title
    assert [e.path for e in warm.edits] == [e.path for e in cold.edits]
    assert warm.totals.cost == pytest.approx(cold.totals.cost)


def test_a_universe_with_only_a_codex_root_opens(codex_dir, tmp_path):
    fixture_codex_session(cwd="/tmp/tt").save(codex_dir)
    with universe.open_universe(tmp_path / "no-claude", codex_dir) as u:
        assert [s.agent for s in u.sessions()] == ["codex"]


def test_no_root_at_all_is_one_named_failure(tmp_path):
    with pytest.raises(universe.UniverseError) as e:
        with universe.open_universe(tmp_path / "nowhere", tmp_path / "nor-here"):
            pass
    assert "no Claude Code logs found" in str(e.value) and "Codex" in str(e.value)
