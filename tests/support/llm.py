"""A fake LLM transport: canned text in, a `Result` out.

Every paid pass Standup makes goes through one seam — prompt and model in, text
plus priceable `usage` out (`llmpass`, ADR 0003 § the LLM-pass seam) — so a
single double stands in for the whole outside world on the generation path. With
it, the work that surrounds a pass (locking, digesting, output parsing, storing,
Overhead itemisation) is exercised with **no `claude` binary and no network**,
which is the suite's standing rule rather than a wall the conftest builds.

`FakeLLM` is a `Transport`: it is called with a `Pass` and answers with a
`Result`. It records every `Pass` it was handed, because *what reached which
pass* is half of what the panel's fan-out has to get right — the Recurrence
Expert must see the siblings and the concluder must never see the transcript
(ADR 0003 § the Audit).
"""

from __future__ import annotations

from standup.llmpass import Pass, Result

from tests.support.sessions import DEFAULT_USAGE

# The reply shape a Session Brief pass is contracted to answer in
# (briefgen.PROMPT). Kept here so a Brief test reads as a test of the *parsing*,
# not of a string literal it also had to invent.
BRIEF_REPLY = (
    "OBJECTIVE: Teach the inbox to read Session Briefs\n"
    "STATUS: in-progress\n"
    "SUMMARY:\n"
    "- read the store\n"
    "- rendered the objective\n"
)


class FakeLLM:
    """A transport that answers from canned text.

    `reply` answers every pass; `replies` overrides it per pass label; `raises`
    maps a label to the exception that pass fails with. `usage` is the priced
    `usage` each Result carries, so a test can assert Brief/Audit Overhead is
    still recorded and still priced by the Rate Card.
    """

    def __init__(self, reply: str = "a claim", *,
                 replies: dict[str, str] | None = None,
                 raises: dict[str, BaseException] | None = None,
                 usage: dict | None = None) -> None:
        self.reply = reply
        self.replies = dict(replies or {})
        self.raises = dict(raises or {})
        self.usage = DEFAULT_USAGE if usage is None else usage
        self.passes: list[Pass] = []

    def __call__(self, p: Pass) -> Result:
        self.passes.append(p)
        exc = self.raises.get(p.label)
        if exc is not None:
            raise exc
        return Result(label=p.label, model=p.model,
                      text=self.replies.get(p.label, self.reply), usage=self.usage)

    # -- reading back what the seam was asked ----------------------------

    @property
    def labels(self) -> list[str]:
        return [p.label for p in self.passes]

    def pass_for(self, label: str) -> Pass:
        for p in self.passes:
            if p.label == label:
                return p
        raise AssertionError(f"no pass labelled {label!r}; saw {self.labels}")

    def text_for(self, label: str) -> str:
        """Everything one pass was shown — instruction and evidence together,
        which is the unit a claim about "what this Expert saw" is about."""
        p = self.pass_for(label)
        return p.prompt + "\n" + p.context
