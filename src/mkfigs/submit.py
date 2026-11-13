# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""
mkfigs-submit – run the notebooks in mkfigs.sh as one PBS job each.

`qsub mkfigs.sh` runs every notebook one after the other in a single job.
This instead submits mkfigs.sh once per notebook (each job runs just that
notebook, with the queue, storage, memory and walltime from the #PBS header
of mkfigs.sh), plus one small summary job that waits for all of them and
writes the same summary, mkfigs_errors.log and next steps that a single
`qsub mkfigs.sh` would. The run then takes as long as the slowest notebook
rather than the sum of all of them, for about the same SU cost.

Run it on a login node, from the mkfigs.sh directory, instead of `qsub mkfigs.sh`:

    python3 -m mkfigs.submit --dry-run     # print the qsub commands, submit nothing
    python3 -m mkfigs.submit               # every notebook in the mkfigs.sh array
    python3 -m mkfigs.submit SST MLD       # just these notebooks

Requires a mkfigs.sh that leaves MKFIGS_NOTEBOOKS alone when it is already set:
    MKFIGS_NOTEBOOKS="${MKFIGS_NOTEBOOKS:-$(printf '%s:' "${array[@]}")}"
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from ._paths import find_notebooks_dir

# Resources for the summary job: it only reads the per-notebook status files.
SUMMARY_RESOURCES = "ncpus=1,mem=4GB,jobfs=1GB,walltime=00:15:00"


def check_mkfigs_sh(sh: Path) -> None:
    """Refuse to submit if mkfigs.sh would overwrite MKFIGS_NOTEBOOKS (every job would run everything)."""
    text = sh.read_text()
    if not re.search(r"MKFIGS_NOTEBOOKS[:=]?-", text):
        sys.exit(
            f"ERROR: {sh} always sets MKFIGS_NOTEBOOKS from its array, so every job would run every\n"
            "notebook. Change it to keep a value passed in by qsub -v, e.g.\n\n"
            "  printf -v _all '%s:' \"${array[@]}\"\n"
            "  MKFIGS_NOTEBOOKS=\"${MKFIGS_NOTEBOOKS:-${_all%:}}\"\n"
        )


def job_name(nb: str) -> str:
    """PBS job names must start with a letter; keep them short enough to read in qstat."""
    return ("mk_" + re.sub(r"[^A-Za-z0-9_.-]", "_", nb))[:15]


def qsub(args: list[str], dry_run: bool, cwd: Path) -> str:
    """Run qsub and return the job id (or a placeholder in dry-run mode)."""
    cmd = ["qsub", *args]
    print("  " + shlex.join(cmd))
    if dry_run:
        return f"<{args[args.index('-N') + 1]}>"
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"ERROR: qsub failed (exit {out.returncode}): {out.stderr.strip() or out.stdout.strip()}\n"
                 "(jobs submitted before this one, if any, are listed above; qdel them if needed)")
    print(f"    -> {out.stdout.strip()}")
    return out.stdout.strip()


def project_args(sh: Path) -> list[str]:
    """`-P $PROJECT` unless mkfigs.sh sets `#PBS -P` itself.

    Gadi's qsub falls back to $PROJECT for the project to charge, but not when -v is given
    (it then fails with "You have not requested a project"), and every job here uses -v.
    """
    if re.search(r"^#PBS\s+-P\s", sh.read_text(), re.MULTILINE):
        return []
    project = os.environ.get("PROJECT")
    if not project:
        sys.exit("ERROR: set $PROJECT (or add #PBS -P <project> to mkfigs.sh)")
    return ["-P", project]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("notebooks", nargs="*", help="notebooks to run (default: the whole mkfigs.sh array)")
    p.add_argument("--dry-run", action="store_true", help="print the qsub commands, submit nothing")
    args = p.parse_args(argv)

    here = find_notebooks_dir()
    sh = here / "mkfigs.sh"
    if not sh.exists():
        sys.exit("ERROR: run this from the directory that contains mkfigs.sh")
    check_mkfigs_sh(sh)
    from .pushit import parse_mkfigs_sh  # the same reading of mkfigs.sh that pushit uses
    ename, _esmdir, array = parse_mkfigs_sh()

    notebooks = args.notebooks or array
    unknown = [nb for nb in notebooks if nb not in array]
    if unknown:
        sys.exit(f"ERROR: not in the mkfigs.sh array: {' '.join(unknown)}")
    if not notebooks:
        sys.exit("ERROR: no notebooks to run")

    # Results of an earlier run of these notebooks must not be mistaken for this run's.
    status_dir = here / f"mkfigs_output_{ename}" / "mkmd" / "status"
    if not args.dry_run:
        for nb in notebooks:
            (status_dir / f"{nb}.txt").unlink(missing_ok=True)

    print(f"Experiment {ename}: {len(notebooks)} notebook job(s) + 1 summary job")
    proj = project_args(sh)
    ids = []
    for nb in notebooks:
        ids.append(qsub([*proj, "-N", job_name(nb), "-m", "n", "-v", f"MKFIGS_NOTEBOOKS={nb},MKFIGS_SKIP_REPORT=1",
                         "mkfigs.sh"], args.dry_run, here))
    summary = qsub([*proj, "-N", "mk_summary", "-l", SUMMARY_RESOURCES,
                    "-W", "depend=afterany:" + ":".join(ids),
                    "-v", "MKFIGS_NOTEBOOKS=" + ":".join(notebooks) + ",MKFIGS_REPORT_ONLY=1",
                    "mkfigs.sh"], args.dry_run, here)

    if args.dry_run:
        print("\nDry run: nothing submitted.")
        return
    print(f"\nSubmitted. The summary job {summary} runs when all of them have finished; then:")
    print(f"  grep -hE 'Succeeded|FAILED' mk_summary.o{summary.split('.')[0]}")
    print("  qdel " + " ".join(ids + [summary]) + "   # to cancel everything")


if __name__ == "__main__":
    main()
