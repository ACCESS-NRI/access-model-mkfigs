# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""
mkfigs-run – run evaluation notebooks via papermill.

Called by mkfigs.sh after modules are loaded.  Receives ENAME, ESMDIR, and
WFOLDER as command-line arguments.

Usage (via mkfigs.sh):
    qsub mkfigs.sh

After the job completes, run `python3 -m mkfigs.pushit` on a login node (from
the directory containing mkfigs.sh) to upload figures to Figshare and prepare
the git commit. The exact commands are printed at the end of the job.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from . import get_mkfigs_version


def _extract_notebook_error(rendered_path: Path) -> str | None:
    """Return the last error cell output from a rendered notebook, ANSI-stripped."""
    try:
        with open(rendered_path) as f:
            nb = json.load(f)
        for cell in reversed(nb.get("cells", [])):
            for output in cell.get("outputs", []):
                if output.get("output_type") == "error":
                    ename = output.get("ename", "")
                    evalue = output.get("evalue", "")
                    tb = "\n".join(output.get("traceback", []))
                    tb = re.sub(r'\x1b\[[0-9;]*m', '', tb)
                    return f"{ename}: {evalue}\n{tb}"
    except Exception:
        pass
    return None


def _fix_kernel(nb_path: Path) -> Path:
    """Normalise the notebook kernel to 'python3' so papermill can execute it.

    Notebooks opened interactively in ARE may have a kernel name like
    'conda-env-analysis3-25.07-py' that does not exist on the command line.
    This writes the kernel-fixed notebook to a private, PID-suffixed copy
    next to nb_path and returns that path — nb_path itself is never
    modified. Concurrent mkfigs-run jobs (e.g. multiple experiments queued
    at once, all reading the same shared notebooks/<name>.ipynb template)
    used to race on an in-place tmp.replace(nb_path) of that shared file;
    writing a private copy per run removes the shared mutable state.
    """
    with open(nb_path) as f:
        d = json.load(f)
    d["metadata"]["kernelspec"]["display_name"] = "Python 3 (ipykernel)"
    d["metadata"]["kernelspec"]["name"] = "python3"
    fixed = nb_path.with_name(f"{nb_path.stem}.kernel-fixed.{os.getpid()}.ipynb")
    with open(fixed, "w") as f:
        json.dump(d, f)
    return fixed


def _check_nci_environment() -> None:
    try:
        import nci_ipynb  # noqa: F401
    except ModuleNotFoundError:
        sys.exit(
            "ERROR: nci_ipynb not found.\n"
            "Please load the conda environment before running this script:\n\n"
            "  module purge\n"
            "  module use /g/data/xp65/public/modules\n"
            "  module load conda/analysis3\n"
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ename",   required=True, help="Experiment name (ENAME)")
    p.add_argument("--esmdir",  required=True, help="Path to ESM datastore JSON")
    p.add_argument("--wfolder", required=True, help="Repo root folder")
    p.add_argument(
        "--notebooks-subdir", default="",
        help="Subdirectory under <wfolder>/notebooks/ where the .ipynb files "
             "actually live (e.g. 'polished-python' for access-cm3-paper-1). "
             "Default '' preserves the original access-om3-paper-1 layout, "
             "where notebooks sit directly in <wfolder>/notebooks/.",
    )
    return p.parse_args()


def setup_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-8s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file)),
        ],
    )
    return logging.getLogger(__name__)


# Set by mkfigs itself for every notebook: not overridable through MKFIGS_PARAMETERS.
RESERVED_PARAMETERS = ("esm_file", "papermill", "cwd", "nbname")
_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAM_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/+-]+$")


def parse_parameters(text: str) -> list[tuple[str, str]]:
    """Extra papermill parameters from MKFIGS_PARAMETERS: space-separated NAME=VALUE pairs.

    e.g. "NYEARS=5 start_year=1990". Every notebook in the run gets them; one that has no such
    parameter just gets a papermill warning. Values are passed with papermill's -p, which turns
    numbers and True/False into ints, floats and booleans.
    """
    params: list[tuple[str, str]] = []
    for item in text.split():
        name, sep, value = item.partition("=")
        if not sep or not _PARAM_NAME_RE.match(name) or not _PARAM_VALUE_RE.match(value):
            raise ValueError(f"bad parameter {item!r}: expected NAME=VALUE "
                             "(value: letters, digits and . _ : / + -)")
        if name in RESERVED_PARAMETERS:
            raise ValueError(f"parameter {name!r} is set by mkfigs and cannot be changed")
        if any(name == n for n, _ in params):
            raise ValueError(f"parameter {name!r} is given twice")
        params.append((name, value))
    return params


def run_notebook(nb: str, esmdir: str, ofol: Path, notebooks_dir: Path) -> bool:
    """Strip outputs, run via papermill, convert to markdown. Returns True on success.

    Extra papermill parameters come from MKFIGS_PARAMETERS (see parse_parameters).
    """
    extra = [a for name, value in parse_parameters(os.environ.get("MKFIGS_PARAMETERS", ""))
             for a in ("-p", name, value)]
    nb_path = notebooks_dir / f"{nb}.ipynb"
    fixed_path = _fix_kernel(nb_path)

    try:
        rendered = str(ofol / f"{nb}_rendered.ipynb")
        result = subprocess.run(
            [
                "papermill", str(fixed_path), rendered,
                "-p", "esm_file", esmdir,
                "-p", "papermill", "True",
                "-p", "cwd", str(ofol) + "/",
                "-p", "nbname", f"{nb}.ipynb",
                *extra,
            ],
            cwd=str(notebooks_dir),
        )
    finally:
        fixed_path.unlink(missing_ok=True)

    subprocess.run(
        ["jupyter", "nbconvert", "--to", "markdown", rendered],
        check=False,
    )
    return result.returncode == 0


def main() -> None:
    _check_nci_environment()
    args = parse_args()

    ename  = args.ename
    esmdir = args.esmdir
    # Notebooks name their figure paths after the experiment (MkmdWriter); make
    # that the ENAME mkfigs-pushit will upload under, whatever the datastore path looks like.
    os.environ["MKFIGS_ENAME"] = ename
    notebooks_dir = Path(args.wfolder) / "notebooks" / args.notebooks_subdir

    ofol  = notebooks_dir / f"mkfigs_output_{ename}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True, exist_ok=True)

    log = setup_logging(mdfol / "mkfigs_run.log")

    mkfigs_ver = get_mkfigs_version()

    log.info("Experiment            : %s", ename)
    log.info("ESMDIR                : %s", esmdir)
    log.info("Output dir            : %s", ofol)
    log.info("Log file              : %s", mdfol / "mkfigs_run.log")
    log.info("access-model-mkfigs   : %s", mkfigs_ver)

    try:
        parameters = parse_parameters(os.environ.get("MKFIGS_PARAMETERS", ""))
    except ValueError as exc:
        log.error("MKFIGS_PARAMETERS: %s", exc)
        sys.exit(1)
    if parameters:
        log.info("Parameters            : %s", " ".join(f"{n}={v}" for n, v in parameters))

    notebooks_env = os.environ.get("MKFIGS_NOTEBOOKS", "")
    if not notebooks_env:
        log.error("MKFIGS_NOTEBOOKS env var not set — run via mkfigs.sh")
        sys.exit(1)
    notebooks = [n for n in notebooks_env.split(":") if n]

    succeeded: list[str] = []
    failed:    list[str] = []

    log.info("")
    log.info("Running %d notebooks ...", len(notebooks))
    log.info("")
    for nb in notebooks:
        log.info("START  %s", nb)
        ok = run_notebook(nb, esmdir, ofol, notebooks_dir)
        if ok:
            succeeded.append(nb)
            log.info("OK     %s", nb)
        else:
            failed.append(nb)
            log.error("FAILED %s", nb)

    log.info("")
    log.info("=" * 56)
    log.info("Run complete")
    log.info("=" * 56)
    log.info("Output folder : %s", ofol)
    log.info("Markdown dir  : %s", mdfol)
    log.info("Log file      : %s", mdfol / "mkfigs_run.log")

    if succeeded:
        log.info("Succeeded (%d): %s", len(succeeded), ", ".join(succeeded))
    if failed:
        log.error("FAILED    (%d): %s", len(failed), ", ".join(failed))
        errors_log = mdfol / "mkfigs_errors.log"
        with open(errors_log, "w") as ef:
            for nb in failed:
                rendered = ofol / f"{nb}_rendered.ipynb"
                ef.write(f"{'=' * 56}\n")
                ef.write(f"FAILED: {nb}\n")
                ef.write(f"{'=' * 56}\n")
                error = _extract_notebook_error(rendered)
                ef.write(error + "\n" if error else "(no error output found in rendered notebook)\n")
                ef.write("\n")
        log.error("Error details: %s", errors_log)

    _print_next_steps(Path(args.wfolder), notebooks_dir)

def _print_next_steps(wfolder: Path, notebooks_dir: Path) -> None:
    """Print the login-node commands to run after the batch job."""
    wfolder, notebooks_dir = wfolder.resolve(), notebooks_dir.resolve()
    mkfigs_src = wfolder / "external" / "access-model-mkfigs" / "src"
    sep = "=" * 56
    lines = [
        "", sep, "Run complete — next steps on a login node:", sep, "",
        "  module purge",
        "  module use /g/data/xp65/public/modules",
        "  module load conda/analysis3",
    ]
    if mkfigs_src.is_dir():
        lines.append(f'  export PYTHONPATH="{mkfigs_src}:${{PYTHONPATH}}"')
    lines += [
        f"  cd {notebooks_dir}",
        "",
        "  python3 -m mkfigs.pushit --dry-run                   # optional preview",
        "  python3 -m mkfigs.pushit",
        "  python3 -m mkfigs.pushit --check-figshare-integrity  # optional, before publishing",
        "  # log in to Figshare and publish the article, then:",
        "  python3 -m mkfigs.pushit --check-figshare-upload",
        sep, "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
