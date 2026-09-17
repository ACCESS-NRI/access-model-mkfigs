"""Tests for mkfigs.fetch."""

import json

import pytest

from mkfigs import fetch


@pytest.fixture
def fake_urlretrieve(monkeypatch):
    calls = []

    def _fake(url, dest):
        calls.append((url, dest))
        with open(dest, "w") as f:
            f.write("{}")

    monkeypatch.setattr(fetch.urllib.request, "urlretrieve", _fake)
    return calls


def _write_manifest(docs_root, ename, url_map):
    exp_dir = docs_root / ename
    exp_dir.mkdir(parents=True)
    (exp_dir / "notebooks_urls.json").write_text(json.dumps(url_map))
    return exp_dir


def test_iter_manifests_skips_bookkeeping_keys(tmp_path):
    docs_root = tmp_path / "experiments"
    _write_manifest(
        docs_root,
        "expA",
        {"SST": "https://example.org/sst.ipynb", "_run_times": {"SST": "2026-01-01"}},
    )

    results = list(fetch.iter_manifests(docs_root))

    assert len(results) == 1
    ename, exp_dir, url_map = results[0]
    assert ename == "expA"
    assert url_map == {"SST": "https://example.org/sst.ipynb"}


def test_iter_manifests_missing_root_yields_nothing(tmp_path):
    assert list(fetch.iter_manifests(tmp_path / "does-not-exist")) == []


def test_download_from_manifest_downloads_and_skips(tmp_path, fake_urlretrieve):
    docs_root = tmp_path / "experiments"
    _write_manifest(docs_root, "expA", {"SST": "https://example.org/sst.ipynb", "MLD": "https://example.org/mld.ipynb"})

    # Pre-create one notebook so it should be skipped rather than re-downloaded.
    (docs_root / "expA" / "notebooks").mkdir(parents=True)
    (docs_root / "expA" / "notebooks" / "MLD.ipynb").write_text("existing")

    downloaded, skipped = fetch.download_from_manifest(docs_root)

    assert downloaded == 1
    assert skipped == 1
    assert (docs_root / "expA" / "notebooks" / "SST.ipynb").exists()
    # Skipped file must be left untouched.
    assert (docs_root / "expA" / "notebooks" / "MLD.ipynb").read_text() == "existing"
    assert len(fake_urlretrieve) == 1


def test_download_from_manifest_empty_map_is_not_an_error(tmp_path, fake_urlretrieve):
    docs_root = tmp_path / "experiments"
    _write_manifest(docs_root, "expA", {})

    downloaded, skipped = fetch.download_from_manifest(docs_root)

    assert (downloaded, skipped) == (0, 0)
    assert fake_urlretrieve == []
