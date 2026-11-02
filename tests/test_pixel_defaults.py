"""Pixel defaults, safe migration, and output utilities."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sarpanel.cli import main
from sarpanel.config import OutputConfig, RunConfig
from sarpanel.observations import combine_pixel_frames, date_pixel_columns
from sarpanel.provenance import write_parquet_with_provenance
from sarpanel.runner import output_path_for, process_scene


def config_for(tmp_path, granularity=None):
    output = {"root": str(tmp_path / "run")}
    if granularity:
        output["granularity"] = granularity
    return RunConfig.from_dict({
        "name": "test", "aoi": {"source": "-84.6,42.6,-84.5,42.7"},
        "search": {"start_date": "2020-06-01", "end_date": "2020-06-30"},
        "processing": {"product_type": "GRD"}, "output": output,
    })


def pixels(scene_id="one", date="2020-06-08"):
    return date_pixel_columns(pd.DataFrame({
        "aoi_id": ["field"] * 4, "scene_id": [scene_id] * 4,
        "date": [date] * 4, "relative_orbit": [48] * 4,
        "x": [500005, 500015, 500005, 500015],
        "y": [4500005, 4500005, 4499995, 4499995],
        "VV_linear": [0.1, 0.2, 0.3, 0.4],
        "VH_linear": [0.01, 0.02, 0.03, 0.04],
        "VV_db": 10 * np.log10([0.1, 0.2, 0.3, 0.4]),
        "VH_db": 10 * np.log10([0.01, 0.02, 0.03, 0.04]),
        "inc_angle": [38, 38.1, 38.2, 38.3],
    }), date, scene_id)


def save(df, path, config=None):
    return write_parquet_with_provenance(df, path, {
        "config_checksum": config.checksum() if config else "test", "aoi_checksum": "aoi",
        "sarpanel_run_name": "test", "software": json.dumps({"snap": "14"}),
    })


def load_tool(name):
    path = Path(__file__).resolve().parents[1] / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_polygon_output_is_removed_and_legacy_pixel_config_is_accepted(tmp_path):
    assert not hasattr(OutputConfig(root=str(tmp_path)), "granularity")
    assert not hasattr(config_for(tmp_path), "granularity")
    config_for(tmp_path, "pixel")
    with pytest.raises(ValueError, match="removed"):
        config_for(tmp_path, "polygon")


def test_all_shipped_configs_are_pixel_only():
    for path in Path("configs").glob("*.yaml"):
        config = RunConfig.from_yaml(path)
        assert "granularity" not in config.as_dict()["output"]
    demo = RunConfig.from_yaml("configs/local_demo.yaml")
    assert demo.root == Path("runs/local_demo_wide")


@pytest.mark.parametrize("existing", ["polygon", "pixel"])
def test_resume_checks_granularity_before_download(tmp_path, monkeypatch, existing):
    import sarpanel.runner as runner

    cfg = config_for(tmp_path)
    scene = pd.Series({"scene_id": "one", "acquisition_date": "2020-06-08"})
    df = pixels()
    if existing == "polygon":
        df = pd.DataFrame({"aoi_id": ["field"], "scene_id": ["one"], "date": ["2020-06-08"]})
        df["n_pixels_valid"] = 4
    save(df, output_path_for(cfg, scene), cfg)

    def unexpected(*args, **kwargs):
        pytest.fail("Existing output must be inspected before any download")

    monkeypatch.setattr(runner, "download_scene", unexpected)
    result = process_scene(scene, None, cfg, None, None, {})
    if existing == "polygon":
        assert result.status == "failed"
        assert "new output.root" in result.error
    else:
        assert result.status == "skipped" and result.n_observations == 4


def test_aggregate_preserves_all_pixels_from_same_date_scenes(tmp_path, capsys):
    cfg = config_for(tmp_path)
    path = cfg.write(tmp_path / "config.yaml")
    for name in ("one", "two"):
        save(pixels(name), cfg.observations_dir / f"{name}.parquet", cfg)
    assert main(["aggregate", str(path)]) == 0
    df = pd.read_parquet(cfg.root / "test_observations.parquet")
    assert len(df) == 4
    assert not df.duplicated(["aoi_id", "x", "y"]).any()
    assert len([name for name in df if name.startswith("VV_db__2020-06-08__")]) == 2
    assert "incidence" in capsys.readouterr().out


def test_aggregate_rejects_old_polygon_files(tmp_path, capsys):
    cfg = config_for(tmp_path)
    path = cfg.write(tmp_path / "config.yaml")
    df = pd.DataFrame({"aoi_id": ["field"], "scene_id": ["one"], "date": ["2020-06-08"]})
    df["n_pixels_valid"] = 4
    save(df, cfg.observations_dir / "old.parquet", cfg)
    assert main(["aggregate", str(path)]) == 1
    assert "new output.root" in capsys.readouterr().out
    assert not (cfg.root / "test_observations.parquet").exists()


def test_check_output_accepts_pixel_schema(tmp_path, monkeypatch, capsys):
    cfg = config_for(tmp_path)
    path = cfg.write(tmp_path / "config.yaml")
    save(pixels(), cfg.observations_dir / "one.parquet")
    monkeypatch.setattr(sys, "argv", ["check_output", str(path)])
    assert load_tool("check_output").main() == 0
    assert "All checks passed" in capsys.readouterr().out


def test_compare_pixel_runs_matches_scene_and_coordinates(tmp_path, monkeypatch, capsys):
    for directory in ("baseline", "candidate"):
        for name in ("one", "two"):
            save(pixels(name), tmp_path / directory / f"{name}.parquet")
    monkeypatch.setattr(sys, "argv", [
        "compare_runs", str(tmp_path / "baseline"), str(tmp_path / "candidate")
    ])
    assert load_tool("compare_runs").main() == 0
    out = capsys.readouterr().out
    assert "matched: 4" in out
    assert "VV_db" in out and "radiometry preserved" in out


def test_wide_join_retains_missing_dates_and_same_day_granules():
    first, second = pixels("first"), pixels("second")
    second.loc[3, "x"] = 500025
    panel = combine_pixel_frames([first, second])
    assert len(panel) == 5
    assert panel["VV_linear__2020-06-08__first"].notna().sum() == 4
    assert panel["VV_linear__2020-06-08__second"].notna().sum() == 4
    assert panel["VV_linear__2020-06-08__first"].isna().sum() == 1
    assert panel["VV_linear__2020-06-08__second"].isna().sum() == 1


def test_repeated_acquisitions_and_duplicate_pixels_are_rejected():
    with pytest.raises(ValueError, match="Repeated acquisition"):
        combine_pixel_frames([pixels(), pixels()])
    duplicate = pd.concat([pixels(), pixels().iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate pixel"):
        combine_pixel_frames([duplicate])


def test_geocode_requests_a_common_grid():
    from sarpanel.processing import GeocodeCapabilities, ProcessingConfig

    caps = GeocodeCapabilities.__new__(GeocodeCapabilities)
    caps.params = {"spacing", "alignToStandardGrid", "standardGridOriginX", "standardGridOriginY"}
    kwargs = caps.build_kwargs(ProcessingConfig(product_type="GRD"), "in", "out", "tmp")
    assert kwargs["alignToStandardGrid"] is True
    assert kwargs["standardGridOriginX"] == 0
    assert kwargs["standardGridOriginY"] == 0


def test_two_dates_join_side_by_side_without_duplicate_pixel_rows():
    panel = combine_pixel_frames([pixels("first"), pixels("second", "2020-06-20")])
    assert len(panel) == 4
    assert "VV_db__2020-06-08__first" in panel
    assert "VV_db__2020-06-20__second" in panel
    assert not panel.duplicated(["aoi_id", "x", "y"]).any()


def test_shifted_grid_is_rejected():
    from rasterio.transform import from_origin
    from sarpanel.observations import validate_pixel_grid

    validate_pixel_grid(from_origin(500000, 4500000, 10, 10), 10)
    with pytest.raises(ValueError, match="common pixel"):
        validate_pixel_grid(from_origin(500005, 4500000, 10, 10), 10)


def test_legacy_long_pixel_file_is_not_reused(tmp_path, monkeypatch):
    cfg = config_for(tmp_path)
    scene = pd.Series({"scene_id": "one", "acquisition_date": "2020-06-08"})
    frame = pixels().rename(columns={name: name.split("__", 1)[0] for name in pixels()})
    save(frame, output_path_for(cfg, scene), cfg)
    result = process_scene(scene, None, cfg, None, None, {})
    assert result.status == "failed" and "dated pixel" in result.error


def test_fractional_spacing_uses_identical_pixel_keys_despite_float_roundoff():
    from sarpanel.observations import canonicalize_pixel_coordinates

    first, second = pixels("first"), pixels("second", "2020-06-20")
    for frame in (first, second):
        frame["x"] = (np.array([10000, 10001, 10000, 10001]) + 0.5) * 7.3
        frame["y"] = (np.array([20001, 20001, 20000, 20000]) + 0.5) * 7.3
    second["x"] += 1e-9
    second["y"] -= 1e-9
    panel = combine_pixel_frames([
        canonicalize_pixel_coordinates(first, 7.3),
        canonicalize_pixel_coordinates(second, 7.3),
    ])
    assert len(panel) == 4
    assert panel.filter(like="VV_linear__").notna().all().all()
