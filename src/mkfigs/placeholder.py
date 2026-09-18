# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""
mkfigs-placeholder – fake the output of a real notebook run, purely so
`mkfigs-pushit --local` has something to preview a site's structure from
before any papermill run has happened.

Reads ENAME and the notebook array from mkfigs.sh (same parser
mkfigs-pushit uses), and for every notebook writes:

  - mkfigs_output_<ename>/mkmd/<nb>_01.png   a plain placeholder image,
                                              notebook name printed on it
  - mkfigs_output_<ename>/mkmd/<nb>.md       a minimal stub summary page
  - mkfigs_output_<ename>/<nb>_rendered.ipynb  a one-cell stub notebook

This is exactly the shape mkfigs-pushit expects a real run to have left
behind (see _seed_notebook_outputs-style fixtures in the test suite) --
nothing else needed to change for --local to pick it up. Follow with:

    mkfigs-pushit --local

to get a full site preview (nav, pages, images) with zero papermill and
zero Figshare involvement. Delete mkfigs_output_<ename>/ afterwards --
none of this is real output and must never be mistaken for it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import pushit


def _placeholder_png(nb_name: str, dest: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.set_facecolor("#e0e0e0")
    ax.text(0.5, 0.5, f"placeholder\n{nb_name}", ha="center", va="center", fontsize=14, color="#555555")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.savefig(dest, dpi=100)
    plt.close(fig)


def _placeholder_notebook() -> dict:
    return {
        "cells": [{
            "cell_type": "markdown",
            "source": ["**Placeholder** -- this notebook has not actually been run yet."],
            "metadata": {},
        }],
        "metadata": {"kernelspec": {"display_name": "Python 3 (ipykernel)", "name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def make_placeholder_output(ename: str, notebooks: list[str], notebooks_dir: Path) -> Path:
    """Write fake mkfigs-run output for every notebook in *notebooks*.
    Returns the mkfigs_output_<ename> directory that was written.
    """
    ofol = notebooks_dir / f"mkfigs_output_{ename}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True, exist_ok=True)

    for nb in notebooks:
        png_name = f"{nb}_01.png"
        _placeholder_png(nb, mdfol / png_name)

        (mdfol / f"{nb}.md").write_text(
            "<!-- placeholder -- not a real run -->\n"
            f"# {nb}\n \n"
            f"## Placeholder\n \n"
            f"![placeholder](/assets/experiments/{ename}/{png_name}) \n \n"
            " Caption: this notebook has not actually been run yet.\n \n"
        )

        (ofol / f"{nb}_rendered.ipynb").write_text(json.dumps(_placeholder_notebook()))

    return ofol


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ename", default=None, help="Override experiment name (default: parsed from mkfigs.sh)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ename, _esmdir, notebooks = pushit.parse_mkfigs_sh()
    if args.ename:
        ename = args.ename

    if not notebooks:
        raise SystemExit("ERROR: no notebooks found in mkfigs.sh's array=(...)")

    ofol = make_placeholder_output(ename, notebooks, pushit.HERE)

    print(f"Wrote placeholder output for {len(notebooks)} notebook(s) to:")
    print(f"  {ofol}")
    print()
    print("Next: mkfigs-pushit --local")
    print(f"Then: cd ../documentation && mkdocs serve")
    print()
    print(f"Delete {ofol} when done -- it is not a real run.")


if __name__ == "__main__":
    main()
