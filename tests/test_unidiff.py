"""One unified-diff parser, and one file-summary line, for both diff surfaces.

The Watch used to carry its own diff state machine beside `unidiff`, so git's
output was read twice by two readers that disagreed — most visibly about `---`
and `+++`, which are file headers *before* the first hunk and ordinary rows
inside one. The Watch now projects `unidiff.parse` (ADR 0004 § the stream/UI
boundary: the parser sits on the Textual-free side, so both surfaces call it),
and the `path · change · +N −M` line is composed once in `diffrows`.

These tests drive real `git show` output rather than hand-written diff text
wherever the shape is the point: the bug being fixed is a disagreement with
git, and a fixture diff is only ever a second opinion about what git emits.
"""

from __future__ import annotations

from datetime import datetime

from rich.text import Text

from standup import diffrows, diffview, unidiff
from standup.theme import Theme
from standup.watchstream import CommitFile, FeedEvent, WatchStream, _GitWatcher
from standup.watchui import EventWidget


def commit_files(repo, sha: str) -> list[CommitFile]:
    return _GitWatcher._commit_files(str(repo.path), sha)


# --- one parser: `---` and `+++` inside a hunk body ------------------------


def test_a_dashes_line_in_a_hunk_body_is_a_row_not_a_header(scratch_repo):
    """Deleting a line that itself starts with `--` emits `--- …`; adding one
    that starts with `++` emits `+++ …`. The Watch's own reader took both for
    file headers — swallowing the rows and, for `+++`, rewriting the file's
    path from its content. The shared parser only reads them as headers before
    the first hunk, so the Watch inherits the fix."""
    repo = scratch_repo("dash")
    repo.write("q.sql", "a\n-- sql comment\nb\n")
    repo.commit("add q.sql")
    repo.write("q.sql", "a\n++ bumped\nb\n")
    sha = repo.commit("edit q.sql")

    (f,) = commit_files(repo, sha)

    assert f.path == "q.sql"
    assert f.change == "modify"
    assert f.removed == "-- sql comment"
    assert f.added == "++ bumped"


def test_a_dirty_files_delta_keeps_its_dashes_too(scratch_repo, projects_dir):
    """The same rule on the sibling path. An uncommitted change is diffed
    against a snapshot rather than by git, and that reader used to filter
    `---`/`+++` out of `difflib`'s *text* — swallowing the identical rows for
    the identical reason. It reads the matcher's opcodes now, so there is no
    formatted diff to misread."""
    repo = scratch_repo("dirtydash")
    repo.write("q.sql", "a\n-- sql comment\nb\n")
    repo.commit("add q.sql")
    ws = WatchStream(str(repo.path), projects_dir)

    repo.write("q.sql", "a\n++ bumped\nb\n")
    ws._last_git = float("-inf")
    events = ws.poll()

    (ev,) = [e for e in events if e.kind == "file"]
    assert (ev.path, ev.change) == ("q.sql", "modify")
    assert ev.added == "++ bumped"
    assert ev.removed == "-- sql comment"


# --- one parser: the diff-header path reading ------------------------------


def test_a_quoted_path_is_unquoted_once_for_both_surfaces(scratch_repo):
    """git C-quotes a path holding non-ASCII bytes (`"b/caf\\303\\251.txt"`).
    Both readers used to leave debris — the Watch's kept the `b/`, `unidiff`'s
    kept the quotes too — and `diffview` joins the path onto the checkout to
    attribute it, so debris means a path that matches nothing on disk
    (ADR 0004 § the stream/UI boundary)."""
    repo = scratch_repo("quoted")
    repo.write("café.txt", "x\n")
    sha = repo.commit("add a non-ASCII path")

    (f,) = commit_files(repo, sha)
    assert f.path == "café.txt"
    assert f.change == "create"

    out = repo.git("show", "--format=", "-U0", "--no-color", sha)
    assert [fd.path for fd in unidiff.parse(out)] == ["café.txt"]


def test_a_deleted_quoted_path_reads_off_the_git_header(scratch_repo):
    """A delete has no `+++` to be authoritative — it is `/dev/null` — so the
    `diff --git` line is the only source, and there both quoted sides sit on
    one line. The closing quote ends the a-side (ADR 0004 § the stream/UI
    boundary)."""
    repo = scratch_repo("delquoted")
    repo.write("café.txt", "x\n")
    repo.commit("add it")
    (repo.path / "café.txt").unlink()
    sha = repo.commit("delete it")

    (f,) = commit_files(repo, sha)

    assert (f.path, f.change, f.removed) == ("café.txt", "delete", "x")


def test_a_path_with_a_space_drops_gits_tab_delimiter(scratch_repo):
    """git terminates a `+++` field with a literal tab when the name holds a
    space — `+++ b/my file.txt\\t`. The tab is git's delimiter, not part of the
    name; keeping it yields a path matching nothing on disk. This is the
    reading the Watch had and `unidiff` did not, so merging the two had to take
    the union (ADR 0004 § the stream/UI boundary)."""
    repo = scratch_repo("spaced")
    repo.write("my file.txt", "x\n")
    sha = repo.commit("add a spaced path")

    (f,) = commit_files(repo, sha)

    assert (f.path, f.change) == ("my file.txt", "create")


def test_a_rename_reads_the_post_image_path(scratch_repo):
    """`rename to` names the new path, and it is the new path the Watch shows:
    a rename event is about where the file *is* (ADR 0004 § the stream/UI
    boundary)."""
    repo = scratch_repo("renamed")
    repo.write("old.py", "x = 1\n" * 20)
    repo.commit("add old.py")
    repo.git("mv", "old.py", "new.py")
    sha = repo.commit("rename")

    (f,) = commit_files(repo, sha)

    assert (f.path, f.change) == ("new.py", "rename")


# --- one parser: the Watch projects instead of re-parsing ------------------


def test_a_commits_files_project_git_own_account(scratch_repo):
    """create / modify / delete in one commit, each with the added and removed
    blocks the Watch renders. `-U0` keeps the blocks context-free, so a
    committed change reads exactly like an uncommitted one."""
    repo = scratch_repo("mixed")
    repo.write("keep.py", "one\ntwo\n")
    repo.write("gone.py", "bye\n")
    repo.commit("seed")

    repo.write("keep.py", "one\nTWO\n")
    repo.write("fresh.py", "new\nlines\n")
    (repo.path / "gone.py").unlink()
    sha = repo.commit("the commit under test")

    by_path = {f.path: f for f in commit_files(repo, sha)}

    assert set(by_path) == {"keep.py", "gone.py", "fresh.py"}
    assert (by_path["keep.py"].change, by_path["keep.py"].added,
            by_path["keep.py"].removed) == ("modify", "TWO", "two")
    assert (by_path["fresh.py"].change, by_path["fresh.py"].added,
            by_path["fresh.py"].removed) == ("create", "new\nlines", "")
    assert (by_path["gone.py"].change, by_path["gone.py"].added,
            by_path["gone.py"].removed) == ("delete", "", "bye")


def test_a_merge_commit_shows_no_files_rather_than_guessing(scratch_repo):
    """`git show` prints no combined diff for a merge, so there is nothing to
    project. The Watch renders a bare header, which is honest — no diff was
    read — and must not invent one."""
    repo = scratch_repo("merged")
    repo.write("a.py", "a\n")
    repo.commit("on main")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("b.py", "b\n")
    repo.commit("on side")
    repo.git("checkout", "-q", "main")
    repo.write("c.py", "c\n")
    repo.commit("on main again")
    repo.git("merge", "--no-ff", "-m", "merge side", "side")

    assert commit_files(repo, repo.head) == []


def test_an_oversized_commit_diff_is_not_projected(scratch_repo, monkeypatch):
    """MAX_COMMIT_DIFF_BYTES bounds the work before the parse, not after: the
    point is not to read a huge diff at all."""
    repo = scratch_repo("big")
    repo.write("a.py", "x = 1\n" * 100)
    sha = repo.commit("a hundred lines")
    assert commit_files(repo, sha) != []

    monkeypatch.setattr("standup.watchstream.MAX_COMMIT_DIFF_BYTES", 10)

    assert commit_files(repo, sha) == []


def test_a_binary_file_is_left_out_of_the_file_list(scratch_repo):
    """A binary blob has no added or removed lines to show, and `CommitFile`
    has nowhere to say "binary" — so it stays out of the list rather than
    appearing as a `+0` that claims an empty change."""
    repo = scratch_repo("binary")
    (repo.path / "blob.bin").write_bytes(bytes(range(256)))
    repo.write("a.py", "x = 1\n")
    sha = repo.commit("a blob and a file")

    assert [f.path for f in commit_files(repo, sha)] == ["a.py"]


# --- one file-summary line for both surfaces -------------------------------


def test_the_file_summary_line_is_composed_once():
    """`path · change · +N −M`, in `diffrows` beside the shared row shape
    (ADR 0004 § the stream/UI boundary)."""
    t = Theme(depth="truecolor")

    line = diffrows.file_summary(t, "src/a.py", "modify", 2, 1)

    assert line.plain == "src/a.py  modify  +2 −1"


def test_the_file_summary_pads_the_path_column_to_a_width():
    """`pad` aligns the change column down a list of ragged paths; standing
    alone the line takes two spaces (ADR 0004 § the stream/UI boundary)."""
    t = Theme(depth="truecolor")

    line = diffrows.file_summary(t, "a.py", "create", 3, 0, pad=10)

    assert line.plain == "a.py      create  +3"


def test_the_file_summary_carries_a_note_instead_of_counts():
    """A binary file, or one the Attributed Diff could not read, says so where
    the counts would be — never `+0 −0`, which claims an empty change
    (ADR 0004 § the stream/UI boundary)."""
    t = Theme(depth="truecolor")

    line = diffrows.file_summary(t, "blob.bin", "modify", 0, 0, note="binary")

    assert line.plain == "blob.bin  modify  binary"


def test_both_diff_surfaces_render_the_same_file_summary():
    """The Watch's commit file list and the Attributed Diff's `--stat` line are
    the same statement about the same file, so they are one composition. Only
    each surface's own gutter may differ (ADR 0004 § the stream/UI
    boundary)."""
    t = Theme(depth="truecolor")
    pad = len("src/a.py") + 2          # the Watch's own rule for a one-file list
    summary = diffrows.file_summary(t, "src/a.py", "modify", 2, 1, pad=pad)

    hunk = unidiff.Hunk(old_start=1, new_start=1)
    hunk.rows = [unidiff.Row("-", "three", 1), unidiff.Row("+", "one", 1),
                 unidiff.Row("+", "two", 2)]
    static = diffview._file_line(
        t, diffview.FileBlock("", "src/a.py", "modify", "",
                              blocks=[diffview.HunkBlock(hunk, verdict=None)]),
        multi=False, pad=pad)

    event = FeedEvent(kind="commit", when=datetime(2026, 1, 1), sha="abc1234",
                      message="a commit",
                      files=[CommitFile(path="src/a.py", change="modify",
                                        added="one\ntwo", removed="three")])
    widget = EventWidget(event, t, 1)
    widget.expand_level = 1
    live = widget._commit_body(Text(), 100)

    assert static.plain.endswith(summary.plain)
    assert live.plain.endswith(summary.plain)
