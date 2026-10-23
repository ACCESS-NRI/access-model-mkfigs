"""mkfigs-backfill: create figure-explorer data for every run on the Experiments page.

Run from the paper repo's ``notebooks/`` folder on Gadi::

    mkfigs-backfill list                 # runs, forcing, tags, Figshare articles
    mkfigs-backfill submit [--only RUN]  # one mkfigs.sh job per run, line-plot notebooks only
    mkfigs-backfill upload [--only RUN]  # bundle, upload, check, publish, index

``upload`` publishes a new version of each run's Figshare article only after
Figshare's own MD5 of the uploaded bundle matches the local file. Use
``--no-publish`` to stop before that step, and ``--dry-run`` on any command to
see what would happen.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import experiments, figuredata

NOTEBOOKS = [
    "timeseries", "Timeseries_daily_extreme_from_2D_fields", "StraitTransports",
    "MeridionalHeatTransport", "Overturning_in_ACCESS_OM3", "SeaIce_area", "SeaIce_Vol",
    "SeaIce_mass_budget_climatology",
]


def _select(runs, only):
    if not only:
        return runs
    unknown = sorted(set(only) - {r.name for r in runs})
    if unknown:
        sys.exit(f"ERROR: not on the Experiments page: {', '.join(unknown)}")
    return [r for r in runs if r.name in only]


def cmd_list(runs, repo: Path) -> int:
    tags = experiments.git_tags(repo)
    for r in runs:
        tag = experiments.tag_for(r, tags)
        article = experiments.figshare_article(repo, tag) if tag else None
        flags = [f for f, bad in (("no datastore", not Path(r.datastore).exists()), ("no tag", not tag),
                                  ("no article", tag and not article),
                                  (f"Base Configuration says {r.base_configuration}", r.forcing_mismatch)) if bad]
        print(f"{r.forcing:7s} {r.name:52s} {tag or '-':60s} {article or '-'}" + (f"  [{'; '.join(flags)}]" if flags else ""))
    return 0


def cmd_submit(runs, notebooks_dir: Path, notebooks: list[str], dry_run: bool) -> int:
    submitted, skipped = 0, []
    for r in runs:
        if not Path(r.datastore).exists():
            skipped.append(f"{r.name}: datastore missing ({r.datastore})")
            continue
        # Gadi's qsub ignores $PROJECT once -v is given ("You have not requested a project to charge"), so pass it
        project = ["-P", os.environ["PROJECT"]] if os.environ.get("PROJECT") else []
        cmd = ["qsub", *project, "-v", f"ENAME={r.name},ESMDIR={r.datastore},MKFIGS_NOTEBOOKS={':'.join(notebooks)}", "mkfigs.sh"]
        if dry_run:
            print("DRY RUN:", " ".join(cmd))
            continue
        out = subprocess.run(cmd, cwd=notebooks_dir, capture_output=True, text=True, check=False)
        if out.returncode:
            skipped.append(f"{r.name}: qsub failed: {out.stderr.strip()}")
            continue
        print(f"{r.name}: {out.stdout.strip()}")
        submitted += 1
    for s in skipped:
        print("SKIPPED", s)
    print(f"{submitted} job(s) submitted, {len(skipped)} skipped.")
    return 1 if skipped else 0


def cmd_upload(runs, repo: Path, notebooks_dir: Path, dry_run: bool, publish: bool) -> int:
    from . import get_mkfigs_version
    from .configdoc import FigshareUploader

    token = ""
    if not dry_run:
        from .pushit import resolve_figshare_token
        token = resolve_figshare_token()
        if not token:
            sys.exit("ERROR: no Figshare token (set FIGSHARE_TOKEN or ~/.figshare_token)")
    tags, done, failed = experiments.git_tags(repo), [], []
    index = repo / figuredata.INDEX_RELPATH
    for r in runs:
        mdfol = notebooks_dir / f"mkfigs_output_{r.name}" / "mkmd"
        recs = figuredata.records(mdfol)
        tag = experiments.tag_for(r, tags)
        article = experiments.figshare_article(repo, tag) if tag else None
        if not recs or not article:
            failed.append(f"{r.name}: " + ("no line data yet (job not run, or it failed)" if not recs else "no Figshare article found via its tag"))
            continue
        prov = figuredata.provenance(repo, mdfol, get_mkfigs_version(), r.datastore)
        bundle = figuredata.build_bundle(mdfol, r.name, mdfol.parent, prov)
        print(f"{r.name}: {len(recs)} figures, {bundle.stat().st_size / 1e3:.0f} kB, article {article}")
        for note in figuredata.problems(recs):
            print(f"    check: {note}")
        if dry_run:
            print("    DRY RUN: would upload, check, " + ("publish, " if publish else "") + "and add the index entry")
            continue
        uploader = FigshareUploader(token, r.name, str(mdfol))
        url = uploader._upload_file(article, str(bundle))
        if not figuredata.uploaded_intact(uploader, article, bundle):
            failed.append(f"{r.name}: Figshare's checksum does not match the local bundle; not published")
            continue
        if publish:
            figuredata.publish(token, article)
            print(f"    published a new version of article {article}")
        figuredata.update_index(index, r.name, figuredata.pointer(url, bundle, article, prov))
        done.append(r.name)
    for f in failed:
        print("NOT DONE", f)
    if done:
        print(f"\n{len(done)} run(s) added to {index.relative_to(repo)}. Commit it to the paper repo:\n"
              f"  git add {figuredata.INDEX_RELPATH} && git commit -m 'Figure explorer data for {len(done)} run(s)'")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mkfigs-backfill", description=__doc__.split("\n\n")[0])
    p.add_argument("command", choices=["list", "submit", "upload"])
    p.add_argument("--only", action="append", default=[], metavar="RUN", help="limit to this run (repeatable)")
    p.add_argument("--table", default=experiments.EXPERIMENTS_URL, help="Experiments page (URL or file)")
    p.add_argument("--notebooks", nargs="+", default=NOTEBOOKS, help="notebooks to run (submit only)")
    p.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    p.add_argument("--no-publish", action="store_true", help="upload and check, but do not publish (upload only)")
    args = p.parse_args(argv)

    notebooks_dir = Path.cwd()
    if not (notebooks_dir / "mkfigs.sh").exists():
        sys.exit("ERROR: run from the paper repo's notebooks/ folder (mkfigs.sh not found)")
    repo = notebooks_dir.parent
    runs = _select(experiments.load_experiments(args.table), set(args.only))
    if args.command == "list":
        return cmd_list(runs, repo)
    if args.command == "submit":
        return cmd_submit(runs, notebooks_dir, args.notebooks, args.dry_run)
    return cmd_upload(runs, repo, notebooks_dir, args.dry_run, publish=not args.no_publish)


if __name__ == "__main__":
    sys.exit(main())
