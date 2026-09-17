# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""
mkfigs-fetch-notebooks – download rendered notebooks from Figshare at
ReadTheDocs build time.

Every experiment that mkfigs-pushit has published writes a small JSON
manifest into the repo at:

    documentation/docs/pages/experiments/<ename>/notebooks_urls.json

mapping notebook stem -> Figshare download URL. The rendered notebooks
themselves (with cell outputs intact) are too large to store in git, so
they live on Figshare and are fetched here, right before MkDocs builds
the site, so mkdocs-jupyter can render each <nb>.ipynb as a page.

This is a generalised, testable version of the pre_build snippet that
used to be pasted inline into every repo's .readthedocs.yaml. Repos call
it the same way regardless of which paper repo they are:

    python3 -m mkfigs.fetch

Notebooks already present on disk are skipped (idempotent). A manifest
with an empty map, or a missing "notebooks_urls.json", is silently
skipped -- an experiment with no published notebooks yet is not an
error.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def iter_manifests(docs_root: Path):
    """Yield (ename, url_map) for every notebooks_urls.json under docs_root."""
    if not docs_root.exists():
        return
    for urls_json in sorted(docs_root.glob("*/notebooks_urls.json")):
        ename = urls_json.parent.name
        try:
            url_map = json.loads(urls_json.read_text())
        except Exception as exc:
            print(f"[{ename}] WARNING: could not read {urls_json}: {exc}", file=sys.stderr)
            continue
        # Manifests also carry "_run_times" / "_run_versions" bookkeeping
        # keys alongside the real notebook -> URL entries; skip those.
        url_map = {k: v for k, v in url_map.items() if not k.startswith("_")}
        yield ename, urls_json.parent, url_map


def download_manifest_notebooks(url_map: dict, nb_dir: Path) -> tuple[int, int]:
    """Download every notebook in url_map into nb_dir. Returns (downloaded, skipped).

    Split out from download_from_manifest so any future caller that reads a
    manifest from somewhere other than this repo's own docs tree (e.g. a
    submodule) can reuse the download step without duplicating it.
    """
    downloaded = 0
    skipped = 0
    nb_dir.mkdir(parents=True, exist_ok=True)
    for nb_name, url in url_map.items():
        dest = nb_dir / f"{nb_name}.ipynb"
        if dest.exists():
            print(f"  {nb_name}.ipynb already present - skipping")
            skipped += 1
            continue
        print(f"  {nb_name}.ipynb  <-  {url}")
        try:
            urllib.request.urlretrieve(url, dest)
            downloaded += 1
        except Exception as exc:
            print(f"  ERROR downloading {nb_name}: {exc}", file=sys.stderr)
    return downloaded, skipped


def download_from_manifest(docs_root: Path) -> tuple[int, int]:
    """Download every notebook referenced by manifests under docs_root.

    Returns (total_downloaded, total_skipped).
    """
    total_downloaded = 0
    total_skipped = 0

    found_any = False
    for ename, exp_dir, url_map in iter_manifests(docs_root):
        found_any = True
        if not url_map:
            print(f"[{ename}] No notebook URLs recorded yet - skipping.")
            continue

        print(f"[{ename}] Downloading {len(url_map)} notebook(s)...")
        downloaded, skipped = download_manifest_notebooks(url_map, exp_dir / "notebooks")
        total_downloaded += downloaded
        total_skipped += skipped

    if not found_any:
        print(f"No experiments directory found under {docs_root} - nothing to download.")

    return total_downloaded, total_skipped


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--docs-root",
        default="documentation/docs/pages/experiments",
        help="Directory containing <ename>/notebooks_urls.json manifests "
             "(default: documentation/docs/pages/experiments)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    docs_root = Path(args.docs_root)

    print("=== Downloading rendered notebooks from Figshare ===")
    downloaded, skipped = download_from_manifest(docs_root)
    print()
    print(f"=== Done: {downloaded} downloaded, {skipped} already present ===")


if __name__ == "__main__":
    main()
