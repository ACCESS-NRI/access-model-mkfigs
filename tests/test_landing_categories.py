"""Tests for the categorised landing-page tables built by build_run_summary."""
from __future__ import annotations

from datetime import datetime, timezone

from mkfigs.pushit import (
    STATUS_HEADER,
    UNCATEGORISED,
    build_run_summary,
    group_notebooks_by_category,
)

_GH = "https://github.com/org/repo/issues"
ISSUES = {
    "SST": f"[#9]({_GH}/9) — Global SST bias",
    "SSH": f"[#4]({_GH}/4) Dynamic sea level, [#5]({_GH}/5) SLA std",
    "Currents": f"[#18]({_GH}/18) SW Atlantic, [#41]({_GH}/41) WBC timeseries",
    "SeaIce_area": f"[#20]({_GH}/20) Extent",
}
CATEGORIES = {
    "Global temperature and salinity structure": [9, 10, "MLD_manual"],
    "Circulation": [4, 41],
    "Regional analysis": [18],
    "WOMBAT": [47],  # nothing run -> section dropped
}
RUN = datetime(2026, 10, 14, 2, 50, tzinfo=timezone.utc)


def test_grouping_by_issue_number_and_stem():
    g = group_notebooks_by_category(
        ["SST", "SSH", "Currents", "SeaIce_area", "MLD_manual", "Transects"], ISSUES, CATEGORIES
    )
    assert list(g) == ["Global temperature and salinity structure", "Circulation", UNCATEGORISED]
    assert UNCATEGORISED == "Miscellaneous"
    assert g["Global temperature and salinity structure"] == ["SST", "MLD_manual"]
    # Currents matches Circulation (#41) and Regional (#18): first category wins, once only
    assert g["Circulation"] == ["SSH", "Currents"]
    assert g[UNCATEGORISED] == ["SeaIce_area", "Transects"]


def test_stem_pins_override_issue_matches_and_warn(capsys):
    cats = {**CATEGORIES, "Regional analysis": [18, "Currents"]}
    g = group_notebooks_by_category(["Currents"], ISSUES, cats, warn=True)
    assert g == {"Regional analysis": ["Currents"]}
    assert capsys.readouterr().out == ""
    group_notebooks_by_category(["Currents"], ISSUES, CATEGORIES, warn=True)
    assert "Currents matches Circulation, Regional analysis" in capsys.readouterr().out


def test_issue_number_is_not_prefix_matched():
    # #4 must not match an issue #41 link
    g = group_notebooks_by_category(["Currents"], ISSUES, {"Only 4": [4]})
    assert "Only 4" not in g


def test_markdown_has_subheadings_jump_links_and_renamed_column():
    _, md = build_run_summary(
        "expt", "/esm.json", ["SST", "Currents"], ["SSH"], [], ["SeaIce_area"], RUN,
        notebook_issues=ISSUES, categories=CATEGORIES,
    )
    assert "**Jump to:** [Global temperature and salinity structure](#cat-global-temperature-and-salinity-structure) (1)" in md
    assert "### Circulation {#cat-circulation}" in md
    assert "### WOMBAT" not in md
    assert f"| Notebook | {STATUS_HEADER} | GitHub Issue(s) | Links |" in md
    assert "| Status |" not in md
    assert "| `SSH` | ❌ No |" in md
    assert "| `SeaIce_area` | ✅ Yes (earlier run) |" in md
    assert md.count("| `Currents` | ✅ Yes |") == 1
    assert "[Figures](experiments/expt/SST.md)&nbsp;·&nbsp;[Notebook](experiments/expt/notebooks/SST/)" in md


def test_no_categories_gives_single_flat_table():
    _, md = build_run_summary(
        "expt", "/esm.json", ["SST"], [], ["SSH"], [], RUN, notebook_issues=ISSUES,
    )
    assert "###" not in md and "Jump to" not in md
    assert md.count(f"| {STATUS_HEADER} |") == 1
    assert "| `SSH` | ⏭ Not run |" in md


def test_nav_stays_flat_and_alphabetical(tmp_path, monkeypatch):
    import yaml
    from mkfigs import pushit
    yml = tmp_path / "mkdocs.yml"
    yml.write_text(yaml.safe_dump({"nav": [
        {"Home": "pages/index.md"},
        {"Y": [{"Summary": "pages/experiments/old/Y.md"}]},
    ]}))
    monkeypatch.setattr(pushit, "MKDOCS_YML", yml)
    pushit.update_mkdocs_nav("expt", ["timeseries", "SST", "MLD"])
    nav = yaml.safe_load(yml.read_text())["nav"]
    assert [next(iter(i)) for i in nav] == ["Home", "MLD", "SST", "timeseries"]


def test_miscellaneous_pin_overrides_issue_match_and_tables_are_alphabetical():
    cats = {**CATEGORIES, UNCATEGORISED: ["SSH"]}
    g = group_notebooks_by_category(["SSH", "Currents"], ISSUES, cats)
    assert g == {"Circulation": ["Currents"], UNCATEGORISED: ["SSH"]}
    _, md = build_run_summary(
        "expt", "/esm.json", ["timeseries", "SST"], [], [], [], RUN,
        notebook_issues={"SST": ISSUES["SST"], "timeseries": f"[#3]({_GH}/3) x"},
        categories={"T": [9, 3]},
    )
    assert md.index("`SST`") < md.index("`timeseries`")


def test_intro_links_category_source(tmp_path, monkeypatch):
    from mkfigs import pushit
    monkeypatch.setattr(pushit, "DOCS_PAGES", tmp_path)
    pushit.update_top_index("expt", ["SST"], ["SSH"], [], "TABLE", "", categorised=True,
                            categories_source="https://github.com/org/repo/issues/23")
    md = (tmp_path / "index.md").read_text()
    assert "grouped below by category, following [issue #23](https://github.com/org/repo/issues/23);" in md
    assert "lists all available notebooks alphabetically" in md
    assert "delineated by notebook" not in md
    assert "Incomplete notebooks" not in md
