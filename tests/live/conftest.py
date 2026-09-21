"""Fixtures for the opt-in live Figshare test suite.

Everything under tests/live/ is SKIPPED BY DEFAULT. It only runs when both:

  1. MKFIGS_LIVE_FIGSHARE_TESTS=1 is set, AND
  2. FIGSHARE_TOKEN (or ~/.figshare_token) resolves to a real token

This two-flag gate is deliberate: a token being present (e.g. because a
developer has one set up for normal NCI use) should not be enough to make
a test suite start silently talking to production Figshare on every
`pytest` invocation. Wire MKFIGS_LIVE_FIGSHARE_TESTS=1 into a manually
triggered or nightly-scheduled CI job only -- never into the per-PR job
that runs on every push, so a Figshare outage, rate limit, or slow network
never blocks a merge.

Per Figshare support's own guidance (private individual accounts have no
sandbox, and DELETE only works on unpublished/private articles): every
test here creates a PRIVATE article, asserts against it, and deletes it
in a finally block. Nothing in tests/live/ ever calls the publish
endpoint. If a test here needs to end up in a state that can't be
cleaned up automatically, it should not exist.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests


LIVE_TEST_ENV = "MKFIGS_LIVE_FIGSHARE_TESTS"


def _live_tests_enabled() -> bool:
    """
    Return True only when live tests are explicitly enabled and a token exists
    """
    if os.environ.get(LIVE_TEST_ENV) != "1":
        return False
    from mkfigs.pushit import resolve_figshare_token
    return bool(resolve_figshare_token())


@pytest.fixture(autouse=True)
def _require_explicit_live_figshare_opt_in(monkeypatch):
    """
    Skip every test in tests/live unless real-network access was opted into explicitly.
    """
    if not _live_tests_enabled():
        pytest.skip(
            "live Figshare tests are opt-in: set MKFIGS_LIVE_FIGSHARE_TESTS=1 "
            "and provide a real FIGSHARE_TOKEN to run them"
        )

    from mkfigs import configdoc

    real_figshare_request = configdoc._figshare_request

    def guarded_figshare_request(method, url, *args, **kwargs):
        if url.rstrip("/").endswith("/publish"):
            pytest.fail(
                "live figshare tests must never publish an article"
            )
        return real_figshare_request(method, url, *args, **kwargs)

    monkeypatch.setattr(configdoc, "_figshare_request", guarded_figshare_request)


@pytest.fixture
def live_token() -> str:
    """
    Return the real figshare token after the live-test gate have passed.
    """
    from mkfigs.pushit import resolve_figshare_token
    token = resolve_figshare_token()
    if not token:
        pytest.skip("no FIGSHARE_TOKEN available")
    return token


@pytest.fixture
def live_article_identity() -> dict[str, str]:
    """
    Return a unique experiment/title pair for one live test.

    Static titles are unsafe because a previous crashed test may have left 
    a private article behind. A later test could then accidentally reuse
    that old article through _get_or_create_article().
    """
    run_id = uuid.uuid4().hex[:12]
    return {
        "experiment": f"live-test-{run_id}",
        "title": f"Live test article {run_id} -- safe to delete",
    }


@pytest.fixture
def create_private_article(live_token):
    """
    Create private figshare articles and clean them up afterwards.

    article_id = create_private_article(uploader)

    rather than calling _get_or_create_article() and then separately
    remembering to register the resulting ID.

    Cleanup attempts every registered article even if an earlier deletion fails,
    then fails the test during teardown if anything could not be removed.
    """
    created: list[int] = []

    def create(uploader) -> int:
        article_id = uploader._get_or_create_article()
        created.append(article_id)
        return article_id
    yield create

    cleanup_failures: list[tuple[int, Exception]] = []

    for article_id in created:
        try:
            response = requests.delete(
                f"https://api.figshare.com/v2/account/articles/{article_id}",
                headers={"Authorization": f"token {live_token}"},
                timeout=(10, 30),
            )

            if response.status_code == 404:
                # A missing article is not a failure
                continue
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover -- best-effort cleanup
            cleanup_failures.append((article_id, exc))

    if cleanup_failures:
        failures_str = "\n".join(
            f"  {article_id}: {exc}" for article_id, exc in cleanup_failures
        )
        pytest.fail(
            f"live figshare test cleanup failed; private test articles may"
            f"require manual deletion:\n{failures_str}",
            pytrace=False,
        )
