"""One file of figure data per run, and where the figure explorer finds it.

A run's ``*.lines.json`` records (written by ``MkmdWriter.savefig``) are bundled
into ``figure-data_<run>.json.gz`` and uploaded to the run's Figshare article.
The bundle's link and checksum go into one index, ``documentation/figure-data.json``
on the paper repo's main branch, keyed by run name:

* **new runs**: pushit adds the entry, and it is committed with the run's pages
  (the same commit that gets tagged), so it needs no extra step;
* **existing runs**: ``mkfigs-backfill upload`` adds the entries.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .configdoc import FIGSHARE_BASE_URL, _figshare_request, _md5

SCHEMA = 1
INDEX_RELPATH = Path("documentation") / "figure-data.json"


def bundle_name(run: str) -> str:
    return f"figure-data_{run}.json.gz"


def records(mdfol) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(mdfol).glob("*.lines.json"))]


def provenance(repo: Path, mdfol, mkfigs_commit: str, esm_file: str = "") -> dict:
    """Which code made the data and when. The time is the newest record's, so an
    unchanged run rebuilds a byte-identical bundle and pushit's MD5 check skips re-uploading it."""
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    newest = max((p.stat().st_mtime for p in Path(mdfol).glob("*.lines.json")), default=0)
    return {
        "paper_commit": commit.stdout.strip() if commit.returncode == 0 else "",
        "mkfigs_commit": mkfigs_commit,
        "generated_at": datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds"),
        "esm_file": esm_file,
    }


def build_bundle(mdfol, run: str, out_dir, prov: dict) -> Path:
    """Write the run's bundle deterministically (sorted keys, gzip mtime 0)."""
    payload = {"schema": SCHEMA, "run": run, "provenance": prov, "figures": records(mdfol)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
    path = Path(out_dir) / bundle_name(run)
    with open(path, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(raw)
    return path


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def problems(recs: list[dict]) -> list[str]:
    """Figures worth a look before publishing: blank figures and lines not credited to the run."""
    notes = {"empty": "blank figure (nothing drawn when saved)",
             "no-own-lines": "has lines, but none credited to this run",
             "all-lines-own": "no label names the run, so every line was taken as its own"}
    return [f"{r['png']}: {notes[r['status']]}" for r in recs if r["status"] in notes]


def pointer(url: str, bundle: Path, article_id: int, prov: dict) -> dict:
    return {"schema": SCHEMA, "url": url, "sha256": sha256(bundle), "bytes": bundle.stat().st_size,
            "figshare_article": article_id, **prov}


def update_index(index_path, run: str, entry: dict) -> None:
    path = Path(index_path)
    data = json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA, "runs": {}}
    data.setdefault("runs", {})[run] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def index_entry(index_path, run: str) -> dict | None:
    path = Path(index_path)
    return json.loads(path.read_text()).get("runs", {}).get(run) if path.exists() else None


def download_matches(url: str, expected_sha256: str) -> bool:
    """After publishing: the public link serves exactly the bundle that was uploaded."""
    import requests

    response = requests.get(url, timeout=120)
    return response.ok and hashlib.sha256(response.content).hexdigest() == expected_sha256


def uploaded_intact(uploader, article_id: int, path) -> bool:
    """True once Figshare has the complete file: its own computed MD5 equals the local one."""
    remote = uploader._find_remote_file(article_id, Path(path).name)
    return bool(remote) and remote.get("status") == "available" and remote.get("computed_md5") == _md5(str(path))


def publish(token: str, article_id: int) -> None:
    """Publish the article's pending changes as a new version (irreversible)."""
    _figshare_request("POST", FIGSHARE_BASE_URL.format(endpoint=f"account/articles/{article_id}/publish"), token)
