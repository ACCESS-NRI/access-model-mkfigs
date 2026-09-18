"""Unit tests for the pure, non-networked helpers in mkfigs.configdoc.

These need no Figshare account, no mocking, and no filesystem beyond
tmp_path -- they should be the fastest, most reliable layer of the suite
and are a good place to pin down edge cases precisely because they run in
milliseconds.
"""
from __future__ import annotations

from pathlib import Path

from mkfigs.configdoc import _md5, assign_pngs_to_notebooks
from mkfigs.pushit import _classify_duplicates


def test_md5_matches_hashlib(tmp_path: Path):
    """_md5 should match hashlib.md5 directly."""
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    import hashlib
    assert _md5(p) == hashlib.md5(b"hello world").hexdigest()


def test_assign_pngs_prefix_collision_resolved_longest_first(tmp_path: Path):
    """Regression test for the MLD / MLD_max prefix-collision bug described
    in assign_pngs_to_notebooks' docstring: a naive f"{nb}_*.png" match
    would let 'MLD' steal 'MLD_max_01.png' too.
    """
    mdfol = tmp_path
    (mdfol / "MLD_01.png").touch()
    (mdfol / "MLD_max_01.png").touch()
    (mdfol / "MLD_max_02.png").touch()

    owned = assign_pngs_to_notebooks(mdfol, ["MLD", "MLD_max"])

    assert owned["MLD"] == ["MLD_01.png"]
    assert owned["MLD_max"] == ["MLD_max_01.png", "MLD_max_02.png"]


def test_assign_pngs_missing_dir_returns_empty_map_not_error(tmp_path: Path):
    """A missing directory should return an empty list, not raise."""
    owned = assign_pngs_to_notebooks(tmp_path / "does-not-exist", ["SST"])
    assert owned == {"SST": []}


def test_assign_pngs_ignores_non_png_files(tmp_path: Path):
    """Only .png files should be picked up, not other file types."""
    (tmp_path / "SST_01.png").touch()
    (tmp_path / "SST_notes.txt").touch()
    owned = assign_pngs_to_notebooks(tmp_path, ["SST"])
    assert owned["SST"] == ["SST_01.png"]


# ---------------------------------------------------------------------------
# _classify_duplicates -- every branch documented in its own docstring.
# Lives in pushit.py (not configdoc.py) but is pure/offline like the tests
# above, so it belongs in this fast unit-test layer rather than the
# HTTP-mocked integration layer.
# ---------------------------------------------------------------------------

def _entry(id, status="available", md5="abc123"):
    """Build a minimal fake Figshare file-listing entry."""
    return {"id": id, "status": status, "computed_md5": md5 if status == "available" else ""}


def test_classify_duplicates_single_entry_is_a_noop():
    """A single entry should never be treated as a duplicate."""
    assert _classify_duplicates([_entry(1)], "abc123") == (None, [], "")


def test_classify_duplicates_stub_alongside_working_copy():
    """A broken stub alongside a working copy should be flagged for deletion."""
    entries = [_entry(1, status="available"), _entry(2, status="created", md5="")]
    action, ids, note = _classify_duplicates(entries, "abc123")
    assert action == "delete_stubs"
    assert ids == [2]


def test_classify_duplicates_identical_complete_copies_matching_local_keeps_newest():
    """Matching duplicates should keep only the newest file id."""
    entries = [_entry(10), _entry(20), _entry(15)]
    action, ids, note = _classify_duplicates(entries, "abc123")
    assert action == "delete_older_dupes"
    assert set(ids) == {10, 15}  # everything except id 20, the max


def test_classify_duplicates_identical_copies_but_stale_vs_local():
    """Duplicates that don't match local should be reported as stale, not deleted."""
    entries = [_entry(1, md5="deadbeef"), _entry(2, md5="deadbeef")]
    action, ids, note = _classify_duplicates(entries, "abc123")
    assert action == "stale_duplicates"
    assert ids == []


def test_classify_duplicates_conflicting_complete_copies():
    """Conflicting duplicates should be reported, not auto-resolved."""
    entries = [_entry(1, md5="aaa"), _entry(2, md5="bbb")]
    action, ids, note = _classify_duplicates(entries, "abc123")
    assert action == "conflicting"
    assert ids == []
