"""Generating the two Out-of-Band Artifacts, with the model faked out
(ADR 0003 § the LLM-pass seam).

The seam makes the *generators* testable: a Session Brief's gating, locking,
digesting, output parsing and storing, and the Audit's Expert Panel fan-out —
who sees which evidence, what the concluder is handed, how Overhead is itemised
— all run here against canned text, with no `claude` binary and no network.

What is deliberately **not** pinned: the prompt wording. A prompt is a claim
about how to ask, not a contract; these tests assert which *evidence* reached
which pass.
"""

from __future__ import annotations

import pytest

from standup import audit as audit_mod
from standup import auditgen, briefgen
from standup import brief as brief_mod
from standup import cache as cache_mod
from standup import install as install_mod
from standup import universe
from standup.llmpass import PassError

from tests.support.llm import BRIEF_REPLY
from tests.support.sessions import SessionLog


# ── a Session worth summarising ────────────────────────────────────────────

def _session(cwd: str = "/tmp/tt") -> SessionLog:
    """A small Session with prose, an Edit and a plain Call — the shape both
    digests have to survive, tool calls included."""
    return (SessionLog(cwd=cwd)
            .ai_title("Teach the inbox to read")
            .prompt("render the brief under the title")
            .turn("Reading the store first.")
            .call("Read", file_path=f"{cwd}/brief.py")
            .edit(f"{cwd}/render.py", old_string="a", new_string="b")
            .turn("Done."))


# ── the Session Brief ──────────────────────────────────────────────────────

@pytest.mark.parametrize("reply, expected", [
    (BRIEF_REPLY, ("Teach the inbox to read Session Briefs", "in-progress",
                   "- read the store\n- rendered the objective")),
    # the format is a contract with a model, so it is read leniently: casing and
    # surrounding prose are not the claim
    ("objective: Ship the cost view\nstatus: DONE\nsummary:\n- shipped",
     ("Ship the cost view", "done", "- shipped")),
    # a status outside the four is no status at all, never a made-up one
    ("OBJECTIVE: Ship it\nSTATUS: nearly\nSUMMARY:\n- some of it",
     ("Ship it", None, "- some of it")),
    # objective-only is legitimate: the body is what `standup session` shows
    ("OBJECTIVE: Ship it", ("Ship it", None, "")),
    # no objective is nothing worth showing
    ("I could not read the session.", ("", None, "")),
])
def test_the_brief_output_format_is_parsed_leniently(reply, expected):
    """The Brief's reply format (`OBJECTIVE` / `STATUS` / `SUMMARY`) is the one
    thing a Brief pass is contracted on, and the parse is what turns a model's
    text into the frontmatter contract (ADR 0003 § the Session Brief)."""
    assert briefgen.parse_output(reply) == expected


def test_generating_a_brief_stores_the_objective_and_its_overhead(
        projects_dir, fake_llm):
    """The whole generation path with the model faked: digest → pass → parse →
    store. The Brief that lands is the one a render path reads, and its recorded
    `usage` is what prices Brief Overhead (ADR 0003 § the Session Brief)."""
    log = _session().save(projects_dir)
    fake = fake_llm(reply=BRIEF_REPLY)

    path = briefgen.generate(log.stem, log, transport=fake)

    assert path is not None and path.exists()
    b = brief_mod.load_one(log.stem)
    assert b is not None
    assert b.objective == "Teach the inbox to read Session Briefs"
    assert b.status == "in-progress"
    assert "read the store" in b.body
    assert b.model == briefgen.MODEL
    assert brief_mod.overhead_cost(b) > 0


def test_the_digest_carries_the_session_including_its_tool_calls(
        projects_dir, fake_llm):
    """What the pass is shown is the Session, not the log: your prompts, Claude's
    prose, and each tool call as a one-liner. A digest that fell over on a
    session containing a tool call would leave every real session Brief-less."""
    log = _session().save(projects_dir)
    fake = fake_llm(reply=BRIEF_REPLY)

    briefgen.generate(log.stem, log, transport=fake)

    (p,) = fake.passes
    assert p.label == "brief" and p.model == briefgen.MODEL
    assert "USER: render the brief under the title" in p.context
    assert "CLAUDE: Reading the store first." in p.context
    assert "Read" in p.context and "Edit" in p.context
    # the instruction is the prompt; the evidence is context, never argv-bound
    assert "OBJECTIVE:" in p.prompt and p.prompt not in p.context


def test_an_over_budget_digest_keeps_head_and_tail_and_marks_the_elision(
        projects_dir, fake_llm):
    """A long Session is cut in the middle — the head sets the objective, the
    tail says where it landed — and the cut is marked rather than silent."""
    log = _session()
    for i in range(400):
        log.turn(f"step {i} " + "x" * 200)
    saved = log.prompt("and finally, ship it").save(projects_dir)
    fake = fake_llm(reply=BRIEF_REPLY)

    briefgen.generate(saved.stem, saved, transport=fake)

    (p,) = fake.passes
    assert len(p.context) <= briefgen.MAX_DIGEST + 40
    assert "USER: render the brief under the title" in p.context
    assert "USER: and finally, ship it" in p.context
    assert "elided" in p.context


def test_a_failed_pass_stores_no_brief_and_leaves_no_lock(
        projects_dir, fake_llm):
    """A summariser problem must never break a session, and must never leave a
    lock behind that stops the next one (ADR 0003 § the Artifact store)."""
    log = _session().save(projects_dir)
    fake = fake_llm(raises={"brief": PassError("brief: empty result")})

    assert briefgen.generate(log.stem, log, transport=fake) is None
    assert brief_mod.load_one(log.stem) is None
    assert not brief_mod.STORE.lock_for(log.stem).exists()


def test_an_objectiveless_reply_stores_no_brief(projects_dir, fake_llm):
    """A Brief with no objective is not worth showing, so it is not written: the
    Session renders title-only, exactly as before Briefs existed."""
    log = _session().save(projects_dir)

    assert briefgen.generate(log.stem, log,
                             transport=fake_llm(reply="no idea, sorry")) is None
    assert brief_mod.load_one(log.stem) is None


def test_a_generation_in_flight_declines_the_second_one(projects_dir, fake_llm):
    """The lockfile is the debounce's other half: while one generation holds it,
    a second declines rather than paying for the same Brief twice
    (ADR 0003 § the Artifact store)."""
    log = _session().save(projects_dir)
    lock = brief_mod.STORE.lock_for(log.stem)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    fake = fake_llm(reply=BRIEF_REPLY)

    assert briefgen.generate(log.stem, log, transport=fake) is None
    assert fake.passes == []
    assert lock.exists()          # the holder's lock is not stolen or removed


# ── the Audit's Expert Panel ───────────────────────────────────────────────

PANEL_REPLIES = {
    "loop-expert": "- the Read→Edit loop is scriptable (t2)",
    "llm-as-cpu": "No findings.",
    "prompt-structure": "- the instruction belongs in CLAUDE.md",
    "recurrence": "- recurred in 1 sibling, combined Loop Cost $0.10",
    "concluder": "## Verdict\nMostly hand work.\n\n## Handoff Prompt\n(no handoff)",
}


def _panel(fake_llm, projects_dir, repo, **kwargs):
    """The target Session plus a sibling in the same Repo Entry, and a Universe
    over them — the deterministic evidence a panel is assembled from."""
    target = _session(cwd=str(repo.path)).save(projects_dir)
    (SessionLog(session_id="9f8e7d6c-0000-4000-8000-000000000002",
                cwd=str(repo.path))
     .ai_title("A sibling that ground the same way")
     .prompt("do it again")
     .call("Read", file_path=f"{repo.path}/brief.py")
     .edit(f"{repo.path}/brief.py", old_string="a", new_string="b")
     .save(projects_dir))
    fake = fake_llm(replies={**PANEL_REPLIES, **kwargs})
    return fake, target


def test_the_panel_fans_out_to_four_experts_and_one_concluder(
        projects_dir, scratch_repo, fake_llm):
    """The panel's roster is Standup's code, never a model's discretion: four
    narrow Sonnet Experts and one Opus concluder, every time
    (ADR 0003 § the Audit)."""
    repo = scratch_repo("tt")
    fake, target = _panel(fake_llm, projects_dir, repo)
    u = universe.Universe(projects_dir, cache_mod.NullCache())

    auditgen.generate(target, u, transport=fake)

    assert set(fake.labels) == set(PANEL_REPLIES)
    # the Experts run in parallel and land in any order; the concluder runs last,
    # because it judges what they claimed
    assert fake.labels[-1] == "concluder"
    experts = [p for p in fake.passes if p.label != "concluder"]
    assert {p.model for p in experts} == {auditgen.EXPERT_MODEL}
    assert fake.pass_for("concluder").model == auditgen.CONCLUDER_MODEL


def test_each_expert_sees_only_the_evidence_it_needs(
        projects_dir, scratch_repo, fake_llm):
    """Only the target Session gets the deep read; the Recurrence Expert gets
    siblings as metadata and Loop fingerprints, so audit cost does not scale with
    sibling count (ADR 0003 § the Audit)."""
    repo = scratch_repo("tt")
    fake, target = _panel(fake_llm, projects_dir, repo)
    u = universe.Universe(projects_dir, cache_mod.NullCache())

    auditgen.generate(target, u, transport=fake)

    for label in ("loop-expert", "llm-as-cpu", "prompt-structure"):
        assert "USER: render the brief under the title" in fake.text_for(label)
    recurrence = fake.text_for("recurrence")
    assert "A sibling that ground the same way" in recurrence
    assert "USER: render the brief under the title" not in recurrence


def test_the_concluder_weighs_claims_and_never_reads_the_transcript(
        projects_dir, scratch_repo, fake_llm):
    """The expensive model receives the Experts' claims plus the deterministic
    Loop table — never the raw transcript. That is what keeps its context small
    and its cost predictable (ADR 0003 § the Audit)."""
    repo = scratch_repo("tt")
    fake, target = _panel(fake_llm, projects_dir, repo)
    u = universe.Universe(projects_dir, cache_mod.NullCache())

    auditgen.generate(target, u, transport=fake)

    seen = fake.text_for("concluder")
    for claim in ("scriptable", "belongs in CLAUDE.md", "combined Loop Cost"):
        assert claim in seen
    assert "USER: render the brief under the title" not in seen


def test_an_audit_stores_the_concluders_markdown_and_itemised_overhead(
        projects_dir, scratch_repo, fake_llm):
    """The Audit proper is the concluder's markdown; every pass's own `usage` is
    recorded so Audit Overhead is itemised per Expert
    (ADR 0003 § the Audit)."""
    repo = scratch_repo("tt")
    fake, target = _panel(fake_llm, projects_dir, repo)
    u = universe.Universe(projects_dir, cache_mod.NullCache())
    paid: list[str] = []

    auditgen.generate(target, u, progress=lambda r: paid.append(r.label),
                      transport=fake)

    a = audit_mod.load_one(target.stem)
    assert a is not None
    assert a.body.startswith("## Verdict")
    assert [label for label, _cost in a.overhead_items()] == [
        "loop-expert", "llm-as-cpu", "prompt-structure", "recurrence", "concluder"]
    assert all(cost > 0 for _label, cost in a.overhead_items())
    assert a.overhead_cost == pytest.approx(
        sum(cost for _label, cost in a.overhead_items()))
    assert sorted(paid) == sorted(PANEL_REPLIES)
    assert a.siblings_considered == 1


def test_a_failed_pass_stores_no_audit(projects_dir, scratch_repo, fake_llm):
    """Nothing partial is ever stored: a panel that lost an Expert raises rather
    than writing an Audit missing a voice (ADR 0003 § the Audit)."""
    repo = scratch_repo("tt")
    fake, target = _panel(fake_llm, projects_dir, repo, **{"llm-as-cpu": ""})
    u = universe.Universe(projects_dir, cache_mod.NullCache())

    with pytest.raises(auditgen.AuditError, match="llm-as-cpu"):
        auditgen.generate(target, u, transport=fake)

    assert audit_mod.load_one(target.stem) is None


# ── the doctor probe ───────────────────────────────────────────────────────

def test_the_doctor_probe_reports_a_failing_pass_and_says_nothing_when_it_works(
        monkeypatch, fake_llm):
    """`standup install`'s doctor runs a real pass through the same seam Brief
    generation uses — same binary, same flags — so what it verifies is what the
    hook will do (ADR 0003 § the Session Brief)."""
    monkeypatch.setattr(install_mod.shutil, "which", lambda name: "/usr/bin/claude")

    assert install_mod._doctor(transport=fake_llm(reply="OK")) is None

    warning = install_mod._doctor(
        transport=fake_llm(raises={"doctor": PassError("doctor: exited 1")}))
    assert warning is not None and "Briefs won't generate" in warning


def test_the_doctor_pays_for_nothing_when_claude_is_absent(monkeypatch, fake_llm):
    """No binary, no pass: the warning is free, and the probe is not attempted."""
    monkeypatch.setattr(install_mod.shutil, "which", lambda name: None)
    fake = fake_llm(reply="OK")

    warning = install_mod._doctor(transport=fake)

    assert warning is not None and "not on PATH" in warning
    assert fake.passes == []
