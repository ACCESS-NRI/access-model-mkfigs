"""Capture the line data a saved figure draws, for the access-om3-live figure explorer.

``MkmdWriter.savefig`` calls :func:`capture` on every figure it saves and writes
the record next to the PNG as ``<notebook>_<NN>.lines.json``. The record holds
exactly what was plotted, read back from the matplotlib figure, so no notebook
changes are needed:

* every line's values, label, colour and style;
* reference marks: ``axhline``/``axvline`` and constant two-point lines, and
  ``axhspan``/``axvspan`` bands and periods;
* panel titles and axis labels, with run names and date ranges stripped out for
  the figure key but kept, alongside the originals, as metadata for display.

Each line gets a role. A line is the run's **own** when its label contains the
run's name (see :func:`run_name`), or when it is unlabelled and drawn in the same
colour as an own line on the same axes (the faint monthly values behind a rolling
mean). In a figure where no label names the run, every line is taken as own and
the record says so (``status: "all-lines-own"``). Marker-only lines are
observations (``reference``), and everything else is ``other`` (other runs, OM2,
CM3), which the explorer resolves against the Experiments table. Setting
``gid="reference"``, ``gid="own"`` or ``gid="other"`` on a line overrides all of this.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import numpy as np
from matplotlib import dates as mdates
from matplotlib.colors import to_hex
from matplotlib.patches import Polygon, Rectangle

SCHEMA = 1
GENERIC_PARENT_DIRS = ("archive", "cm3-datastore")
SIG_FIGS = 6
ROLES = ("own", "other", "reference")

_DATE = r"\d{4}(?:-\d{2}(?:-\d{2})?)?"
_PERIOD = re.compile(
    rf"({_DATE})\s*(?:-|–|—|to)\s*({_DATE})(?:\s+(mean|maximum|minimum|average|climatology|max|min))?",
    re.IGNORECASE,
)


def run_name(datastore_path: str) -> str:
    """Name of the run a datastore belongs to.

    The datastore's folder, skipping generic catalogue folders, so
    ``<run>/datastore.json`` and ``<run>/archive/experiment_datastore.json`` both
    give ``<run>``. Same rule as access-om3-paper-1's ``get_exptname_from_path``,
    which builds the labels this module matches against.
    """
    parts = os.path.normpath(str(datastore_path)).split(os.sep)
    name = parts[-2] if len(parts) >= 2 else parts[-1]
    if name in GENERIC_PARENT_DIRS and len(parts) >= 3:
        name = parts[-3]
    return name


def split_title(text: str, run: str | None = None) -> tuple[str, list[str]]:
    """Return ``(title without run name or date ranges, date ranges found)``."""
    text = text or ""
    periods = [re.sub(r"\s+", " ", m.group(0)).strip() for m in _PERIOD.finditer(text)]
    clean = _PERIOD.sub(" ", text)
    if run:
        clean = clean.replace(run, " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"(\s+(in|for|of|from))+$", "", clean).strip(" -–—:,;")
    return clean, periods


def _label(artist) -> str:
    label = artist.get_label() or ""
    return "" if label.startswith("_") else label


def _colour(artist) -> str | None:
    try:
        return to_hex(artist.get_color(), keep_alpha=False)
    except (ValueError, TypeError, AttributeError):
        return None


def _values(values) -> tuple[list, str, str | None]:
    """JSON-ready values, the axis kind ("time" or "number") and any cftime calendar."""
    arr = np.asarray(values, dtype=object).ravel()
    first = next((v for v in arr if v is not None and not (isinstance(v, float) and np.isnan(v))), None)
    if isinstance(first, (np.datetime64, dt.date)) or hasattr(first, "calendar"):
        out = []
        for v in arr:
            if v is None or (isinstance(v, np.datetime64) and np.isnat(v)):
                out.append(None)
                continue
            if isinstance(v, np.datetime64):
                v = v.astype("datetime64[s]").item()
            if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
                v = dt.datetime(v.year, v.month, v.day)
            out.append(v.isoformat()[:19] if hasattr(v, "isoformat") else str(v)[:19])
        return out, "time", getattr(first, "calendar", None)
    try:
        nums = np.asarray(values, dtype=float).ravel()
    except (TypeError, ValueError):
        return [None if v is None else str(v) for v in arr], "category", None
    return [float(f"{v:.{SIG_FIGS}g}") if np.isfinite(v) else None for v in nums], "number", None


def _axis_values(ax, which: str, values):
    """Convert numbers stored in axis units (as patches keep them) back to dates where possible."""
    axis = ax.xaxis if which == "x" else ax.yaxis
    converter = axis.get_converter() if hasattr(axis, "get_converter") else getattr(axis, "converter", None)
    if converter is not None and "Date" in type(converter).__name__:
        return [mdates.num2date(v).replace(tzinfo=None).isoformat()[:19] for v in values]
    return [float(f"{v:.{SIG_FIGS}g}") for v in values]


def _blended(artist) -> bool:
    """True for artists drawn partly in axes units (axhline, axvspan, ...).

    Patches report their full transform (patch + data) from get_transform();
    the one they were drawn with is get_data_transform().
    """
    tr = artist.get_data_transform() if hasattr(artist, "get_data_transform") else artist.get_transform()
    return type(tr).__name__.startswith("Blended")


def _marks_and_lines(ax):
    lines, marks = [], []
    for line in ax.get_lines():
        if not line.get_visible():
            continue
        x, y = line.get_xdata(orig=True), line.get_ydata(orig=True)
        label = _label(line)
        if _blended(line) and len(x) == 2:  # axhline / axvline
            xs, ys = np.asarray(x, dtype=object), np.asarray(y, dtype=object)
            if list(xs) == [0, 1] and ys[0] == ys[1]:
                marks.append({"kind": "hline", "label": label, "y": _values([ys[0]])[0][0]})
            elif list(ys) == [0, 1] and xs[0] == xs[1]:
                marks.append({"kind": "vline", "label": label, "x": _values([xs[0]])[0][0]})
            continue
        xv, x_kind, calendar = _values(x)
        yv, _, _ = _values(y)
        markers_only = line.get_linestyle() in ("None", "none", " ", "") and line.get_marker() not in (None, "None", "none", "", " ")
        if len(xv) == 2 and yv[0] == yv[1] and label and not markers_only:  # constant obs line drawn with plot()
            marks.append({"kind": "hline", "label": label, "y": yv[0]})
            continue
        if not xv:
            continue
        alpha = line.get_alpha()
        style = "points" if markers_only else ("thin" if alpha is not None and alpha < 0.8 else "line")
        lines.append({"label": label, "role": None, "style": style, "color": _colour(line), "x": xv, "y": yv,
                      "_gid": line.get_gid(), "_x_kind": x_kind, "_calendar": calendar})
    for patch in ax.patches:
        if not (_blended(patch) and isinstance(patch, (Rectangle, Polygon))):
            continue
        verts = patch.get_xy() if isinstance(patch, Polygon) else None
        if isinstance(patch, Rectangle):
            x0, y0, w, h = patch.get_x(), patch.get_y(), patch.get_width(), patch.get_height()
        else:
            xs, ys = verts[:, 0], verts[:, 1]
            x0, y0, w, h = xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min()
        if np.isclose(w, 1.0) and np.isclose(x0, 0.0):  # axhspan: x in axes units, y in data units
            marks.append({"kind": "band", "label": _label(patch), "y": _axis_values(ax, "y", [y0, y0 + h])})
        elif np.isclose(h, 1.0) and np.isclose(y0, 0.0):  # axvspan
            marks.append({"kind": "span", "label": _label(patch), "x": _axis_values(ax, "x", [x0, x0 + w])})
    return lines, marks


def _has_content(ax) -> bool:
    return bool(ax.get_lines() or ax.collections or ax.images or ax.patches or ax.texts)


def _attribute(panels: list[dict], run: str) -> str:
    """Give every line a role; return the figure status."""
    def names_run(line):
        return bool(line["label"]) and run in line["label"]

    any_named = any(names_run(line) for p in panels for line in p["lines"])
    for p in panels:
        own_colours = {line["color"] for line in p["lines"] if names_run(line)}
        for line in p["lines"]:
            gid = line.pop("_gid", None)
            if gid in ROLES:
                line["role"] = gid
            elif names_run(line) or not line["label"] and line["color"] in own_colours:
                line["role"] = "own"
            elif line["style"] == "points":
                line["role"] = "reference"
            elif not any_named:
                line["role"] = "own"
            else:
                line["role"] = "other"
    n_lines = sum(len(p["lines"]) for p in panels)
    n_own = sum(1 for p in panels for line in p["lines"] if line["role"] == "own")
    if n_lines == 0:
        return "no-lines"
    if n_own == 0:
        return "no-own-lines"
    return "ok" if any_named else "all-lines-own"


def figure_key(notebook: str, suptitle: str, descriptors: list[str], seen: set | None) -> str:
    """Stable key: notebook, figure title and panel titles; a counter only on a clash."""
    base = "|".join([notebook, suptitle, " / ".join(descriptors)])
    key, n = base, 2
    while seen is not None and key in seen:
        key, n = f"{base}#{n}", n + 1
    if seen is not None:
        seen.add(key)
    return key


def capture(figure, *, run: str, notebook: str, fignum: int, title: str, caption: str, png: str,
            seen: set | None = None) -> dict:
    """Read a figure's line data and metadata into a JSON-ready record."""
    sup = getattr(figure, "_suptitle", None)
    sup_full = sup.get_text() if sup is not None else ""
    sup_clean, sup_periods = split_title(sup_full, run)
    title_clean, title_periods = split_title(title, run)
    panels = []
    content = False
    for ax in figure.get_axes():
        content = content or _has_content(ax)
        lines, marks = _marks_and_lines(ax)
        if not lines and not marks:
            continue
        full = ax.get_title() or ax.get_title(loc="left") or ax.get_title(loc="right")
        clean, periods = split_title(full, run)
        kinds = {line.pop("_x_kind") for line in lines}
        calendars = {c for c in (line.pop("_calendar") for line in lines) if c}
        panels.append({
            "title": clean, "title_full": full, "periods": periods,
            "x_label": ax.get_xlabel(), "y_label": ax.get_ylabel(),
            "x_kind": "time" if "time" in kinds else ("category" if kinds == {"category"} else "number"),
            "x_calendar": calendars.pop() if len(calendars) == 1 else None,
            "y_log": ax.get_yscale() == "log",
            "lines": lines, "marks": marks,
        })
    status = _attribute(panels, run) if panels else ("no-lines" if content else "empty")
    descriptors = [p["title"] or split_title(p["y_label"], run)[0] for p in panels]
    return {
        "schema": SCHEMA,
        "key": figure_key(notebook, sup_clean, descriptors, seen) if panels else None,
        "notebook": notebook, "fignum": fignum, "png": png, "run": run,
        "title": title, "title_clean": title_clean, "caption": caption,
        "suptitle": sup_clean, "suptitle_full": sup_full,
        "periods": sorted(set(title_periods + sup_periods + [x for p in panels for x in p["periods"]])),
        "status": status, "panels": panels,
    }


def summary(record: dict) -> str:
    """One console line per figure, so problems show up in the job log."""
    lines = [line for p in record["panels"] for line in p["lines"]]
    roles = {r: sum(1 for line in lines if line["role"] == r) for r in ROLES}
    marks = sum(len(p["marks"]) for p in record["panels"])
    flag = "" if record["status"] in ("ok", "no-lines") else "  <-- check"
    return (f"[linedata] {Path(record['png']).stem}: {record['status']}, {len(record['panels'])} panel(s), "
            f"{roles['own']} own, {roles['other']} other, {roles['reference']} reference line(s), {marks} mark(s){flag}")


def write(record: dict, path) -> None:
    Path(path).write_text(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
