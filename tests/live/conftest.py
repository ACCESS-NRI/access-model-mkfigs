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

import pytest


def _live_tests_enabled() -> bool:
    if os.environ.get("MKFIGS_LIVE_FIGSHARE_TESTS") != "1":
        return False
    from mkfigs.pushit import resolve_figshare_token
    return bool(resolve_figshare_token())


pytestmark = pytest.mark.skipif(
    not _live_tests_enabled(),
    reason="live Figshare tests are opt-in: set MKFIGS_LIVE_FIGSHARE_TESTS=1 "
           "and a real FIGSHARE_TOKEN to run them",
)


@pytest.fixture
def live_token() -> str:
    from mkfigs.pushit import resolve_figshare_token
    token = resolve_figshare_token()
    if not token:
        pytest.skip("no FIGSHARE_TOKEN available")
    return token


@pytest.fixture
def cleanup_private_articles(live_token):
    """Yield a list; any article id appended to it is DELETE'd on teardown.

    Only ever call this with a PRIVATE (unpublished) article id -- per
    Figshare support, DELETE on a public/published article returns 403/405
    and is not something this fixture (or anything in this repo) should
    ever attempt to work around.
    """
    import requests
    created: list[int] = []
    yield created
    for article_id in created:
        try:
            requests.delete(
                f"https://api.figshare.com/v2/account/articles/{article_id}",
                headers={"Authorization": f"token {live_token}"},
                timeout=30,
            )
        except Exception as exc:  # pragma: no cover -- best-effort cleanup
            print(f"[live test cleanup] WARNING: failed to delete article "
                  f"{article_id}: {exc}. Delete it manually on Figshare.")
