"""`standup install` / `standup uninstall` (ADR 0003 § the Session Brief).

Turnkey setup for the Session Brief Stop hook: merges a hook entry into the
user-level ~/.claude/settings.json so it fires for every session on the machine,
idempotently and without clobbering existing hooks. Claude Code's file watcher
picks the change up with no restart.

The hook command shims into this package (`standup _brief`); the logic thus stays
versioned and testable, and a missing `standup` on PATH just makes the hook a
no-op (it can never break a session).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import briefgen, llmpass

SETTINGS = Path(os.path.expanduser("~/.claude/settings.json"))
PROBE_TIMEOUT = 60          # seconds; a doctor-check must not hang an install
HOOK_COMMAND = "standup _brief"
HOOK_ENTRY = {"hooks": [{"type": "command", "command": HOOK_COMMAND, "async": True}]}


def _load() -> dict:
    if not SETTINGS.exists():
        return {}
    try:
        data = json.loads(SETTINGS.read_text())
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")


def _stop_hooks(data: dict) -> list:
    return data.setdefault("hooks", {}).setdefault("Stop", [])


def _is_ours(entry: dict) -> bool:
    return any(h.get("command") == HOOK_COMMAND
              for h in entry.get("hooks", []) if isinstance(h, dict))


def _doctor(transport: llmpass.Transport | None = None) -> str | None:
    """Return None if a real Session Brief pass works, else a human warning.

    The probe goes through the one seam Brief generation uses, on the Brief's own
    model (ADR 0003 § the LLM-pass seam) — same binary, same flags, same
    `--output-format json` the Overhead figure is read from — so a pass that
    works here is a pass the hook can make. A missing binary is answered without
    paying for anything.
    """
    if not shutil.which("claude"):
        return "`claude` is not on PATH — the hook can't generate Briefs until Claude Code is installed."
    try:
        llmpass.run(llmpass.Pass(label="doctor", model=briefgen.MODEL,
                                 prompt="reply with exactly: OK",
                                 timeout=PROBE_TIMEOUT), transport)
    except llmpass.PassError as e:
        return f"a headless `claude -p` pass failed ({e}); Briefs won't generate. Try running it once interactively to sign in."
    return None


def install() -> int:
    data = _load()
    stop = _stop_hooks(data)
    if any(_is_ours(e) for e in stop if isinstance(e, dict)):
        print(f"standup: Session Brief hook already installed in {SETTINGS}")
    else:
        stop.append(HOOK_ENTRY)
        _save(data)
        print(f"standup: installed the Session Brief Stop hook in {SETTINGS}")
        print("  it fires for every Claude Code session; Briefs land in ~/.standup/briefs/")

    warning = _doctor()
    if warning:
        print(f"\n  ⚠ {warning}")
        return 0
    print("  ✓ headless `claude -p` authenticated — Briefs will generate.")
    return 0


def uninstall() -> int:
    data = _load()
    stop = data.get("hooks", {}).get("Stop")
    if not isinstance(stop, list):
        print("standup: no Session Brief hook found.")
        return 0
    kept = [e for e in stop if not (isinstance(e, dict) and _is_ours(e))]
    if len(kept) == len(stop):
        print("standup: no Session Brief hook found.")
        return 0
    data["hooks"]["Stop"] = kept
    _save(data)
    print(f"standup: removed the Session Brief Stop hook from {SETTINGS}")
    print("  (existing Briefs in ~/.standup/briefs/ are left in place)")
    return 0
