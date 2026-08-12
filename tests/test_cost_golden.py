"""What `standup cost` prints, frozen.

The cost view was migrated off a JSONL scanner of its own and onto the shared
cached reading (ADR 0001 § the one log reader). The migration's contract is
that *nothing a user sees moves*: same dollars, same buckets, same marks, same
ranking. These goldens were captured from the view before the migration, so a
figure that shifts fails here rather than in someone's month-end review.

Wall-clock stamps are normalised (`humanize` prints local `HH:MM`) — the
recency *position* is pinned by the ordering tests, not by the clock.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from standup import cli

from tests.support.sessions import SessionLog

# a turn heavy in output tokens, on a second model family — what makes the
# overview's model split and the `out-heavy` why-tag appear
BIG = {"input_tokens": 900, "output_tokens": 120_000,
       "cache_read_input_tokens": 4_000, "cache_creation_input_tokens": 1_000}

_TIME_RE = re.compile(r"(?:yesterday |[A-Z][a-z]{2} )?\d\d:\d\d")


def _normalise(text: str) -> str:
    return _TIME_RE.sub("<time>", text)


def _universe(projects_dir) -> None:
    """Three Sessions over two projects: a delegating one, one that spent a
    turn on a model the Rate Card has no row for, and one elsewhere."""
    now = datetime.now(timezone.utc)
    parent = (SessionLog(session_id="aaaaaaaa-0000-4000-8000-000000000001",
                         cwd="/tmp/tt", start=now - timedelta(hours=3))
              .prompt("fan the work out").ai_title("Heavy lifting")
              .turn("spawning").turn("reporting"))
    agent = (SessionLog(session_id="a1", cwd="/tmp/tt", sidechain=True,
                        start=now - timedelta(hours=2)).turn("delegated"))
    parent.save(projects_dir)
    agent.save_subagent(projects_dir, parent)

    (SessionLog(session_id="bbbbbbbb-0000-4000-8000-000000000002",
                cwd="/tmp/tt", start=now - timedelta(hours=1))
     .prompt("try the new model")
     .turn("from the future", model="claude-nova-6")
     .turn("and back again")
     .save(projects_dir))

    (SessionLog(session_id="cccccccc-0000-4000-8000-000000000003",
                cwd="/tmp/other", start=now - timedelta(minutes=30))
     .prompt("write the long one").custom_title("Long output")
     .turn("at length", model="claude-opus-5", usage=BIG)
     .save(projects_dir))


def _run(capsys, *argv) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


OVERVIEW = """\
COST · last 30d
notional API-equivalent load — not money paid (real spend: claude.ai)

  $3.01  other   1 session   opus 100%
  $0.07  tt   2 sessions   sonnet 100%
  ─────
  $3.08  total  ·  opus $3.01 · sonnet $0.07
"""

DETAIL = """\
tt — $0.07 notional · last 30d
notional API-equivalent load — not money paid (real spend: claude.ai)

  $0.05  aaaaaaaa  "Heavy lifting"  sonnet  cache-heavy
         in 36 · out 1k · cache-w 10k · cache-r 84k · incl 1 subagent · <time>
         standup session aaaaaaaa

  $0.02  bbbbbbbb  "try the new model"  sonnet  cache-heavy
         in 12 · out 340 · cache-w 4k · cache-r 28k · <time>
         standup session bbbbbbbb

"""


def test_the_overview_is_what_it_was(projects_dir, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    _universe(projects_dir)

    out = _run(capsys, "cost", "-s", "30d", "-P", "--projects-dir", str(projects_dir))

    assert _normalise(out) == OVERVIEW


def test_the_drill_down_is_what_it_was(projects_dir, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    _universe(projects_dir)

    out = _run(capsys, "cost", "tt", "-s", "30d", "-P",
               "--projects-dir", str(projects_dir))

    assert _normalise(out) == DETAIL


def test_the_json_payload_is_what_it_was(projects_dir, capsys):
    _universe(projects_dir)

    payload = json.loads(_run(capsys, "cost", "-s", "30d", "-j",
                              "--projects-dir", str(projects_dir)))

    # the two stamps that move with the clock, checked for shape and dropped
    assert payload.pop("generated_at")
    assert payload.pop("window_start")
    stamps = [s.pop("last_activity") for p in payload["projects"]
              for s in p["sessions"]]
    assert all(stamps)
    assert payload == {
        "window": "last 30d",
        "order": "cost",
        "disclaimer": "Notional Cost — API-equivalent load, not money paid. "
                      "Real spend: claude.ai only.",
        "total": 3.0838,
        "projects": [
            {
                "name": "other",
                "path": "/tmp/other",
                "cost": 3.0128,
                "brief_overhead": 0.0,
                "brief_count": 0,
                "audit_overhead": 0.0,
                "audit_count": 0,
                "by_model": {"claude-opus-5": 3.0128},
                "sessions": [
                    {
                        "handle": "cccccccc",
                        "session_id": "cccccccc-0000-4000-8000-000000000003",
                        "title": "Long output",
                        "brief": None,
                        "cost": 3.0128,
                        "by_model": {"claude-opus-5": 3.0128},
                        "tokens": {"input": 900, "output": 120000,
                                   "cache_write": 1000, "cache_read": 4000},
                        "turns": 1,
                        "subagents": 0,
                        "why": "out-heavy",
                        "loop_cost": 0.0,
                        "loops": [],
                    },
                ],
            },
            {
                "name": "tt",
                "path": "/tmp/tt",
                "cost": 0.0711,
                "brief_overhead": 0.0,
                "brief_count": 0,
                "audit_overhead": 0.0,
                "audit_count": 0,
                "by_model": {"claude-sonnet-5": 0.0711},
                "sessions": [
                    {
                        "handle": "aaaaaaaa",
                        "session_id": "aaaaaaaa-0000-4000-8000-000000000001",
                        "title": "Heavy lifting",
                        "brief": None,
                        "cost": 0.0533,
                        "by_model": {"claude-sonnet-5": 0.0533},
                        "tokens": {"input": 36, "output": 1020,
                                   "cache_write": 10500, "cache_read": 83988},
                        "turns": 3,
                        "subagents": 1,
                        "why": "cache-heavy",
                        "loop_cost": 0.0,
                        "loops": [],
                    },
                    {
                        "handle": "bbbbbbbb",
                        "session_id": "bbbbbbbb-0000-4000-8000-000000000002",
                        "title": "try the new model",
                        "brief": None,
                        "cost": 0.0178,
                        "by_model": {"claude-sonnet-5": 0.0178},
                        "tokens": {"input": 12, "output": 340,
                                   "cache_write": 3500, "cache_read": 27996},
                        "turns": 1,
                        "subagents": 0,
                        "why": "cache-heavy",
                        "loop_cost": 0.0,
                        "loops": [],
                    },
                ],
            },
        ],
    }
