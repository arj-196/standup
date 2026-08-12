"""Suite-wide fixtures, and the isolation that makes them safe.

The suite must run with no network, no `claude` binary, and no real
`~/.claude` — and, just as important, no real `~/.standup`, which is a
**durable** root: it holds Session Briefs and Audits that nothing can recompute
(ADR 0001 § the Derived Cache). A test that wrote there would be destroying the
user's data, not just polluting a temp dir.

`fake_home` is autouse and does both halves of that job:

* `HOME` is repointed, which covers every `expanduser("~")` evaluated at call
  time — the `--projects-dir` defaults, `handles`' home-relative display, and
  the `git` subprocesses `gitstate` runs (also cut off from global and system
  git config, so the developer's own `user.email` or aliases cannot change an
  answer);
* the durable-root constants are rebound module by module, because they were
  frozen at *import* time and an `HOME` change arrives too late for them.
  `cache.open_cache` needs the same treatment for the same reason: its default
  argument captured `CACHE_PATH` when the module was imported.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from standup import artifacts as artifacts_mod
from standup import cache as cache_mod
from standup import install as install_mod

from tests.support.repos import ScratchRepo, make_repo
from tests.support.sessions import SessionLog, fixture_session

# (module, attribute) pairs holding a path under the durable `~/.standup` root
# or under `~/.claude`, captured at import. Every Session Brief and Audit hangs
# off `artifacts.ROOT` — one rebinding covers both kinds, and any kind added
# later — because an Artifact store resolves its directory from that root at
# call time (ADR 0003 § the Artifact store).
_DURABLE_ROOTS = [
    (artifacts_mod, "ROOT", ()),
    (cache_mod, "CACHE_PATH", ("cache", "cache.db")),
    (install_mod, "SETTINGS", None),   # ~/.claude/settings.json — see below
]


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory, monkeypatch) -> Path:
    """A throwaway `$HOME`, with every path Standup froze at import redirected
    into it. Autouse: opting in per test is how one forgets."""
    home = tmp_path_factory.mktemp("home")
    standup_root = home / ".standup"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))          # expanduser on Windows
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    # git reads three config scopes; HOME only moves the first of them
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")        # never block on a prompt
    monkeypatch.delenv("GIT_DIR", raising=False)

    for module, attr, parts in _DURABLE_ROOTS:
        target = (home / ".claude" / "settings.json" if parts is None
                  else standup_root.joinpath(*parts))
        monkeypatch.setattr(module, attr, target)

    # open_cache's default argument captured the old CACHE_PATH at import time,
    # so rebinding the constant alone would leave `open_cache()` writing to the
    # real cache.
    real_open_cache = cache_mod.open_cache
    monkeypatch.setattr(
        cache_mod, "open_cache",
        lambda path=None: real_open_cache(cache_mod.CACHE_PATH if path is None else path))
    return home


@pytest.fixture
def projects_dir(fake_home) -> Path:
    """An empty `~/.claude/projects` — the Scan Universe's root, in the layout
    both scanners glob (`<cwd-slug>/<sessionId>.jsonl`). Write into it with
    `SessionLog.save()`."""
    d = fake_home / ".claude" / "projects"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def null_cache():
    """The Derived Cache, switched off. Output is byte-identical warm or cold
    (ADR 0001 § the Derived Cache), so a test that is not *about* caching should
    not be running one."""
    return cache_mod.NullCache()


@pytest.fixture
def scratch_repo(tmp_path):
    """Factory for scratch git repos: `scratch_repo("tt")`,
    `scratch_repo("tt", remote=True)`. Paths land under the test's tmp dir."""
    def _make(name: str = "tt", **kwargs) -> ScratchRepo:
        return make_repo(tmp_path / name, **kwargs)
    return _make


@pytest.fixture
def session_log():
    """Factory for the canonical fixture Session (titles, two edits, a commit
    hash, per-turn usage). Pass `cwd=` to point it at a scratch repo."""
    def _make(**kwargs) -> SessionLog:
        return fixture_session(**kwargs)
    return _make
