"""Tests for FigshareUploader._upload_parts -- the concurrent part-upload,
retry, and resume logic. These monkeypatch requests.put / time.sleep /
_is_part_complete directly rather than going through FakeFigshareServer:
the behaviour under test is about *how the client reacts to transport
failures*, which is easier to state precisely as "the 2nd PUT call raises,
the 3rd succeeds" than to model as HTTP server state.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from mkfigs import configdoc
from mkfigs.configdoc import FigshareUploader


def _uploader(tmp_path: Path) -> FigshareUploader:
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir(exist_ok=True)
    return FigshareUploader(token="tok", experiment="exp", mdfol=str(mdfol))


def _ok_response():
    r = MagicMock()
    r.raise_for_status.return_value = None
    return r


def test_upload_parts_skips_parts_already_marked_complete(tmp_path, monkeypatch):
    up = _uploader(tmp_path)
    fpath = tmp_path / "big.bin"
    fpath.write_bytes(b"0123456789")

    calls = []

    def fake_put(url, headers=None, data=None, timeout=None):
        calls.append(url)
        return _ok_response()

    monkeypatch.setattr(configdoc.requests, "put", fake_put)

    parts_info = {"parts": [
        {"partNo": 1, "startOffset": 0, "endOffset": 4, "status": "COMPLETE"},
        {"partNo": 2, "startOffset": 5, "endOffset": 9, "status": "PENDING"},
    ]}
    up._upload_parts("https://api.figshare.com/v2/upload", parts_info, str(fpath), "big.bin")

    assert len(calls) == 1
    assert calls[0].endswith("/2")  # only the pending part was ever PUT


def test_upload_parts_retries_transient_failure_then_succeeds(tmp_path, monkeypatch):
    up = _uploader(tmp_path)
    fpath = tmp_path / "small.bin"
    fpath.write_bytes(b"abcde")

    attempts = {"n": 0}

    def flaky_put(url, headers=None, data=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise requests.exceptions.ConnectionError("simulated drop")
        return _ok_response()

    monkeypatch.setattr(configdoc.requests, "put", flaky_put)
    monkeypatch.setattr(configdoc.time, "sleep", lambda *_: None)  # skip real backoff delay
    # Not complete server-side yet on the first (failed) attempt.
    monkeypatch.setattr(FigshareUploader, "_is_part_complete", lambda self, *_: False)

    parts_info = {"parts": [{"partNo": 1, "startOffset": 0, "endOffset": 4, "status": "PENDING"}]}
    up._upload_parts("https://api.figshare.com/v2/upload", parts_info, str(fpath), "small.bin")

    assert attempts["n"] == 2  # failed once, succeeded on retry


def test_upload_parts_treats_dropped_connection_as_success_if_figshare_confirms_complete(
    tmp_path, monkeypatch
):
    """A PUT can time out client-side after the data has already fully
    landed server-side (high-latency link). _is_part_complete should let
    that be recognised as a success instead of retrying (and potentially
    exhausting retries on) a part that's actually already done.
    """
    up = _uploader(tmp_path)
    fpath = tmp_path / "small.bin"
    fpath.write_bytes(b"abcde")

    def always_times_out(url, headers=None, data=None, timeout=None):
        raise requests.exceptions.ConnectionError("simulated drop")

    monkeypatch.setattr(configdoc.requests, "put", always_times_out)
    monkeypatch.setattr(FigshareUploader, "_is_part_complete", lambda self, *_: True)

    parts_info = {"parts": [{"partNo": 1, "startOffset": 0, "endOffset": 4, "status": "PENDING"}]}
    # Must NOT raise, even though every PUT call fails -- _is_part_complete
    # says the part is already done, so it's accepted without a retry loop.
    up._upload_parts("https://api.figshare.com/v2/upload", parts_info, str(fpath), "small.bin")


def test_upload_parts_exhausts_retries_and_raises(tmp_path, monkeypatch):
    up = _uploader(tmp_path)
    fpath = tmp_path / "small.bin"
    fpath.write_bytes(b"abcde")

    def always_fails(url, headers=None, data=None, timeout=None):
        raise requests.exceptions.ConnectionError("simulated drop")

    monkeypatch.setattr(configdoc.requests, "put", always_fails)
    monkeypatch.setattr(configdoc.time, "sleep", lambda *_: None)
    monkeypatch.setattr(FigshareUploader, "_is_part_complete", lambda self, *_: False)

    parts_info = {"parts": [{"partNo": 1, "startOffset": 0, "endOffset": 4, "status": "PENDING"}]}
    with pytest.raises(requests.exceptions.ConnectionError):
        up._upload_parts("https://api.figshare.com/v2/upload", parts_info, str(fpath), "small.bin",
                          max_attempts=2)
