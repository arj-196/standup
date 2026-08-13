"""`--json`: the payload is derived from the types, not hand-copied from them.

`standup --json` is the collect layer other tools read. A hand-written key list
is how a field added to `models.py` gets dropped from it silently — nothing
fails, the payload just quietly stops carrying something. `models.Commit`
gained `author_name` and the key appeared with no code change; these tests pin
that property rather than that one field.

Two payloads, two rules, because they serialise different things:

* the **inbox** payload carries *models*, so it is `asdict`-driven and every
  model field must reach it;
* the **cost** payload carries `cost`'s view types, whose figures live in
  properties over a held `UsageTotals` — serialising the fields would publish
  the fold instead of the figures. So it stays hand-written, and the guard is
  that it names every field *and* property, minus a listed few.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from standup import brief as brief_mod
from standup import cli, models
from standup.cost import ProjectCost, SessionCost
from standup.models import Brief

# The models the inbox payload serialises. `Rollup` is absent deliberately: it
# is a render-time join of a Session against one repo's pending files
# (`join.rollups`), computed below the payload and never in it.
PAYLOAD_MODELS = [models.RepoEntry, models.Checkout, models.PendingFile,
                  models.Commit, models.Attribution, models.Session,
                  models.Brief]
NOT_SERIALISED = {"Rollup"}

# `SessionCost` members the cost payload does not name, each because something
# else in it already says the same thing:
#   session  — expanded in place as `session_id`, `title` and `brief`
#   usage    — expanded in place as `cost`, `by_model`, `tokens`, `turns`
#   dominant_model — `max(by_model)`, which the consumer has
COST_SESSION_EXEMPT = {"session", "usage", "dominant_model"}


@pytest.fixture
def one_repo_inbox(scratch_repo, projects_dir, session_log):
    """A Repo Entry with all three tiers in play, a Session that claims the dirt,
    and a Brief on that Session — so every model in `PAYLOAD_MODELS` has at least
    one instance in the payload, `Attribution`, `Commit` and `Brief` included."""
    repo = scratch_repo("tt", remote=True)
    repo.write("alpha.py", "print('one')\n")
    sha = repo.commit("Add alpha")
    repo.push()
    repo.write("beta.py", "print('two')\n")
    repo.commit("Add beta")
    repo.write("alpha.py", "print('one, changed')\n")
    log = session_log(cwd=str(repo.path), sha=sha).save(projects_dir)
    brief_mod.save(Brief(session_id=log.stem, objective="Teach the inbox to read",
                         status="in-progress", generated=datetime.now(timezone.utc),
                         model="claude-haiku-4-5", body="- read a fixture",
                         gen_usage={"input_tokens": 12, "output_tokens": 340}))
    return repo


def _payload(capsys, *argv) -> dict:
    assert cli.main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def _keys(node) -> set[str]:
    """Every dict key anywhere in the tree."""
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def _surface(cls) -> set[str]:
    """A view type's whole public surface: its fields plus its properties."""
    return ({f.name for f in fields(cls)}
            | {n for n, v in vars(cls).items() if isinstance(v, property)})


def test_every_model_field_reaches_the_inbox_payload(one_repo_inbox, projects_dir,
                                                     capsys):
    payload = _payload(capsys, "--projects-dir", str(projects_dir), "-a", "-j")

    keys = _keys(payload)
    missing = {f"{m.__name__}.{f.name}" for m in PAYLOAD_MODELS
               for f in fields(m) if f.name not in keys}

    assert missing == set()


def test_the_payload_accounts_for_every_model_in_models_py():
    """The list above is only a guard while it is complete: a model added to
    `models.py` has to be either serialised or listed as deliberately not."""
    declared = {name for name, obj in vars(models).items()
                if is_dataclass(obj) and obj.__module__ == models.__name__}

    assert declared == {m.__name__ for m in PAYLOAD_MODELS} | NOT_SERIALISED


def test_a_model_field_round_trips_its_value(one_repo_inbox, projects_dir, capsys):
    """Names in the payload are not enough — the values have to be the models'.

    Two fields that a hand-written key list used to drop entirely stand in for
    the rest: a Session's `slug`/`last_prompt` half of the title fallback, and a
    Commit's `author_name`, which the commit header prints.
    """
    payload = _payload(capsys, "--projects-dir", str(projects_dir), "-a", "-j")

    (session,) = payload["sessions"]
    assert session["last_prompt"] == "teach the inbox to read a fixture"
    assert session["ai_title"] == "Teach the inbox to read"
    assert session["title"] == session["ai_title"]     # the derived property too
    assert session["log_path"].endswith(".jsonl")

    commits = [c for repo in payload["repos"] for co in repo["checkouts"]
               for c in co["unpushed"]] + [c for repo in payload["repos"]
                                           for c in repo["done"]]
    assert commits, "the fixture has an unpushed and a Done commit"
    assert all(c["author_name"] == "Standup Tests" for c in commits)


def test_the_cost_payload_names_every_view_type_member(projects_dir, capsys,
                                                       session_log):
    """The cost payload's guard: hand-written, but not free to omit. A property
    added to `SessionCost` or `ProjectCost` fails here until it is either in the
    payload or in the exempt list with a reason.
    """
    session_log(cwd="/tmp/tt").save(projects_dir)

    payload = _payload(capsys, "cost", "-s", "30d", "-j",
                       "--projects-dir", str(projects_dir))

    project = payload["projects"][0]
    session = project["sessions"][0]
    assert _surface(ProjectCost) - set(project) == set()
    assert _surface(SessionCost) - set(session) == COST_SESSION_EXEMPT
