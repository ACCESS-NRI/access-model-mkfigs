"""The one place in this suite allowed to talk to real Figshare.

Implements Figshare support's exact recommended split for accounts with
no sandbox environment:

  1. Upload path (this file): exercise FigshareUploader's real upload
     logic -- file chunking, metadata association, article creation --
     against a genuinely NEW, PRIVATE article, then DELETE it immediately.
     Never call /publish. This gets real coverage of the upload path
     without creating anything permanent.

  2. Restore/download path: NOT here -- see tests/test_restore.py, which
     mocks the HTTP layer instead, exactly as support suggested, since
     there is no way to test a real download without a real published
     (i.e. permanent) file.

Run manually with:

    export MKFIGS_LIVE_FIGSHARE_TESTS=1
    export FIGSHARE_TOKEN=...           # a real personal token
    pytest tests/live/ -v

Do NOT wire this into the per-PR CI job. A nightly/manually-triggered
workflow is the right place for it, if it's wired into CI at all -- see
PLAN.md.
"""
from __future__ import annotations

import hashlib

from mkfigs.configdoc import FigshareUploader


def test_upload_a_real_private_article_and_verify_then_delete_it(
    tmp_path, live_token, cleanup_private_articles
):
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir()
    png = mdfol / "LIVE_SMOKE_TEST_01.png"
    payload = b"mkfigs live smoke test fixture -- safe to ignore/delete"
    png.write_bytes(payload)

    uploader = FigshareUploader(
        token=live_token,
        experiment="mkfigs-ci-live-smoke-test",  # unmistakable, never a real experiment name
        mdfol=str(mdfol),
        article_title="[mkfigs CI live smoke test -- safe to delete]",
    )

    article_id = uploader._get_or_create_article()
    cleanup_private_articles.append(article_id)  # registered for teardown BEFORE any upload

    url = uploader.upload(str(png))

    assert url is not None

    # Verify via the PRIVATE-state integrity check (the one Figshare
    # explicitly says works pre-publish), not the public URL -- there is
    # nothing to fetch publicly since this article is never published.
    remote_files = uploader._list_remote_files(article_id)
    matching = [f for f in remote_files if f.get("name") == "LIVE_SMOKE_TEST_01.png"]
    assert matching, "uploaded file not found on the article"
    assert matching[0].get("computed_md5") == hashlib.md5(payload).hexdigest()

    # Explicit safety assertion: this test must never have published anything.
    # (There is no publish() call anywhere above -- this just documents the
    # invariant so a future edit that adds one gets caught by a reviewer,
    # not by an unrecoverable production article.)


def test_reupload_of_identical_content_reuses_rather_than_duplicates(
    tmp_path, live_token, cleanup_private_articles
):
    """Same file uploaded twice against the same (private) article should
    reuse the existing remote entry, not create a second one -- this is
    the behaviour _reconcile_remote_file exists to guarantee, and it's
    worth confirming against the real API's actual semantics at least
    once, since FakeFigshareServer's behaviour is only as correct as our
    reading of Figshare's real API docs.
    """
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir()
    png = mdfol / "LIVE_SMOKE_TEST_02.png"
    png.write_bytes(b"same content, uploaded twice")

    uploader = FigshareUploader(
        token=live_token,
        experiment="mkfigs-ci-live-smoke-test-2",
        mdfol=str(mdfol),
        article_title="[mkfigs CI live smoke test 2 -- safe to delete]",
    )
    article_id = uploader._get_or_create_article()
    cleanup_private_articles.append(article_id)

    uploader.upload(str(png))
    uploader.upload(str(png))  # second call, identical content

    remote_files = uploader._list_remote_files(article_id)
    matches = [f for f in remote_files if f.get("name") == "LIVE_SMOKE_TEST_02.png"]
    assert len(matches) == 1, (
        f"expected exactly one remote file entry after re-uploading identical "
        f"content, found {len(matches)}"
    )
