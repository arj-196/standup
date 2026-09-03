"""`standup session` — the Transcript, and the digest behind Briefs and Audits.

Both render the one prompt reading (ADR 0001 § the one log reader), so what
they may say about a line is fixed there: these tests pin the one line whose
text nobody typed but which reads as if they had.
"""

from __future__ import annotations

from standup import transcript

from tests.support.sessions import SessionLog


def _log(projects_dir):
    return (SessionLog(cwd="/tmp/tt")
            .prompt("fix the parser")
            .turn("starting on it")
            .call("Bash", command="sleep 600")
            .interrupt()
            .prompt("never mind, run the suite")
            .save(projects_dir))


def test_the_transcript_marks_an_interrupt_instead_of_crediting_it_to_you(
        projects_dir):
    """An interrupt is not a prompt — `[Request interrupted by user]` is text
    nobody typed — but it is why the turn above it stops mid-sentence, so the
    Transcript keeps it under a rule of its own. Rendering it as `you` would
    put words in the reader's mouth; dropping it would lose the reason the
    answer ends (ADR 0001 § the one log reader)."""
    out = transcript.render_transcript(_log(projects_dir))

    assert "interrupted" in out
    assert "[Request interrupted by user]" not in out
    assert out.count("── you") == 2          # the two real prompts, not three


def test_the_digest_labels_an_interrupt_as_one(projects_dir):
    """The Audit's prompt-structure Expert judges the human's side of a
    session, where an abandoned turn is evidence — so the digest keeps the
    interrupt, labelled as itself rather than as a question that was asked."""
    out = transcript.digest(_log(projects_dir), max_chars=10_000,
                            head_chars=5_000)

    assert "INTERRUPTED BY USER" in out
    assert "USER: [Request interrupted" not in out
    assert "USER: fix the parser" in out
    assert "USER: never mind, run the suite" in out
