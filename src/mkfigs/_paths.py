# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""Locate a paper repo's mkfigs.sh directory and repo root (shared by pushit and restore)."""

from __future__ import annotations

from pathlib import Path


def find_notebooks_dir() -> Path:
    """Return the nearest directory at or above CWD containing mkfigs.sh (else CWD).

    OM3 keeps mkfigs.sh in notebooks/, CM3 in notebooks/polished-python/.
    """
    candidate = Path.cwd()
    for _ in range(4):
        if (candidate / "mkfigs.sh").exists():
            return candidate.resolve()
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return Path.cwd()


def find_repo_root(notebooks_dir: Path) -> Path:
    """Return the nearest ancestor with .git or documentation/mkdocs.yml (else the parent)."""
    candidate = notebooks_dir
    for _ in range(4):
        if (candidate / ".git").exists() or (candidate / "documentation" / "mkdocs.yml").exists():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return notebooks_dir.parent
