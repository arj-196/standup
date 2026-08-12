"""Parse git's unified diff into located hunks. The one such parser.

The **Attributed Diff** keeps the Watch's typography and takes git's structure
instead (ADR 0005 § two grammars), so it needs real hunks with real line
numbers.

The **Watch** wants less — it renders a change as an added block and a removed
block, which is right for narrating one edit as it lands — but it wants it from
here, flattened (`added_lines` / `removed_lines`), rather than from a second
reader of its own. It kept one until it didn't, and the two disagreed exactly
where git's format is subtle: `---` and `+++` are file headers *before* the
first hunk and ordinary rows inside one, so the Watch's reader swallowed a
deleted `-- comment` and rewrote a file's path from an added `++ bumped`.
One parser is one place for that rule to be right.

Every row carries the line number of the side it exists on: a context or added
row carries its **new**-file number, a removed row its **old**-file number.
That is deliberately one column rather than two — the sign says which side you
are reading, so a second column would restate it at the cost of scarce width.

Nothing here reads a session log or makes a claim: this is git's own account of
the tree, and the only ground truth the diff view has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Change kinds, shared with the Watch's CommitFile vocabulary.
CREATE, MODIFY, DELETE, RENAME = "create", "modify", "delete", "rename"


@dataclass
class Row:
    """One line of a hunk. `sign` is " " (context), "-" or "+"; `number` is the
    line's own number on the side it exists on."""
    sign: str
    text: str
    number: int


@dataclass
class Hunk:
    old_start: int
    new_start: int
    rows: list[Row] = field(default_factory=list)

    @property
    def added(self) -> list[str]:
        return [r.text for r in self.rows if r.sign == "+"]

    @property
    def removed(self) -> list[str]:
        return [r.text for r in self.rows if r.sign == "-"]

    def runs(self) -> list[tuple[str, tuple[str, ...]]]:
        """The hunk's maximal consecutive same-sign blocks, as
        `("+"|"-", lines)`.

        A run is the unit attribution is decided on (ADR 0007). It has to be:
        an *added-only* line list is generally **not** contiguous in the text a
        session recorded — an Edit that inserts two lines around a kept one
        produces a hunk whose `+` rows are separated by context — whereas each
        maximal run of `+` rows *is* contiguous in the new file, and therefore
        contiguous inside the `new_string` that produced it.
        """
        out: list[tuple[str, tuple[str, ...]]] = []
        cur_sign: str | None = None
        cur: list[str] = []
        for r in self.rows:
            if r.sign == " ":
                if cur_sign:
                    out.append((cur_sign, tuple(cur)))
                cur_sign, cur = None, []
                continue
            if r.sign != cur_sign:
                if cur_sign:
                    out.append((cur_sign, tuple(cur)))
                cur_sign, cur = r.sign, []
            cur.append(r.text)
        if cur_sign:
            out.append((cur_sign, tuple(cur)))
        return out


@dataclass
class FileDiff:
    path: str
    change: str = MODIFY
    old_path: str | None = None      # set for renames
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False

    @property
    def added_lines(self) -> list[str]:
        """Every added line of the file, in file order — the flat form the Watch
        projects into a `CommitFile`, hunk boundaries dropped."""
        return [ln for h in self.hunks for ln in h.added]

    @property
    def removed_lines(self) -> list[str]:
        return [ln for h in self.hunks for ln in h.removed]

    @property
    def added(self) -> int:
        return len(self.added_lines)

    @property
    def removed(self) -> int:
        return len(self.removed_lines)


_ESCAPE_RE = re.compile(r"\\([0-7]{3}|.)")
_C_ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", "b": b"\b",
              "f": b"\f", "v": b"\v", "a": b"\a"}


def _unquote(p: str) -> str:
    """Undo git's C-quoting of a path holding non-ASCII or control bytes:
    `"a/caf\\303\\251.txt"` -> `a/café.txt`.

    Octal escapes are *bytes*, so they are decoded as bytes and only then as
    UTF-8 — a two-byte character arrives as two escapes. Left alone the debris
    is not cosmetic: `diffview` joins the path onto the checkout to attribute
    the change, and a quoted path matches nothing on disk.
    """
    if len(p) < 2 or not (p.startswith('"') and p.endswith('"')):
        return p
    body, out, pos = p[1:-1], bytearray(), 0
    for m in _ESCAPE_RE.finditer(body):
        out += body[pos:m.start()].encode()
        tok = m.group(1)
        out += (bytes([int(tok, 8) & 0xFF]) if len(tok) == 3
                else _C_ESCAPES.get(tok, tok.encode()))
        pos = m.end()
    out += body[pos:].encode()
    return out.decode("utf-8", "replace")


def _header_path(raw: str, prefix: str = "") -> str:
    """A path as a diff header writes it: unquoted, then stripped of git's
    `a/`/`b/` side prefix. The one such reading in the codebase — the Watch used
    to keep a second one, which stripped the quotes but not the prefix under
    them, and so mangled exactly the paths this one exists for."""
    return _unquote(raw).removeprefix(prefix)


def _git_header_path(line: str) -> str:
    """`diff --git a/x b/x` -> `x`. Provisional: a path containing " b/" splits
    wrong here, and the following `+++ b/x` corrects it. It is the only source
    for a *deleted* file, whose `+++` is `/dev/null`."""
    rest = line[len("diff --git "):].strip()
    # the b-side is quoted or bare, and both sides are quoted together
    markers = [i for i in (rest.find(" b/"), rest.find(' "b/')) if i > 0]
    return _header_path(rest[:min(markers)] if markers else rest, "a/")


def parse(text: str) -> list[FileDiff]:
    """Parse `git diff`/`git show` output. Unknown or malformed sections are
    skipped rather than raised on: a diff we cannot read must degrade to "no
    diff shown", never to a traceback in front of a review."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old_n = new_n = 0

    for line in (text or "").splitlines():
        if line.startswith("diff --git "):
            cur = FileDiff(path=_git_header_path(line))
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue

        if line.startswith("new file mode"):
            cur.change = CREATE
            continue
        if line.startswith("deleted file mode"):
            cur.change = DELETE
            continue
        if line.startswith("rename from "):
            cur.change = RENAME
            cur.old_path = _header_path(line[len("rename from "):])
            continue
        if line.startswith("rename to "):
            cur.change = RENAME
            cur.path = _header_path(line[len("rename to "):])
            continue
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur.binary = True
            continue
        # `---`/`+++` are file headers only *before* the first hunk. Inside a
        # hunk body they are ordinary rows: deleting a line that reads `-- x`
        # emits `--- x`, and treating that as a header would silently swallow it
        # and rewrite the file's path from its content.
        if hunk is None and (line.startswith("--- ") or line == "--- /dev/null"):
            continue
        if hunk is None and (line.startswith("+++ ") or line == "+++ /dev/null"):
            p = line[4:]
            if p != "/dev/null":          # authoritative path (see _git_header_path)
                cur.path = _header_path(p, "b/")
            continue

        m = HUNK_RE.match(line)
        if m:
            old_n, new_n = int(m.group(1)), int(m.group(3))
            hunk = Hunk(old_start=old_n, new_start=new_n)
            cur.hunks.append(hunk)
            continue
        if hunk is None:
            continue

        if line.startswith("\\"):         # "\ No newline at end of file"
            continue
        if line.startswith(" ") or line == "":
            hunk.rows.append(Row(" ", line[1:] if line else "", new_n))
            old_n += 1
            new_n += 1
        elif line.startswith("-"):
            hunk.rows.append(Row("-", line[1:], old_n))
            old_n += 1
        elif line.startswith("+"):
            hunk.rows.append(Row("+", line[1:], new_n))
            new_n += 1
        # anything else (index lines, mode changes, --stat trailers) is not a row

    return [f for f in files if f.hunks or f.binary or f.change != MODIFY]


def untracked_filediff(path: str, content: str) -> FileDiff:
    """A brand-new file as a diff. `git diff HEAD` says nothing about untracked
    files, but the inbox counts them as **Active Work**, so the view has to show
    them: one hunk, every line added, numbered from 1."""
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()                       # a trailing newline is not a last line
    h = Hunk(old_start=0, new_start=1)
    h.rows = [Row("+", ln, i) for i, ln in enumerate(lines, start=1)]
    return FileDiff(path=path, change=CREATE, hunks=[h] if h.rows else [])
