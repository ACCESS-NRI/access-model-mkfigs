"""Run-summary version column links to the right access-model-mkfigs ref."""

import pytest

from mkfigs.pushit import MKFIGS_REPO_URL, _mkfigs_version_md

R = MKFIGS_REPO_URL


@pytest.mark.parametrize(
    "version, expected",
    [
        ("git+4e25018", f"[`git+4e25018`]({R}/commit/4e25018)"),
        ("git+4e25018-dirty", f"[`git+4e25018`]({R}/commit/4e25018) (+ local changes)"),
        ("v0.2.0", f"[`v0.2.0`]({R}/tree/v0.2.0)"),
        ("v0.2.0-3-gabc1234", f"[`v0.2.0-3-gabc1234`]({R}/commit/abc1234)"),
        ("0.1.0+abc1234", f"[`0.1.0+abc1234`]({R}/commit/abc1234)"),
        ("0.1.0", "0.1.0"),
        ("unknown", "unknown"),
        ("—", "—"),
    ],
)
def test_mkfigs_version_md(version, expected):
    assert _mkfigs_version_md(version) == expected
