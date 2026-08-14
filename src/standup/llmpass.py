"""One LLM pass — the seam every paid pass goes through
(ADR 0003 § the LLM-pass seam).

A **Pass** is prompt and model in; a **Result** is text plus priceable `usage`
out. Session Brief generation, the Audit's Expert Panel fan-out and `standup
install`'s doctor probe reach a model only through here, so everything that
surrounds a pass — locking, digesting, output parsing, storing, itemising
Overhead — is testable against a fake transport returning canned text, with no
`claude` binary and no network.

Two rules live in the seam rather than once per generator, because both are
statements about *a pass*, not about a Brief or an Audit:

- **an empty result is a failure.** A pass that returned nothing produced no
  claim, and an artifact is never stored partial (ADR 0003 § the shared model).
- **a pass that outruns its timeout is a failure**, named by its label. Each
  transport spells a timeout in its own dialect; a caller sees one `PassError`.

Both transports live here too, and the seam picks one from a pass's *shape*
rather than letting a generator name it: a single pass goes through `claude -p`
(`run`), a fan-out through the Agent SDK (`run_all`). Neither is an accident —
see the ADR section for why one transport cannot serve both.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Iterable

DEFAULT_TIMEOUT = 120.0    # seconds; every caller sets its own


class PassError(Exception):
    """A pass produced no usable text: empty result, timeout, or a transport
    that failed. The one exception type a generator has to know about."""


@dataclass(frozen=True)
class Pass:
    """One request to a model.

    `prompt` is the instruction; `context` is bulk evidence (a transcript
    digest, a siblings table, an Expert's claims). They are separate because the
    `claude -p` transport pipes the evidence on **stdin** — a 120 KB digest must
    never become an argv — and because the split says which half a prompt change
    is a change to.
    """

    label: str                     # names the pass in progress output and Overhead
    model: str
    prompt: str
    context: str = ""
    cwd: str | None = None
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Result:
    """What a pass answered, plus the `usage` its Overhead is priced from."""

    label: str
    model: str
    text: str
    usage: dict | None = None

    def overhead_row(self) -> dict:
        """The `{label, model, usage}` row an Audit's frontmatter itemises and a
        `cost` view prices (ADR 0003 § the Audit)."""
        return {"label": self.label, "model": self.model, "usage": self.usage}


Transport = Callable[[Pass], Result]


# ── the seam ───────────────────────────────────────────────────────────────

def run(p: Pass, transport: Transport | None = None) -> Result:
    """One pass, through `claude -p` unless a transport is injected.

    Raises `PassError` — and only `PassError` — when the pass produced nothing
    usable.
    """
    return _guard(p, transport or claude_cli)


def run_all(passes: Iterable[Pass], transport: Transport | None = None,
            progress: Callable[[Result], None] = lambda r: None) -> list[Result]:
    """A fan-out of passes in parallel, through the Agent SDK unless a transport
    is injected. Results come back **in the caller's order** (the panel's shape
    is code, and both the concluder's sections and the Overhead itemisation are
    read off it), while `progress` fires per pass as it lands.

    The first failure fails the whole fan-out: a panel missing an Expert is a
    different panel, not a cheaper one. Accepted cost — the failing call still
    waits for its siblings to finish, because a running subprocess cannot be
    cancelled the way a coroutine could.
    """
    t = transport or agent_sdk
    todo = list(passes)
    if not todo:
        return []
    out: list[Result | None] = [None] * len(todo)
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        futures = {pool.submit(_guard, p, t): i for i, p in enumerate(todo)}
        for fut in as_completed(futures):
            res = fut.result()          # a PassError here fails the fan-out
            out[futures[fut]] = res
            progress(res)
    return [r for r in out if r is not None]


def _guard(p: Pass, transport: Transport) -> Result:
    """The two rules, applied to every pass whatever ran it."""
    try:
        res = transport(p)
    except PassError:
        raise
    except (TimeoutError, subprocess.TimeoutExpired) as e:
        raise PassError(f"{p.label}: timed out after {p.timeout:.0f}s") from e
    except Exception as e:  # noqa: BLE001 — see the module docstring: the seam is
        # the one place that knows a transport failure must reach a generator as
        # a failed *pass*, never as the transport's own exception type.
        raise PassError(f"{p.label}: {e}") from e
    if not res.text.strip():
        raise PassError(f"{p.label}: empty result from {p.model}")
    return res


# ── transports ─────────────────────────────────────────────────────────────

def claude_cli(p: Pass) -> Result:
    """`claude -p` in print mode: the keyless path verified for the Session
    Brief (ADR 0003 § the shared model).

    `--output-format json` is what makes the run's own `usage` readable, so
    Brief Overhead is priced from the same object every other turn is;
    `--no-session-persistence` keeps a generation out of `~/.claude/projects`,
    where Standup would otherwise read its own pass back as a Session. Evidence
    rides stdin, never argv.
    """
    try:
        proc = subprocess.run(
            ["claude", "-p", p.prompt, "--model", p.model,
             "--output-format", "json", "--no-session-persistence"],
            input=p.context, capture_output=True, text=True,
            cwd=p.cwd or None, timeout=p.timeout,
        )
    except subprocess.TimeoutExpired:
        raise
    except (OSError, subprocess.SubprocessError) as e:
        raise PassError(f"{p.label}: could not run claude -p ({e})") from e
    if proc.returncode != 0:
        raise PassError(f"{p.label}: claude -p exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError) as e:
        raise PassError(f"{p.label}: unreadable json from claude -p") from e
    if not isinstance(data, dict):
        raise PassError(f"{p.label}: unreadable json from claude -p")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    return Result(label=p.label, model=p.model,
                  text=str(data.get("result") or ""), usage=usage)


def agent_sdk(p: Pass) -> Result:
    """The Claude Agent SDK — the programmatic face of `claude -p`, driving the
    same binary and inheriting the same login (ADR 0003 § the LLM-pass seam).
    Imported lazily, so the display path gains no dependency.

    One turn, no tools, no user settings: an Expert judges the evidence it was
    handed and nothing else, which is what keeps a panel's cost predictable.
    """
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions, query  # lazy: generation only

    prompt = f"{p.prompt}\n\n{p.context}" if p.context else p.prompt

    async def one() -> Result:
        opts = ClaudeAgentOptions(model=p.model, max_turns=1, allowed_tools=[],
                                  setting_sources=[], cwd=p.cwd or None)
        text, usage = "", None
        async for msg in query(prompt=prompt, options=opts):
            if type(msg).__name__ == "ResultMessage":
                text = msg.result or ""
                usage = msg.usage if isinstance(msg.usage, dict) else None
        return Result(label=p.label, model=p.model, text=text, usage=usage)

    return asyncio.run(asyncio.wait_for(one(), p.timeout))
