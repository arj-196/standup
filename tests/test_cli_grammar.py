"""The two-grammar rewrite (ADR 0005 § two grammars).

`cli._normalize` is the whole of grammar B: `standup <repo> <view>` is rewritten
to `standup <view> <repo>` before dispatch, so there is one implementation and
no parallel command tree. A table, because the rule is a table — every row is a
spelling the ADR either promises or refuses.
"""

from __future__ import annotations

import pytest

from standup import cli


# (name, argv, expected) — expected is the rewritten argv.
UNCHANGED = [
    ("empty", []),
    ("bare view", ["cost"]),
    ("verb-first with repo", ["cost", "st"]),
    ("verb-first alias with repo", ["c", "st"]),
    ("verb-first behind an option", ["-j", "cost", "st"]),
    # options only: the inbox's own flags, no view to hand them to
    ("options only", ["-a", "-j"]),
    ("long options only", ["--all"]),
    # audit stays verb-first and is untouched in that spelling
    ("verb-first audit", ["audit", "45e5247"]),
    # a view name whose preceding token is an option is not a repo/view pair:
    # `--in` owns `diff` here, so the rewrite declines and argparse complains
    ("view preceded by an option", ["st", "--in", "diff"]),
    # a bare repo is still the drill-down, not a pair
    ("bare repo", ["st"]),
]

REWRITTEN = [
    ("repo then view", ["st", "diff"], ["diff", "st"]),
    ("repo then alias", ["st", "d"], ["d", "st"]),
    ("repo then watch", ["st", "watch"], ["watch", "st"]),
    ("repo then watch alias", ["st", "w"], ["w", "st"]),
    ("repo then cost", ["st", "cost"], ["cost", "st"]),
    # a path is a repo too, recognised by shape (ADR 0005 § Project Handles)
    ("path-shaped repo", [".", "diff"], ["diff", "."]),
    ("relative path repo", ["../tt", "watch"], ["watch", "../tt"]),
    # the view's own argument rides along after the repo. A bare hex is a
    # Session Handle; only the `@` sigil makes it a commit
    # (ADR 0005 § a commit hash)
    ("commit hash argument", ["st", "diff", "@abc1234"], ["diff", "st", "@abc1234"]),
    ("session handle argument", ["st", "diff", "45e5247"], ["diff", "st", "45e5247"]),
    ("trailing option", ["st", "watch", "-s", "30m"], ["watch", "st", "-s", "30m"]),
    # `session`'s positional is a Session Handle, so the repo goes onto `--in`
    ("repo then session", ["st", "session"], ["session", "--in", "st"]),
    ("repo then session alias", ["st", "s"], ["s", "--in", "st"]),
    ("repo then session with handle", ["st", "session", "45e5247"],
     ["session", "--in", "st", "45e5247"]),
    # the option splice: a leading value-taking option in front of the pair.
    # Found by positional scan — the rewrite never learns that `-s` swallows the
    # token after it — and the options are handed to the parser that runs.
    ("option splice", ["-s", "2h", "st", "watch"], ["watch", "st", "-s", "2h"]),
    ("long option splice", ["--since", "2h", "st", "watch"],
     ["watch", "st", "--since", "2h"]),
    ("boolean option splice", ["-j", "st", "cost"], ["cost", "st", "-j"]),
    ("options both sides", ["-j", "st", "diff", "-n"], ["diff", "st", "-j", "-n"]),
    # the spellings that made the scan positional: the rewrite used to bail on
    # a leading `-`, so these errored with `unrecognized arguments: diff`. The
    # rewrite succeeds and `diff` — which never had `-a` — is left to complain
    # about the flag rather than the view (ADR 0005 § the reserved-letter rule)
    ("flag the view refuses", ["-a", "st", "diff"], ["diff", "st", "-a"]),
    ("long flag the view refuses", ["--all", "st", "diff"], ["diff", "st", "--all"]),
]


@pytest.mark.parametrize("argv", [a for _, a in UNCHANGED],
                         ids=[n for n, _ in UNCHANGED])
def test_verb_first_is_left_alone(argv):
    assert cli._normalize(list(argv)) == argv


@pytest.mark.parametrize("argv,expected", [(a, e) for _, a, e in REWRITTEN],
                         ids=[n for n, _, _ in REWRITTEN])
def test_object_first_is_rewritten(argv, expected):
    assert cli._normalize(list(argv)) == expected


@pytest.mark.parametrize("argv", [["tt", "audit"], ["tt", "a"], ["-j", "tt", "audit"]],
                         ids=["audit", "alias", "behind an option"])
def test_object_first_audit_is_rejected(argv):
    """`audit` is the one paid view, so it must name its target explicitly —
    `standup tt audit` is an error, not a shorthand (ADR 0005 § two grammars)."""
    with pytest.raises(SystemExit) as exc:
        cli._normalize(list(argv))
    msg = str(exc.value)
    assert "audit" in msg
    assert "tt" in msg                        # the message quotes what was typed
    assert "standup audit <handle>" in msg    # and names the spelling that works


def test_audit_is_absent_from_the_object_first_table():
    assert "audit" not in cli.OBJECT_FIRST
    assert cli.OBJECT_FIRST == {"cost", "watch", "session", "diff"}


def test_no_repo_named_reads_the_option_value_as_one():
    """The accepted cost of the positional scan (ADR 0005 § the reserved-letter
    rule): `standup -s 3d diff` names no repo, so `3d` is read as one and `-s`
    is left stranded without its value. Input that was already an error."""
    assert cli._normalize(["-s", "3d", "diff"]) == ["diff", "3d", "-s"]


def test_a_stranded_option_is_refused_by_the_view_that_runs(capsys):
    """And the complaint names the flag, not the view: leading options belong
    to the parser that runs, so `diff` refuses the `-s` it never had."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["-s", "3d", "diff"])

    assert exc.value.code == 2
    assert "unrecognized arguments: -s" in capsys.readouterr().err


def test_normalize_does_not_mutate_its_argument():
    argv = ["st", "diff"]
    cli._normalize(argv)
    assert argv == ["st", "diff"]
