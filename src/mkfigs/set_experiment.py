# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""
mkfigs-set-experiment – point mkfigs.sh at one experiment, the way you would by hand.

Used by the mkfigs GitHub Actions workflow on its own checkout of a paper repo, so the
rest of the workflow (qsub mkfigs.sh / mkfigs.submit, pushit) runs unchanged:

  * comments out every active ENAME= and ESMDIR= line and adds the new pair after the
    last of them;
  * optionally replaces the notebook `array=( ... )`;
  * optionally sets extra papermill parameters (`export MKFIGS_PARAMETERS="NYEARS=5 ..."`, read by
    mkfigs-run; any active MKFIGS_PARAMETERS line is commented out);
  * adds the datastore's /g/data or /scratch project to the `#PBS -l storage=` line.

Run from the directory that contains mkfigs.sh:

    python3 -m mkfigs.set_experiment --ename E --esmdir /g/data/xx00/.../datastore.json
    python3 -m mkfigs.set_experiment --ename E --esmdir D --notebooks SST MLD
"""

from __future__ import annotations

import argparse
import re
import sys

from ._paths import find_notebooks_dir

# Kept in step with the checks in .github/workflows/mkfigs.yml.
ENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
ESMDIR_RE = re.compile(r"^/(g/data|scratch)/[a-z0-9]+/[A-Za-z0-9._+/-]+\.json$")
NOTEBOOK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_ACTIVE_VAR_RE = re.compile(r"^\s*(ENAME|ESMDIR)=")   # what pushit.parse_mkfigs_sh reads
_ACTIVE_PARAMS_RE = re.compile(r"^\s*(export\s+)?MKFIGS_PARAMETERS=")
_ARRAY_RE = re.compile(r"^array=\(.*?^\)", re.DOTALL | re.MULTILINE)
_STORAGE_RE = re.compile(r"^(#PBS\s+-l\s+storage=)(\S*)", re.MULTILINE)


def storage_flag(path: str) -> str:
    """The PBS storage flag for *path*: gdata/<proj> or scratch/<proj>."""
    parts = path.split("/")
    if path.startswith("/g/data/"):
        return f"gdata/{parts[3]}"
    if path.startswith("/scratch/"):
        return f"scratch/{parts[2]}"
    raise ValueError(f"not on /g/data or /scratch: {path}")


def validate(ename: str, esmdir: str, notebooks: list[str] | None) -> None:
    if not ENAME_RE.match(ename):
        raise ValueError(f"invalid experiment name {ename!r} (letters, digits, . _ + - only)")
    if not ESMDIR_RE.match(esmdir) or "/../" in esmdir or "/./" in esmdir:
        raise ValueError(f"invalid datastore path {esmdir!r} (a .json under /g/data/<proj>/ or /scratch/<proj>/)")
    for nb in notebooks or []:
        if not NOTEBOOK_RE.match(nb):
            raise ValueError(f"invalid notebook name {nb!r}")


def set_experiment(text: str, ename: str, esmdir: str, notebooks: list[str] | None = None,
                   note: str = "", parameters: list[str] | None = None) -> str:
    """Return mkfigs.sh *text* set to run *ename* / *esmdir* (and *notebooks* and papermill
    *parameters*, NAME=VALUE strings, if given; None leaves them as they are)."""
    validate(ename, esmdir, notebooks)
    if parameters is not None:
        from .run import parse_parameters
        parse_parameters(" ".join(parameters))   # raises ValueError if any is bad
    lines = text.splitlines(keepends=True)

    last = None
    for i, line in enumerate(lines):
        if _ACTIVE_VAR_RE.match(line):
            lines[i] = "#" + line
            last = i
    if last is None:
        raise ValueError("mkfigs.sh has no active ENAME= or ESMDIR= line to replace")
    block = [
        f"# Set by mkfigs.set_experiment{(': ' + note) if note else ''}\n",
        f"ESMDIR={esmdir}\n",
        f"ENAME={ename}\n",
    ]
    if parameters is not None:
        for i, line in enumerate(lines):
            if _ACTIVE_PARAMS_RE.match(line):
                lines[i] = "#" + line
        if parameters:
            block.append(f'export MKFIGS_PARAMETERS="{" ".join(parameters)}"   # extra papermill parameters\n')
    lines[last + 1:last + 1] = block
    text = "".join(lines)

    if notebooks:
        if not _ARRAY_RE.search(text):
            raise ValueError("mkfigs.sh has no array=( ... ) notebook list to replace")
        new_array = "array=(\n" + "".join(f"    {nb}\n" for nb in notebooks) + ")"
        text = _ARRAY_RE.sub(lambda _m: new_array, text, count=1)

    flag = storage_flag(esmdir)
    m = _STORAGE_RE.search(text)
    if m is None:
        raise ValueError("mkfigs.sh has no '#PBS -l storage=' line")
    if flag not in m.group(2).split("+"):
        joined = f"{m.group(2)}+{flag}" if m.group(2) else flag
        text = text[:m.start(2)] + joined + text[m.end(2):]
    return text


def current_experiment(text: str) -> tuple[str, str]:
    """The ENAME and ESMDIR mkfigs.sh currently runs (its last active assignments)."""
    values = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(ENAME|ESMDIR)=([^\s#]+)", line)
        if m:
            values[m.group(1)] = m.group(2).strip("\"'")
    if "ENAME" not in values or "ESMDIR" not in values:
        raise ValueError("mkfigs.sh has no active ENAME= and ESMDIR= lines")
    ename = values["ENAME"]
    esmdir = values["ESMDIR"].replace("${ENAME}", ename).replace("$ENAME", ename)
    return ename, esmdir


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print-current", action="store_true",
                   help="only print the ENAME and ESMDIR mkfigs.sh runs now (tab-separated), and exit")
    p.add_argument("--ename", help="experiment name")
    p.add_argument("--esmdir", help="path to the experiment's datastore .json")
    p.add_argument("--notebooks", nargs="*", default=None,
                   help="notebooks to run (default: keep the mkfigs.sh array)")
    p.add_argument("--note", default="", help="text for the comment above the new lines, e.g. a run URL")
    p.add_argument("--parameters", nargs="*", default=None, metavar="NAME=VALUE",
                   help="extra papermill parameters for every notebook, e.g. NYEARS=5 "
                        "(default: keep mkfigs.sh's; give none to clear them)")
    args = p.parse_args(argv)

    sh = find_notebooks_dir() / "mkfigs.sh"
    if not sh.exists():
        sys.exit("ERROR: run this from the directory that contains mkfigs.sh")
    if args.print_current:
        try:
            print("\t".join(current_experiment(sh.read_text())))
        except ValueError as exc:
            sys.exit(f"ERROR: {exc}")
        return
    if not (args.ename and args.esmdir):
        p.error("--ename and --esmdir are required")
    try:
        new = set_experiment(sh.read_text(), args.ename, args.esmdir, args.notebooks, args.note,
                             args.parameters)
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}")
    sh.write_text(new)
    print(f"mkfigs.sh now runs {args.ename} ({args.esmdir})")


if __name__ == "__main__":
    main()
