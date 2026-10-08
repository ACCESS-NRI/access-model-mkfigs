"""
Tests for the safety gate protecting tests/live/

These tests never contact Figshare. They verify that merely having a token
configured is insufficient to enable the real-network test suite.
"""
import pytest

from mkfigs import pushit
from tests.live import conftest as live_conftest


@pytest.mark.parametrize(
    ("env_value", "token", "expected"),
    [
        # Default: no env var, no token, no live tests
        (None, None, False),
        # no env var, but a token exists: still no live tests
        (None, "real_token", False),
        # env var set, but no token: still no live tests
        ("1", None, False),
        # env var set, and a token exists: live tests enabled
        ("1", "real_token", True),
        # env var set to something other than "1", and a token exists: still no live tests
        ("true", "real_token", False),
    ],
)
def test_live_tests_require_explicit_flag_and_token(
    monkeypatch,
    env_value,
    token,
    expected,
):
    if env_value is None:
        monkeypatch.delenv(
            live_conftest.LIVE_TEST_ENV, raising=False,
        )
    else:
        monkeypatch.setenv(
            live_conftest.LIVE_TEST_ENV, env_value,
        )

    monkeypatch.setattr(
        pushit, "resolve_figshare_token", lambda: token
    )
    assert live_conftest._live_tests_enabled() is expected
