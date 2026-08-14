"""The LLM-pass seam: what a pass promises, and how it fails
(ADR 0003 § the LLM-pass seam).

Standup makes paid passes in three places — a Session Brief, the Audit's Expert
Panel, and `standup install`'s doctor probe — and all three reach a model only
through `llmpass`. What is pinned here is the seam's own contract, which is why
the generators can be tested against canned text at all:

* a pass answers with text plus the `usage` an Overhead figure is priced from;
* an **empty result is a failure**, never a stored artifact;
* a pass that **outruns its timeout is a failure**, named by its label;
* no transport failure escapes wearing the transport's own exception type.
"""

from __future__ import annotations

import subprocess

import pytest

from standup import llmpass, rates
from standup.llmpass import Pass, PassError


def _pass(label: str = "brief", model: str = "claude-haiku-4-5", **kwargs) -> Pass:
    return Pass(label=label, model=model, prompt="summarise this", **kwargs)


def test_a_pass_answers_with_text_and_the_usage_its_overhead_is_priced_from(fake_llm):
    """Prompt and model in, text plus priceable usage out. The Result's Overhead
    row is exactly what a Brief's `gen_usage` and an Audit's `overhead` carry, so
    an Overhead figure is the Rate Card applied to what the seam returned
    (ADR 0003 § the LLM-pass seam)."""
    fake = fake_llm(reply="OBJECTIVE: something")

    res = llmpass.run(_pass(), fake)

    assert res.text == "OBJECTIVE: something"
    assert res.label == "brief" and res.model == "claude-haiku-4-5"
    row = res.overhead_row()
    assert row["label"] == "brief" and row["model"] == "claude-haiku-4-5"
    assert rates.turn_cost(row["model"], row["usage"]) > 0


def test_the_seam_hands_the_transport_the_whole_pass(fake_llm):
    """The transport is handed a `Pass`, not a pile of arguments: instruction,
    model, bulk evidence, cwd and the pass's own timeout travel together, so a
    generator names none of them twice."""
    fake = fake_llm()

    llmpass.run(_pass(context="--- DIGEST ---\nUSER: go", cwd="/tmp/tt",
                      timeout=42), fake)

    (p,) = fake.passes
    assert p.context.startswith("--- DIGEST ---")
    assert (p.cwd, p.timeout) == ("/tmp/tt", 42)


@pytest.mark.parametrize("reply", ["", "   \n  "])
def test_an_empty_result_is_a_failure(reply, fake_llm):
    """A pass that returned nothing has produced no claim. The rule lives in the
    seam because it is a statement about a pass, not about a Brief or an Audit —
    and because nothing partial may ever be stored (ADR 0003 § the LLM-pass
    seam)."""
    fake = fake_llm(reply=reply)

    with pytest.raises(PassError, match="empty result"):
        llmpass.run(_pass(label="loop-expert"), fake)


@pytest.mark.parametrize("boom", [
    TimeoutError("no answer"),
    subprocess.TimeoutExpired(cmd=["claude", "-p"], timeout=120),
])
def test_a_timed_out_pass_is_a_failure_naming_the_pass(boom, fake_llm):
    """Both transports can time out, and each spells it in its own dialect (an
    awaited coroutine, a killed subprocess). The seam collapses them into one
    `PassError` that names the pass and the bound it broke, so a caller never
    matches on a transport's exception type."""
    fake = fake_llm(raises={"recurrence": boom})

    with pytest.raises(PassError, match=r"recurrence: timed out after 120s"):
        llmpass.run(_pass(label="recurrence", timeout=120), fake)


def test_no_transport_failure_escapes_as_itself(fake_llm):
    """The seam is the one place that knows a transport's failure must arrive as
    a failed *pass*: a missing binary, a broken pipe, an SDK error and a
    malformed reply are all one thing to a generator that must not break the
    session it summarises."""
    fake = fake_llm(raises={"brief": RuntimeError("sdk exploded")})

    with pytest.raises(PassError, match="sdk exploded"):
        llmpass.run(_pass(), fake)


def test_a_fan_out_keeps_the_callers_order_and_reports_each_pass_as_it_lands(fake_llm):
    """The Expert Panel's shape is Standup's code, so the fan-out preserves the
    order the caller declared — the concluder's sections and the Overhead
    itemisation are read off it — while `progress` fires per pass so a run can
    print what it just paid for (ADR 0003 § the Audit)."""
    fake = fake_llm(replies={"a": "claim a", "b": "claim b", "c": "claim c"})
    seen: list[str] = []
    passes = [_pass(label=lbl, model="claude-sonnet-5") for lbl in ("a", "b", "c")]

    out = llmpass.run_all(passes, fake, progress=lambda r: seen.append(r.label))

    assert [r.label for r in out] == ["a", "b", "c"]
    assert [r.text for r in out] == ["claim a", "claim b", "claim c"]
    assert sorted(seen) == ["a", "b", "c"]


def test_one_failed_pass_fails_the_whole_fan_out(fake_llm):
    """A panel with a missing Expert is not a cheaper panel: it is a different
    one. The fan-out surfaces the first failure rather than returning a partial
    roster its caller would have to notice."""
    fake = fake_llm(replies={"b": ""})
    passes = [_pass(label=lbl, model="claude-sonnet-5") for lbl in ("a", "b", "c")]

    with pytest.raises(PassError, match="b: empty result"):
        llmpass.run_all(passes, fake)


def test_a_fan_out_of_nothing_is_not_a_pass(fake_llm):
    assert llmpass.run_all([], fake_llm()) == []
