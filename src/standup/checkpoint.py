"""The Checkpoint: timestamp of the previous standup run — the only stored state."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return Path(base) / "standup"


def _path() -> Path:
    return _state_dir() / "checkpoint"


def read() -> datetime | None:
    try:
        raw = _path().read_text().strip()
        return datetime.fromisoformat(raw)
    except (OSError, ValueError):
        return None


def read_or_default() -> datetime:
    return read() or datetime.now(timezone.utc) - timedelta(hours=24)


def write(when: datetime) -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)
    _path().write_text(when.isoformat() + "\n")
