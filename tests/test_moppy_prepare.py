# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mkfigs.moppy_prepare (the ACCESS-MOPPy workarounds). Need access_moppy >= 1.8; skipped otherwise."""

from __future__ import annotations

import json

import pytest
import yaml

from mkfigs import moppy_prepare

if not moppy_prepare.moppy_version_ok():  # older MOPPy prompts for creator details on import
    pytest.skip("needs access-moppy >= 1.8", allow_module_level=True)
access_moppy = pytest.importorskip("access_moppy")
from access_moppy import _config, ocean_supergrid  # noqa: E402
from access_moppy import vocabulary_processors as vp  # noqa: E402
from access_moppy.esmval import cli_commands  # noqa: E402
from access_moppy.esmval.orchestrator import CMORiseOrchestrator  # noqa: E402


@pytest.fixture
def restore_moppy(monkeypatch):
    """Undo every in-memory patch after the test (monkeypatch restores what it saw first)."""
    monkeypatch.setattr(CMORiseOrchestrator, "prepare_recipe", CMORiseOrchestrator.prepare_recipe)
    monkeypatch.setattr(ocean_supergrid.Supergrid, "get_supergrid_path", ocean_supergrid.Supergrid.get_supergrid_path)
    monkeypatch.setattr(_config, "CONFIG_PATH", _config.CONFIG_PATH)
    monkeypatch.setattr(vp, "_CMIP6_TEMP_SOURCE_OVERRIDES", dict(vp._CMIP6_TEMP_SOURCE_OVERRIDES))
    monkeypatch.setattr(cli_commands, "_ACCESS_DATASETS", cli_commands._ACCESS_DATASETS)
    from access_moppy import base
    original = getattr(base, "_mkfigs_original_preprocess", base._preprocess_open_mfdataset)
    monkeypatch.setattr(base, "_preprocess_open_mfdataset", original)
    monkeypatch.delattr(base, "_mkfigs_original_preprocess", raising=False)


def test_main_applies_workarounds_and_passes_the_rest_to_moppy(tmp_path, monkeypatch, restore_moppy):
    hgrid = tmp_path / "ocean_hgrid.nc"
    hgrid.write_bytes(b"")
    seen = {}

    def fake_main_prepare(argv):
        seen["argv"] = argv
        seen["allowed"] = []
        monkeypatch.setattr("access_moppy.esmval.orchestrator.RecipeReader",
                            lambda path, allowed_datasets=None: seen["allowed"].append(allowed_datasets)
                            or type("R", (), {"tasks": []})())
        CMORiseOrchestrator(input_root=tmp_path, cache_dir=tmp_path / "c", model_id="ACCESS-OM3").prepare_recipe("r.yml")
        return 0

    monkeypatch.setattr(cli_commands, "main_prepare", fake_main_prepare)
    user_cfg = tmp_path / "out" / "moppy_user.yml"
    rc = moppy_prepare.main([
        "r.yml", "--input-root", "/in", "--model-id", "ACCESS-OM3", "--pattern", "Omon.mlotst:x*.nc",
        "--supergrid", str(hgrid), "--nominal-resolution", "25 km", "--user-config", str(user_cfg),
    ])
    assert rc == 0
    # mkfigs options are consumed; MOPPy's own are passed through unchanged
    assert seen["argv"] == ["r.yml", "--input-root", "/in", "--model-id", "ACCESS-OM3", "--pattern", "Omon.mlotst:x*.nc"]
    assert seen["allowed"] == [frozenset({"ACCESS-OM3"})]
    entry = vp._CMIP6_TEMP_SOURCE_OVERRIDES["ACCESS-OM3"]
    assert entry["source_id"] == "ACCESS-OM3"
    assert entry["model_component"]["ocean"]["native_nominal_resolution"] == "25 km"
    assert "ACCESS-OM2" not in json.dumps(entry["model_component"]) and "MOM5" not in json.dumps(entry["model_component"])
    assert entry["model_component"]["atmos"]["description"] == "none"
    assert ocean_supergrid.Supergrid.get_supergrid_path(None, "25 km") == str(hgrid)
    assert _config.CONFIG_PATH == str(user_cfg)
    assert yaml.safe_load(user_cfg.read_text()) == moppy_prepare.DEFAULT_CREATOR


def test_existing_user_config_is_kept(tmp_path, restore_moppy):
    cfg = tmp_path / "me.yml"
    cfg.write_text("creator_name: Me\n")
    moppy_prepare.use_user_config(str(cfg))
    assert cfg.read_text() == "creator_name: Me\n" and _config.CONFIG_PATH == str(cfg)


def test_register_source_id_leaves_models_already_in_the_cv_alone(restore_moppy):
    assert moppy_prepare.register_source_id("ACCESS-ESM1-5", "ACCESS-OM2") is False
    assert "ACCESS-ESM1-5" not in vp._CMIP6_TEMP_SOURCE_OVERRIDES


def test_missing_supergrid_is_an_error(tmp_path, restore_moppy):
    with pytest.raises(SystemExit):
        moppy_prepare.use_supergrid(str(tmp_path / "nope.nc"))


@pytest.mark.parametrize("argv, expected", [
    (["r.yml", "--model-id", "ACCESS-OM3"], "ACCESS-OM3"),
    (["r.yml", "--model-id=ACCESS-OM3"], "ACCESS-OM3"),
    (["r.yml"], ""),
])
def test_model_id_parsing(argv, expected):
    assert moppy_prepare._model_id(argv) == expected


def test_recipe_versions_are_not_pinned(tmp_path, restore_moppy):
    recipe = tmp_path / "r.yml"
    recipe.write_text(yaml.safe_dump({
        "datasets": [{"dataset": "ACCESS-OM3", "project": "CMIP6", "exp": "omip2"},
                     {"dataset": "ACCESS-ESM1-5", "project": "CMIP6", "exp": "historical"}],
        "diagnostics": {"d": {"variables": {"tos": {"mip": "Omon"}}}},
    }))
    moppy_prepare.allow_datasets(frozenset({"ACCESS-OM3"}))
    # No pinned copy: published ACCESS-ESM1-5 keeps its own version, and ESMValCore picks the newest cached one.
    assert cli_commands._pin_recipe_dataset_version(recipe, version="v20261112") == (None, 0)


def test_z_level_axes_are_read_as_native_mom6_axes(restore_moppy, monkeypatch):
    import numpy as np
    import xarray as xr
    from access_moppy import base

    moppy_prepare.support_z_level_output()
    moppy_prepare.support_z_level_output()   # idempotent: wraps once
    assert base._mkfigs_original_preprocess is not moppy_prepare._z_level_preprocess

    ds = xr.Dataset(
        {"so": (("time", "z_l", "yh", "xh"), np.zeros((1, 2, 1, 1)))},
        coords={"time": [0.0], "z_l": ("z_l", [1.0, 3.0], {"edges": "z_i"}), "z_i": [0.0, 2.0, 5.0],
                "yh": [0.0], "xh": [0.0]},
    )
    out = base._preprocess_open_mfdataset(ds, required_vars=frozenset({"so", "zl", "zi"}))
    assert out["so"].dims == ("time", "zl", "yh", "xh")
    assert list(out["zi"].values) == [0.0, 2.0, 5.0] and out["zl"].attrs["edges"] == "zi"
    # native MOM6 output is passed through unchanged
    native = ds.rename({"z_l": "zl", "z_i": "zi"})
    assert base._preprocess_open_mfdataset(native, required_vars=frozenset({"so"}))["so"].dims == ("time", "zl", "yh", "xh")
