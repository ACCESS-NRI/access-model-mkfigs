"""Tests for mkfigs.configdoc.FigshareUploader, entirely against
FakeFigshareServer (see conftest.py) -- no real network, no real account,
nothing ever published. This is the "test the upload path" half of
Figshare support's recommended split; the mirror-image
"mock the restore/download path" half lives in test_restore.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests
import hashlib

from mkfigs.configdoc import FigshareUploader
from .conftest import FIGSHARE_BASE, make_png


def _uploader(tmp_path: Path, token="tok") -> FigshareUploader:
    """Build a FigshareUploader against the fake_figshare fixture."""
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir(exist_ok=True)
    return FigshareUploader(token=token, experiment="test_experiment_01", mdfol=str(mdfol))


def test_fake_figshare_records_uploaded_part_bytes(
    fake_figshare,
):
    """
    Prove the fake records the actual PUT body, not just an upload success status.
    The subsequent checksum test depends on this invariant.
    """
    article_id = fake_figshare.seed_article("test")
    payload = b"actual uploaded bytes"

    response = requests.post(
        f"{FIGSHARE_BASE}/account/articles/{article_id}/files",
        json={
            "name": "testfile.txt",
            "size": len(payload),
            "md5": "client-supplied-md5"
        },
    )
    response.raise_for_status()

    file_url = response.json()["location"]
    file_id = int(file_url.rsplit("/", 1)[-1])

    response = requests.get(file_url)
    response.raise_for_status()

    upload_url = response.json()["upload_url"]
    response = requests.get(upload_url)
    response.raise_for_status()
    parts = response.json()["parts"]
    assert len(parts) == 1

    response = requests.put(
        f"{upload_url}/1",
        data=payload,
    )
    response.raise_for_status()

    assert fake_figshare._files[file_id]["_uploaded_parts"][1] == payload


def test_fake_figshare_computes_md5_from_uploaded_bytes(fake_figshare):
    """
    Deliberately claim a wrong MD5 when creating the upload session, then PUT real bytes.
    The computed checksum must follow the bytes, not the claim.
    """
    article_id = fake_figshare.seed_article("test")
    payload = b"actual uploaded bytes"
    wrong_md5 = "client-supplied-md5"

    response = requests.post(
        f"{FIGSHARE_BASE}/account/articles/{article_id}/files",
        json={
            "name": "testfile.txt",
            "size": len(payload),
            "md5": wrong_md5,
        },
    )
    response.raise_for_status()

    file_url = response.json()["location"]

    response = requests.get(file_url)
    response.raise_for_status()

    upload_url = response.json()["upload_url"]

    # Initialise/discover upload parts
    response = requests.get(upload_url)
    response.raise_for_status()

    parts = response.json()["parts"]
    assert len(parts) == 1
    assert parts[0]["partNo"] == 1

    response = requests.put(
        f"{upload_url}/1",
        data=payload,
    )
    response.raise_for_status()

    response = requests.post(file_url)
    response.raise_for_status()

    response = requests.get(file_url)
    response.raise_for_status()

    remote_file = response.json()

    assert remote_file["status"] == "available"
    # The fake keeps the checksum claimed by the client
    assert remote_file["supplied_md5"] == wrong_md5

    assert remote_file["computed_md5"] != wrong_md5
    assert remote_file["computed_md5"] == hashlib.md5(payload).hexdigest()


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


def test_get_or_create_article_falls_back_to_pagination_when_search_misses(fake_figshare, tmp_path):
    """
    Search can miss an article that still exists in the account listing.
    The override controls the callback already registered by responses;
    counters prove the listing fallback ran without creating a duplicate.
    """
    up = _uploader(tmp_path)
    existing_id = fake_figshare.seed_article(up.article_title)

    # Simulate Figshare search returning no result even though the article really exists
    # The paginated article listing still contains it
    fake_figshare.article_search_override = []
    found_id = up._get_or_create_article()

    assert found_id == existing_id

    # prove that this test genuinely excercised the fallback path
    assert fake_figshare.article_search_calls == 1
    assert fake_figshare.article_list_calls == 1

    # pagination found the existing article so no dup should have been created
    assert fake_figshare.created_article_ids == []


# ---------------------------------------------------------------------------
# File upload workflow
# ---------------------------------------------------------------------------

def test_upload_pngs_for_notebook_uploads_and_records_manifest(fake_figshare, tmp_path):
    """
    Cover more than the isolated reconciliation decisions: select this notebook's pngs,
    persist their checksums/URLs, and verify remote state.
    """
    up = _uploader(tmp_path)
    mdfol = Path(up.mdfol)

    make_png(mdfol / "SST_01.png", b"sst-one")
    make_png(mdfol / "SST_02.png", b"sst-two")
    make_png(mdfol / "MLD_01.png", b"other-notebook")

    results = up.upload_pngs_for_notebook("SST")

    assert set(results) == {"SST_01.png", "SST_02.png"}
    assert all(
        url.startswith(
            "https://ndownloader.figshare.com/files/"
        )
        for url in results.values()
    )

    manifest = json.loads(
        (mdfol / "figshare_manifest.json").read_text()
    )
    article_id = manifest[f"article_id_{up.experiment}"]
    assert manifest["file_SST_01.png"] == {
        "md5": hashlib.md5(b"sst-one").hexdigest(),
        "download_url": results["SST_01.png"],
    }
    assert manifest["file_SST_02.png"] == {
        "md5": hashlib.md5(b"sst-two").hexdigest(),
        "download_url": results["SST_02.png"],
    }
    assert "file_MLD_01.png" not in manifest
    remote_names = {
        remote_file["name"]
        for remote_file in fake_figshare.files_for(article_id)
    }
    assert remote_names == {"SST_01.png", "SST_02.png"}


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


# ---------------------------------------------------------------------------
# Remote-file reconciliation
# Preserve fresh/reuse/replace/resume decisions without pinning every
# internal step of _upload_file ahead of the planned refactor.
# ---------------------------------------------------------------------------

def test_reconcile_remote_file_returns_fresh_when_missing(
    fake_figshare,
    tmp_path,
):
    """
    If no remote file exists, it should be marked as fresh for upload.
    """
    up = _uploader(tmp_path)
    article_id = fake_figshare.seed_article("test")
    file_md5 = hashlib.md5(b"content").hexdigest()

    result = up._reconcile_remote_file(article_id, "SST_01.png", file_md5)

    assert result == ("fresh", None, None)


def test_reconcile_remote_file_reuses_matching_complete_file(
    fake_figshare,
    tmp_path,
):
    """
    If a remote file exists and its MD5 matches, it should be reused.
    """
    up = _uploader(tmp_path)
    article_id = fake_figshare.seed_article("test")
    content = b"same-content"
    file_id = fake_figshare.seed_file(article_id, "SST_01.png", content, status="available")

    file_md5 = hashlib.md5(content).hexdigest()
    result = up._reconcile_remote_file(article_id, "SST_01.png", file_md5)

    assert result == (
        "reuse",
        f"https://ndownloader.figshare.com/files/{file_id}",
        None
    )

    assert file_id not in fake_figshare.deleted_file_ids  # not deleted, still there


def test_reconcile_remote_file_replaces_mismatched_complete_file(
    fake_figshare,
    tmp_path,
):
    """
    If a remote file exists but its MD5 does not match, it should be replaced.
    """
    up = _uploader(tmp_path)
    article_id = fake_figshare.seed_article("test")
    stale_id = fake_figshare.seed_file(article_id, "SST_01.png", b"old-content", status="available")

    file_md5 = hashlib.md5(b"new-content").hexdigest()
    result = up._reconcile_remote_file(article_id, "SST_01.png", file_md5)

    assert result == ("fresh", None, None)

    assert stale_id in fake_figshare.deleted_file_ids  # the stale file was deleted


def test_reconcile_remote_file_resumes_matching_incomplete_file(
    fake_figshare,
    tmp_path,
):
    """
    A "created" remote file has a matching claimed MD5 but no completed
    content. Reuse its upload session rather than starting a duplicate.
    """
    up = _uploader(tmp_path)
    article_id = fake_figshare.seed_article("test")
    content = b"same-content"
    file_id = fake_figshare.seed_file(article_id, "SST_01.png", content, status="created")

    file_md5 = hashlib.md5(content).hexdigest()
    result = up._reconcile_remote_file(article_id, "SST_01.png", file_md5)

    expected_url = f"{FIGSHARE_BASE}/account/articles/{article_id}/files/{file_id}/upload"

    assert result == (
        "resume",
        file_id,
        expected_url,
    )

    assert file_id not in fake_figshare.deleted_file_ids  # not deleted, still there
