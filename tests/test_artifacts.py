"""The Artifact store: the shape a Session Brief and an Audit share on disk.

Both are Out-of-Band Artifacts (ADR 0003 § the shared model) — durable-root
markdown with a frontmatter contract, written atomically, staled rather than
refreshed, pruned with the session log. These tests pin that one shape, and
pin the two adapters against it.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from standup import artifacts
from standup import audit as audit_mod
from standup import brief as brief_mod
from standup import claude_logs, cli, render, transcript


def test_a_store_lives_in_the_durable_root_never_the_cache(fake_home):
    """Neither artifact is recomputable, so a cache wipe must not destroy them
    (ADR 0003 § the shared model)."""
    for store in (brief_mod.STORE, audit_mod.STORE):
        path = store.path_for("sid")
        assert fake_home / ".standup" in path.parents
        assert "cache" not in path.parts


def test_frontmatter_round_trips_through_the_store():
    """What the generator writes is what the reader reads — every value type the
    two artifacts carry (text, timestamp, count, JSON object, JSON list)."""
    store = brief_mod.STORE
    generated = datetime(2026, 7, 22, 21, 40, tzinfo=timezone.utc)
    store.write("sid", {
        "objective": "Teach the inbox to read",
        "generated": generated,
        "siblings_considered": 4,
        "gen_usage": {"input_tokens": 12, "output_tokens": 340},
        "overhead": [{"label": "loop-expert", "model": "claude-sonnet-5"}],
        "status": None,          # absent values are omitted, not written empty
    }, "the body\n")

    doc = store.read("sid")

    assert doc is not None
    assert doc.frontmatter.text("objective") == "Teach the inbox to read"
    assert doc.frontmatter.timestamp("generated") == generated
    assert doc.frontmatter.count("siblings_considered") == 4
    assert doc.frontmatter.mapping("gen_usage") == {"input_tokens": 12,
                                                    "output_tokens": 340}
    assert doc.frontmatter.records("overhead") == [{"label": "loop-expert",
                                                    "model": "claude-sonnet-5"}]
    assert doc.frontmatter.text("status") is None
    assert doc.body == "the body"


# ── staleness: one comparison, one clock ───────────────────────────────────


def _log_written_at(projects_dir, session_log, when: datetime):
    """The fixture Session's log, with its *mtime* set to `when` — the one clock
    staleness is measured against (ADR 0003 § the shared model)."""
    log = session_log(cwd="/tmp/tt").save(projects_dir)
    os.utime(log, (when.timestamp(), when.timestamp()))
    return log


@pytest.mark.parametrize("lag, stale", [
    (timedelta(minutes=6), True),
    (artifacts.TOLERANCE, False),      # exactly at tolerance is not yet stale:
    (timedelta(0), False),             # the generator is allowed to lag that far
], ids=["past tolerance", "exactly at tolerance", "no lag"])
def test_a_session_that_grew_past_the_claim_stales_it(projects_dir, session_log,
                                                      lag, stale):
    generated = datetime.now(timezone.utc) - timedelta(hours=2)
    log = _log_written_at(projects_dir, session_log, generated + lag)

    assert artifacts.log_advanced_past(generated, log) is stale


def test_a_naive_generated_timestamp_is_read_as_utc(projects_dir, session_log):
    """Every writer stamps UTC, so a naive `generated` is a hand edit that
    dropped the offset — it is compared, not silently exempted from hedging."""
    aware = datetime.now(timezone.utc) - timedelta(hours=2)
    log = _log_written_at(projects_dir, session_log, aware + timedelta(minutes=6))

    assert artifacts.log_advanced_past(aware.replace(tzinfo=None), log) is True


def test_nothing_to_compare_against_never_hedges(projects_dir, session_log):
    """An undated artifact, or one whose Session log is gone, is left unhedged:
    silence about staleness is not evidence of it."""
    log = _log_written_at(projects_dir, session_log, datetime.now(timezone.utc))

    assert artifacts.log_advanced_past(None, log) is False
    assert artifacts.log_advanced_past(datetime.now(timezone.utc), None) is False
    assert artifacts.log_advanced_past(datetime.now(timezone.utc),
                                       log.parent / "gone.jsonl") is False


def test_every_surface_hedges_the_same_brief(projects_dir, session_log, null_cache):
    """Trustworthiness is a property of the artifact, so the Triage Inbox and
    the Transcript must never disagree about it (ADR 0003 § the shared model).
    The fixture's in-file timestamps predate the Brief; only the log's mtime
    says the Session moved on — and that is the clock both surfaces read."""
    generated = datetime.now(timezone.utc) - timedelta(minutes=30)
    log = _log_written_at(projects_dir, session_log, datetime.now(timezone.utc))
    brief_mod.save(log.stem, "Teach the inbox to read", "in-progress",
                   "did some things", "claude-haiku-4-5", None, generated)

    (session,) = claude_logs.scan_sessions(projects_dir, null_cache)
    inbox = brief_mod.load_for_sessions([session])
    transcript_text = transcript.render_transcript(log)

    assert inbox[log.stem].stale is True
    assert "may be stale" in transcript_text


# ── pruning: an orphan goes the way of a cache row ─────────────────────────


def _write_artifact(store, session_id: str) -> None:
    if store is brief_mod.STORE:
        brief_mod.save(session_id, "An objective", None, "body",
                       "claude-haiku-4-5", None, datetime.now(timezone.utc))
    else:
        audit_mod.save(session_id, "A title", 0, "## Verdict\nbody", [],
                       datetime.now(timezone.utc))


@pytest.mark.parametrize("store", [brief_mod.STORE, audit_mod.STORE],
                         ids=["briefs", "audits"])
def test_an_orphan_artifact_is_pruned_and_a_live_one_kept(store):
    _write_artifact(store, "live")
    _write_artifact(store, "dead")

    store.prune_orphans({"live"})

    assert store.read("live") is not None
    assert store.read("dead") is None


@pytest.mark.parametrize("store", [brief_mod.STORE, audit_mod.STORE],
                         ids=["briefs", "audits"])
def test_pruning_an_absent_store_is_not_an_error(store):
    """Nothing has ever been generated: pruning is best-effort, never raises."""
    assert not store.dir.exists()
    store.prune_orphans({"live"})


def test_the_inbox_prunes_both_kinds_of_artifact(projects_dir, scratch_repo,
                                                 session_log, capsys):
    """One inbox run drops the Briefs *and* the Audits of logs that are gone
    (ADR 0003 § the shared model)."""
    repo = scratch_repo("tt")
    log = session_log(cwd=str(repo.path)).save(projects_dir)
    for sid in (log.stem, "dead"):
        _write_artifact(brief_mod.STORE, sid)
        _write_artifact(audit_mod.STORE, sid)

    assert cli.main(["--projects-dir", str(projects_dir), "-j"]) == 0
    capsys.readouterr()

    assert brief_mod.load_one(log.stem) is not None
    assert audit_mod.load_one(log.stem) is not None
    assert brief_mod.load_one("dead") is None
    assert audit_mod.load_one("dead") is None


# ── one tolerance behind both the debounce and the hedge ───────────────────


@pytest.mark.parametrize("lag, fresh", [
    (timedelta(minutes=4), True),
    (timedelta(minutes=6), False),
], ids=["inside tolerance", "past tolerance"])
def test_the_generation_debounce_flips_where_the_hedge_does(projects_dir, session_log,
                                                            lag, fresh):
    """The generator declines to rewrite exactly while a reader would not hedge:
    one number, not two kept equal by a comment
    (ADR 0003 § the Artifact store)."""
    now = datetime.now(timezone.utc)
    generated = now - lag
    brief_mod.save("sid", "An objective", None, "body", "claude-haiku-4-5",
                   None, generated)
    os.utime(brief_mod.STORE.path_for("sid"),
             (generated.timestamp(), generated.timestamp()))
    log = _log_written_at(projects_dir, session_log, now)

    assert brief_mod.STORE.written_within_tolerance("sid", now) is fresh
    assert artifacts.log_advanced_past(generated, log) is not fresh


def test_a_generation_lock_can_be_taken_on_a_fresh_install():
    """The lock is the first thing a generation writes, and on a machine that
    has never produced an artifact its directory does not exist yet. The store
    owns the location, so the store makes the room."""
    assert not brief_mod.STORE.dir.exists()

    lock = brief_mod.STORE.lock_for("sid")
    os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))

    assert lock.parent == brief_mod.STORE.dir
    # and the lock is not an artifact: pruning leaves it to its generation
    brief_mod.STORE.prune_orphans(set())
    assert lock.exists()


# ── the two adapters, over the one store ───────────────────────────────────


def test_a_saved_brief_reads_back_as_the_brief_the_views_render():
    generated = datetime.now(timezone.utc)
    usage = {"input_tokens": 12, "output_tokens": 340}
    brief_mod.save("sid", "Teach the inbox to read", "in-progress",
                   "- did a thing", "claude-haiku-4-5", usage, generated)

    b = brief_mod.load_one("sid")

    assert (b.objective, b.status, b.model) == ("Teach the inbox to read",
                                                "in-progress", "claude-haiku-4-5")
    assert b.generated == generated
    assert b.body == "- did a thing"
    assert b.gen_usage == usage
    assert brief_mod.overhead_cost(b) > 0      # priced as Brief Overhead
    assert b.stale is False                    # a fresh claim is never hedged


def test_a_saved_audit_reads_back_with_its_itemised_overhead():
    generated = datetime.now(timezone.utc)
    overhead = [
        {"label": "loop-expert", "model": "claude-sonnet-5",
         "usage": {"input_tokens": 900, "output_tokens": 200}},
        {"label": "concluder", "model": "claude-opus-5",
         "usage": {"input_tokens": 400, "output_tokens": 600}},
        {"label": "no-usage", "model": "claude-sonnet-5", "usage": None},
    ]
    audit_mod.save("sid", "Teach the inbox to read", 12,
                   "## Verdict\nMostly scriptable.", overhead, generated)

    a = audit_mod.load_one("sid")

    assert a.target_title == "Teach the inbox to read"
    assert a.siblings_considered == 12
    assert a.body == "## Verdict\nMostly scriptable."
    # a pass with no recorded usage is not written, so it cannot be itemised
    assert [label for label, _ in a.overhead_items()] == ["loop-expert", "concluder"]
    assert a.overhead_cost == pytest.approx(sum(c for _, c in a.overhead_items()))
    assert a.overhead_cost > 0


def test_both_artifacts_render_as_marked_claims(projects_dir, session_log):
    """Neither is a derived fact, so both wear the `~` idiom and say when they
    are only a status, not a conclusion (CONTEXT.md → Session Brief, Audit)."""
    generated = datetime.now(timezone.utc)
    log = _log_written_at(projects_dir, session_log, generated)
    brief_mod.save(log.stem, "Teach the inbox to read", "in-progress",
                   "- read a fixture", "claude-haiku-4-5", None, generated)
    audit_mod.save(log.stem, "Teach the inbox to read", 3,
                   "## Verdict\nMostly scriptable.",
                   [{"label": "concluder", "model": "claude-opus-5",
                     "usage": {"input_tokens": 400, "output_tokens": 600}}],
                   generated)

    transcript_text = transcript.render_transcript(log)
    audit_text = cli._render_audit(audit_mod.load_one(log.stem),
                                   render._style(), 100)

    assert "~ brief · in-progress" in transcript_text
    assert "Teach the inbox to read" in transcript_text
    assert "- read a fixture" in transcript_text
    assert generated.date().isoformat() in transcript_text   # provenance line
    assert "may be stale" not in transcript_text             # nothing moved on
    assert "~ audit · " + generated.date().isoformat() in audit_text
    assert "3 siblings considered" in audit_text
    assert "concluder $" in audit_text                       # itemised overhead


def test_an_unreadable_artifact_degrades_to_absent():
    """A hand-edited file the reader cannot make sense of is not an error — the
    view shows what it showed before the artifact existed."""
    brief_mod.STORE.path_for("sid").parent.mkdir(parents=True, exist_ok=True)
    brief_mod.STORE.path_for("sid").write_text("no frontmatter, no objective\n")

    assert brief_mod.load_one("sid") is None
    assert brief_mod.load_one("never-generated") is None
    assert audit_mod.load_one("never-generated") is None
