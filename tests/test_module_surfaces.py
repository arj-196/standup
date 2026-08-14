"""What one module may reach for in another.

A module's underscore-private names are the parts it reserves the right to
change. A consumer that reaches for one makes a rival out of a helper — the
failure ADR 0001 § the one log reader names for readings, and the reason
`gitstate` keeps its git runner private (`test_gitstate.py`). This is the
whole-package form of that guard: a source scan, so the mistake fails at the
moment it is written rather than the day a private is renamed.

Also pinned here: the `~`-claim rule has one implementation. An out-of-band
Artifact is a claim, and the marking exists so two views can never disagree
about its trustworthiness (ADR 0003 § the shared model) — three surfaces
spelling the hedge themselves is exactly how that disagreement arrives.
"""

from __future__ import annotations

import re
from pathlib import Path

import standup

SRC = Path(standup.__file__).parent

# `from .other import _name`, `from standup.other import _name`
_PRIVATE_IMPORT = re.compile(
    r"from\s+\.*(\w+)\s+import\s+\(?([^)\n]*\b_\w+[^)\n]*)\)?")
# `other._name(` / `other._name` — a reach by attribute
_PRIVATE_ATTR = re.compile(r"\b(\w+)\.(_\w+)\b")

# Every module in the package: the set an attribute reach is looked up against,
# so `self._x` and `obj._y` are not mistaken for one.
MODULES = {p.stem for p in SRC.glob("*.py")} - {"__init__"}

# Reaches that are still here, each with the reason and the work that retires
# it. An *exact* comparison, not a ceiling: an entry that goes stale fails this
# test, which is how the list empties instead of growing a graveyard.
KNOWN: set[tuple[str, str, str]] = set()


def _reaches(module: str, text: str) -> set[tuple[str, str, str]]:
    found = set()
    for owner, names in _PRIVATE_IMPORT.findall(text):
        if owner not in MODULES or owner == module:
            continue
        for name in (n.strip() for n in names.split(",")):
            if name.startswith("_"):
                found.add((module, owner, name))
    for owner, name in _PRIVATE_ATTR.findall(text):
        if owner in MODULES and owner != module:
            found.add((module, owner, name))
    return found


def test_no_module_reaches_into_another_modules_privates():
    reaches = set()
    for py in sorted(SRC.glob("*.py")):
        reaches |= _reaches(py.stem, py.read_text())

    assert reaches == KNOWN


# the claim rule's own glyph run, and the roomy hedge spelled out. `"stale"` on
# its own is deliberately not here: it is also a payload key and a dataclass
# field, and a scan that cannot tell those apart would be turned off.
_CLAIM_LAYOUT = re.compile(r"── ~|may be stale")


def test_the_claim_mark_has_one_implementation():
    """`termout` owns the `~` glyph and the hedge vocabulary; the inbox, the
    Transcript and the `audit` view render through it. A surface that spelled a
    hedge itself would be free to disagree with the others about the same
    artifact (ADR 0003 § the shared model), and three spellings of one hedge is
    how that starts — this view used to say `stale`, that one `may be stale`,
    the third `may be stale — session continued after this audit`.

    A scan for the two things only a claim renderer would hold. The behavioural
    half — all three surfaces hedging the same artifact, in `termout`'s words —
    is `test_artifacts.py`.
    """
    offenders = {}
    for py in sorted(SRC.glob("*.py")):
        if py.stem == "termout":
            continue
        hits = _CLAIM_LAYOUT.findall(py.read_text())
        if hits:
            offenders[py.stem] = hits

    assert offenders == {}
