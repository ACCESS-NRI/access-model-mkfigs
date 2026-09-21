"""Tests for mkfigs.configdoc.FigshareUploader, entirely against
FakeFigshareServer (see conftest.py) -- no real network, no real account,
nothing ever published. This is the "test the upload path" half of
Figshare support's recommended split; the mirror-image
"mock the restore/download path" half lives in test_restore.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from mkfigs.configdoc import FigshareUploader

from .conftest import make_png


def _uploader(tmp_path: Path, token="tok") -> FigshareUploader:
    """Build a FigshareUploader against the fake_figshare fixture."""
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir(exist_ok=True)
    return FigshareUploader(token=token, experiment="test_experiment_01", mdfol=str(mdfol))


# ---------------------------------------------------------------------------
# Article creation / reuse
# ---------------------------------------------------------------------------

def test_get_or_create_article_creates_once_and_caches_in_manifest(fake_figshare, tmp_path):
    """A second call should reuse the on-disk manifest entry, not create a duplicate article."""
    up = _uploader(tmp_path)
    aid1 = up._get_or_create_article()
    aid2 = up._get_or_create_article()  # second call: manifest fast-path
    assert aid1 == aid2
    # manifest file was actually persisted to disk, not just in-memory
    manifest = json.loads((tmp_path / "mkmd" / "figshare_manifest.json").read_text())
    assert manifest[f"article_id_{up.experiment}"] == aid1


def test_get_or_create_article_finds_existing_by_title_search(fake_figshare, tmp_path):
    """An existing article found by title search should be reused, not duplicated."""
    up = _uploader(tmp_path)
    existing_id = fake_figshare.seed_article(up.article_title)

    found_id = up._get_or_create_article()

    assert found_id == existing_id  # reused the pre-existing article, no duplicate created


def test_get_or_create_article_falls_back_to_pagination_when_search_misses(
    fake_figshare, tmp_path, monkeypatch
):
    """Regression coverage for the '+'/'-' title-search quirk: Figshare's
    search endpoint can come back with zero matches for a title that
    genuinely exists (if it contains '+' or '-', which most experiment
    names here do) without erroring. _get_or_create_article must fall back
    to full pagination rather than trusting a clean-but-wrong empty result.
    """
    up = _uploader(tmp_path)
    existing_id = fake_figshare.seed_article(up.article_title)

    # Force the search endpoint to return nothing, as if it choked on the
    # title's special characters, without touching the pagination path.
    monkeypatch.setattr(fake_figshare, "_articles_search", lambda request: (200, {}, json.dumps([])))

    found_id = up._get_or_create_article()

    assert found_id == existing_id


# ---------------------------------------------------------------------------
# File upload: fresh, reuse, mismatch-triggers-replace
# ---------------------------------------------------------------------------

def test_upload_pngs_for_notebook_uploads_and_records_manifest(fake_figshare, tmp_path):
    """Only PNGs belonging to the given notebook should be uploaded, not others in the same folder."""
    up = _uploader(tmp_path)
    mdfol = Path(up.mdfol)
    make_png(mdfol / "SST_01.png")
    make_png(mdfol / "SST_02.png")
    make_png(mdfol / "MLD_01.png")  # belongs to a different notebook

    results = up.upload_pngs_for_notebook("SST")

    assert set(results) == {"SST_01.png", "SST_02.png"}
    for url in results.values():
        assert url.startswith("https://ndownloader.figshare.com/files/")


def test_upload_file_reuses_identical_remote_file_without_reuploading(fake_figshare, tmp_path):
    """A remote file with matching content should be reused, not re-uploaded."""
    up = _uploader(tmp_path)
    article_id = up._get_or_create_article()
    fpath = tmp_path / "SST_01.png"
    make_png(fpath, b"same-bytes")
    fake_figshare.seed_file(article_id, "SST_01.png", b"same-bytes", status="available")

    n_files_before = len(fake_figshare.files_for(article_id))
    url = up._upload_file(article_id, str(fpath))
    n_files_after = len(fake_figshare.files_for(article_id))

    assert url is not None
    assert n_files_after == n_files_before  # nothing new was created -- reused


def test_upload_file_replaces_remote_file_when_content_differs(fake_figshare, tmp_path):
    """A stale remote file (MD5 mismatch) should be deleted and replaced, not left or duplicated."""
    up = _uploader(tmp_path)
    article_id = up._get_or_create_article()
    fpath = tmp_path / "SST_01.png"
    make_png(fpath, b"new-content")
    stale_id = fake_figshare.seed_file(article_id, "SST_01.png", b"old-content", status="available")

    up._upload_file(article_id, str(fpath))

    assert stale_id in fake_figshare.deleted_file_ids
    remaining = [f for f in fake_figshare.files_for(article_id) if f["name"] == "SST_01.png"]
    assert len(remaining) == 1
    assert remaining[0]["computed_md5"] == __import__("hashlib").md5(b"new-content").hexdigest()


def test_find_remote_file_cleans_up_broken_stub_alongside_working_copy(fake_figshare, tmp_path):
    """A broken stub alongside a working copy should be cleaned up automatically."""
    up = _uploader(tmp_path)
    article_id = up._get_or_create_article()
    good_id = fake_figshare.seed_file(article_id, "SST_01.png", b"content", status="available")
    stub_id = fake_figshare.seed_file(article_id, "SST_01.png", b"", status="created")

    found = up._find_remote_file(article_id, "SST_01.png")

    assert found["id"] == good_id
    assert stub_id in fake_figshare.deleted_file_ids


# ---------------------------------------------------------------------------
# Notebook upload + markdown rewriting (both first-run and already-rewritten)
# ---------------------------------------------------------------------------

def test_rewrite_markdown_replaces_local_asset_path_on_first_run(fake_figshare, tmp_path):
    """The local asset path in the markdown should be replaced with the real Figshare URL."""
    up = _uploader(tmp_path)
    mdfol = Path(up.mdfol)
    (mdfol / "SST.md").write_text(
        "![fig](/assets/experiments/test_experiment_01/SST_01.png)\n"
    )
    make_png(mdfol / "SST_01.png")

    url_map = up.upload_pngs_for_notebook("SST")
    up.rewrite_markdown(url_map, nb_name="SST")

    content = (mdfol / "SST.md").read_text()
    assert "/assets/experiments/" not in content
    assert url_map["SST_01.png"] in content


def test_rewrite_markdown_replaces_previous_url_on_second_run(fake_figshare, tmp_path):
    """The bug this guards against: after the first rewrite the local
    asset path is gone from the markdown, so a naive second rewrite that
    only ever looks for the ORIGINAL local path finds nothing and freezes
    the embedded URL forever, even once the underlying file id changes.
    """
    up = _uploader(tmp_path)
    mdfol = Path(up.mdfol)
    (mdfol / "SST.md").write_text(
        "![fig](/assets/experiments/test_experiment_01/SST_01.png)\n"
    )
    make_png(mdfol / "SST_01.png", b"v1")

    url_map_1 = up.upload_pngs_for_notebook("SST")
    up.rewrite_markdown(url_map_1, nb_name="SST")
    old_url = url_map_1["SST_01.png"]

    # Simulate the underlying file changing content on a later run.
    make_png(mdfol / "SST_01.png", b"v2-different-content")
    url_map_2 = up.upload_pngs_for_notebook("SST")
    up.rewrite_markdown(url_map_2, nb_name="SST")
    new_url = url_map_2["SST_01.png"]

    content = (mdfol / "SST.md").read_text()
    assert new_url != old_url
    assert old_url not in content
    assert new_url in content


# ---------------------------------------------------------------------------
# Stale-URL refresh (carried-forward notebooks_urls.json entries)
# ---------------------------------------------------------------------------

def test_validate_and_refresh_notebook_urls_refreshes_stale_entry(fake_figshare, tmp_path):
    """A stale URL entry should be refreshed to point at its replacement file."""
    up = _uploader(tmp_path)
    article_id = up._get_or_create_article()
    old_id = fake_figshare.seed_file(article_id, "SST_rendered.ipynb", b"v1", status="available")
    old_url = f"https://ndownloader.figshare.com/files/{old_id}"

    # Simulate the file having been re-uploaded under a new id since
    # (e.g. by duplicate cleanup) without this notebook being reprocessed.
    fake_figshare.expire_file(old_id)
    new_id = fake_figshare.seed_file(article_id, "SST_rendered.ipynb", b"v2", status="available")

    refreshed = up.validate_and_refresh_notebook_urls(article_id, {"SST": old_url})

    assert refreshed["SST"] == f"https://ndownloader.figshare.com/files/{new_id}"


def test_validate_and_refresh_notebook_urls_leaves_entry_as_is_when_no_replacement_exists(
    fake_figshare, tmp_path
):
    """If the stale file has no replacement at all, the entry must be left
    AS-IS (not dropped) so it still shows up as a real, visible failure in
    --check-figshare-upload rather than silently vanishing.
    """
    up = _uploader(tmp_path)
    article_id = up._get_or_create_article()
    stale_url = "https://ndownloader.figshare.com/files/999999"

    refreshed = up.validate_and_refresh_notebook_urls(article_id, {"SST": stale_url})

    assert refreshed["SST"] == stale_url


def test_validate_and_refresh_notebook_urls_noop_when_empty():
    """An empty map should short-circuit with no HTTP calls."""
    up_stub = object.__new__(FigshareUploader)  # no HTTP should happen at all
    result = FigshareUploader.validate_and_refresh_notebook_urls(up_stub, 1, {})
    assert result == {}
