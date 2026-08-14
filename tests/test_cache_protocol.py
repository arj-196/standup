"""The Derived Cache's accelerator protocol, and the disposability contract.

Two things are pinned here, both properties of the *protocol* rather than of
any one derivation (ADR 0001 § the Derived Cache):

* **a declaration is the whole cost of a new derivation.** Kind, codec,
  version and liveness are declared; the table, the stat key, the invalidation,
  the buffered write and the prune all follow from the declaration. A test that
  registers a throwaway derivation and gets all of it is what keeps the next
  cached artifact from arriving as another method pair plus a flush branch.
* **the cache is disposable**, which is the reason `rm -rf ~/.standup/cache` is
  always safe. Four ways it can be unusable — no writable root, a corrupt DB, a
  version its rows were not written for, and a log that no longer exists — and
  none of them may change a byte of output or reach the user as an error.

The byte-identity half of the contract is checked over *every* declaration, so
a derivation added without a view exercising it fails here.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from standup import cache as cache_mod
from standup import claude_logs, cli

from tests.support.sessions import SessionLog


def _never(*a, **kw):
    raise AssertionError("this value should have been served from the cache")


def _rows(kind: str) -> list[str]:
    """The keys a table holds, read straight from the DB — pruning is otherwise
    unobservable, and the contract is about rows."""
    conn = sqlite3.connect(str(cache_mod.CACHE_PATH))
    try:
        spec = next(s for s in cache_mod.DERIVED if s.kind == kind)
        return sorted(r[0] for r in
                      conn.execute(f"SELECT {spec.key_column} FROM {kind}"))
    finally:
        conn.close()


@pytest.fixture
def universe(scratch_repo, projects_dir, session_log):
    """One Repo Entry that exercises all four derivations: a Session with edits
    and a captured commit (`sessions`, `logs`), a commit no Session captured so
    attribution has to ask git for its files (`commit_files`), tool calls to
    detect over (`loops`), and a subagent transcript, whose `logs` row is the
    one keyed on something the Session sweep never enumerates."""
    repo = scratch_repo("tt", remote=True)
    repo.write("alpha.py", "print('one')\n")
    sha = repo.commit("Add alpha")
    repo.push()
    repo.write("beta.py", "print('two')\n")
    repo.commit("Add beta by hand")
    repo.write("alpha.py", "print('one, changed')\n")
    parent = session_log(cwd=str(repo.path), sha=sha)
    parent.save(projects_dir)
    (SessionLog(session_id="a1", cwd=str(repo.path), sidechain=True)
     .turn("delegated").save_subagent(projects_dir, parent))
    return repo


def _run(capsys, projects_dir, *argv) -> str:
    assert cli.main([*argv, "--projects-dir", str(projects_dir)]) == 0
    return capsys.readouterr().out


VIEWS = [
    (),                          # the Triage Inbox overview
    ("-a",),                     # the retrospective
    ("tt",),                     # the drill-down
    ("tt", "diff", "-P"),        # the Attributed Diff
    ("cost", "-s", "30d", "-P"),
    ("cost", "tt", "-s", "30d", "-P"),
    ("session", "1a2b3c4d", "-P"),
]


# --- the protocol -----------------------------------------------------------


def test_a_declaration_is_the_whole_cost_of_a_new_derivation(
        projects_dir, monkeypatch):
    """Kind, codec, version, liveness — declared; everything else derived. No
    method pair on the cache, no branch in `flush`, no table in `prune`."""
    log = SessionLog(cwd="/tmp/tt").prompt("hello").save(projects_dir)
    widgets = cache_mod.Derivation(
        kind="widgets", codec=cache_mod.ZLIB_JSON,
        version_column="widget_version", version=lambda: 1,
        live_keys=claude_logs.session_log_ids)
    monkeypatch.setattr(cache_mod, "DERIVED", (*cache_mod.DERIVED, widgets))
    stamp = cache_mod.Stamp.of(log)

    cold = cache_mod.open_cache()          # the table follows from the declaration
    assert cold.derive(widgets, log.stem, stamp, compute=lambda: {"n": 1}) == {"n": 1}
    cold.flush()

    warm = cache_mod.open_cache()
    assert warm.derive(widgets, log.stem, stamp, compute=_never) == {"n": 1}
    warm.prune(projects_dir)               # its own enumerator, its own rows
    warm.flush()
    assert _rows("widgets") == [log.stem]

    log.unlink()
    dead = cache_mod.open_cache()
    dead.prune(projects_dir)
    dead.flush()
    assert _rows("widgets") == []


def test_a_derivation_declines_a_row_it_cannot_encode(projects_dir, monkeypatch):
    """Declining is a right the accelerator has, and taking it changes no
    output: the value comes back, the row simply is not there next time."""
    log = SessionLog(cwd="/tmp/tt").prompt("hello").save(projects_dir)
    monkeypatch.setattr(cache_mod, "MAX_BLOB_BYTES", 1)
    stamp = cache_mod.Stamp.of(log)

    cold = cache_mod.open_cache()
    assert cold.derive(cache_mod.LOGS, log.stem, stamp,
                       compute=lambda: {"n": 1}) == {"n": 1}
    cold.flush()

    assert _rows("logs") == []
    assert cache_mod.open_cache().derive(
        cache_mod.LOGS, log.stem, stamp, compute=lambda: {"n": 2}) == {"n": 2}


# --- the disposability contract ---------------------------------------------


def test_a_root_that_cannot_be_written_falls_back_to_a_null_cache(
        universe, projects_dir, fake_home, capsys, monkeypatch):
    """No cache at all is a supported state: the CLI answers the same, and
    never says a word about it."""
    with_cache = _run(capsys, projects_dir)

    blocked = fake_home / "not-a-directory"
    blocked.write_text("")
    monkeypatch.setattr(cache_mod, "CACHE_PATH", blocked / "cache" / "cache.db")
    assert isinstance(cache_mod.open_cache(), cache_mod.NullCache)

    assert _run(capsys, projects_dir) == with_cache
    assert capsys.readouterr().err == ""


def test_a_corrupt_db_is_rebuilt_without_a_word(universe, projects_dir, capsys):
    """A file that is not a database at all — the shape a half-written or
    externally clobbered cache takes — is deleted and rebuilt, silently."""
    good = _run(capsys, projects_dir)
    cache_mod.CACHE_PATH.write_bytes(b"this is not a database" * 100)

    assert _run(capsys, projects_dir) == good
    assert capsys.readouterr().err == ""
    assert not isinstance(cache_mod.open_cache(), cache_mod.NullCache)
    assert _rows("sessions"), "the rebuilt DB was not written to"


def test_a_db_from_a_newer_schema_is_started_over(universe, projects_dir, capsys):
    """The one mismatch the generated DDL cannot repair in place: a DB stamped
    by a Standup whose columns this one may not know. Rebuilt, not half-read."""
    good = _run(capsys, projects_dir)
    conn = sqlite3.connect(str(cache_mod.CACHE_PATH))
    conn.execute(f"PRAGMA user_version = {cache_mod.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    assert _run(capsys, projects_dir) == good
    assert _rows("sessions"), "the rebuilt DB was not written to"


def test_a_version_bump_invalidates_that_derivations_rows_only(
        universe, projects_dir, capsys, monkeypatch):
    """Each derivation's version is its owner's, read at query time: bumping
    the reader's re-reads the logs and leaves the sweep's rows alone."""
    before = _run(capsys, projects_dir, "cost", "-s", "30d", "-P")
    swept = _rows("sessions")

    reads = []
    real_parse = claude_logs.parse_log
    monkeypatch.setattr(claude_logs, "parse_log",
                        lambda p: reads.append(p) or real_parse(p))
    monkeypatch.setattr(claude_logs, "READER_VERSION",
                        claude_logs.READER_VERSION + 1)

    assert _run(capsys, projects_dir, "cost", "-s", "30d", "-P") == before
    assert reads, "a bumped reader version must not be served the old rows"
    assert _rows("sessions") == swept


def test_the_rows_of_a_session_that_is_gone_are_pruned(
        universe, projects_dir, capsys, monkeypatch):
    """Liveness is the declaration's, not the sweep's: every derivation keyed
    on a log enumerates its own keys, so a Session log that is deleted takes
    its rows with it — and the subagent transcript's row, which no sweep of
    Sessions can see, stays (ADR 0002 § subagent usage)."""
    _run(capsys, projects_dir)
    _run(capsys, projects_dir, "cost", "-s", "30d", "-P")
    sid = "1a2b3c4d-0000-4000-8000-000000000001"
    assert _rows("sessions") == [sid]
    assert _rows("loops") == [sid]
    assert _rows("logs") == sorted([sid, f"{sid}/agent-a1"])

    for log in projects_dir.glob("*/*.jsonl"):
        log.unlink()
    _run(capsys, projects_dir)

    assert _rows("sessions") == []
    assert _rows("loops") == []
    assert _rows("logs") == [f"{sid}/agent-a1"]


# --- byte-identity, over every declaration ----------------------------------


@pytest.mark.parametrize("argv", VIEWS, ids=lambda a: "-".join(a) or "inbox")
def test_every_view_is_byte_identical_warm_cold_and_deleted(
        universe, projects_dir, fake_home, capsys, argv, monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    cache_dir = fake_home / ".standup" / "cache"

    cold = _run(capsys, projects_dir, *argv)
    assert cache_dir.is_dir(), "the first run should have written a cache"
    warm = _run(capsys, projects_dir, *argv)
    shutil.rmtree(cache_dir)
    deleted = _run(capsys, projects_dir, *argv)

    assert cold == warm
    assert cold == deleted


def test_the_views_above_cover_every_declaration(
        universe, projects_dir, capsys, monkeypatch):
    """The guard on the parametrisation above: a derivation no view exercises
    is a derivation nothing proves the accelerator's contract for."""
    monkeypatch.setenv("COLUMNS", "100")
    for argv in VIEWS:
        _run(capsys, projects_dir, *argv)

    assert {spec.kind for spec in cache_mod.DERIVED if not _rows(spec.kind)} == set()
