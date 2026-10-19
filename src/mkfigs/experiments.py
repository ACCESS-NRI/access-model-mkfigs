"""The Experiments page as the list of runs, and how each run maps to its evaluation tag.

The Experiments page in access-om3-configs is the single list of runs for the
figure explorer, both for generating its data (``mkfigs-backfill``) and for
building the explorer itself (access-om3-live). Run names come from each row's
datastore path via :func:`mkfigs.linedata.run_name`, the same rule the
notebooks' labels and ``MkmdWriter`` use.

Pure functions only: no NCI checks and no import-time side effects, so other
repositories can import this module.
"""

from __future__ import annotations

import re
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .linedata import run_name

EXPERIMENTS_URL = (
    "https://raw.githubusercontent.com/ACCESS-NRI/access-om3-configs/main/"
    "documentation/docs/pages/Experiments.md"
)
_DATASTORE = re.compile(r"(?<![:/\w])(/[^\s`|<>)\]]+?\.json)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_FIGSHARE_ARTICLE = re.compile(r"figshare\.com/articles/[^)\s]*?/(\d+)")


@dataclass(frozen=True)
class Run:
    """One row of the Experiments page."""

    name: str
    datastore: str
    evaluation_url: str
    group: str
    table_name: str
    base_configuration: str = ""
    model_build: str = ""
    length: str = ""
    date_completed: str = ""
    description: str = ""

    @property
    def forcing(self) -> str:
        """IAF or RYF, from the datastore path: the same test the notebooks use
        (``IAF = esm_file.find('iaf') > 0``), falling back to Base Configuration."""
        return _forcing(self.datastore) or _forcing(self.base_configuration) or "unknown"

    @property
    def forcing_mismatch(self) -> bool:
        """True when Base Configuration names the other forcing (e.g. an RYF run listed with an iaf base)."""
        base = _forcing(self.base_configuration)
        return bool(base) and base != self.forcing

    @property
    def evaluation_slug(self) -> str:
        return self.evaluation_url.rstrip("/").rsplit("/", 1)[-1]

    def as_dict(self) -> dict:
        return {**asdict(self), "forcing": self.forcing, "forcing_mismatch": self.forcing_mismatch}


def _forcing(text: str) -> str | None:
    low = text.lower()
    if "ryf" in low:
        return "RYF"
    if "iaf" in low:
        return "IAF"
    return None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    text = _LINK.sub(lambda m: m.group(1), cell)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>|`", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_experiments(markdown: str) -> list[Run]:
    """Rows of every table on the page, in page order, read by column name."""
    runs, group, header = [], "", None
    for line in markdown.splitlines():
        if line.startswith("#"):
            group, header = line.lstrip("#").strip(), None
            continue
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = [_plain(c).lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        row = dict(zip(header, cells))

        def col(prefix: str, row: dict = row) -> str:
            return next((v for k, v in row.items() if k.startswith(prefix)), "")

        store = _DATASTORE.search(col("esm datastore") or line)
        links = [m.group(2) for m in _LINK.finditer(col("evaluation"))]
        if not store or not links:
            continue
        runs.append(Run(
            name=run_name(store.group(1)), datastore=store.group(1), evaluation_url=links[0], group=group,
            table_name=_plain(col("experiment")), base_configuration=_plain(col("base configuration")),
            model_build=_plain(col("model build")), length=_plain(col("length")),
            date_completed=_plain(col("date completed")), description=_plain(col("description")),
        ))
    return runs


def load_experiments(source: str = EXPERIMENTS_URL) -> list[Run]:
    """Read the Experiments page from a URL or a local file."""
    if re.match(r"https?://", source):
        with urllib.request.urlopen(source, timeout=60) as response:
            return parse_experiments(response.read().decode())
    return parse_experiments(Path(source).read_text())


def tag_for(run: Run, tags: list[str]) -> str | None:
    """The evaluation tag behind a row's Evaluation link (ReadTheDocs lowercases and drops '+')."""
    matches = [t for t in tags if t.lower().replace("+", "") == run.evaluation_slug]
    return matches[0] if len(matches) == 1 else None


def git_tags(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "tag", "--list"], capture_output=True, text=True, check=True)
    return out.stdout.split()


def figshare_article(repo: Path, tag: str) -> int | None:
    """The Figshare article linked from a tag's landing page."""
    out = subprocess.run(["git", "-C", str(repo), "show", f"{tag}:documentation/docs/pages/index.md"],
                         capture_output=True, text=True, check=False)
    match = _FIGSHARE_ARTICLE.search(out.stdout) if out.returncode == 0 else None
    return int(match.group(1)) if match else None
